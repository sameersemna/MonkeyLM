"""Transport for OpenAI-compatible chat completion and embedding APIs.

Requests are sent to ``{OPENAI_BASE_URL}/chat/completions`` and
``{OPENAI_BASE_URL}/embeddings``. ``OPENAI_BASE_URL`` defaults to
``http://localhost:11434`` (Ollama), whose OpenAI-compatible path is ``/v1``;
that path is added automatically for Ollama's default port. When
``OPENAI_API_KEY`` is set, it is sent as a bearer token. Both values are
loaded from the project-root ``.env`` file by :mod:`monkeylm.config`.
"""

from __future__ import annotations

import atexit
import base64
import json
import mimetypes
import threading
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional
from urllib.parse import urlsplit

from monkeylm.config import Settings, httpx


DEFAULT_OPENAI_BASE_URL = "http://localhost:11434"
DEFAULT_OPENAI_TIMEOUT_SECONDS = 15.0

_CLIENTS: Dict[str, Any] = {}
_CLIENTS_LOCK = threading.Lock()
_HTTP_TIMEOUT = getattr(httpx, "Timeout", None)


class ModelAPIError(RuntimeError):
    """Raised when an OpenAI-compatible endpoint returns an unusable response."""

    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _require_httpx() -> Any:
    if httpx is None:
        raise RuntimeError(
            "httpx is required to call the configured LLM endpoint; install it with `pip install httpx`."
        )
    return httpx


def normalize_base_url(base_url: Optional[str]) -> str:
    """Return the configured API base URL.

    Ollama's default port is normalized to its OpenAI-compatible ``/v1`` path.
    Any other explicitly configured URL is used unchanged so providers with
    custom prefixes (for example ``/v1beta/openai``) are not corrupted.
    """
    base = (base_url or "").strip().rstrip("/")
    if not base:
        base = DEFAULT_OPENAI_BASE_URL
    parsed = urlsplit(base)
    if parsed.path in ("", "/"):
        try:
            port = parsed.port
        except ValueError:
            port = None
        if port == 11434:
            base = f"{base}/v1"
    return base


def _client_for(settings: Settings) -> Any:
    http = _require_httpx()
    base_url = normalize_base_url(settings.openai_base_url)
    with _CLIENTS_LOCK:
        client = _CLIENTS.get(base_url)
        if client is None:
            client = http.Client()
            _CLIENTS[base_url] = client
        return client


def reset_client_cache() -> None:
    """Close and clear cached HTTP clients (used on shutdown and by tests)."""
    with _CLIENTS_LOCK:
        clients = list(_CLIENTS.values())
        _CLIENTS.clear()
    for client in clients:
        try:
            client.close()
        except Exception:
            pass


atexit.register(reset_client_cache)


def _endpoint(settings: Settings, path: str) -> str:
    return f"{normalize_base_url(settings.openai_base_url)}/{path.lstrip('/')}"


def _headers(settings: Settings) -> Dict[str, str]:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    api_key = (settings.openai_api_key or "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _timeout(settings: Settings, timeout_seconds: Optional[float]) -> Any:
    if timeout_seconds is None:
        timeout_seconds = settings.ollama_timeout_seconds or DEFAULT_OPENAI_TIMEOUT_SECONDS
    total = max(0.001, float(timeout_seconds))
    if _HTTP_TIMEOUT is None:
        return total
    connect = min(total, 2.0)
    return _HTTP_TIMEOUT(connect=connect, read=total, write=total, pool=connect)


def _chat_payload(
    model: str,
    messages: List[Dict[str, Any]],
    *,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    max_tokens: Optional[int] = None,
    json_mode: bool = False,
    stream: bool = False,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"model": model, "messages": messages, "stream": bool(stream)}
    if temperature is not None:
        payload["temperature"] = float(temperature)
    if top_p is not None:
        payload["top_p"] = float(top_p)
    if max_tokens is not None:
        payload["max_tokens"] = int(max_tokens)
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    return payload


def _decode_json(response: Any, url: str) -> Dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        text = (response.text or "").strip()
        if text.endswith("data: [DONE]"):
            text = text[: -len("data: [DONE]")].rstrip()
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise ModelAPIError(
                f"LLM endpoint {url} returned invalid JSON: {response.text[:500]}"
            ) from exc
    if not isinstance(data, dict):
        raise ModelAPIError(f"LLM endpoint {url} returned an unexpected payload: {str(data)[:500]}")
    return data


def _raise_for_status(response: Any, url: str) -> None:
    if response.status_code >= 400:
        raise ModelAPIError(
            f"LLM endpoint {url} returned {response.status_code}: {response.text[:500]}",
            status_code=response.status_code,
        )


def chat_completion(
    settings: Settings,
    *,
    model: str,
    messages: List[Dict[str, Any]],
    timeout_seconds: Optional[float] = None,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    max_tokens: Optional[int] = None,
    json_mode: bool = False,
) -> Dict[str, Any]:
    """Send a chat completion request and return the decoded response."""
    url = _endpoint(settings, "chat/completions")
    payload = _chat_payload(
        model,
        messages,
        temperature=temperature,
        top_p=top_p,
        max_tokens=max_tokens,
        json_mode=json_mode,
    )
    client = _client_for(settings)
    response = client.post(
        url,
        json=payload,
        headers=_headers(settings),
        timeout=_timeout(settings, timeout_seconds),
    )
    _raise_for_status(response, url)
    return _decode_json(response, url)


def stream_chat_completion(
    settings: Settings,
    *,
    model: str,
    messages: List[Dict[str, Any]],
    timeout_seconds: Optional[float] = None,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
) -> Iterator[Dict[str, Any]]:
    """Yield server-sent-event chunks from a streaming chat completion."""
    url = _endpoint(settings, "chat/completions")
    payload = _chat_payload(
        model,
        messages,
        temperature=temperature,
        top_p=top_p,
        stream=True,
    )
    client = _client_for(settings)
    with client.stream(
        "POST",
        url,
        json=payload,
        headers=_headers(settings),
        timeout=_timeout(settings, timeout_seconds),
    ) as response:
        if response.status_code >= 400:
            body = response.read().decode("utf-8", errors="replace")
            raise ModelAPIError(
                f"LLM endpoint {url} returned {response.status_code}: {body[:500]}",
                status_code=response.status_code,
            )
        for line in response.iter_lines():
            stripped = line.strip()
            if not stripped.startswith("data:"):
                continue
            raw = stripped[5:].strip()
            if raw == "[DONE]":
                break
            try:
                chunk = json.loads(raw)
            except ValueError:
                continue
            if isinstance(chunk, dict):
                yield chunk


def embed_text(
    settings: Settings,
    *,
    model: str,
    text: str,
    timeout_seconds: Optional[float] = None,
) -> List[float]:
    """Embed ``text`` through the configured ``/v1/embeddings`` endpoint."""
    url = _endpoint(settings, "embeddings")
    payload = {"model": model, "input": text}
    client = _client_for(settings)
    response = client.post(
        url,
        json=payload,
        headers=_headers(settings),
        timeout=_timeout(settings, timeout_seconds),
    )
    _raise_for_status(response, url)
    data = _decode_json(response, url)

    items = data.get("data") or []
    if items and isinstance(items[0], dict):
        embedding = items[0].get("embedding")
        if isinstance(embedding, list) and embedding:
            return [float(value) for value in embedding]

    legacy = data.get("embedding")
    if isinstance(legacy, list) and legacy:
        return [float(value) for value in legacy]

    raise ModelAPIError(f"LLM endpoint {url} returned no usable embedding: {str(data)[:300]}")


def extract_content(response: Any) -> str:
    """Extract assistant text from an OpenAI response (legacy Ollama shape accepted)."""
    if not isinstance(response, dict):
        return ""

    choices = response.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        choice = choices[0]
        message = choice.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return "".join(
                    part.get("text", "")
                    for part in content
                    if isinstance(part, dict) and part.get("type") in (None, "text")
                )
        text = choice.get("text")
        if isinstance(text, str):
            return text

    message = response.get("message")
    if isinstance(message, dict) and isinstance(message.get("content"), str):
        return message["content"]
    return ""


def build_vision_message(prompt: str, image_path: str) -> Dict[str, Any]:
    """Build an OpenAI vision-format user message with a base64 data URL."""
    path = Path(image_path)
    mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{encoded}"}},
        ],
    }
