"""Ollama client wrappers - chat with retry, JSON parsing, and security helpers."""

from __future__ import annotations

import asyncio
import json
import random
import re
from typing import Any, Dict, List, Optional

import ollama

from monkeylm.config import OLLAMA_DECISION_OPTIONS, Settings, _local_service_log


_MAX_LLM_INPUT_CHARS = 512_000
_MAX_PROMPT_DATA_CHARS = 64_000
_CONTROL_CHARS = "".join(chr(c) for c in range(0, 32) if c not in (9, 10, 13))
_BOUNDARY_TAG_RE = re.compile(r"<<<[^>]{0,64}>>>")


def _redact_secrets(value: str) -> str:
    if not isinstance(value, str):
        return value
    return re.sub(
        r"(?i)(bearer\s+|api[_-]?key[_-]?=\s*|sk-[A-Za-z0-9]{6})[A-Za-z0-9\-_]{4,}",
        r"\1***REDACTED***",
        value,
    )


def _sanitize_prompt_input(value: Any, max_chars: int = _MAX_PROMPT_DATA_CHARS) -> str:
    if value is None:
        return ""
    text = str(value)
    text = text.translate(str.maketrans("", "", _CONTROL_CHARS))
    text = text.replace("‮", "").replace("‭", "")
    text = _BOUNDARY_TAG_RE.sub(" ", text)
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n...[truncated {len(text) - max_chars} chars]"
    return text


def _extract_first_json_object(text: str) -> Optional[Dict[str, Any]]:
    if not isinstance(text, str) or not text:
        return None
    if len(text) > _MAX_LLM_INPUT_CHARS:
        text = text[:_MAX_LLM_INPUT_CHARS]
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(text[start : i + 1])
                    except Exception:
                        parsed = None
                    if isinstance(parsed, dict):
                        return parsed
                    break
        start = text.find("{", start + 1)
    return None


def _safe_json_parse(text: str) -> Optional[Any]:
    if not isinstance(text, str) or not text.strip():
        return None
    cleaned = text.replace("```json", "").replace("```", "").strip()
    if len(cleaned) > _MAX_LLM_INPUT_CHARS:
        cleaned = cleaned[:_MAX_LLM_INPUT_CHARS]
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    return _extract_first_json_object(cleaned)


def _is_ollama_overload_error(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    if status_code == 503:
        return True
    exc_str = str(exc).lower()
    return any(marker in exc_str for marker in ["503", "overload", "queue", "busy", "too many requests"])


async def _ollama_chat_with_retry(
    *,
    settings: Settings,
    model: str,
    messages: List[Dict[str, str]],
    timeout_seconds: float,
    max_retries: int = 3,
) -> Optional[ollama.ChatResponse]:
    if settings.ollama_model and model != settings.ollama_model:
        print(f"   └─ 🤖 Calling fallback model {model}")
    else:
        print(f"   └─ 🤖 Calling {model}")

    base_delay = 1.0
    last_exc: Optional[Exception] = None

    for attempt in range(1, max_retries + 1):
        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    ollama.chat,
                    model=model,
                    messages=messages,
                    format="json",
                    options=OLLAMA_DECISION_OPTIONS,
                ),
                timeout=timeout_seconds,
            )
            return response
        except asyncio.TimeoutError as exc:
            last_exc = exc
            _local_service_log(
                f"Ollama inference timed out after {timeout_seconds}s (attempt {attempt}/{max_retries})",
                settings.output_dir,
            )
        except Exception as exc:
            last_exc = exc
            if _is_ollama_overload_error(exc):
                _local_service_log(
                    f"Ollama inference overloaded (attempt {attempt}/{max_retries}): {_redact_secrets(str(exc))}",
                    settings.output_dir,
                )
            else:
                return None

        if attempt >= max_retries:
            break

        delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0.0, 2.0)
        _local_service_log(
            f"Backing off from Ollama for {delay:.2f}s before retry {attempt + 1}/{max_retries}",
            settings.output_dir,
        )
        await asyncio.sleep(delay)

    if last_exc is not None:
        _local_service_log(
            f"Ollama inference failed after {max_retries} attempts: {_redact_secrets(str(last_exc))}",
            settings.output_dir,
        )
    return None
