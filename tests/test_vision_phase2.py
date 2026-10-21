"""Phase 2 tests: the new ``monkeylm.models.vision`` subpackage.

These tests pin down the Phase 2 contracts (B3-B7 + B10):

* **B5** — :class:`DefectVerdict` schema and ``parse_verdict`` parse
  the full 8-field model output (verdict, box_2d, defect_type,
  severity, description, remediation, action_url, confidence) and
  coerce unknown enum values to the documented fall-back.
* **B7** — per-class prompt dispatcher routes executor-type strings
  onto the closed ``DEFECT_TYPES`` enum and embeds OCR/contrast/threshold
  evidence when supplied.
* **B3** — :func:`monkeylm.browser.snapshot.cv.defect_context` builds a
  JSON-roundtrip-safe prompt payload of CV/OCR evidence.
* **B6** — :func:`render_numbered_overlay` writes an overlay file with
  index labels on the candidate boxes; ``regions_to_som`` validates the
  input shape; ``som_prompt_intro`` returns the SoM preamble.
* **B10** — :func:`run_verification` issues a verification call and
  returns ``(answer, confidence)`` or short-circuits.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import pytest
from PIL import Image as PILImage

# Make ``monkeylm`` importable.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from monkeylm.browser.snapshot.cv import defect_context
from monkeylm.models.vision import (
    DEFECT_TYPES,
    SEVERITIES,
    build_prompt_for_defect_type,
    empty_verdict,
    normalize_defect_type,
    parse_verdict,
    regions_to_som,
    render_numbered_overlay,
    run_verification,
    som_prompt_intro,
)
from monkeylm.types import Settings


# ---------------------------------------------------------------------------
# B5: DefectVerdict schema
# ---------------------------------------------------------------------------


def test_defect_verdict_defaults_are_documented():
    v = empty_verdict()
    assert v.box_2d == [0.0, 0.0, 0.0, 0.0]
    assert v.defect_type == "other"
    assert v.severity == "info"
    assert v.verdict == "accept"
    assert v.description == ""
    assert v.confidence == 0.0


def test_parse_verdict_full_schema():
    payload = json.dumps(
        {
            "verdict": "refine",
            "box_2d": [0.1, 0.2, 0.3, 0.4],
            "defect_type": "ocr-error-text",
            "severity": "serious",
            "description": "Form validation message",
            "remediation": "Increase contrast on the form label",
            "action_url": "https://example.com/a11y",
            "confidence": 0.83,
        }
    )
    v = parse_verdict(payload, model="llama3.2-vision")
    assert v.verdict == "refine"
    assert v.box_2d == pytest.approx([0.1, 0.2, 0.3, 0.4])
    assert v.defect_type == "ocr-error-text"
    assert v.severity == "serious"
    assert v.description == "Form validation message"
    assert v.remediation == "Increase contrast on the form label"
    assert v.action_url == "https://example.com/a11y"
    assert v.confidence == pytest.approx(0.83)


def test_parse_verdict_legacy_two_field_shape():
    """The Phase-1 model output (``box_2d`` + ``description``) must still
    parse into a DefectVerdict with the close enum fall-backs."""
    v = parse_verdict(
        json.dumps({"box_2d": [0.1, 0.2, 0.3, 0.4], "description": "x"}),
        model="llama3.2-vision",
    )
    assert v.box_2d == pytest.approx([0.1, 0.2, 0.3, 0.4])
    assert v.description == "x"
    assert v.defect_type == "other"
    assert v.severity == "info"
    assert v.verdict == "accept"


def test_parse_verdict_unknown_enum_values_fall_back():
    payload = json.dumps(
        {
            "box_2d": [0.1, 0.2, 0.3, 0.4],
            "defect_type": "novel-category-from-future",
            "severity": "blocker",
            "verdict": "ignore",
        }
    )
    v = parse_verdict(payload)
    assert v.defect_type == "other"
    assert v.severity == "info"
    assert v.verdict == "accept"


def test_parse_verdict_clamps_out_of_range_box():
    """Out-of-range boxes collapse to all-zeros (the empty-box escape)."""
    payload = json.dumps({"box_2d": [1.5, -0.5, 2.0, 0.6]})
    v = parse_verdict(payload)
    assert v.box_2d == [0.0, 0.0, 0.0, 0.0]


def test_parse_verdict_prose_fallback():
    """A non-JSON string with a recognizable 4-tuple falls back to the
    prose parser (``_extract_box_from_prose``)."""
    v = parse_verdict("I see this. box_2d: [0.11, 0.22, 0.33, 0.44]")
    assert v.box_2d == pytest.approx([0.11, 0.22, 0.33, 0.44])
    assert v.description == ""


def test_parse_verdict_passes_model_through():
    """``parse_verdict`` honors the model kwarg so the Phase-1 rescaling
    propagates into DefectVerdict.box_2d for Gemini-style replies."""
    payload = json.dumps({"box_2d": [125, 60, 875, 940]})
    v = parse_verdict(payload, model="gemini-3-flash-preview")
    assert v.box_2d == pytest.approx([0.125, 0.06, 0.875, 0.94])


def test_defect_types_are_closed_enum():
    assert set(DEFECT_TYPES) == {
        "visual-regression",
        "blank-screen",
        "ocr-error-text",
        "low-contrast",
        "layout-shift",
        "element-missing",
        "other",
    }
    assert set(SEVERITIES) == {"info", "minor", "serious", "critical"}


# ---------------------------------------------------------------------------
# B7: per-class prompt dispatcher
# ---------------------------------------------------------------------------


def test_normalize_defect_type_maps_executor_strings():
    assert normalize_defect_type("visual-diff") == "visual-regression"
    assert normalize_defect_type("blank-screen") == "blank-screen"
    assert normalize_defect_type("ocr-error-text") == "ocr-error-text"
    assert normalize_defect_type("ocr-low-contrast") == "low-contrast"
    assert normalize_defect_type("layout-shift") == "layout-shift"
    assert normalize_defect_type("layout-instability") == "layout-shift"
    assert normalize_defect_type("dom-collapse") == "element-missing"
    assert normalize_defect_type("functional-failure:click") == "other"
    assert normalize_defect_type(None) == "other"
    assert normalize_defect_type("") == "other"


def test_build_prompt_for_defect_type_embeds_ocr_text():
    prompt = build_prompt_for_defect_type(
        issue="login form says 'bad password'",
        defect_type="ocr-error-text",
        ocr_text="bad password",
    )
    assert "bad password" in prompt
    assert "vertical" in prompt.lower() or "ocr" in prompt.lower()
    assert "box_2d" in prompt
    # The schema footer carries the VerdictVerdict fields.
    assert "defect_type" in prompt
    assert "severity" in prompt
    assert "confidence" in prompt


def test_build_prompt_for_defect_type_includes_threshold_for_layout_shift():
    prompt = build_prompt_for_defect_type(
        issue="page shifted",
        defect_type="layout-shift",
        threshold=180.0,
    )
    assert "180" in prompt


def test_build_prompt_includes_contrast_ratio_for_low_contrast():
    prompt = build_prompt_for_defect_type(
        issue="label is unreadable",
        defect_type="low-contrast",
        contrast_ratio=2.3,
        min_ratio=4.5,
        ocr_text="Submit",
    )
    assert "2.3" in prompt
    assert "4.5" in prompt
    assert "Submit" in prompt


def test_build_prompt_uses_other_fallback_for_unknown_class():
    prompt = build_prompt_for_defect_type(
        issue="weird thing",
        defect_type="nonexistent-class",
    )
    assert "does not fit a known class" in prompt
    assert "box_2d" in prompt


# ---------------------------------------------------------------------------
# B3: defect_context() — CV/OCR evidence normalizer
# ---------------------------------------------------------------------------


def test_defect_context_returns_normalized_box_2d():
    ctx = defect_context(
        {
            "ssim_score": 0.55,
            "diff_ratio": 0.12,
            "diff_regions": [
                {
                    "box_2d": [0.10, 0.20, 0.30, 0.40],
                    "area_ratio": 0.4,
                    "x": 200,
                    "y": 100,
                    "w": 200,
                    "h": 200,
                }
            ],
        }
    )
    assert ctx["ssim_score"] == 0.55
    assert ctx["diff_ratio"] == 0.12
    assert len(ctx["regions"]) == 1
    assert ctx["regions"][0]["box_2d"] == pytest.approx([0.1, 0.2, 0.3, 0.4])
    assert ctx["regions"][0]["area_ratio"] == 0.4


def test_defect_context_rejects_garbage_box():
    ctx = defect_context(
        {
            "diff_regions": [
                {"box_2d": [1.5, 0.5, 2.0, 0.6]},  # > 1.0
                {"box_2d": [0.4, 0.5, 0.3, 0.6]},  # ymax <= ymin
                {"box_2d": "not a list"},
                "not a dict",
            ]
        }
    )
    assert ctx["regions"] == []
    assert ctx["n_regions"] == 0


def test_defect_context_caps_at_max_regions():
    regions = [
        {"box_2d": [0.0, 0.0, 0.1, 0.1], "area_ratio": float(i) / 100.0}
        for i in range(1, 12)
    ]
    ctx = defect_context({"diff_regions": regions}, max_regions=3)
    assert len(ctx["regions"]) == 3
    # Sorted by descending area_ratio; first item should be area_ratio=0.11.
    assert ctx["regions"][0]["area_ratio"] == pytest.approx(0.11)


def test_defect_context_truncates_ocr_text():
    long_text = "abc" * 1000
    ctx = defect_context({}, ocr={"text": long_text})
    assert len(ctx["ocr_error_text"]) == 500


def test_defect_context_handles_missing_keys():
    ctx = defect_context(None, ocr=None)
    assert ctx["ssim_score"] is None
    assert ctx["diff_ratio"] is None
    assert ctx["regions"] == []
    assert ctx["ocr_error_text"] == ""
    assert ctx["n_regions"] == 0
    assert ctx["error"] is None


def test_defect_context_extracts_ocr_violations():
    ctx = defect_context(
        {},
        ocr={
            "violations": [
                {"text": "Sign in", "contrast_ratio": 2.1, "min_ratio": 4.5},
                {"text": 42, "contrast_ratio": "bad"},
            ],
        },
    )
    assert len(ctx["ocr_violations"]) == 2
    assert ctx["ocr_violations"][0] == {
        "text": "Sign in",
        "contrast_ratio": 2.1,
    }
    assert ctx["ocr_violations"][1] == {"text": "", "contrast_ratio": None}


def test_defect_context_is_json_roundtrip_safe():
    """Eval-harness fixtures and prompt consumers JSON-dump this payload
    verbatim; roundtrip must lose nothing that matters for downstream
    classification."""
    payload = {
        "ssim_score": 0.61,
        "diff_ratio": 0.08,
        "diff_regions": [
            {"box_2d": [0.0, 0.0, 0.5, 0.5], "area_ratio": 0.5},
        ],
        "ocr": {
            "text": "error",
            "violations": [{"text": "v", "contrast_ratio": 1.5}],
        },
    }
    ctx = defect_context(payload, ocr=payload.get("ocr"))
    roundtripped = json.loads(json.dumps(ctx))
    assert roundtripped == ctx


# ---------------------------------------------------------------------------
# B6: Set-of-Mark overlay
# ---------------------------------------------------------------------------


def _make_test_image(path: str, size=(800, 600)) -> None:
    PILImage.new("RGB", size, color=(240, 240, 250)).save(path)


def test_render_numbered_overlay_writes_output(tmp_path):
    src = tmp_path / "shot.png"
    overlay_out = tmp_path / "overlay.png"
    _make_test_image(str(src))
    regions = [
        {"box_2d": [0.1, 0.1, 0.3, 0.3]},
        {"box_2d": [0.4, 0.4, 0.6, 0.7]},
    ]
    written = render_numbered_overlay(
        str(src), regions, str(overlay_out)
    )
    assert written == str(overlay_out)
    assert overlay_out.exists()
    assert overlay_out.stat().st_size > 0


def test_render_numbered_overlay_draws_red_pixels(tmp_path):
    """Indices 1..N must actually be drawn so the model sees them."""
    src = tmp_path / "shot.png"
    overlay_out = tmp_path / "overlay.png"
    _make_test_image(str(src))
    regions = [{"box_2d": [0.1, 0.1, 0.5, 0.5]}]
    render_numbered_overlay(str(src), regions, str(overlay_out))
    img = PILImage.open(overlay_out).convert("RGBA")
    red_px = 0
    for px in img.getdata():
        if px[0] > 180 and px[1] < 80 and px[2] < 80:
            red_px += 1
    assert red_px > 50, (
        f"expected visible red marker pixels, got {red_px}"
    )


def test_regions_to_som_filters_garbage():
    cleaned = regions_to_som(
        [
            {"box_2d": [0.1, 0.2, 0.3, 0.4]},
            {"box_2d": [1.5, 0.5, 0.6, 0.7]},  # > 1.0
            {"box_2d": [0.4, 0.5, 0.3, 0.5]},  # ymax <= ymin
            {"box_2d": None},  # missing
            "string",  # wrong type
            None,  # wrong type
        ]
    )
    assert len(cleaned) == 1
    assert cleaned[0]["box_2d"] == pytest.approx([0.1, 0.2, 0.3, 0.4])


def test_som_prompt_intro_handles_zero_regions():
    assert som_prompt_intro(0) == ""
    assert som_prompt_intro(-1) == ""
    # Three regions should produce a real preamble.
    intro = som_prompt_intro(3)
    assert "3 numbered" in intro
    assert "region_index" in intro


# ---------------------------------------------------------------------------
# B10: second-pass verifier
# ---------------------------------------------------------------------------


def _settings(tmp_path) -> Settings:
    s = Settings(
        vision_model="llama3.2-vision",
        pdf_vision_model="llama3.2-vision",
        vision_annotation_timeout_seconds=2.0,
    )
    s._output_dir_override = str(tmp_path)
    return s


def test_run_verification_short_circuits_without_description(tmp_path):
    """When no first-pass description is supplied, the verifier returns
    None immediately rather than burning a round-trip."""
    image_path = str(tmp_path / "shot.png")
    PILImage.new("RGB", (200, 200), color="white").save(image_path)
    settings = _settings(tmp_path)
    result = asyncio.run(
        run_verification(
            settings, image_path,
            issue_context="defect", candidate_box=[0.1] * 4,
            description="",
        )
    )
    assert result is None


def test_run_verification_returns_answer_when_endpoint_responds(tmp_path):
    image_path = str(tmp_path / "shot.png")
    PILImage.new("RGB", (200, 200), color="white").save(image_path)
    settings = _settings(tmp_path)

    fake_response = {
        "choices": [
            {"message": {"content": json.dumps({"answer": "yes", "confidence": 0.91})}}
        ]
    }

    from unittest.mock import patch

    # Patch at the verifier module's import site because ``from
    # monkeylm.models.openai_client import chat_completion`` binds the
    # function into the verifier module's namespace and the source
    # module rebind does not propagate.
    with patch(
        "monkeylm.models.vision.verifier.chat_completion",
        return_value=fake_response,
    ):
        result = asyncio.run(
            run_verification(
                settings, image_path,
                issue_context="defect", candidate_box=[0.1, 0.2, 0.3, 0.4],
                description="Submit button clipped",
            )
        )

    assert result == ("yes", 0.91)


def test_run_verification_handles_prose_answer(tmp_path):
    image_path = str(tmp_path / "shot.png")
    PILImage.new("RGB", (200, 200), color="white").save(image_path)
    settings = _settings(tmp_path)
    fake_response = {
        "choices": [
            {"message": {"content": "answer: no\nconfidence: 0.4"}}
        ]
    }

    from unittest.mock import patch

    with patch(
        "monkeylm.models.vision.verifier.chat_completion",
        return_value=fake_response,
    ):
        result = asyncio.run(
            run_verification(
                settings, image_path,
                issue_context="defect",
                candidate_box=[0.1, 0.2, 0.3, 0.4],
                description="highlighted region",
            )
        )

    assert result == ("no", 0.4)


def test_run_verification_handles_endpoint_failure(tmp_path):
    image_path = str(tmp_path / "shot.png")
    PILImage.new("RGB", (200, 200), color="white").save(image_path)
    settings = _settings(tmp_path)

    from unittest.mock import patch

    def boom(*_a, **_k):
        raise RuntimeError("network down")

    with patch(
        "monkeylm.models.vision.verifier.chat_completion", new=boom
    ):
        result = asyncio.run(
            run_verification(
                settings, image_path,
                issue_context="defect",
                candidate_box=[0.1, 0.2, 0.3, 0.4],
                description="submit button",
            )
        )
    assert result is None
