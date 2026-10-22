"""Vision model routing - cloud/local detection, annotation prompts, and screenshot annotation."""

from __future__ import annotations

import asyncio
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from monkeylm import config
from monkeylm.config import PIL_ImageFont, Settings
from monkeylm.models.ollama import _safe_json_parse


def _is_cloud_vision_model(model_name: str) -> bool:
    if not model_name:
        return False
    name = model_name.lower().strip()
    return name.endswith("-preview") or name.endswith("-cloud") or name.startswith("minimax-m3")


# Native bounding-box coordinate scales emitted by current vision-LLM families.
# Gemini and Qwen-VL pre-training established a 0-1000 normalized grid that is
# surprisingly fragile to prompt engineering: "rescaling outputs from this
# internal space to image dimensions yields up to 48% F1 improvement" while
# "prompt engineering shows negligible effect (+/- 1-4% F1)"
# (IEEE Access, doi 10.1109/access.2026.3675517).  InternVL/DeepSeek-VL2
# follow the same 0-999/0-1000 convention.  Claude and most local llama
# variants default to absolute pixel coordinates instead; the caller rescales
# using the post-resize image dimensions.  ``None`` means "treat the response
# as already-normalized [0,1]" — the historical default and what every test
# pinned before this change.
_COORD_SCALE_PREFIXES: Dict[str, int] = {
    "gemini": 1000,
    "qwen": 1000,
    "internvl": 1000,
    "deepseek": 1000,
}


def _coord_scale(model_name: Optional[str]) -> Optional[int]:
    """Return the native coordinate scale for ``model_name`` or ``None``.

    Returns:
        * ``int`` — the divisor the parser must apply (e.g. ``1000`` for
          ``gemini-3-flash-preview`` so a Gemini reply of
          ``[250,120,480,900]`` is rescaled to ``[0.25,0.12,0.48,0.90]``).
        * ``None`` — the response is either already normalized [0,1] or
          expected in absolute pixel coordinates (in which case the caller
          supplies ``image_size`` so the parser can rescale).
    """
    if not model_name:
        return None
    name = model_name.lower().strip()
    for prefix, scale in _COORD_SCALE_PREFIXES.items():
        if prefix in name:
            return scale
    return None


def _build_vision_annotation_prompt(context_issue: str) -> str:
    """Legacy single-class prompt dispatcher.

    Phase 2 routes through :func:`build_prompt_for_defect_type` when the
    executor knows the defect class; this thin wrapper keeps the
    ``(issue) -> str`` signature so existing callers (e.g.
    ``scripts/backfill_annotations.py``) continue to work unchanged.
    """
    from monkeylm.models.vision.prompts import build_prompt_for_defect_type

    return build_prompt_for_defect_type(issue=context_issue)


def _extract_description(content: str) -> Optional[str]:
    """Extract the ``description`` field from a vision-JSON response.

    The model is asked for ``{box_2d, description}``; :func:`_parse_vision_box`
    only inspects the box. This helper pulls the description out so the PDF
    plate caption can render it (Phase 1.6). Falls back to ``None`` when the
    response is prose, malformed, or absent.
    """
    if not isinstance(content, str):
        return None
    try:
        parsed = _safe_json_parse(content)
    except Exception:
        return None
    if isinstance(parsed, dict):
        desc = parsed.get("description")
        if isinstance(desc, str):
            text = desc.strip()
            return text or None
    return None


def _parse_vision_box(
    content: str,
    *,
    model: Optional[str] = None,
    image_size: Optional[Tuple[int, int]] = None,
) -> Optional[List[float]]:
    """Parse a vision-model bounding box reply into the canonical [0,1] layout.

    Args:
        content: The raw model response (JSON or prose fallback).
        model: Optional model name. When the model is recognized as a 0-1000
            family (Gemini/Qwen/InternVL/DeepSeek) the response is divided by
            the family's native scale.  When ``None`` (or an unrecognized
            model) the response is assumed to already be normalized to [0,1].
        image_size: Optional ``(width, height)`` of the post-resize image.  Used
            to rescale **absolute pixel** coordinate replies from llama-style
            vision models to [0,1].  Ignored when the response is already
            normalized or comes from a 0-1000 model.

    The existing positional-only contract is preserved: callers that omit
    ``model`` keep the original 0-1 behavior, which is what the
    ``test_parse_vision_box_*`` regression tests pin.
    """
    parsed = _safe_json_parse(content)
    raw: Optional[List[float]] = None
    if isinstance(parsed, dict) and "box_2d" in parsed:
        box = parsed.get("box_2d")
        if isinstance(box, (list, tuple)) and len(box) == 4:
            try:
                values = [float(v) for v in box]
            except (TypeError, ValueError):
                values = []
            if len(values) == 4:
                raw = values
    if raw is None:
        raw = _extract_box_from_prose(content)
    if raw is None:
        return None

    scale = _coord_scale(model)
    rescaled: List[float] = list(raw)
    if scale is not None and any(v > 1.0 for v in rescaled):
        # Native 0-1000 (or 0-N) grid: only rescale when the model emitted
        # a value > 1.0.  Models that already output floats in [0,1] under
        # the same name (e.g. Gemini-1.0 returning ``0.12`` instead of
        # ``120``) are accepted as-is to avoid the model-mismatch silent
        # rescaling that this branch previously caused.
        rescaled = [v / float(scale) for v in rescaled]
    elif image_size is not None and any(v > 1.0 for v in rescaled):
        # Looks like absolute pixels (any value > 1) and the caller supplied
        # image dims — rescale.  This branch is intentionally permissive
        # so llama-style absolute-pixel replies don't silently pass the
        # bounds check below as "already normalized".
        width, height = image_size
        if width <= 0 or height <= 0:
            return None
        ymin, xmin, ymax, xmax = rescaled
        rescaled = [ymin / height, xmin / width, ymax / height, xmax / width]

    if not all(0.0 <= v <= 1.0 for v in rescaled):
        return None
    # The empty box ``[0.0, 0.0, 0.0, 0.0]`` is an explicit signal from the
    # prompt that the model found no issue; preserve the original parser
    # behavior of returning it as-is.  For any non-empty box, require a real
    # rectangle.
    if not (rescaled == [0.0, 0.0, 0.0, 0.0]):
        if rescaled[2] <= rescaled[0] or rescaled[3] <= rescaled[1]:
            return None
    return rescaled


def _extract_box_from_prose(content: str) -> Optional[List[float]]:
    if not isinstance(content, str) or not content:
        return None
    patterns = [
        r"\[\s*(\d+\.?\d*)\s*,\s*(\d+\.?\d*)\s*,\s*(\d+\.?\d*)\s*,\s*(\d+\.?\d*)\s*\]",
        r"\[\s*(\d+\.?\d*)\s+\d+\.?\d*\s+\d+\.?\d*\s+(\d+\.?\d*)\s*\]",
        r"(\d+\.?\d*)\s*[, ]\s*(\d+\.?\d*)\s*[, ]\s*(\d+\.?\d*)\s*[, ]\s*(\d+\.?\d*)",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, content):
            try:
                values = [float(match.group(i)) for i in range(1, 5)]
            except Exception:
                continue
            if all(0.0 <= v <= 1.0 for v in values):
                return values
    return None


def _resolve_label_font(font_size: int) -> Any:
    if PIL_ImageFont is None:
        return None
    candidate_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    ]
    for path in candidate_paths:
        try:
            if os.path.isfile(path):
                return PIL_ImageFont.truetype(path, font_size)
        except Exception:
            continue
    try:
        return PIL_ImageFont.load_default()
    except Exception:
        return None


def _wrap_text_to_lines(text: str, font: Any, max_width: int, draw: Any) -> List[str]:
    if not text:
        return []
    truncated = False
    if font is None or max_width <= 0:
        chunk = max(1, max_width // 10)
        chunks = [text[i : i + chunk] for i in range(0, len(text), chunk)]
        if len(chunks) > 3:
            chunks = chunks[:3]
            truncated = True
        if truncated and chunks:
            last = chunks[-1]
            keep = max(0, len(last) - 3)
            chunks[-1] = last[:keep] + "..."
        return chunks
    words = text.split()
    lines: List[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        try:
            bbox = draw.textbbox((0, 0), candidate, font=font)
            width = bbox[2] - bbox[0]
        except Exception:
            width = len(candidate) * 7
        if width <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
        if len(lines) == 3:
            if word != current:
                truncated = True
            break
    if current and len(lines) < 3:
        lines.append(current)
    used = sum(len(line.split()) for line in lines)
    total = len(words)
    if used < total:
        truncated = True
    if truncated and lines:
        last = lines[-1]
        try:
            bbox = draw.textbbox((0, 0), last + "...", font=font)
            if bbox[2] - bbox[0] <= max_width:
                lines[-1] = last + "..."
            else:
                keep = max(0, len(last) - 3)
                lines[-1] = last[:keep] + "..."
        except Exception:
            keep = max(0, len(last) - 3)
            lines[-1] = last[:keep] + "..."
    return lines


def _draw_red_box_arrow(
    image_path: str,
    box_pct: List[float],
    context_issue: str,
    output_path: str,
    description: Optional[str] = None,
    step_num: Optional[int] = None,
) -> bool:
    if config.Image is None or config.ImageDraw is None or config.PIL_Image is None or config.PIL_ImageDraw is None:
        msg = (
            "annotate_relevant_screenshot: PIL symbols unavailable "
            "(Image/ImageDraw/PIL_Image/PIL_ImageDraw). The annotation drawer "
            "will fall back to a copy of the original screenshot."
        )
        print(f"   ⚠️  {msg}")
        config._local_service_log(msg, os.path.dirname(output_path) or ".")
        return False
    try:
        with config.PIL_Image.open(image_path) as img:
            img = img.convert("RGBA")
            width, height = img.size
            ymin, xmin, ymax, xmax = (float(v) for v in box_pct)
            x0 = int(max(0, min(width - 1, xmin * width)))
            y0 = int(max(0, min(height - 1, ymin * height)))
            x1 = int(max(0, min(width - 1, xmax * width)))
            y1 = int(max(0, min(height - 1, ymax * height)))
            if x1 <= x0 or y1 <= y0:
                return False
            border_w = max(4, min(width, height) // 250)
            halo_w = border_w + 2
            arrow_w = max(3, min(width, height) // 400)
            head_len = max(20, min(width, height) // 30)
            draw = config.PIL_ImageDraw.Draw(img)
            draw.rectangle(
                [x0 - halo_w // 2, y0 - halo_w // 2, x1 + halo_w // 2, y1 + halo_w // 2],
                outline=(0, 0, 0, 255),
                width=halo_w,
            )
            draw.rectangle(
                [x0, y0, x1, y1],
                outline=(220, 30, 30, 255),
                width=border_w,
            )
            box_cx, box_cy = (x0 + x1) // 2, (y0 + y1) // 2
            corners = [
                (40, 40),
                (width - 40, 40),
                (40, height - 40),
                (width - 40, height - 40),
            ]
            tail = max(corners, key=lambda c: (c[0] - box_cx) ** 2 + (c[1] - box_cy) ** 2)
            box_corners = [(x0, y0), (x1, y0), (x0, y1), (x1, y1)]
            head_target = min(
                box_corners,
                key=lambda c: (c[0] - tail[0]) ** 2 + (c[1] - tail[1]) ** 2,
            )
            tail_radius = max(8, min(width, height) // 140)
            draw.ellipse(
                [tail[0] - tail_radius - 2, tail[1] - tail_radius - 2, tail[0] + tail_radius + 2, tail[1] + tail_radius + 2],
                fill=(0, 0, 0, 255),
            )
            draw.ellipse(
                [tail[0] - tail_radius, tail[1] - tail_radius, tail[0] + tail_radius, tail[1] + tail_radius],
                fill=(220, 30, 30, 255),
            )
            draw.line([tail, head_target], fill=(0, 0, 0, 255), width=arrow_w + 2)
            draw.line([tail, head_target], fill=(220, 30, 30, 255), width=arrow_w)
            dx = head_target[0] - tail[0]
            dy = head_target[1] - tail[1]
            length = max(1.0, (dx * dx + dy * dy) ** 0.5)
            ux, uy = dx / length, dy / length
            px, py = -uy, ux
            p1 = (head_target[0] + int(head_len * (-ux + 0.55 * px)), head_target[1] + int(head_len * (-uy + 0.55 * py)))
            p2 = (head_target[0] + int(head_len * (-ux - 0.55 * px)), head_target[1] + int(head_len * (-uy - 0.55 * py)))
            draw.polygon([head_target, p1, p2], fill=(220, 30, 30, 255))
            draw.line([p1, head_target, p2], fill=(0, 0, 0, 255), width=max(2, arrow_w // 2))
            label_text = (description or "").strip()
            if not label_text:
                label_text = (context_issue or "").strip().splitlines()[0] if context_issue else ""
            label_text = label_text[:240]
            if label_text:
                font_size = max(14, min(width, height) // 50)
                font = _resolve_label_font(font_size)
                max_label_w = width - 40
                lines = _wrap_text_to_lines(label_text, font, max_label_w, draw)
                line_widths = []
                ascent = font_size
                for line in lines:
                    try:
                        if font is not None:
                            bbox = draw.textbbox((0, 0), line, font=font)
                            line_widths.append(bbox[2] - bbox[0])
                            ascent = max(ascent, bbox[3] - bbox[1])
                        else:
                            line_widths.append(len(line) * 8)
                    except Exception:
                        line_widths.append(len(line) * 8)
                if line_widths:
                    label_w = min(max_label_w, max(line_widths) + 24)
                else:
                    label_w = 200
                line_h = int(font_size * 1.25)
                label_h = line_h * max(1, len(lines)) + 16
                label_x = max(20, min(width - label_w - 20, x0))
                if y0 - label_h - 12 >= 8:
                    label_y = y0 - label_h - 12
                else:
                    label_y = min(height - label_h - 8, y1 + 12)
                draw.rectangle([label_x, label_y, label_x + label_w, label_y + label_h], fill=(15, 15, 20, 235))
                draw.rectangle([label_x, label_y, label_x + label_w, label_y + label_h], outline=(220, 30, 30, 255), width=2)
                for i, line in enumerate(lines):
                    try:
                        if font is not None:
                            draw.text((label_x + 12, label_y + 8 + i * line_h), line, fill=(255, 255, 255, 255), font=font)
                        else:
                            draw.text((label_x + 12, label_y + 8 + i * line_h), line, fill=(255, 255, 255, 255))
                    except Exception:
                        pass
            if step_num is not None:
                badge_text = f"Step {step_num}"
                badge_font_size = max(12, min(width, height) // 80)
                badge_font = _resolve_label_font(badge_font_size)
                try:
                    if badge_font is not None:
                        bbox = draw.textbbox((0, 0), badge_text, font=badge_font)
                        bw = bbox[2] - bbox[0] + 20
                        bh = bbox[3] - bbox[1] + 12
                    else:
                        bw = len(badge_text) * 8 + 20
                        bh = 26
                except Exception:
                    bw, bh = 110, 26
                bx, by = 16, 16
                draw.rectangle([bx, by, bx + bw, by + bh], fill=(220, 30, 30, 255))
                draw.rectangle([bx, by, bx + bw, by + bh], outline=(0, 0, 0, 255), width=2)
                try:
                    if badge_font is not None:
                        draw.text((bx + 10, by + 4), badge_text, fill=(255, 255, 255, 255), font=badge_font)
                    else:
                        draw.text((bx + 10, by + 4), badge_text, fill=(255, 255, 255, 255))
                except Exception:
                    pass
            img.save(output_path)
            return True
    except Exception as exc:
        config._local_service_log(f"Failed to draw annotation box for {image_path}: {exc}")
        return False


async def annotate_relevant_screenshot(
    settings: Settings,
    image_path: str,
    context_issue: str,
    step_num: Optional[int] = None,
    *,
    defect_type: Optional[str] = None,
    defect_context_payload: Optional[Dict[str, Any]] = None,
    ocr_text: Optional[str] = None,
    contrast_ratio: Optional[float] = None,
    min_ratio: Optional[float] = None,
    threshold: Optional[float] = None,
    removed_element: Optional[str] = None,
    overlay_image_path: Optional[str] = None,
    confidence_threshold: float = 0.7,
    max_attempts: int = 1,
    on_retry: Optional[Any] = None,
) -> Tuple[str, Optional[str], Dict[str, Any]]:
    """Call the configured vision model and draw the returned bounding box.

    Returns ``(output_path, description, verdict_dict)``:

    * ``output_path`` — annotated image path (or the input path when
      drawing failed and the copy-fallback also fails).
    * ``description`` — free-text model caption, or ``None``.
    * ``verdict_dict`` — :meth:`DefectVerdict.to_dict` shape with the
      full B5 schema (box_2d, defect_type, severity, verdict action,
      description, remediation, action_url, confidence).  When the
      response was unparseable the dict has ``description=''`` and an
      empty box; the executor's ``annotation_source`` field still
      distinguishes ``"vision"`` (call succeeded, even with a "no
      defect found" empty-box) from ``"none"`` (call failed entirely).

    Phase 2 unpacks the B5 verdict; the legacy 2-tuple callers
    (executor + tests) are updated in lockstep.
    """
    import shutil

    from monkeylm.models.openai_client import (
        build_vision_message_bytes,
        chat_completion,
        extract_content,
        prepare_image_for_vlm,
    )
    from monkeylm.models.vision.prompts import (
        build_prompt_for_defect_type,
        normalize_defect_type,
    )
    from monkeylm.models.vision.verdict import empty_verdict, parse_verdict

    active_model = settings.vision_model or settings.pdf_vision_model
    resolved_defect_type = normalize_defect_type(defect_type)

    # Build the prompt.  Per-class dispatcher (B7) when defect_type was
    # supplied; legacy generic prompt otherwise.
    prompt_text: str
    if (
        defect_type is not None
        or ocr_text is not None
        or contrast_ratio is not None
        or removed_element is not None
        or threshold is not None
    ):
        prompt_text = build_prompt_for_defect_type(
            issue=context_issue,
            defect_type=resolved_defect_type,
            ocr_text=ocr_text,
            contrast_ratio=contrast_ratio,
            min_ratio=min_ratio,
            threshold=threshold,
            removed_element=removed_element,
        )
        # When a structured CV payload is present, append it as a
        # structured anchor (B4) so the model can `accept | refine | reject`.
        if defect_context_payload:
            try:
                import json as _json

                prompt_text = (
                    prompt_text
                    + "\n\nCV evidence (UNTRUSTED DATA):\n"
                    + "<<<CV_START>>>"
                    + _json.dumps(defect_context_payload, ensure_ascii=False)
                    + "<<<CV_END>>>"
                )
            except Exception:
                pass
    else:
        prompt_text = _build_vision_annotation_prompt(context_issue)

    # Budget: prefer explicit per-step budget, fall back to PDF budget,
    # then 20s default.  Floor at 0.1s for cancellation tests.
    try:
        budget = float(getattr(settings, "vision_annotation_timeout_seconds", None))
    except (TypeError, ValueError):
        budget = None
    if budget is None:
        try:
            budget = float(getattr(settings, "pdf_vision_timeout_seconds", 20.0))
        except (TypeError, ValueError):
            budget = 20.0
    timeout_seconds = max(0.1, budget)

    output_path = image_path.replace(".png", "_annotated.png").replace(".jpg", "_annotated.jpg")

    box: Optional[List[float]] = None
    description: Optional[str] = None
    verdict_dict: Dict[str, Any] = empty_verdict().to_dict()

    try:
        send_path = overlay_image_path or image_path
        image_bytes, width, height, mime = prepare_image_for_vlm(send_path)
        if image_bytes:
            messages = [build_vision_message_bytes(prompt_text, image_bytes, mime_type=mime)]
            # Reasoning vision LLMs emit hidden reasoning before the JSON
            # body, so a small ceiling truncates the reply to
            # ``finish_reason="length"`` with empty content and silently
            # drops every mark.  Budget from settings, not a literal.
            try:
                max_tokens = int(getattr(settings, "vision_annotation_max_tokens", 2048))
            except (TypeError, ValueError):
                max_tokens = 2048
            max_tokens = max(256, max_tokens)
            response = None
            attempts = max(1, int(max_attempts))
            last_error: Optional[BaseException] = None
            for attempt in range(1, attempts + 1):
                try:
                    response = await asyncio.wait_for(
                        asyncio.to_thread(
                            chat_completion,
                            settings,
                            model=active_model,
                            messages=messages,
                            timeout_seconds=timeout_seconds,
                            json_mode=True,
                            temperature=0.0,
                            max_tokens=max_tokens,
                        ),
                        timeout=timeout_seconds,
                    )
                    break
                except (asyncio.TimeoutError, Exception) as attempt_exc:
                    last_error = attempt_exc
                    if attempt < attempts:
                        config._local_service_log(
                            f"Vision model {active_model} attempt {attempt}/{attempts} "
                            f"failed for {image_path} ({type(attempt_exc).__name__}: "
                            f"{attempt_exc}); retrying once.",
                            os.path.dirname(output_path) or ".",
                        )
                        if on_retry is not None:
                            try:
                                on_retry(attempt, attempts, attempt_exc)
                            except Exception:
                                pass
            if response is None and last_error is not None:
                raise last_error
            try:
                content = extract_content(response) if response else ""
                # A truncated reply is not a legitimate "no defect" answer:
                # surface it instead of silently shipping an unmarked copy.
                finish_reason = ""
                try:
                    finish_reason = str(response["choices"][0].get("finish_reason") or "")
                except Exception:
                    finish_reason = ""
                if finish_reason == "length" and not content.strip():
                    config._local_service_log(
                        f"Vision model {active_model} hit the {max_tokens}-token "
                        f"ceiling (finish_reason='length') and returned no JSON "
                        f"for {image_path}; no annotation will be drawn. Raise "
                        f"VISION_ANNOTATION_MAX_TOKENS or lower "
                        f"VISION_ANNOTATION_TIMEOUT_SECONDS pressure on the model.",
                        os.path.dirname(output_path) or ".",
                    )
            except asyncio.TimeoutError:
                config._local_service_log(
                    f"Vision model {active_model} timed out at {timeout_seconds}s for {image_path}",
                    os.path.dirname(output_path) or ".",
                )
                content = ""
            except Exception as exc:
                config._local_service_log(
                    f"Vision model {active_model} failed: {exc}",
                    os.path.dirname(output_path) or ".",
                )
                content = ""

            if isinstance(content, str) and content:
                # B5: parse the full B5 verdict dataclass.  The parser
                # accepts the legacy 2-field shape too, defaulting
                # defect_type='other' / severity='info'.  Pass the active
                # model name so the B2 model-aware coord rescaling
                # propagates into the verdict.
                image_size_arg = (
                    (width, height) if width and height else None
                )
                verdict = parse_verdict(
                    content,
                    model=active_model,
                    image_size=image_size_arg,
                )
                verdict_dict = verdict.to_dict()
                description = verdict.description or None

                # Empty-box escape: nothing to draw, but a valid
                # verdict still flows through.
                box = list(verdict.box_2d)
                if box == [0.0, 0.0, 0.0, 0.0]:
                    box = None

                # B10: second-pass verifier when the first-pass
                # confidence sits below the threshold and the call
                # produced a real bbox (not the empty-box escape).
                if (
                    box is not None
                    and verdict.confidence < confidence_threshold
                    and verdict.description
                ):
                    try:
                        from monkeylm.models.vision.verifier import run_verification

                        verified = await run_verification(
                            settings,
                            image_path,
                            issue_context=context_issue,
                            candidate_box=box,
                            description=verdict.description,
                            confidence_threshold=confidence_threshold,
                        )
                        if verified is not None:
                            answer, verifier_conf = verified
                            verdict_dict["verifier_answer"] = answer
                            verdict_dict["verifier_confidence"] = verifier_conf
                            if answer == "no":
                                # The verifier rejected the candidate —
                                # collapse the box so the executor
                                # records ``source='none'`` and the
                                # PDF plate is not drawn on a
                                # wrong-feature hot spot.
                                box = None
                    except Exception as exc:
                        config._local_service_log(
                            f"Verifier call raised (ignored): {exc}",
                            os.path.dirname(output_path) or ".",
                        )

    except Exception as exc:
        config._local_service_log(
            f"annotate_relevant_screenshot: unexpected error {exc!r}",
            os.path.dirname(output_path) or ".",
        )
        box = None
        description = None
        verdict_dict = empty_verdict().to_dict()

    if box and len(box) == 4:
        ok = _draw_red_box_arrow(
            image_path,
            box,
            context_issue,
            output_path,
            description=description,
            step_num=step_num,
        )
        if ok:
            return output_path, description, verdict_dict

    try:
        shutil.copy2(image_path, output_path)
    except Exception:
        return image_path, description, verdict_dict
    return output_path, description, verdict_dict
