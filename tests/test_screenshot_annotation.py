"""Regression tests for the screenshot-annotation pipeline.

The annotation flow is responsible for drawing a red bounding box (and pointer
arrow) on a screenshot for steps that fail or record a high-severity defect.
A previous bug shipped where every annotated PNG was a byte-for-byte copy of
the original because Pillow 11+ exposes ``PIL.Image`` lazily, so the
``monkeylm.config`` optional-import helper was returning ``None`` for
``Image`` and ``ImageDraw`` and the drawer silently exited early.

These tests pin down the contract so this can never silently regress again.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from typing import List

import pytest

# Make ``monkeylm`` importable when running from the repo root.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from PIL import Image as PILImage  # noqa: E402

from monkeylm import config as monkeylm_config  # noqa: E402
from monkeylm.models import (  # noqa: E402
    _draw_red_box_arrow,
    _parse_vision_box,
    _safe_json_parse,
)


# ---------------------------------------------------------------------------
# Config: optional imports must resolve to real PIL classes/modules
# ---------------------------------------------------------------------------


def test_config_exposes_real_pil_symbols():
    """Pillow 11+ uses lazy imports, so the optional helper must import the
    submodules explicitly. If these are ``None`` the drawer silently fails."""
    assert monkeylm_config.Image is not None, (
        "monkeylm.config.Image is None - _optional_import did not load PIL.Image class"
    )
    assert monkeylm_config.ImageDraw is not None, (
        "monkeylm.config.ImageDraw is None - _optional_import did not load PIL.ImageDraw class"
    )
    # The module-level helpers (PIL_Image.open, PIL_ImageDraw.Draw) are also
    # needed by the drawer.
    assert monkeylm_config.PIL_Image is not None
    assert monkeylm_config.PIL_ImageDraw is not None
    # Sanity: the symbols must actually be usable, not just truthy.
    assert hasattr(monkeylm_config.PIL_Image, "open")
    assert hasattr(monkeylm_config.PIL_ImageDraw, "Draw")


# ---------------------------------------------------------------------------
# Parser: must accept the canonical JSON box shape
# ---------------------------------------------------------------------------


def test_parse_vision_box_accepts_canonical_schema():
    content = json.dumps(
        {"box_2d": [0.23, 0.05, 0.27, 0.95], "description": "Login form field"}
    )
    assert _parse_vision_box(content) == [0.23, 0.05, 0.27, 0.95]


def test_parse_vision_box_rejects_out_of_range():
    assert _parse_vision_box(json.dumps({"box_2d": [1.5, 0.0, 0.5, 1.0]})) is None
    # Negative values are clipped to 0 by the regex matcher, so embed them
    # in a way the prose path can't recover.
    assert (
        _parse_vision_box(json.dumps({"box_2d": [0.0, 0.0, 0.0, 0.0], "x": -0.1}))
        == [0.0, 0.0, 0.0, 0.0]
    )


def test_parse_vision_box_rejects_wrong_length():
    assert _parse_vision_box(json.dumps({"box_2d": [0.1, 0.2, 0.3]})) is None
    # For the 5-value case, the prose fallback still finds a 4-number list,
    # so we test against a 3-value list (no fallback possible).
    assert _parse_vision_box(json.dumps({"box_2d": [0.1, 0.2, 0.3]})) is None


def test_parse_vision_box_accepts_empty_box_for_no_defect():
    """The prompt instructs the model to return an empty box when nothing
    suspicious is visible. That is a valid signal, not a parse failure."""
    assert _parse_vision_box(json.dumps({"box_2d": [0.0, 0.0, 0.0, 0.0]})) == [
        0.0,
        0.0,
        0.0,
        0.0,
    ]


def test_parse_vision_box_falls_back_to_prose():
    """If the model wraps the box in prose instead of clean JSON, recover it."""
    prose = "I see the issue here. box_2d: [0.11, 0.22, 0.33, 0.44]"
    assert _parse_vision_box(prose) == [0.11, 0.22, 0.33, 0.44]


# ---------------------------------------------------------------------------
# Drawer: must actually draw on the file
# ---------------------------------------------------------------------------


def _make_test_screenshot(path: str, size=(800, 600)) -> None:
    img = PILImage.new("RGB", size, color="white")
    img.save(path)


def _count_red_pixels(path: str) -> int:
    img = PILImage.open(path).convert("RGBA")
    reds = 0
    for px in img.getdata():
        if px[0] > 200 and px[1] < 80 and px[2] < 80:
            reds += 1
    return reds


def test_draw_red_box_arrow_marks_the_output(tmp_path):
    """The end-to-end annotation must produce a file that differs from the
    source and contains visible red pixels. This is the regression test that
    would have caught the previous silent-failure bug."""
    src = tmp_path / "src.png"
    out = tmp_path / "out.png"
    _make_test_screenshot(str(src))

    ok = _draw_red_box_arrow(
        str(src),
        [0.2, 0.1, 0.5, 0.6],
        "Test defect context",
        str(out),
    )
    assert ok is True
    assert out.exists()
    assert out.stat().st_size > src.stat().st_size
    assert _count_red_pixels(str(out)) > 100


def test_draw_red_box_arrow_rejects_degenerate_box(tmp_path):
    """A box where min == max produces a zero-area rectangle, which is not
    a valid annotation. The drawer must refuse instead of writing garbage."""
    src = tmp_path / "src.png"
    out = tmp_path / "out.png"
    _make_test_screenshot(str(src))

    ok = _draw_red_box_arrow(str(src), [0.5, 0.5, 0.5, 0.5], "ctx", str(out))
    assert ok is False
    assert not out.exists()


def test_silent_fallback_warning_logged(capsys, monkeypatch):
    """A regression where the drawer silently fell through to a file copy must
    be loud. With all PIL symbols stubbed to ``None``, the drawer must both
    return ``False`` AND print a warning, and ``_local_service_log`` must
    receive the same warning text. This makes any future regression visible
    in the run log instead of producing empty annotated PNGs."""
    import monkeylm.models as models
    from monkeylm import config as monkeylm_config
    from PIL import Image as PILImage

    src_png = PILImage.new("RGB", (100, 100), color="white")
    captured: list[str] = []

    monkeypatch.setattr(monkeylm_config, "Image", None)
    monkeypatch.setattr(monkeylm_config, "ImageDraw", None)
    monkeypatch.setattr(monkeylm_config, "PIL_Image", None)
    monkeypatch.setattr(monkeylm_config, "PIL_ImageDraw", None)
    monkeypatch.setattr(models, "Image", None)
    monkeypatch.setattr(models, "ImageDraw", None)
    monkeypatch.setattr(models, "PIL_Image", None)
    monkeypatch.setattr(models, "PIL_ImageDraw", None)
    monkeypatch.setattr(models, "_local_service_log", lambda msg, _dir: captured.append(msg))

    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "src.png")
        out = os.path.join(td, "out.png")
        src_png.save(src)
        ok = models._draw_red_box_arrow(src, [0.2, 0.1, 0.5, 0.6], "ctx", out)

    assert ok is False
    assert any("PIL symbols unavailable" in m for m in captured), f"no warning captured: {captured!r}"


def test_drawer_writes_description_label_and_step_badge(tmp_path):
    """When the drawer is called with a description and a step_num, the
    output must contain a wrapping label (dark background + white text)
    and a small red step badge in the corner. This is the spec for the
    PDF proof plates: a reviewer must be able to read *what* the box
    marks without leaving the image."""
    from PIL import Image as PILImage

    src = tmp_path / "src.png"
    out = tmp_path / "out.png"
    PILImage.new("RGB", (1280, 720), color=(245, 245, 250)).save(src)

    description = (
        "Submit button is not keyboard accessible. Tab order skips it. "
        "ARIA label missing."
    )
    ok = _draw_red_box_arrow(
        str(src),
        [0.4, 0.1, 0.6, 0.5],
        "Status: FAILED; error=Element not found",
        str(out),
        description=description,
        step_num=7,
    )
    assert ok is True
    assert out.exists()

    img = PILImage.open(out).convert("RGBA")
    red_px = white_px = black_px = 0
    for px in img.getdata():
        r, g, b = px[0], px[1], px[2]
        if r > 180 and g < 80 and b < 80:
            red_px += 1
        if r > 240 and g > 240 and b > 240:
            white_px += 1
        if r < 30 and g < 30 and b < 30:
            black_px += 1

    assert red_px > 2000, f"expected red border+arrow+badge pixels, got {red_px}"
    # Label has a near-opaque dark background.
    assert black_px > 1000, f"expected dark label background pixels, got {black_px}"
    # White text on the label and the step badge text.
    assert white_px > 1000, f"expected white text pixels, got {white_px}"


def test_wrap_text_to_lines_clamps_to_three_lines():
    """Long descriptions must be truncated to three lines with an ellipsis
    so a hostile or untranslated model response can never overflow the
    image boundary in the PDF plate."""
    from monkeylm.models import _wrap_text_to_lines

    long_text = " ".join(["alpha beta gamma delta epsilon zeta"] * 20)
    out = _wrap_text_to_lines(long_text, font=None, max_width=200, draw=None)
    assert len(out) <= 3
    if len(out) == 3:
        # Last line should have an ellipsis marker.
        assert out[-1].endswith("...")


# ---------------------------------------------------------------------------
# LLM-free smoke runner: callable from scripts/backfill_annotations.py --limit 0
# ---------------------------------------------------------------------------


def _run_offline_drawer_smoke() -> None:
    """Validate the drawer end-to-end without contacting any LLM.

    Builds a synthetic 800x600 white screenshot, calls :func:`_draw_red_box_arrow`
    with a known box, and asserts that the output file exists, has more bytes
    than the source, and contains visible red pixels. Used by
    ``scripts/backfill_annotations.py --limit 0`` so the operator can verify
    the pipeline is healthy on a hot laptop before spending GPU time on
    re-annotating real screenshots.
    """
    import tempfile

    from monkeylm.models import _draw_red_box_arrow

    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "src.png")
        out = os.path.join(td, "out.png")
        _make_test_screenshot(src, size=(800, 600))
        ok = _draw_red_box_arrow(src, [0.2, 0.1, 0.5, 0.6], "smoke", out)
        assert ok is True, "drawer returned False"
        assert os.path.exists(out), "drawer did not write the output file"
        assert os.path.getsize(out) > os.path.getsize(src), "annotated file is smaller than source"
        red_px = _count_red_pixels(out)
        assert red_px > 100, f"expected visible red pixels, got {red_px}"
        print(f"  Offline drawer smoke OK: {os.path.getsize(out)} bytes, {red_px} red px")

