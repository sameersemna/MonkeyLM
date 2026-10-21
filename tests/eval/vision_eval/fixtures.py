"""Synthetic fixtures for the vision eval harness.

Phase 3.2 builds the first synthetic-diff fixtures here.  The
construction pattern follows the DiffSpot methodology (arXiv 2605.29615):
render a base HTML/file once, mutate one element on the second frame,
record the changed element + bounding region + magnitude.  Every
fixture carries a stable id so eval runs are reproducible across
commits.

Each fixture exports:

* ``id`` — stable string identifier ("synthetic_button_color_change_v1").
* ``image`` — PIL-Image-compatible in-memory bytes.
* ``ground_truth`` — dict with ``box_2d`` (normalized), ``defect_type``,
  ``severity``, ``description``.
* ``negative`` — bool; ``True`` marks a no-change pair so the harness
  can measure false-positive rate separately.

The fixtures are deliberately tiny: the harness is what matters; the
data feeds it.  Real MonkeyLM runs come in via :func:`load_real_fixtures`
once we have a hand-labeled set.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from PIL import Image as PILImage


@dataclass
class EvalFixture:
    """One evaluation record: image bytes plus ground-truth annotation."""

    id: str
    image_bytes: bytes
    width: int
    height: int
    ground_truth: Dict[str, object]
    negative: bool = False
    source: str = "synthetic"
    description_ref: str = ""
    prelabel_box: Optional[List[float]] = None
    prelabel_defect_type: Optional[str] = None
    splits: Tuple[str, ...] = field(default_factory=lambda: ("test",))


def _build_image(
    *,
    base_color: Tuple[int, int, int],
    accent_color: Tuple[int, int, int],
    accent_box_px: Tuple[int, int, int, int],
    accent_label: str,
    size: Tuple[int, int] = (1280, 720),
    use_text: bool = True,
) -> Tuple[bytes, Tuple[int, int, int, int]]:
    """Render an image carrying one off-color block (the synthetic defect).

    Returns ``(image_bytes, accent_box_px)``.  The accent box is the
    region that will carry the "defect" annotation in the ground
    truth.
    """
    img = PILImage.new("RGB", size, color=base_color)
    pixels = img.load()
    x0, y0, x1, y1 = accent_box_px
    for y in range(y0, y1):
        for x in range(x0, x1):
            pixels[x, y] = accent_color
    if use_text:
        # Pillow's default font is reliable across environments and is
        # sufficient as a visual marker for the eval harness.
        from PIL import ImageDraw

        draw = ImageDraw.Draw(img)
        draw.text(
            (x0 + 12, y0 + max(0, ((y1 - y0) - 18) // 2)),
            accent_label,
            fill=(255, 255, 255),
        )
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue(), accent_box_px


def _normalize_box(
    box_px: Tuple[int, int, int, int],
    size: Tuple[int, int],
) -> List[float]:
    """Convert pixel box to [0,1] normalized in ymin/xmin/ymax/xmax order."""
    x0, y0, x1, y1 = box_px
    width, height = size
    return [
        round(y0 / height, 4),
        round(x0 / width, 4),
        round(y1 / height, 4),
        round(x1 / width, 4),
    ]


def _make_visual_diff_fixture() -> EvalFixture:
    """A button turns from green to red — a synthetic visual regression."""
    accent_box = (880, 540, 1180, 660)
    image_bytes, _ = _build_image(
        base_color=(245, 245, 250),
        accent_color=(200, 50, 50),
        accent_box_px=accent_box,
        accent_label="Submit",
    )
    return EvalFixture(
        id="synthetic_button_color_change_v1",
        image_bytes=image_bytes,
        width=1280,
        height=720,
        ground_truth={
            "box_2d": _normalize_box(accent_box, (1280, 720)),
            "defect_type": "visual-regression",
            "severity": "serious",
            "description": "The primary 'Submit' button changed color.",
        },
        description_ref="Submit button color changed from green to red.",
        splits=("train", "test"),
    )


def _make_blank_screen_fixture() -> EvalFixture:
    """A uniform near-white image — synthetic blank-screen regression."""
    image_bytes, _ = _build_image(
        base_color=(255, 255, 255),
        accent_color=(255, 255, 255),
        accent_box_px=(0, 0, 1280, 720),
        accent_label="",
        use_text=False,
    )
    return EvalFixture(
        id="synthetic_blank_screen_v1",
        image_bytes=image_bytes,
        width=1280,
        height=720,
        ground_truth={
            "box_2d": [0.0, 0.0, 0.0, 0.0],  # global; no region
            "defect_type": "blank-screen",
            "severity": "critical",
            "description": "Screenshot is uniform near-white (no UI rendered).",
        },
        description_ref="Blank screen",
        negative=True,  # used as a *defect* fixture, not negative
        splits=("test",),
    )


def _make_element_missing_fixture() -> EvalFixture:
    """Nav bar's 'Profile' label becomes a small blank rectangle."""
    accent_box = (40, 100, 240, 160)
    image_bytes, _ = _build_image(
        base_color=(245, 245, 250),
        accent_color=(245, 245, 250),
        accent_box_px=accent_box,
        accent_label="Profile",
    )
    return EvalFixture(
        id="synthetic_element_missing_v1",
        image_bytes=image_bytes,
        width=1280,
        height=720,
        ground_truth={
            "box_2d": _normalize_box(accent_box, (1280, 720)),
            "defect_type": "element-missing",
            "severity": "minor",
            "description": "Profile label disappeared from the nav bar.",
        },
        description_ref="Profile nav label is missing",
        splits=("test",),
    )


def _make_negative_fixture() -> EvalFixture:
    """A normal-looking page with no defects — used to measure FP rate."""
    image_bytes, _ = _build_image(
        base_color=(245, 245, 250),
        accent_color=(40, 110, 240),
        accent_box_px=(80, 600, 380, 660),
        accent_label="Profile",
    )
    return EvalFixture(
        id="synthetic_negative_v1",
        image_bytes=image_bytes,
        width=1280,
        height=720,
        ground_truth={
            "box_2d": [0.0, 0.0, 0.0, 0.0],
            "defect_type": "other",
            "severity": "info",
            "description": "",
        },
        negative=True,
        description_ref="No defect",
        splits=("test",),
    )


def synthetic_fixtures() -> List[EvalFixture]:
    """Return the v1 synthetic fixture set used as the eval baseline."""
    return [
        _make_visual_diff_fixture(),
        _make_blank_screen_fixture(),
        _make_element_missing_fixture(),
        _make_negative_fixture(),
    ]


def load_real_fixtures(path: str) -> List[EvalFixture]:
    """Load a real-run fixture set from a JSON file (post-MVP).

    Phase 3.2 returns an empty list for now; populate this once a
    human-labelled corpus is available.  The signature is fixed so
    the CLI can call it unconditionally.
    """
    return []
