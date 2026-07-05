"""Ollama client wrappers, vision model routing, and decision prompt engineering."""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
from typing import Any, Dict, List, Optional, Tuple

import ollama

from monkeylm.config import (
    Faker,
    FormControlRecord,
    Image,
    ImageDraw,
    OLLAMA_DECISION_OPTIONS,
    Settings,
    _local_service_log,
    normalize_action_plan,
)


# ── Ollama chat with retry ────────────────────────────────────────────────────


def _is_ollama_overload_error(exc: Exception) -> bool:
    """Detect Ollama 503/overload or queue-pressure errors."""
    status_code = getattr(exc, "status_code", None)
    if status_code == 503:
        return True
    exc_str = str(exc).lower()
    return any(marker in exc_str for marker in ["503", "overload", "queue", "busy", "too many requests"])


async def _ollama_chat_with_retry(
    *,
    settings: Settings,
    model: str,
    messages: List[Dict[str, str]],
    timeout_seconds: float,
    max_retries: int = 3,
) -> Optional[Dict[str, Any]]:
    """Call ollama.chat asynchronously with a strict timeout and exponential backoff on overload."""
    # Log which model is being used for this inference
    print(f"   └─ 🤖 Calling {model} (timeout={timeout_seconds}s, retries={max_retries})")
    
    base_delay = 1.0
    last_exc: Optional[Exception] = None

    for attempt in range(1, max_retries + 1):
        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    ollama.chat,
                    model=model,
                    messages=messages,
                    format="json",
                    options=OLLAMA_DECISION_OPTIONS,
                ),
                timeout=timeout_seconds,
            )
            return response
        except asyncio.TimeoutError as exc:
            last_exc = exc
            _local_service_log(
                f"Ollama inference timed out after {timeout_seconds}s (attempt {attempt}/{max_retries})",
                settings.output_dir,
            )
        except Exception as exc:
            last_exc = exc
            if _is_ollama_overload_error(exc):
                _local_service_log(
                    f"Ollama inference overloaded (attempt {attempt}/{max_retries}): {exc}",
                    settings.output_dir,
                )
            else:
                return None

        if attempt >= max_retries:
            break

        delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0.0, 2.0)
        _local_service_log(
            f"Backing off from Ollama for {delay:.2f}s before retry {attempt + 1}/{max_retries}",
            settings.output_dir,
        )
        await asyncio.sleep(delay)

    if last_exc is not None:
        _local_service_log(
            f"Ollama inference failed after {max_retries} attempts: {last_exc}",
            settings.output_dir,
        )
    return None


# ── Decision engine ───────────────────────────────────────────────────────────


def _extract_target_id(target: Any) -> Optional[int]:
    if isinstance(target, int):
        return target if target >= 0 else None
    target_str = str(target or "").strip()
    if not target_str:
        return None

    if target_str.isdigit():
        return int(target_str)

    match = re.search(r"\[id\s*=\s*(\d+)\]", target_str, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def parse_action_plan_response(raw_content: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw_content, str):
        return None

    content = raw_content.replace("```json", "").replace("```", "").strip()
    if not content:
        return None

    try:
        parsed = json.loads(content)
    except Exception:
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except Exception:
            return None

    normalized = normalize_action_plan(parsed)
    action = normalized.get("action", "scroll")
    target = normalized.get("target", "")
    if action in {"click", "type"} and _extract_target_id(target) is None:
        return None

    return normalized


def build_decision_prompt(page_state: str, memory_logs: Optional[List[Dict[str, Any]]] = None) -> str:
    memory_logs = memory_logs or []
    memory_json = json.dumps(memory_logs, ensure_ascii=True, indent=2)
    return f"""
You are an Advanced Monkey Testing Agent. Your goal is to deeply test the app by filling forms, submitting data, and handling modals.

Current Page State:
{page_state}

## Memory Logs of Previous Vibe Changes
{memory_json}

Choose ONE action from this list:
1. "click": Click a button or link.
2. "type": Type a single value into one input field.
3. "submit_form": Fill a form and submit it. Use this when a form is present.
4. "handle_modal": If a modal/dialog is detected, try to close it (click 'X', 'Cancel', 'Close') or accept it.
5. "scroll": Scroll the page.

When you choose "submit_form" or "type" on a form control, you MUST also choose an action_strategy:
- "HAPPY_UPSERT": Generate valid, realistic data that should be accepted (e.g., proper emails, numbers within bounds).
- "EDGE_CASE_FUZZ": Generate data designed to break validation for that specific control type:
  * number fields: strings, negative values, values above max.
  * email/url/phone fields: malformed schemas.
  * required text/textarea fields: empty strings or whitespace only.
  * fields with maxlength/pattern: overflow or mismatch.
  * textarea: newlines, XSS fragments, large blobs.

Rules:
- If you see a <FORM>, prioritize "submit_form" or "type" inside it.
- If you see a <MODAL>, prioritize "handle_modal".
- Each element line starts with [id=N]. Use that numeric id for target selection.
- For actions that need a target, return "target" as [id=N] (example: [id=3]).
- Never return raw text labels as target.
- For "submit_form", include "input_payloads": a list of objects with "target" ([id=N]), "value", and "reason".
- The "action_strategy" field must be either "HAPPY_UPSERT" or "EDGE_CASE_FUZZ" and explain why you chose that payload block.

Respond ONLY with JSON:
{{
  "action": "submit_form",
  "target": "[id=0]",
  "value": "",
  "action_strategy": "HAPPY_UPSERT",
  "input_payloads": [
    {{"target": "[id=1]", "value": "valid@example.com", "reason": "happy_valid_email"}}
  ]
}}
"""


async def decide_next_action(
    settings: Settings,
    page_state: str,
    memory_store: Any = None,
) -> dict:
    """Call the LLM to decide the next monkey-testing action.

    Args:
        settings: Runtime configuration.
        page_state: Serialized PageSnapshot text.
        memory_store: QdrantMemoryStore instance (required; no global fallback).
    """
    if memory_store is None:
        raise ValueError("memory_store must be provided to decide_next_action")

    memory_logs = await memory_store.search_similar_layouts(page_state, limit=3)
    prompt = build_decision_prompt(page_state, memory_logs)

    response = await _ollama_chat_with_retry(
        settings=settings,
        model=settings.ollama_model,
        messages=[{"role": "user", "content": prompt}],
        timeout_seconds=settings.ollama_timeout_seconds,
        max_retries=3,
    )

    if response is not None:
        try:
            content = response["message"]["content"]
            parsed = parse_action_plan_response(content)
            if parsed is not None:
                return parsed
        except Exception as exc:
            _local_service_log(f"Failed to parse Ollama action plan response: {exc}", settings.output_dir)

    return normalize_action_plan({"action": "scroll", "target": "", "value": ""})


# ── Form payload generation ───────────────────────────────────────────────────


def generate_form_payload(control: FormControlRecord, strategy: str) -> Tuple[str, str]:
    """Return (payload, reason) for a form control based on the chosen strategy."""
    kind = control.semantic_kind
    input_type = control.input_type

    if strategy == "HAPPY_UPSERT":
        if kind == "email":
            return ("monkey.test@example.com", "happy_valid_email")
        if kind == "phone":
            return ("+15551234567", "happy_valid_phone")
        if kind == "url":
            return ("https://example.com/monkey-test", "happy_valid_url")
        if kind == "numeric":
            try:
                lo = float(control.min_value) if control.min_value else 0.0
                hi = float(control.max_value) if control.max_value else max(lo + 100.0, 100.0)
                val = lo + (hi - lo) * 0.5
                if input_type == "number" and (not control.step or float(control.step) == 1.0):
                    return (str(int(val)), "happy_mid_range_integer")
                return (f"{val:.2f}", "happy_mid_range_number")
            except Exception:
                return ("42", "happy_default_number")
        if kind == "datetime":
            return ("2026-07-01T12:00", "happy_valid_datetime")
        if kind in ("checkbox", "radio"):
            return ("true", "happy_checked")
        if kind == "password":
            return ("MonkeyP@ssw0rd!2026", "happy_valid_password")
        if kind == "textarea":
            return ("A concise happy-path description for MonkeyLM testing.", "happy_textarea")
        if kind == "select":
            if control.options:
                return (control.options[0], "happy_select_first_option")
            return ("", "happy_select_no_options_skip")
        if control.maxlength is not None and control.maxlength > 0:
            base = "MonkeyLM"
            return (base[: control.maxlength], f"happy_text_within_maxlength_{control.maxlength}")
        return ("MonkeyLM happy path text", "happy_default_text")

    # EDGE_CASE_FUZZ branch
    if kind == "numeric":
        try:
            lo = float(control.min_value) if control.min_value else None
            hi = float(control.max_value) if control.max_value else None
            choices = []
            if lo is not None:
                choices.append((str(lo - 1), "fuzz_below_min"))
            if hi is not None:
                choices.append((str(hi + 1), "fuzz_above_max"))
            choices.extend(
                [
                    ("not-a-number", "fuzz_string_in_number_field"),
                    ("-999999999", "fuzz_large_negative"),
                    ("1e309", "fuzz_numeric_overflow"),
                ]
            )
            return random.choice(choices)
        except Exception:
            return ("not-a-number", "fuzz_string_in_number_field")

    if kind == "email":
        return random.choice(
            [
                ("not-an-email", "fuzz_invalid_email_format"),
                ("a@b", "fuzz_too_short_email"),
                ("test@.com", "fuzz_malformed_domain"),
            ]
        )

    if kind == "url":
        return random.choice([("not-a-url", "fuzz_invalid_url"), ("ftp://missing", "fuzz_partial_url")])

    if kind == "phone":
        return random.choice([("abc-def-ghij", "fuzz_letters_in_phone"), ("", "fuzz_empty_phone")])

    if kind == "datetime":
        return random.choice([("not-a-date", "fuzz_invalid_datetime"), ("0000-00-00T00:00", "fuzz_zero_date")])

    if control.required and kind in {"text", "textarea", "search", "password"}:
        return random.choice([("", "fuzz_empty_required_field"), ("   ", "fuzz_whitespace_only")])

    if control.pattern:
        return random.choice([("!!!INVALID!!!", "fuzz_pattern_mismatch"), ("", "fuzz_empty_against_pattern")])

    if control.maxlength is not None and control.maxlength > 0:
        overflow = "A" * (control.maxlength + 10)
        return (overflow, f"fuzz_maxlength_overflow_{control.maxlength}")

    if kind == "textarea":
        return ("\n\n\n" + "\u003cscript\u003ealert(1)\u003c/script\u003e", "fuzz_textarea_newline_and_xss")

    if kind == "select":
        return ("__monkeylm_invalid_option__", "fuzz_select_invalid_option")

    # Generic text fuzz
    return random.choice(
        [
            ("' OR 1=1 --", "fuzz_sql_fragment"),
            ("\u003cscript\u003ealert('xss')\u003c/script\u003e", "fuzz_xss_payload"),
            ("A" * 12000, "fuzz_large_string_blob"),
            ("\u0000\u0001\u0002", "fuzz_control_chars"),
            ("日本語テスト🐵", "fuzz_unicode"),
        ]
    )


def _step_defects_summary(step_num: int, defects: Any) -> list[str]:
    """Collect short human-readable reasons why a step is considered annotation-worthy.

    Covers all nine DefectTracker categories so every defect type triggers
    vision-model screenshot annotation when PDF_GENERATE is enabled.
    """
    reasons: list[str] = []
    category_names = [
        ("security_risks", "security_risk"),
        ("visual_regressions", "visual_regression"),
        ("layout_instability", "layout_instability"),
        ("accessibility_violations", "a11y_violation"),
        ("performance_bottlenecks", "perf_bottleneck"),
        ("regression_findings", "regression"),
        ("console_findings", "console_finding"),
        ("race_findings", "race_condition"),
        ("boundary_drift", "boundary_drift"),
    ]
    for attr_name, label in category_names:
        for item in getattr(defects, attr_name, []):
            if int(item.get("step", -1)) == step_num:
                dtype = item.get("type", item.get("severity", "unknown"))
                reasons.append(f"{label}:{dtype}")
    return reasons


# ── Anti-loop heuristics ──────────────────────────────────────────────────────


def apply_state_aware_policy(
    settings: Settings,
    action_plan: Dict[str, Any],
    snapshot: Any,
    state_counts: Dict[str, int],
    seen_click_targets: set,
) -> Dict[str, Any]:
    from monkeylm.config import STATE_LOOP_THRESHOLD

    state_key = f"{snapshot.url}::{snapshot.structure_hash}"
    revisit_count = state_counts.get(state_key, 0)
    if revisit_count > STATE_LOOP_THRESHOLD:
        forced = random.choice(["random_jump", "restart_target"])
        return {"action": forced, "target": "", "value": ""}

    action = action_plan.get("action", "scroll")
    if action == "click" and action_plan.get("target") in seen_click_targets:
        clickable = [x for x in snapshot.elements if "<BUTTON" in x or "<A" in x]
        unseen = [x for x in clickable if x not in seen_click_targets]
        if unseen:
            pick = random.choice(unseen)
            id_match = re.search(r"\[id=(\d+)\]", pick)
            if id_match:
                action_plan["target"] = f"[id={id_match.group(1)}]"
    return action_plan


def _extract_all_target_ids(elements: List[str]) -> List[str]:
    """Return all [id=N] selector strings found in the serialized element list."""
    ids: List[str] = []
    for el in elements:
        for match in re.finditer(r"\[id=(\d+)\]", el):
            ids.append(f"[id={match.group(1)}]")
    return ids


def _break_action_loop(
    action_plan: Dict[str, Any], snapshot: Any, worker_label: str
) -> Dict[str, Any]:
    """Force exploration variance when the model repeats the same action/target."""
    current_target = str(action_plan.get("target", ""))
    all_targets = _extract_all_target_ids(snapshot.elements)
    alternatives = [t for t in all_targets if t != current_target]

    if alternatives:
        chosen_target = random.choice(alternatives)
        chosen_element = next((el for el in snapshot.elements if chosen_target in el), "")
        chosen_action = (
            "type"
            if any(tag in chosen_element.upper() for tag in {"<INPUT", "<TEXTAREA", "<SELECT"})
            else "click"
        )
        print(f"   -> 🔄 {worker_label} loop break: switching to {chosen_action} on {chosen_target}")
        return {
            "action": chosen_action,
            "target": chosen_target,
            "value": "",
            "action_strategy": "",
            "input_payloads": [],
        }

    fallback = random.choice(["scroll", "random_jump", "restart_target"])
    print(f"   -> 🔄 {worker_label} loop break: no alternative selectors; using {fallback}")
    return {"action": fallback, "target": "", "value": "", "action_strategy": "", "input_payloads": []}


# ── Vision model routing ──────────────────────────────────────────────────────


def _is_cloud_vision_model(model_name: str) -> bool:
    """Return True if the configured vision model routes through a cloud endpoint."""
    if not model_name:
        return False
    name = model_name.lower().strip()
    return name.endswith("-preview") or name.endswith("-cloud") or name.startswith("minimax-m3")


def _build_vision_annotation_prompt(context_issue: str) -> str:
    """Return a unified prompt that forces cloud and local vision models to emit the same coordinate schema."""
    return (
        "You are a QA vision assistant. A browser test step failed or produced a defect. "
        f"Issue context: {context_issue}\n\n"
        "Look at the screenshot and locate the region that best represents the issue. "
        "Return ONLY a JSON object with this exact schema:\n"
        '{"box_2d": [ymin, xmin, ymax, xmax], "description": "short sentence"}\n'
        "All coordinates are normalized percentages between 0.0 and 1.0. "
        "Return the bounding region using the field name `box_2d` - do not use `box`, `bbox`, or any other key. "
        "If you cannot locate the issue, return an empty box: [0.0, 0.0, 0.0, 0.0]."
    )


def _extract_box_from_prose(content: str) -> Optional[List[float]]:
    """Attempt to recover a normalized [ymin, xmin, ymax, xmax] box from raw prose."""
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


def _draw_red_box_arrow(image_path: str, box_pct: List[float], context_issue: str, output_path: str) -> bool:
    """Draw a red bounding box + pointer arrow on a screenshot."""
    if Image is None or ImageDraw is None:
        return False
    try:
        with Image.open(image_path) as img:
            img = img.convert("RGBA")
            width, height = img.size
            ymin, xmin, ymax, xmax = box_pct
            x0 = int(max(0.0, min(1.0, xmin)) * width)
            y0 = int(max(0.0, min(1.0, ymin)) * height)
            x1 = int(max(0.0, min(1.0, xmax)) * width)
            y1 = int(max(0.0, min(1.0, ymax)) * height)
            if x1 <= x0 or y1 <= y0:
                return False

            draw = ImageDraw.Draw(img)
            draw.rectangle([x0, y0, x1, y1], outline="red", width=4)

            arrow_start = (min(width - 20, x1 + 40), max(20, y0 - 40))
            arrow_end = ((x0 + x1) // 2, (y0 + y1) // 2)
            draw.line([arrow_start, arrow_end], fill="red", width=4)

            dx = arrow_end[0] - arrow_start[0]
            dy = arrow_end[1] - arrow_start[1]
            length = max(1.0, (dx * dx + dy * dy) ** 0.5)
            ux, uy = dx / length, dy / length
            px, py = -uy, ux
            head_len = 12
            p1 = (
                arrow_end[0] + int(head_len * (-ux + 0.5 * px)),
                arrow_end[1] + int(head_len * (-uy + 0.5 * py)),
            )
            p2 = (
                arrow_end[0] + int(head_len * (-ux - 0.5 * px)),
                arrow_end[1] + int(head_len * (-uy - 0.5 * py)),
            )
            draw.line([p1, arrow_end, p2], fill="red", width=4)

            label = context_issue[:80]
            try:
                label_y = max(12, y0 - 18)
                draw.text((x0, label_y), label, fill="red")
            except Exception:
                pass

            img.save(output_path)
            return True
    except Exception as exc:
        _local_service_log(f"Failed to draw annotation box for {image_path}: {exc}")
        return False


async def annotate_relevant_screenshot(settings: Settings, image_path: str, context_issue: str) -> str:
    """Send a screenshot to the vision model and draw an annotated bounding box.

    Returns the path to the annotated image (may be the original if annotation fails).
    """
    active_model = settings.vision_model or settings.pdf_vision_model
    cloud = _is_cloud_vision_model(active_model)
    prompt_text = _build_vision_annotation_prompt(context_issue)

    # Encode screenshot as base64 for vision model
    import base64

    with open(image_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode("utf-8")

    output_path = image_path.replace(".png", "_annotated.png").replace(".jpg", "_annotated.jpg")

    if cloud:
        # Cloud vision model (e.g., minimax-m3, gemini-preview)
        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    ollama.chat,
                    model=active_model,
                    messages=[
                        {
                            "role": "user",
                            "content": prompt_text,
                            "images": [img_b64],
                        }
                    ],
                    format="json",
                    options={"temperature": 0.0},
                ),
                timeout=settings.pdf_vision_timeout_seconds,
            )
            content = response.get("message", {}).get("content", "")
        except Exception as exc:
            _local_service_log(f"Cloud vision model failed: {exc}", settings.output_dir)
            return image_path
    else:
        # Local vision model (e.g., llama3.2-vision)
        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    ollama.chat,
                    model=active_model,
                    messages=[
                        {
                            "role": "user",
                            "content": prompt_text,
                            "images": [img_b64],
                        }
                    ],
                    format="json",
                    options={"temperature": 0.0},
                ),
                timeout=settings.pdf_vision_timeout_seconds,
            )
            content = response.get("message", {}).get("content", "")
        except Exception as exc:
            _local_service_log(f"Local vision model failed: {exc}", settings.output_dir)
            return image_path

    # Parse box_2d from response
    box: Optional[List[float]] = None
    if isinstance(content, str):
        cleaned = content.replace("```json", "").replace("```", "").strip()
        try:
            parsed = json.loads(cleaned)
            if "box_2d" in parsed:
                box = parsed["box_2d"]
        except Exception:
            box = _extract_box_from_prose(content)

    if box and len(box) == 4:
        if _draw_red_box_arrow(image_path, box, context_issue, output_path):
            return output_path

    # Fallback: copy original as annotated
    import shutil

    shutil.copy2(image_path, output_path)
    return output_path
