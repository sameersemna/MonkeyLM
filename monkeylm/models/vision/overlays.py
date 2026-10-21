"""B6 — Set-of-Mark numbered-region overlay renderer.

The single largest published grounding-accuracy gain for VLM UI tasks
comes from marking candidate regions **on the image itself** with a
visible index, and asking the model to return the index instead of an
explicit ``[ymin, xmin, ymax, xmax]`` tuple.  See Set-of-Mark
(arXiv 2310.11441): on REF COCOg, GPT-4V grounding went **25.7 →
86.4**, and VisualWebArena's GPT-4V baseline gained +1.32pp task
success (16.37 vs 15.05).  See ScreenSpot-Pro (arXiv 2504.07981):
cascaded visual search tops out at 48.1% where base grounding
models cap at 18.9% — "strategically reducing the search area
enhances accuracy."

This module renders the numbered overlay (a copy of the screenshot with
indexed candidate boxes drawn on top in red, plus a numeric label per
box).  The overlay is saved next to the source image; the annotator
sends the overlay path to the model alongside a prompt that asks for
**an index in [1..N]** instead of raw coordinates.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from monkeylm import config as _config


def render_numbered_overlay(
    image_path: str,
    regions: Sequence[Dict[str, Any]],
    output_path: str,
    *,
    color: Tuple[int, int, int] = (220, 30, 30),
    halo_color: Tuple[int, int, int] = (0, 0, 0),
    label_bg: Tuple[int, int, int] = (15, 15, 20),
) -> str:
    """Render indexed CV candidate boxes onto a copy of the screenshot.

    ``regions`` must be a sequence of dicts each carrying a normalized
    ``box_2d`` key (ymin, xmin, ymax, xmax, all in [0,1]).  Boxes are
    clipped to the image bounds; indices are 1-based.

    Returns the output path.  When Pillow is not available, returns
    the input path unchanged (the caller must then fall back to the
    un-overlaid image).
    """
    PIL_Image = _config.PIL_Image
    PIL_ImageDraw = _config.PIL_ImageDraw
    PIL_ImageFont = getattr(_config, "PIL_ImageFont", None)
    if PIL_Image is None or PIL_ImageDraw is None:
        return image_path
    try:
        with PIL_Image.open(image_path) as img:
            base = img.convert("RGBA")
            width, height = base.size
            overlay = PIL_Image.new("RGBA", base.size, (0, 0, 0, 0))
            draw = PIL_ImageDraw.Draw(overlay)
            border_w = max(3, min(width, height) // 280)
            font = _resolve_overlay_font(width, height, PIL_ImageFont)
            for idx, region in enumerate(regions, start=1):
                box = region.get("box_2d") if isinstance(region, dict) else None
                if not (isinstance(box, (list, tuple)) and len(box) == 4):
                    continue
                try:
                    ymin, xmin, ymax, xmax = (float(v) for v in box)
                except (TypeError, ValueError):
                    continue
                if ymax <= ymin or xmax <= xmin:
                    continue
                x0 = int(max(0, min(width - 1, xmin * width)))
                y0 = int(max(0, min(height - 1, ymin * height)))
                x1 = int(max(0, min(width - 1, xmax * width)))
                y1 = int(max(0, min(height - 1, ymax * height)))
                if x1 <= x0 or y1 <= y0:
                    continue
                # Halo first, then primary stroke.
                draw.rectangle(
                    [x0 - border_w, y0 - border_w, x1 + border_w, y1 + border_w],
                    outline=(*halo_color, 255),
                    width=border_w + 2,
                )
                draw.rectangle(
                    [x0, y0, x1, y1],
                    outline=(*color, 255),
                    width=border_w,
                )
                # Numeric badge in the top-left corner of the box, padded
                # so consecutive indices don't overlap.
                label = str(idx)
                try:
                    bbox = draw.textbbox((0, 0), label, font=font) if font else None
                    text_w = (bbox[2] - bbox[0]) if bbox else 14 * len(label)
                    text_h = (bbox[3] - bbox[1]) if bbox else 18
                except Exception:
                    text_w, text_h = 14 * len(label), 18
                pad = 4
                bx0 = x0
                by0 = max(0, y0 - text_h - 2 * pad)
                bx1 = bx0 + text_w + 2 * pad
                by1 = by0 + text_h + 2 * pad
                draw.rectangle(
                    [bx0, by0, bx1, by1],
                    fill=(*label_bg, 235),
                )
                draw.rectangle(
                    [bx0, by0, bx1, by1],
                    outline=(*color, 255),
                    width=2,
                )
                draw.text(
                    (bx0 + pad, by0 + pad - 2),
                    label,
                    fill=(255, 255, 255, 255),
                    font=font,
                )
            composed = PIL_Image.alpha_composite(base, overlay)
            composed.convert("RGB").save(output_path)
            return output_path
    except Exception:
        return image_path


def _resolve_overlay_font(width: int, height: int, PIL_ImageFont: Any) -> Any:
    """Pick a font near 1/30 of min(w,h) — large enough to read on PDFs."""
    if PIL_ImageFont is None:
        return None
    size = max(14, min(width, height) // 30)
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    ]
    for path in candidates:
        try:
            if os.path.isfile(path):
                return PIL_ImageFont.truetype(path, size)
        except Exception:
            continue
    try:
        return PIL_ImageFont.load_default()
    except Exception:
        return None


def som_prompt_intro(n_regions: int) -> str:
    """Return the SoM-prompt preamble asking for the index of the matching region.

    Used by the annotator when a numbered overlay is being sent.  Falls
    back to the regular prompt via :func:`build_defect_class_prompt`
    when ``n_regions < 1``.
    """
    if n_regions < 1:
        return ""
    return (
        f"The image contains {n_regions} numbered candidate regions "
        "(small red boxes with white index labels in the top-left corner of "
        "each box). Look at every numbered region and return **the index "
        "number** of the region that best matches the issue. If none of the "
        "regions match, return ``\"region_index\": 0``. Then proceed with the "
        "verdict schema as documented below."
    )


def regions_to_som(regions: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Validate and normalize CV regions into the SoM renderable shape."""
    cleaned: List[Dict[str, Any]] = []
    for region in regions:
        if not isinstance(region, dict):
            continue
        box = region.get("box_2d")
        if not (isinstance(box, (list, tuple)) and len(box) == 4):
            continue
        try:
            floats = [float(v) for v in box]
        except (TypeError, ValueError):
            continue
        if not all(0.0 <= v <= 1.0 for v in floats):
            continue
        if floats[2] <= floats[0] or floats[3] <= floats[1]:
            continue
        cleaned.append({"box_2d": floats})
    return cleaned
