"""Element interaction helpers for browser actions."""

from __future__ import annotations

import asyncio
import re
from typing import Any, List, Optional, Tuple

try:
    from playwright.async_api import Page
    from playwright.async_api import Error as PlaywrightError
except Exception:  # pragma: no cover - optional dependency path
    Page = Any  # type: ignore[misc]
    PlaywrightError = Exception  # type: ignore[misc,assignment]

from monkeylm.browser.snapshot.selectors import INTERACTIVE_ELEMENTS_SELECTOR

# Per-iteration cap on each locator.bounding_box() round-trip.
# The interactive-elements selector can return dozens of matches; without a
# cap, a single slow CDP round-trip on a degraded page can consume the entire
# step-timeout budget. 1.0s is generous for a healthy page and short enough
# to fail fast on a frozen one.
_BOUNDING_BOX_ITER_TIMEOUT_SECONDS = 1.0

# Default timeout for any Locator action method that we call without an
# explicit per-call timeout (e.g. ``locator.evaluate(...)``,
# ``locator.bounding_box()``, ``locator.count()``).
#
# This MUST stay well below ``step_timeout_seconds`` (default 30s) so that
# when the harness's outer ``asyncio.wait_for`` cancels, no Playwright
# internal Future is still in flight — that's exactly the race that produced
# the ``Future exception was never retrieved`` warning + visible
# ``TimeoutError('Timeout 30000ms exceeded')`` stack trace at shutdown.
_DEFAULT_LOCATOR_ACTION_TIMEOUT_MS = 2000


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
    """Resolve a model-chosen ``[id=N]`` to a real ``Locator``.

    Resolves the Nth **visible** element matching ``INTERACTIVE_ELEMENTS_SELECTOR``
    in a single ``page.evaluate()`` round-trip. The previous implementation
    walked the DOM in Python via ``candidates.nth(idx).bounding_box()`` in a
    loop, which:

    1. issued one CDP round-trip per DOM index (dozens on a busy page), and
    2. created one Playwright ``Future`` per iteration. When the step was
       cancelled by ``asyncio.wait_for`` mid-iteration, those Futures were
       abandoned. When the page/context later closed, each abandoned Future
       surfaced as ``TargetClosedError`` and, with no consumer, produced the
       ``Future exception was never retrieved`` asyncio warning at shutdown.

    The single-round-trip implementation removes both problems at the source.
    """
    parsed_id = _extract_target_id(target_id)
    if parsed_id is None:
        return None
    # Short-circuit on a closed page so we don't even queue an evaluate().
    if getattr(page, "is_closed", lambda: False)():
        return None

    js_payload: Any = None
    try:
        # One evaluate() call walks the matched elements in the browser and
        # returns the DOM index of the Nth *visible* one, or -1 if there are
        # fewer than N+1 visible matches. The visibility check uses the same
        # ``getBoundingClientRect().width/height > 0`` test that
        # ``capture_dom_and_layout`` uses, so the two never disagree.
        js_payload = await asyncio.wait_for(
            page.evaluate(
                """([selector, targetVisibleIndex]) => {
                    const interactives = Array.from(document.querySelectorAll(selector));
                    let visibleIndex = 0;
                    for (let i = 0; i < interactives.length; i++) {
                        const el = interactives[i];
                        const rect = el.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) {
                            if (visibleIndex === targetVisibleIndex) return i;
                            visibleIndex += 1;
                        }
                    }
                    return -1;
                }""",
                [INTERACTIVE_ELEMENTS_SELECTOR, parsed_id],
            ),
            timeout=_BOUNDING_BOX_ITER_TIMEOUT_SECONDS,
        )
    except PlaywrightError:
        # Page/context/browser closed between the is_closed check and the
        # evaluate call. Treat as "no element found".
        return None
    except asyncio.TimeoutError:
        # The single evaluate call exceeded the cap. Don't loop, don't queue
        # more round-trips — just bail and let the caller fall back.
        return None
    except Exception:
        # Best-effort: any other failure (evaluation error, transport error)
        # should not crash the step.
        return None

    # Re-check after the await in case the page closed during the evaluate.
    if getattr(page, "is_closed", lambda: False)():
        return None
    if not isinstance(js_payload, int) or js_payload < 0:
        return None

    # Return a real Locator. ``Locator.nth`` is a synchronous factory — no
    # Future is created here. The caller's subsequent action (click, type,
    # fill, etc.) is what creates the single awaited Future.
    try:
        return page.locator(INTERACTIVE_ELEMENTS_SELECTOR).nth(js_payload)
    except PlaywrightError:
        return None
    except Exception:
        return None


async def _resolve_interaction_mode(locator: Any) -> str:
    if locator is None:
        return "unsupported"
    # Use an explicit, short timeout on every Locator action so Playwright
    # fails fast on a degraded page. Without these, Playwright's default
    # 30s action timeout collides with the harness's ``step_timeout_seconds``
    # (also 30s by default), and the two cancellations race — leaving an
    # orphan Playwright Future whose exception surfaces at shutdown as
    # ``Future exception was never retrieved``.
    try:
        tag_name = await locator.evaluate("el => el.tagName.toLowerCase()", timeout=_DEFAULT_LOCATOR_ACTION_TIMEOUT_MS)
        input_type = await locator.evaluate("el => (el.type || '').toLowerCase()", timeout=_DEFAULT_LOCATOR_ACTION_TIMEOUT_MS)
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
            timeout=_DEFAULT_LOCATOR_ACTION_TIMEOUT_MS,
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
            """,
            timeout=_DEFAULT_LOCATOR_ACTION_TIMEOUT_MS,
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
            return invalid_value, "fuzz_select_invalid_option_rejected"
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
            """,
            timeout=_DEFAULT_LOCATOR_ACTION_TIMEOUT_MS,
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
    try:
        tag_name = await target_locator.evaluate("el => el.tagName.toLowerCase()", timeout=_DEFAULT_LOCATOR_ACTION_TIMEOUT_MS)
    except (PlaywrightError, Exception):
        return None, "target_element_unreadable"
    if tag_name == "form":
        try:
            bbox = await target_locator.bounding_box(timeout=_DEFAULT_LOCATOR_ACTION_TIMEOUT_MS)
        except (PlaywrightError, Exception):
            return None, "target_is_form_but_invisible"
        if bbox and bbox.get("width", 0) > 0 and bbox.get("height", 0) > 0:
            return target_locator, "target_is_form"
        return None, "target_is_form_but_invisible"
    try:
        form_handle = await target_locator.evaluate_handle("el => el.closest('form')", timeout=_DEFAULT_LOCATOR_ACTION_TIMEOUT_MS)
        is_null = await form_handle.json_value() is None
        if is_null:
            return None, "no_form_ancestor_found"
        target_bbox = await target_locator.bounding_box(timeout=_DEFAULT_LOCATOR_ACTION_TIMEOUT_MS)
        if not target_bbox:
            return None, "target_element_has_no_bounding_box"
        target_x = target_bbox.get("x", 0)
        target_y = target_bbox.get("y", 0)
    except (PlaywrightError, Exception):
        return None, "target_form_context_unreadable"
    finally:
        try:
            await form_handle.dispose()
        except Exception:
            pass

    if getattr(page, "is_closed", lambda: False)():
        return None, "page_closed_during_resolution"

    # Single round-trip: JS walks every form on the page and reports the
    # best match. The result is a 2-element array:
    #   [domIndex, wasInside]
    # where ``wasInside === true`` means the target point lay inside the
    # form's bounds (the same condition the old Python loop checked first)
    # and ``false`` means we fell back to nearest-by-distance.
    # The Python-side loop we used to have
    # (``forms_locator.nth(idx).bounding_box()`` in a for-loop) was the same
    # anti-pattern that caused the interactive-elements Future leak — it
    # issued N round-trips and spawned N orphan Playwright Futures on cancel.
    best_dom_index: Any = -1
    was_inside = False
    try:
        eval_result = await asyncio.wait_for(
            page.evaluate(
                """([targetX, targetY, tolerance]) => {
                    const forms = Array.from(document.querySelectorAll('form'));
                    let bestIndex = -1;
                    let bestDistSq = Infinity;
                    for (let i = 0; i < forms.length; i++) {
                        const el = forms[i];
                        const rect = el.getBoundingClientRect();
                        if (rect.width <= 0 || rect.height <= 0) continue;
                        if (
                            targetX >= rect.x - tolerance &&
                            targetX <= rect.x + rect.width + tolerance &&
                            targetY >= rect.y - tolerance &&
                            targetY <= rect.y + rect.height + tolerance
                        ) {
                            return [i, true];  // target is inside this form
                        }
                        const cx = rect.x + rect.width / 2;
                        const cy = rect.y + rect.height / 2;
                        const dx = cx - targetX;
                        const dy = cy - targetY;
                        const distSq = dx * dx + dy * dy;
                        if (distSq < bestDistSq) {
                            bestDistSq = distSq;
                            bestIndex = i;
                        }
                    }
                    return [bestIndex, false];
                }""",
                [target_x, target_y, 10.0],
            ),
            timeout=_BOUNDING_BOX_ITER_TIMEOUT_SECONDS,
        )
    except (PlaywrightError, Exception, asyncio.TimeoutError):
        return None, "form_search_failed"

    if (
        not isinstance(eval_result, (list, tuple))
        or len(eval_result) < 2
        or not isinstance(eval_result[0], int)
        or eval_result[0] < 0
    ):
        return None, "no_form_near_target"
    best_dom_index = eval_result[0]
    was_inside = bool(eval_result[1])

    try:
        locator = page.locator("form:visible").nth(best_dom_index)
    except (PlaywrightError, Exception):
        return None, "form_locator_construct_failed"
    return locator, ("target_inside_form_bounds" if was_inside else "nearest_form_by_bounding_box")
