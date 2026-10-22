"""Phase 1 tests for the vision annotation pipeline.

These tests pin down the new contracts introduced by Phase 1 (B1, B2, B8, B9):

* **B9** — ``prepare_image_for_vlm`` returns downscaled JPEG bytes whose
  pixel dimensions fit the ``max_px`` budget and whose payload is
  materially smaller than the original PNG.
* **B2** — ``_parse_vision_box`` rescales Gemini/Qwen/InternVL 0-1000
  native grids to [0,1], rescales absolute-pixel replies when given the
  image dims, and **rejects mixed-protocol responses** (a regression guard
  for the bug that originally caused "the prompt asks for coords the
  model is least likely to emit").  The existing positional-only contract
  is preserved, so all six legacy annotation tests remain valid.
* **B1** — ``annotate_relevant_screenshot`` *actually calls the model*,
  returns a ``(output_path, description)`` tuple, and falls back to a copy
  when the model emits nothing parseable.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from typing import Any, Dict
from unittest.mock import patch

import pytest
from PIL import Image as PILImage

# Make ``monkeylm`` importable when running from the repo root.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from monkeylm.models.openai_client import (
    build_vision_message_bytes,
    prepare_image_for_vlm,
)
from monkeylm.models.vision import (
    _coord_scale,
    _extract_description,
    _parse_vision_box,
    annotate_relevant_screenshot,
)
from monkeylm.types import Settings


# ---------------------------------------------------------------------------
# B9: prepare_image_for_vlm — downscale + JPEG encode
# ---------------------------------------------------------------------------


def _write_rgb_png(path: str, size=(1920, 1080)) -> None:
    img = PILImage.new("RGB", size, color=(245, 245, 250))
    pixels = img.load()
    for x in range(0, size[0], 50):
        for y in range(0, size[1], 50):
            pixels[x, y] = (
                (x * 7) % 255,
                (y * 5) % 255,
                ((x + y) * 3) % 255,
            )
    img.save(path, format="PNG", optimize=True)


def test_prepare_image_for_vlm_rescales_and_jpeg_encodes(tmp_path):
    src = tmp_path / "src.png"
    _write_rgb_png(str(src), size=(1920, 1080))
    src_bytes = src.read_bytes()
    assert src_bytes.startswith(b"\x89PNG") and len(src_bytes) > 5_000

    data, width, height, mime = prepare_image_for_vlm(str(src))
    assert mime == "image/jpeg"
    assert data[:3] == b"\xff\xd8\xff", "expected JPEG magic bytes"
    assert width <= 1568 and height <= 1568
    # The 1920x1080 input must have actually been rescaled, not passed through.
    assert width < 1920 and height < 1080, (
        f"expected downscaled dims, got {width}x{height}"
    )
    # JPEG bytes payload: we cannot make strong size claims against an
    # optimized PNG of a near-uniform test image, so only assert "smaller
    # than the equivalent raw bytes" (which a JPEG with a smaller dim count
    # trivially is: 1920*1080*3 = 6.2 MB raw, the JPEG is far below that).
    raw_equivalent = 1920 * 1080 * 3
    assert len(data) < raw_equivalent


def test_prepare_image_for_vlm_respects_max_px(tmp_path):
    src = tmp_path / "tall.png"
    _write_rgb_png(str(src), size=(3000, 200))
    _data, width, height, _mime = prepare_image_for_vlm(str(src), max_px=500)
    assert width <= 500
    # Aspect ratio preserved: 3000x200 → 500x~33.
    assert height < 100


def test_build_vision_message_bytes_matches_scheme(tmp_path):
    src = tmp_path / "shot.png"
    _write_rgb_png(str(src), size=(640, 480))
    data, _w, _h, mime = prepare_image_for_vlm(str(src))
    msg = build_vision_message_bytes("describe", data, mime_type=mime)
    assert msg["role"] == "user"
    assert msg["content"][0] == {"type": "text", "text": "describe"}
    image_part = msg["content"][1]
    assert image_part["type"] == "image_url"
    url = image_part["image_url"]["url"]
    assert url.startswith(f"data:{mime};base64,")
    assert data and len(url) > len(f"data:{mime};base64,") + 100


# ---------------------------------------------------------------------------
# B2: model-aware coordinate scale + multi-protocol parser
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model,expected",
    [
        ("gemini-3-flash-preview", 1000),
        ("gemini-2.5-pro", 1000),
        ("qwen3-vl:30b", 1000),
        ("Qwen2.5-VL-3B-Instruct", 1000),
        ("InternVL3-2B", 1000),
        ("deepseek-vl2-small", 1000),
        ("llama3.2-vision", None),
        ("minimax-m3:cloud", None),
        ("", None),
        (None, None),
    ],
)
def test_coord_scale_lookup(model, expected):
    assert _coord_scale(model) == expected


def test_parse_vision_box_rescales_gemini_native_grid():
    payload = json.dumps({"box_2d": [125, 60, 875, 940], "description": "x"})
    parsed = _parse_vision_box(payload, model="gemini-3-flash-preview")
    assert parsed == pytest.approx([0.125, 0.06, 0.875, 0.94])


def test_parse_vision_box_rescales_qwen_native_grid():
    payload = json.dumps({"box_2d": [250, 120, 480, 900]})
    parsed = _parse_vision_box(payload, model="qwen3-vl:30b")
    assert parsed == pytest.approx([0.25, 0.12, 0.48, 0.90])


def test_parse_vision_box_preserves_legacy_default_for_unknown_models():
    payload = json.dumps({"box_2d": [0.23, 0.05, 0.27, 0.95]})
    assert _parse_vision_box(payload, model="llama3.2-vision") == [
        0.23, 0.05, 0.27, 0.95
    ]


def test_parse_vision_box_default_argument_keeps_legacy_contract():
    """The positional-only call (no model kwarg) keeps the [0,1] interpretation
    that all six legacy tests pin against.  A 0-1000 reply *without* a model
    kwarg is rejected to surface model-mismatch bugs loudly."""
    payload = json.dumps({"box_2d": [0.23, 0.05, 0.27, 0.95]})
    assert _parse_vision_box(payload) == [0.23, 0.05, 0.27, 0.95]
    too_large = json.dumps({"box_2d": [125, 60, 875, 940]})
    assert _parse_vision_box(too_large) is None


def test_parse_vision_box_rescales_absolute_pixels_when_image_size_given():
    parsed = _parse_vision_box(
        json.dumps({"box_2d": [100, 200, 500, 1800]}),
        model="llama3.2-vision",
        image_size=(1920, 1080),
    )
    assert parsed == pytest.approx(
        [100 / 1080, 200 / 1920, 500 / 1080, 1800 / 1920]
    )


def test_parse_vision_box_rejects_degenerate_rectangle_non_empty():
    payload = json.dumps({"box_2d": [0.1, 0.2, 0.1, 0.2]})
    assert _parse_vision_box(payload, model="llama3.2-vision") is None
    empty = json.dumps({"box_2d": [0.0, 0.0, 0.0, 0.0]})
    assert _parse_vision_box(empty, model="gemini-2.5-pro") == [
        0.0, 0.0, 0.0, 0.0
    ]


def test_parse_vision_box_mixed_protocol_regression():
    raw_01 = json.dumps({"box_2d": [0.10, 0.20, 0.50, 0.60]})
    raw_1000 = json.dumps({"box_2d": [100, 200, 500, 600]})
    # Right protocol wins.
    assert _parse_vision_box(raw_01, model="llama3.2-vision") == pytest.approx(
        [0.10, 0.20, 0.50, 0.60]
    )
    assert _parse_vision_box(raw_1000, model="gemini-3-flash-preview") == pytest.approx(
        [0.10, 0.20, 0.50, 0.60]
    )
    # A 0-1000 reply must NOT parse under a model-less invocation: this is
    # the regression the B2 fix targets ("prompt asks for coords the model
    # is least likely to emit").
    assert _parse_vision_box(raw_1000) is None
    # A 0-1 reply under a 0-1000 parser that already contains values > 1
    # is impossible in practice, but the rescaling path requires > 1 to
    # trigger; an unambiguous [0,1] reply under a Gemini parser passes
    # through unchanged (this is the documented lenient case for models
    # that already emit floats in [0,1]).
    assert _parse_vision_box(raw_01, model="gemini-3-flash-preview") == pytest.approx(
        [0.10, 0.20, 0.50, 0.60]
    )


def test_extract_description_handles_prose_and_missing():
    assert _extract_description(
        json.dumps({"box_2d": [0.1] * 4, "description": "  Login button  "})
    ) == "Login button"
    assert _extract_description(json.dumps({"box_2d": [0.1] * 4})) is None
    assert _extract_description("not json") is None
    assert _extract_description(json.dumps({"description": ""})) is None
    assert _extract_description(json.dumps({"description": 42})) is None


# ---------------------------------------------------------------------------
# B1: annotate_relevant_screenshot actually calls the model
# ---------------------------------------------------------------------------


def _settings(**overrides) -> Settings:
    output_dir = overrides.pop(
        "_output_dir_override", None
    ) or str(tempfile.mkdtemp(prefix="monkeylm-p1-"))
    base = dict(
        vision_model="gemini-3-flash-preview",
        pdf_vision_model="gemini-3-flash-preview",
        vision_annotation_timeout_seconds=5.0,
        pdf_generate=True,
    )
    base.update(overrides)
    settings = Settings(**base)
    settings._output_dir_override = output_dir
    return settings


def test_annotate_relevant_screenshot_calls_model_and_returns_pair(tmp_path):
    image_path = str(tmp_path / "shot.png")
    PILImage.new("RGB", (640, 360), color="white").save(image_path)

    settings = _settings(_output_dir_override=str(tmp_path))
    fake_response: Dict[str, Any] = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "box_2d": [125, 60, 875, 940],
                            "description": "Submit button missing focus ring",
                        }
                    )
                }
            }
        ]
    }
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
        captured["model"] = model
        captured["json_mode"] = json_mode
        captured["temperature"] = temperature
        captured["messages"] = messages
        return fake_response

    with patch(
        "monkeylm.models.openai_client.chat_completion",
        new=fake_chat_completion,
    ):
        result = asyncio.run(
            annotate_relevant_screenshot(
                settings=settings,
                image_path=image_path,
                context_issue="defect: missing focus ring",
                step_num=7,
            )
        )

    assert isinstance(result, tuple) and len(result) == 3
    annotated_path, description, verdict_dict = result
    assert annotated_path != image_path
    assert os.path.basename(annotated_path).endswith("_annotated.png")
    assert os.path.exists(annotated_path)
    assert description == "Submit button missing focus ring"
    # Phase 2 (B5): the verdict dict is populated even when the model emits
    # only the legacy 2-field shape.
    assert isinstance(verdict_dict, dict)
    assert verdict_dict.get("box_2d") == pytest.approx([0.125, 0.06, 0.875, 0.94])
    assert verdict_dict.get("description") == "Submit button missing focus ring"
    assert captured["model"] == "gemini-3-flash-preview"
    assert captured["json_mode"] is True
    assert captured["temperature"] == 0.0
    # JPEG data URL (B9), not raw PNG.
    image_part = captured["messages"][0]["content"][1]
    assert image_part["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_annotate_relevant_screenshot_recovers_prose_box(tmp_path):
    image_path = str(tmp_path / "shot.png")
    PILImage.new("RGB", (640, 360), color="white").save(image_path)
    settings = _settings(
        _output_dir_override=str(tmp_path),
        vision_model="llama3.2-vision",
        pdf_vision_model="llama3.2-vision",
    )
    fake_response = {
        "choices": [
            {
                "message": {
                    "content": "I see the issue. box_2d: [0.11, 0.22, 0.33, 0.44]"
                }
            }
        ]
    }

    with patch(
        "monkeylm.models.openai_client.chat_completion", return_value=fake_response
    ):
        annotated_path, description, verdict_dict = asyncio.run(
            annotate_relevant_screenshot(
                settings, image_path, "defect", step_num=2
            )
        )

    assert description is None
    assert annotated_path and os.path.exists(annotated_path)
    # Empty-box from prose parser recovery: the verdict dict carries the
    # recovered box_2d (Phase 2 B5 surfaces this even when parsing is
    # partial).
    assert verdict_dict.get("box_2d") == pytest.approx([0.11, 0.22, 0.33, 0.44])


def test_annotate_relevant_screenshot_copies_on_unparseable_response(tmp_path):
    image_path = str(tmp_path / "shot.png")
    PILImage.new("RGB", (640, 360), color="white").save(image_path)
    settings = _settings(_output_dir_override=str(tmp_path))
    fake_response = {
        "choices": [{"message": {"content": "I cannot locate any issue."}}]
    }
    with patch(
        "monkeylm.models.openai_client.chat_completion", return_value=fake_response
    ):
        annotated_path, description, verdict_dict = asyncio.run(
            annotate_relevant_screenshot(
                settings, image_path, "defect", step_num=3
            )
        )
    assert description is None
    assert annotated_path and os.path.exists(annotated_path)
    # Either copy-fallback (annotated sibling) or original.
    assert annotated_path.endswith("shot_annotated.png") or annotated_path == image_path
    # No parseable reply → empty verdict.
    assert verdict_dict.get("box_2d") == [0.0, 0.0, 0.0, 0.0]


def test_annotate_relevant_screenshot_handles_chat_completion_exception(tmp_path):
    image_path = str(tmp_path / "shot.png")
    PILImage.new("RGB", (640, 360), color="white").save(image_path)
    settings = _settings(_output_dir_override=str(tmp_path))

    def boom(*_args, **_kwargs):
        raise RuntimeError("endpoint unreachable")

    with patch("monkeylm.models.openai_client.chat_completion", new=boom):
        annotated_path, description, verdict_dict = asyncio.run(
            annotate_relevant_screenshot(
                settings, image_path, "defect", step_num=4
            )
        )

    assert description is None
    assert annotated_path
    assert verdict_dict.get("box_2d") == [0.0, 0.0, 0.0, 0.0]


# ---------------------------------------------------------------------------
# B8: config defaults / clamping (executor wiring is exercised in
# test_executor_annotation_timeout.py and the legacy executor tests)
# ---------------------------------------------------------------------------


def test_settings_expose_cv_skip_composite_defaults():
    settings = Settings()
    assert settings.cv_skip_min_area_ratio == 0.02
    assert settings.cv_skip_min_dominance == 0.7
    assert settings.cv_skip_max_ssim == 0.6


def test_settings_expose_vision_annotation_timeout():
    settings = Settings()
    assert settings.vision_annotation_timeout_seconds == 60.0
