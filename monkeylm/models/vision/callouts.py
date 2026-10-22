"""Multi-region screenshot callouts.

``_draw_red_box_arrow`` marks exactly one region and puts the caption beside
it.  Report screenshots usually need several numbered findings on one plate,
annotated by an engineer's reading order, without the captions covering the
UI they describe.  This module draws:

* one red rectangle per region, with a black halo so it survives on both light
  and dark pages,
* a numbered badge on each rectangle,
* a legend panel parked in whichever corner overlaps the fewest regions,
* a leader line from each legend row to the corner of its region it explains,
* an optional step badge in the top-left.

Coordinates are absolute pixels ``[x0, y0, x1, y1]`` measured on the image
being rendered, which is what an OCR pass returns.  Labels are drawn from the
caller-supplied text, so a plate can only claim what was actually detected.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from monkeylm import config as _config

ACCENT = (220, 30, 30)
PANEL_BG = (15, 15, 20)
TEXT = (255, 255, 255)


def _font(size: int):
    from monkeylm.models.vision._legacy import _resolve_label_font

    return _resolve_label_font(max(11, size))


def _clip_box(px: Sequence[float], width: int, height: int) -> Optional[Tuple[int, int, int, int]]:
    try:
        x0, y0, x1, y1 = (float(v) for v in px)
    except (TypeError, ValueError):
        return None
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    x0 = max(0, min(width - 1, int(x0)))
    y0 = max(0, min(height - 1, int(y0)))
    x1 = max(0, min(width - 1, int(x1)))
    y1 = max(0, min(height - 1, int(y1)))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    return x0, y0, x1, y1


def _measure(draw, text: str, font) -> Tuple[int, int]:
    try:
        if font is not None:
            bbox = draw.textbbox((0, 0), text, font=font)
            return bbox[2] - bbox[0], bbox[3] - bbox[1]
    except Exception:
        pass
    return len(text) * 8, 14


def _wrap(draw, text: str, font, max_width: int) -> List[str]:
    words = text.split()
    if not words:
        return []
    lines: List[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if _measure(draw, candidate, font)[0] <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _overlap_area(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> int:
    ix = min(a[2], b[2]) - max(a[0], b[0])
    iy = min(a[3], b[3]) - max(a[1], b[1])
    return ix * iy if ix > 0 and iy > 0 else 0


def _panel_layout(
    draw, entries: List[Dict[str, Any]], font, width: int, height: int, boxes
) -> Tuple[int, int, int, int, int, int]:
    """Return (px, py, pw, ph, row_h, wrap_width) for the least-obstructive corner."""
    row_h = max(18, int(font.size * 1.75)) if hasattr(font, "size") else 26
    wrap_width = max(120, int(width * 0.30))
    widest = 0
    for entry in entries:
        for line in _wrap(draw, entry["label"], font, wrap_width):
            widest = max(widest, _measure(draw, line, font)[0])
    pw = min(width - 24, widest + 76)
    ph = row_h * sum(max(1, len(_wrap(draw, e["label"], font, wrap_width))) for e in entries) + 20
    ph = min(ph, height - 24)
    margin = 14
    corners = {
        "tl": (margin, margin),
        "tr": (width - pw - margin, margin),
        "bl": (margin, height - ph - margin),
        "br": (width - pw - margin, height - ph - margin),
    }
    best = None
    for name, (px, py) in corners.items():
        panel = (px, py, px + pw, py + ph)
        cost = sum(_overlap_area(panel, b) for b in boxes)
        if best is None or cost < best[0]:
            best = (cost, name, px, py)
    _cost, _name, px, py = best
    return px, py, pw, ph, row_h, wrap_width


def render_callouts(
    image_path: str,
    regions: Sequence[Dict[str, Any]],
    output_path: str,
    *,
    step_num: Optional[int] = None,
    accent: Tuple[int, int, int] = ACCENT,
) -> str:
    """Draw numbered callouts for ``regions`` and save to ``output_path``.

    Each region is ``{"px": [x0, y0, x1, y1], "label": str}``.  Returns the
    output path, which is always a valid image: when nothing could be drawn
    the source is re-saved unchanged there, matching how
    ``annotate_relevant_screenshot`` falls back.  The input path is only
    returned when the source itself cannot be opened.
    """
    pil_image = _config.PIL_Image
    pil_draw = _config.PIL_ImageDraw
    if pil_image is None or pil_draw is None:
        return image_path

    usable: List[Dict[str, Any]] = []
    try:
        with pil_image.open(image_path) as src:
            img = src.convert("RGBA")
    except Exception:
        return image_path
    width, height = img.size

    for region in regions:
        box = _clip_box(region.get("px") or [], width, height)
        label = str(region.get("label") or "").strip()
        if not box or not label:
            continue
        usable.append({"box": box, "label": label})
    if not usable:
        img.convert("RGB").save(output_path)
        return output_path

    scale = max(1, min(width, height) // 300)
    stroke = max(2, scale)
    halo = stroke + 2
    base_size = max(13, min(width, height) // 55)
    font = _font(base_size)
    badge_font = _font(max(11, int(base_size * 0.95)))

    overlay = pil_image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = pil_draw.Draw(overlay)

    # Rectangles + numbered badges.
    for idx, entry in enumerate(usable, start=1):
        x0, y0, x1, y1 = entry["box"]
        draw.rectangle([x0 - halo, y0 - halo, x1 + halo, y1 + halo], outline=(0, 0, 0, 235), width=halo)
        draw.rectangle([x0, y0, x1, y1], outline=(*accent, 255), width=stroke)
        r = max(11, int(base_size * 0.82))
        cx, cy = x0, y0
        draw.ellipse([cx - r - 2, cy - r - 2, cx + r + 2, cy + r + 2], fill=(0, 0, 0, 240))
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*accent, 255))
        num = str(idx)
        tw, th = _measure(draw, num, badge_font)
        draw.text((cx - tw / 2, cy - th / 2 - 1), num, fill=TEXT + (255,), font=badge_font)

    # Legend panel.
    px, py, pw, ph, row_h, wrap_width = _panel_layout(
        draw, usable, font, width, height, [e["box"] for e in usable]
    )
    draw.rectangle([px, py, px + pw, py + ph], fill=PANEL_BG + (242,))
    draw.rectangle([px, py, px + pw, py + ph], outline=(*accent, 255), width=2)

    cursor = py + 10
    for idx, entry in enumerate(usable, start=1):
        lines = _wrap(draw, entry["label"], font, wrap_width)
        block_h = row_h * max(1, len(lines))
        num = str(idx)
        nr = max(9, int(base_size * 0.62))
        ncx, ncy = px + 16 + nr, cursor + row_h // 2
        draw.ellipse([ncx - nr, ncy - nr, ncx + nr, ncy + nr], fill=(*accent, 255))
        tw, th = _measure(draw, num, badge_font)
        draw.text((ncx - tw / 2, ncy - th / 2 - 1), num, fill=TEXT + (255,), font=badge_font)
        for line_no, line in enumerate(lines):
            draw.text((px + 18 + nr * 2 + 6, cursor + line_no * row_h + 3), line,
                      fill=TEXT + (255,), font=font)

        # Leader line: nearest point of the panel edge to the nearest box corner.
        x0, y0, x1, y1 = entry["box"]
        corners = [(x0, y0), (x1, y0), (x0, y1), (x1, y1)]
        target = min(corners, key=lambda c: (c[0] - (px + pw / 2)) ** 2 + (c[1] - (py + ph / 2)) ** 2)
        anchor_y = cursor + row_h // 2
        anchor_x = px + pw if target[0] > px + pw else (px if target[0] < px else px + pw / 2)
        draw.line([(anchor_x, anchor_y), target], fill=(0, 0, 0, 220), width=stroke + 2)
        draw.line([(anchor_x, anchor_y), target], fill=(*accent, 235), width=stroke)
        cursor += block_h + (row_h // 3)

    if step_num is not None:
        text = f"Step {step_num}"
        bfont = _font(max(12, min(width, height) // 80))
        tw, th = _measure(draw, text, bfont)
        bx, by = 16, 16
        draw.rectangle([bx, by, bx + tw + 20, by + th + 14], fill=(*accent, 255))
        draw.rectangle([bx, by, bx + tw + 20, by + th + 14], outline=(0, 0, 0, 255), width=2)
        draw.text((bx + 10, by + 6), text, fill=TEXT + (255,), font=bfont)

    composed = pil_image.alpha_composite(img, overlay)
    composed.convert("RGB").save(output_path)
    return output_path


__all__ = ["render_callouts"]
