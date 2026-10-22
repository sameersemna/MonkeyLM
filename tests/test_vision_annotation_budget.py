"""Regression tests for the screenshot-annotation token-ceiling regression.

A vision LLM that emits hidden reasoning before its JSON body can exhaust a
small ``max_tokens`` ceiling and return ``finish_reason="length"`` with
*empty* content.  The annotation pipeline treated that identically to a
legitimate "no defect found" reply, so no box was parsed, the drawer was
never called, and a byte-identical copy was published as
``step_NNN_annotated.png`` — every report claimed an annotation while the
image carried no marks.

These tests pin:

* the annotation and verifier calls read their token ceiling from
  ``Settings`` instead of a hardcoded literal,
* the default ceiling is large enough for reasoning vision models,
* a truncated reply is logged loudly instead of silently shipping a blank
  annotation.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest

from monkeylm.config import load_settings
from monkeylm.models.vision import annotate_relevant_screenshot
from monkeylm.models.vision.verifier import run_verification
from monkeylm.types import Settings

PILImage = pytest.importorskip("PIL.Image", reason="Pillow is required for annotation tests")


def _settings(tmp_path, **overrides) -> Settings:
    base: Dict[str, Any] = {
        "vision_model": "gemini-3-flash-preview",
        "pdf_vision_model": "gemini-3-flash-preview",
        "vision_annotation_timeout_seconds": 5.0,
        "vision_annotation_max_tokens": 2048,
        "vision_verifier_max_tokens": 2048,
        "pdf_generate": True,
    }
    base.update(overrides)
    settings = Settings(**base)
    settings._output_dir_override = str(tmp_path)
    return settings


def _response(content: str, finish_reason: str = "stop") -> Dict[str, Any]:
    return {
        "choices": [
            {
                "finish_reason": finish_reason,
                "message": {"content": content},
            }
        ]
    }


def test_defaults_budget_for_reasoning_vision_models() -> None:
    """Defaults must clear a reasoning model's overhead.

    The regression shipped 512/256 tokens, which truncation-proofs any model
    that thinks before answering.
    """
    settings = load_settings()
    assert settings.vision_annotation_max_tokens >= 2048
    assert settings.vision_verifier_max_tokens >= 2048


def test_annotation_uses_configured_token_ceiling(tmp_path, monkeypatch) -> None:
    image_path = str(tmp_path / "shot.png")
    PILImage.new("RGB", (640, 360), color="white").save(image_path)

    captured: Dict[str, Any] = {}

    def fake_chat_completion(
        _settings,
        *,
        model,
        messages,
        timeout_seconds=None,
        json_mode=False,
        temperature=None,
        max_tokens=None,
    ):
        captured["max_tokens"] = max_tokens
        return _response(
            json.dumps(
                {
                    "box_2d": [0.1, 0.2, 0.4, 0.6],
                    "description": "Primary CTA is visually disabled",
                    "confidence": 0.9,
                }
            )
        )

    monkeypatch.setattr(
        "monkeylm.models.openai_client.chat_completion", fake_chat_completion
    )

    settings = _settings(tmp_path, vision_annotation_max_tokens=4096)
    out_path, description, verdict = __import__("asyncio").run(
        annotate_relevant_screenshot(settings, image_path, "status=FAILED", step_num=2)
    )

    assert captured["max_tokens"] == 4096
    assert verdict["box_2d"]
    assert description == "Primary CTA is visually disabled"
    assert out_path.endswith("_annotated.png")


def test_truncated_reply_is_logged_not_silently_swallowed(tmp_path, monkeypatch) -> None:
    """``finish_reason='length'`` + empty content must leave a breadcrumb.

    This is the exact shape of the regression: the caller cannot recover the
    marks, but the operator must be told why.
    """
    image_path = str(tmp_path / "shot.png")
    PILImage.new("RGB", (640, 360), color="white").save(image_path)

    def fake_chat_completion(*args, **kwargs):
        return _response("", finish_reason="length")

    monkeypatch.setattr(
        "monkeylm.models.openai_client.chat_completion", fake_chat_completion
    )

    logged: List[str] = []
    monkeypatch.setattr(
        "monkeylm.models.vision._legacy.config._local_service_log",
        lambda msg, *args, **kwargs: logged.append(str(msg)),
    )

    settings = _settings(tmp_path, vision_annotation_max_tokens=256)
    out_path, description, verdict = __import__("asyncio").run(
        annotate_relevant_screenshot(settings, image_path, "status=FAILED", step_num=2)
    )

    assert description is None
    assert verdict["box_2d"] == [0.0, 0.0, 0.0, 0.0]
    assert any("token ceiling" in line for line in logged), logged


def test_verifier_uses_configured_token_ceiling(tmp_path, monkeypatch) -> None:
    image_path = str(tmp_path / "shot.png")
    PILImage.new("RGB", (640, 360), color="white").save(image_path)

    captured: Dict[str, Any] = {}

    def fake_chat_completion(
        _settings,
        *,
        model,
        messages,
        timeout_seconds=None,
        json_mode=False,
        temperature=None,
        max_tokens=None,
    ):
        captured["max_tokens"] = max_tokens
        return _response(json.dumps({"answer": "yes", "confidence": 0.8}))

    monkeypatch.setattr(
        "monkeylm.models.vision.verifier.chat_completion", fake_chat_completion
    )

    settings = _settings(tmp_path, vision_verifier_max_tokens=4096)
    result = __import__("asyncio").run(
        run_verification(
            settings,
            image_path,
            issue_context="status=FAILED",
            candidate_box=[0.1, 0.2, 0.4, 0.6],
            description="Primary CTA is visually disabled",
        )
    )

    assert captured["max_tokens"] == 4096
    assert result == ("yes", 0.8)


if __name__ == "__main__":
    pytest.main([__file__])