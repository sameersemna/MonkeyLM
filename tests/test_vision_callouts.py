"""Tests for the multi-region screenshot callout renderer."""

from __future__ import annotations

from typing import Tuple

import pytest

from monkeylm.models.vision.callouts import _clip_box, render_callouts

pytest.importorskip("PIL")


def _make_image(path, size: Tuple[int, int] = (400, 300)):
    from PIL import Image

    Image.new("RGB", size, (250, 250, 250)).save(path)
    return path


def _red_pixels(path) -> int:
    from PIL import Image

    with Image.open(path) as im:
        rgb = im.convert("RGB")
        return sum(1 for r, g, b in rgb.getdata() if r > 170 and g < 70 and b < 70)


def test_render_callouts_draws_marks_at_requested_boxes(tmp_path):
    src = _make_image(tmp_path / "src.png")
    out = tmp_path / "out.png"

    render_callouts(
        src,
        [
            {"px": [40, 40, 160, 100], "label": "First region"},
            {"px": [220, 150, 360, 240], "label": "Second region"},
        ],
        str(out),
        step_num=3,
    )

    assert out.is_file()
    assert _red_pixels(out) > 500


def test_render_callouts_still_writes_an_image_when_nothing_is_drawable(tmp_path):
    src = _make_image(tmp_path / "src.png")
    out = tmp_path / "out.png"

    result = render_callouts(src, [{"px": [], "label": "ignored"}], str(out))

    assert result == str(out)
    assert out.is_file()
    assert _red_pixels(out) == 0


def test_render_callouts_skips_regions_without_labels(tmp_path):
    src = _make_image(tmp_path / "src.png")
    out = tmp_path / "out.png"

    render_callouts(src, [{"px": [10, 10, 90, 60], "label": "   "}], str(out))

    assert _red_pixels(out) == 0


def test_render_callouts_clips_out_of_bounds_boxes(tmp_path):
    src = _make_image(tmp_path / "src.png", (200, 200))
    out = tmp_path / "out.png"

    render_callouts(src, [{"px": [-50, -50, 400, 400], "label": "Overflow"}], str(out))

    assert out.is_file()
    assert _red_pixels(out) > 100


def test_clip_box_rejects_degenerate_boxes():
    assert _clip_box([10, 10, 12, 12], 400, 300) is None
    assert _clip_box([], 400, 300) is None
    assert _clip_box(["a", "b", "c", "d"], 400, 300) is None


def test_clip_box_orders_inverted_coordinates():
    assert _clip_box([160, 100, 40, 40], 400, 300) == (40, 40, 160, 100)
