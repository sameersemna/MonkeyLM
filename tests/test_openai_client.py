import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from monkeylm.config import load_settings
from monkeylm.models import openai_client
from monkeylm.models.openai_client import (
    DEFAULT_OPENAI_BASE_URL,
    build_vision_message,
    chat_completion,
    embed_text,
    extract_content,
    normalize_base_url,
)
from monkeylm.types import Settings


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    calls: list = []
    instances = 0

    def __init__(self, *args, **kwargs) -> None:
        type(self).instances += 1

    def __enter__(self):
        return self

    def __exit__(self, *args) -> bool:
        return False

    def close(self) -> None:
        pass

    def post(self, url, json=None, headers=None, timeout=None):
        type(self).calls.append({"url": url, "json": json, "headers": headers})
        return _FakeResponse({"choices": [{"message": {"content": "{}"}}]})


class OpenAICompatibleClientTests(unittest.TestCase):
    def setUp(self) -> None:
        openai_client.reset_client_cache()
        openai_client._WARNED_INSECURE_ENDPOINTS.clear()
        _FakeClient.calls = []
        _FakeClient.instances = 0

    def test_normalize_base_url_defaults_to_ollama_v1(self) -> None:
        self.assertEqual(normalize_base_url(None), f"{DEFAULT_OPENAI_BASE_URL}/v1")
        self.assertEqual(normalize_base_url(""), f"{DEFAULT_OPENAI_BASE_URL}/v1")

    def test_normalize_base_url_preserves_explicit_provider_paths(self) -> None:
        self.assertEqual(normalize_base_url("http://localhost:11434"), "http://localhost:11434/v1")
        self.assertEqual(normalize_base_url("http://gpu-box:11434/"), "http://gpu-box:11434/v1")
        self.assertEqual(normalize_base_url("https://api.openai.com/v1"), "https://api.openai.com/v1")
        self.assertEqual(normalize_base_url("https://openrouter.ai/api/v1/"), "https://openrouter.ai/api/v1")
        self.assertEqual(
            normalize_base_url("https://generativelanguage.googleapis.com/v1beta/openai"),
            "https://generativelanguage.googleapis.com/v1beta/openai",
        )
        self.assertEqual(
            normalize_base_url("https://models.inference.ai.azure.com"),
            "https://models.inference.ai.azure.com",
        )
        self.assertEqual(normalize_base_url("http://localhost:8000"), "http://localhost:8000")

    def test_extract_content_supports_openai_and_legacy_shapes(self) -> None:
        openai_response = {"choices": [{"message": {"content": "hello"}}]}
        legacy_response = {"message": {"content": "world"}}
        self.assertEqual(extract_content(openai_response), "hello")
        self.assertEqual(extract_content(legacy_response), "world")
        self.assertEqual(extract_content(None), "")

    def test_chat_completion_uses_configured_url_and_bearer_token(self) -> None:
        settings = Settings(
            openai_base_url="https://api.example.com/v1",
            openai_api_key="sk-test",
        )
        with patch("monkeylm.models.openai_client.httpx", SimpleNamespace(Client=_FakeClient)):
            response = chat_completion(
                settings,
                model="test-model",
                messages=[{"role": "user", "content": "hi"}],
                json_mode=True,
            )

        self.assertEqual(extract_content(response), "{}")
        call = _FakeClient.calls[0]
        self.assertEqual(call["url"], "https://api.example.com/v1/chat/completions")
        self.assertEqual(call["headers"]["Authorization"], "Bearer sk-test")
        self.assertEqual(call["json"]["model"], "test-model")
        self.assertEqual(call["json"]["response_format"], {"type": "json_object"})

    def test_chat_completion_reuses_http_client(self) -> None:
        settings = Settings(openai_base_url="https://api.example.com/v1")
        with patch("monkeylm.models.openai_client.httpx", SimpleNamespace(Client=_FakeClient)):
            chat_completion(settings, model="test-model", messages=[{"role": "user", "content": "hi"}])
            chat_completion(settings, model="test-model", messages=[{"role": "user", "content": "again"}])

        self.assertEqual(_FakeClient.instances, 1)
        self.assertEqual(len(_FakeClient.calls), 2)

    def test_plaintext_remote_endpoint_with_key_warns_once(self) -> None:
        settings = Settings(openai_base_url="http://example.test:1234/v1", openai_api_key="sk-test")
        with patch("monkeylm.models.openai_client.httpx", SimpleNamespace(Client=_FakeClient)):
            with patch("monkeylm.models.openai_client._local_service_log") as log:
                chat_completion(settings, model="test-model", messages=[{"role": "user", "content": "hi"}])
                chat_completion(settings, model="test-model", messages=[{"role": "user", "content": "hi"}])

        self.assertEqual(log.call_count, 1)

    def test_chat_completion_omits_authorization_without_api_key(self) -> None:
        settings = Settings(openai_base_url="http://localhost:11434", openai_api_key="")
        with patch("monkeylm.models.openai_client.httpx", SimpleNamespace(Client=_FakeClient)):
            chat_completion(
                settings,
                model="test-model",
                messages=[{"role": "user", "content": "hi"}],
            )

        call = _FakeClient.calls[0]
        self.assertEqual(call["url"], "http://localhost:11434/v1/chat/completions")
        self.assertNotIn("Authorization", call["headers"])

    def test_embed_text_parses_openai_embedding_response(self) -> None:
        class _EmbeddingClient(_FakeClient):
            def post(self, url, json=None, headers=None, timeout=None):
                type(self).calls.append({"url": url, "json": json, "headers": headers})
                return _FakeResponse({"data": [{"embedding": [0.1, 0.2, 0.3]}]})

        settings = Settings(openai_base_url="http://localhost:11434")
        with patch("monkeylm.models.openai_client.httpx", SimpleNamespace(Client=_EmbeddingClient)):
            vector = embed_text(settings, model="nomic-embed-text", text="layout")

        self.assertEqual(vector, [0.1, 0.2, 0.3])
        self.assertEqual(_EmbeddingClient.calls[0]["url"], "http://localhost:11434/v1/embeddings")

    def test_build_vision_message_embeds_base64_image(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "shot.png"
            image_path.write_bytes(b"\x89PNG\r\n\x1a\n")
            message = build_vision_message("describe", str(image_path))

        self.assertEqual(message["role"], "user")
        self.assertEqual(message["content"][0], {"type": "text", "text": "describe"})
        self.assertTrue(message["content"][1]["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_load_settings_reads_openai_environment(self) -> None:
        env = {"OPENAI_BASE_URL": "http://example.test:1234/v1", "OPENAI_API_KEY": "secret-key"}
        with patch.dict(os.environ, env, clear=True):
            with patch("dotenv.dotenv_values", return_value={}):
                with patch("monkeylm.config.os.makedirs"):
                    settings = load_settings()

        self.assertEqual(settings.openai_base_url, "http://example.test:1234/v1")
        self.assertEqual(settings.openai_api_key, "secret-key")

    def test_load_settings_falls_back_to_legacy_ollama_variables(self) -> None:
        env = {"OLLAMA_HOST": "gpu-box:11434", "OLLAMA_API_KEY": "ollama-secret"}
        with patch.dict(os.environ, env, clear=True):
            with patch("dotenv.dotenv_values", return_value={}):
                with patch("monkeylm.config.os.makedirs"):
                    settings = load_settings()

        self.assertEqual(settings.openai_base_url, "http://gpu-box:11434/v1")
        self.assertEqual(settings.openai_api_key, "ollama-secret")

    def test_openai_variables_override_legacy_ollama_variables(self) -> None:
        env = {
            "OLLAMA_HOST": "gpu-box:11434",
            "OLLAMA_API_KEY": "legacy-key",
            "OPENAI_BASE_URL": "https://api.example.com/v1",
            "OPENAI_API_KEY": "new-key",
        }
        with patch.dict(os.environ, env, clear=True):
            with patch("dotenv.dotenv_values", return_value={}):
                with patch("monkeylm.config.os.makedirs"):
                    settings = load_settings()

        self.assertEqual(settings.openai_base_url, "https://api.example.com/v1")
        self.assertEqual(settings.openai_api_key, "new-key")


if __name__ == "__main__":
    unittest.main()
