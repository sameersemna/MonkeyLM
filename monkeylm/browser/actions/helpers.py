"""Element interaction helpers for browser actions."""

from __future__ import annotations

import asyncio
import re
from typing import Any, List, Optional, Tuple

try:
    from playwright.async_api import Page
except Exception:  # pragma: no cover - optional dependency path
    Page = Any  # type: ignore[misc]

from monkeylm.browser.snapshot.selectors import INTERACTIVE_ELEMENTS_SELECTOR


def _extract_target_id(target: Any) -> Optional[int]:
    if isinstance(target, int):
        return target if target >= 0 else None
    if not isinstance(target, (str, float, bool)):
        return None
    target_str = str(target or "").strip()
    if not target_str:
        return None
    if target_str.isdigit():
        parsed = int(target_str)
        if parsed < 0:
            return None
        return parsed
    match = re.search(r"\[id\s*=\s*(\d+)\]", target_str, re.IGNORECASE)
    if match:
        parsed = int(match.group(1))
        if parsed < 0:
            return None
        return parsed
    return None


async def _locator_for_target_id(page: Page, target_id: Any) -> Optional[Any]:
    parsed_id = _extract_target_id(target_id)
    if parsed_id is None:
        return None
    candidates = page.locator(INTERACTIVE_ELEMENTS_SELECTOR)
    count = await candidates.count()
    visible_index = 0
    for idx in range(count):
        candidate = candidates.nth(idx)
        bbox = await candidate.bounding_box()
        if not bbox:
            continue
        if bbox.get("width", 0) <= 0 or bbox.get("height", 0) <= 0:
            continue
        if visible_index == parsed_id:
            return candidate
        visible_index += 1
    return None


async def _resolve_interaction_mode(locator: Any) -> str:
    if locator is None:
        return "unsupported"
    try:
        tag_name = await locator.evaluate("el => el.tagName.toLowerCase()")
        input_type = await locator.evaluate("el => (el.type || '').toLowerCase()")
    except Exception:
        return "unsupported"
    if tag_name == "select":
        return "select"
    if tag_name == "textarea":
        return "text_input"
    if tag_name == "input":
        if input_type in {"checkbox", "radio"}:
            return "checkbox_radio"
        if input_type in {"file", "hidden", "submit", "button", "image", "reset"}:
            return "unsupported"
        return "text_input"
    return "unsupported"


async def _fill_input_resilient(
    page: Page,
    locator: Any,
    value: str,
    *,
    target: str = "",
    timeout_ms: int = 3000,
    attempts: int = 2,
) -> bool:
    """Fill a form control without letting transient visibility issues consume the whole step."""
    if locator is None:
        return False

    wait_timeout_ms = max(250, min(1000, timeout_ms // 2))
    max_attempts = max(1, attempts)
    last_error: Exception | None = None

    for attempt in range(max_attempts):
        try:
            await locator.scroll_into_view_if_needed(timeout=wait_timeout_ms)
        except (Exception, asyncio.CancelledError):
            pass
        try:
            await locator.wait_for(state="visible", timeout=wait_timeout_ms)
        except (Exception, asyncio.CancelledError):
            pass
        try:
            await locator.fill(str(value), timeout=timeout_ms)
            return True
        except (Exception, asyncio.CancelledError) as exc:  # pragma: no cover - defensive fallback
            last_error = exc
            if attempt < max_attempts - 1:
                await asyncio.sleep(0.1)
                continue
            break

    try:
        await locator.evaluate(
            """
            (el, payload) => {
                if (!el) return false;
                if (el.disabled) return false;
                if (el.readOnly) return false;
                el.focus();
                el.value = payload;
                el.setAttribute('value', payload);
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                return true;
            }
            """,
            str(value),
        )
        return True
    except (Exception, asyncio.CancelledError):
        return False


async def _click_element_resilient(
    page: Page,
    locator: Any,
    *,
    target: str = "",
    timeout_ms: int = 2000,
    attempts: int = 2,
) -> bool:
    """Click a target without letting transient visibility issues consume the whole step."""
    if locator is None:
        return False

    wait_timeout_ms = max(250, min(1000, timeout_ms // 2))
    max_attempts = max(1, attempts)

    for attempt in range(max_attempts):
        try:
            await locator.scroll_into_view_if_needed(timeout=wait_timeout_ms)
        except (Exception, asyncio.CancelledError):
            pass
        try:
            await locator.wait_for(state="visible", timeout=wait_timeout_ms)
        except (Exception, asyncio.CancelledError):
            pass
        try:
            await locator.click(timeout=timeout_ms)
            return True
        except (Exception, asyncio.CancelledError):
            if attempt < max_attempts - 1:
                await asyncio.sleep(0.1)
                continue
            break

    try:
        await locator.evaluate(
            """
            (el) => {
                if (!el) return false;
                if (el.disabled) return false;
                el.click();
                return true;
            }
            """
        )
        return True
    except (Exception, asyncio.CancelledError):
        return False


async def _fill_select_option(
    page: Page, locator: Any, payload_value: str, control_options: List[str], strategy: str
) -> Tuple[str, str]:
    if not locator:
        return "", "select_locator_missing"

    async def _try_select(value: str) -> bool:
        try:
            await locator.select_option(value=value, timeout=1000)
            return True
        except Exception:
            return False

    if payload_value and payload_value in control_options:
        if await _try_select(payload_value):
            return payload_value, "select_model_provided_option"
        return payload_value, "select_model_provided_option_failed"

    if control_options:
        if strategy == "EDGE_CASE_FUZZ":
            invalid_value = "__monkeylm_invalid_option__"
            if await _try_select(invalid_value):
                return invalid_value, "fuzz_select_invalid_option_accepted"
            return invalid_value, f"fuzz_select_invalid_option_rejected"
        chosen = control_options[0]
        if await _try_select(chosen):
            return chosen, "happy_select_first_option"
        return chosen, "happy_select_first_option_failed"

    try:
        await locator.evaluate(
            """
            (el) => {
                if (!el) return false;
                if (el.disabled) return false;
                const options = Array.from(el.options || []);
                if (options.length === 0) return false;
                const firstEnabled = options.find((option) => !option.disabled && option.value);
                if (firstEnabled) {
                    el.value = firstEnabled.value;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    return true;
                }
                return false;
            }
            """
        )
    except Exception:
        pass
    return "", "select_no_options_available"


async def _resolve_form_boundary(
    page: Page, target: str
) -> Tuple[Optional[Any], str]:
    target_locator = await _locator_for_target_id(page, target)
    if target_locator is None:
        return None, "target_element_not_found"
    tag_name = await target_locator.evaluate("el => el.tagName.toLowerCase()")
    if tag_name == "form":
        bbox = await target_locator.bounding_box()
        if bbox and bbox.get("width", 0) > 0 and bbox.get("height", 0) > 0:
            return target_locator, "target_is_form"
        return None, "target_is_form_but_invisible"
    form_handle = await target_locator.evaluate_handle("el => el.closest('form')")
    try:
        is_null = await form_handle.json_value() is None
        if is_null:
            return None, "no_form_ancestor_found"
        target_bbox = await target_locator.bounding_box()
        if not target_bbox:
            return None, "target_element_has_no_bounding_box"
        target_x = target_bbox.get("x", 0)
        target_y = target_bbox.get("y", 0)
        forms_locator = page.locator("form:visible")
        form_count = await forms_locator.count()
        if form_count == 0:
            return None, "no_visible_forms_on_page"
        best_form_idx = None
        best_distance_sq = float("inf")
        for idx in range(form_count):
            form_elem = forms_locator.nth(idx)
            form_bbox = await form_elem.bounding_box()
            if not form_bbox:
                continue
            tol = 10.0
            if (
                target_x >= form_bbox["x"] - tol
                and target_x <= form_bbox["x"] + form_bbox["width"] + tol
                and target_y >= form_bbox["y"] - tol
                and target_y <= form_bbox["y"] + form_bbox["height"] + tol
            ):
                return form_elem, "target_inside_form_bounds"
            dx = (form_bbox["x"] + form_bbox["width"] / 2) - target_x
            dy = (form_bbox["y"] + form_bbox["height"] / 2) - target_y
            dist_sq = dx * dx + dy * dy
            if dist_sq < best_distance_sq:
                best_distance_sq = dist_sq
                best_form_idx = idx
        if best_form_idx is not None:
            return forms_locator.nth(best_form_idx), "nearest_form_by_bounding_box"
        return None, "no_form_near_target"
    finally:
        await form_handle.dispose()
