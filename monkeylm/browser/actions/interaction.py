"""Generic interaction-state safeguards for browser actions."""

from __future__ import annotations

from typing import Any, Dict, Optional

from monkeylm.core.monitor.defects import sanitize_for_storage

# Locator action timeout (ms) for ``detect_click_interception``. Must stay
# well below the harness ``step_timeout_seconds`` (default 30s) so that a
# degraded page cannot cause this detector's call to race with the outer
# step timeout — that race is what produced the
# ``Future exception was never retrieved`` warning at shutdown.
_DETECT_INTERCEPTION_TIMEOUT_MS = 2000


async def detect_click_interception(page: Any, locator: Any, target: Any) -> Dict[str, Any]:
    """Detect whether a target element is likely being intercepted by another UI layer.

    The detector uses the browser's hit-test API and element metadata to infer when
    the clicked element is hidden behind another visible layer or when the target is
    not actually interactive. It avoids app-specific assumptions and is intended to
    be used as a generic guard before attempting a click.
    """

    base_result: Dict[str, Any] = {
        "is_blocked": False,
        "reason": "not_blocked",
        "target": sanitize_for_storage(str(target), max_len=256),
        "top_element": "",
        "top_text": "",
        "target_element": "",
        "target_text": "",
    }

    if locator is None:
        return base_result

    try:
        bbox = await locator.bounding_box(timeout=_DETECT_INTERCEPTION_TIMEOUT_MS)
    except Exception as exc:  # pragma: no cover - defensive fallback
        base_result["reason"] = f"bounding_box_unavailable:{type(exc).__name__}"
        return base_result

    if not bbox or bbox.get("width", 0) <= 0 or bbox.get("height", 0) <= 0:
        base_result["reason"] = "target_has_no_visible_bounds"
        return base_result

    center_x = bbox.get("x", 0) + bbox.get("width", 0) / 2.0
    center_y = bbox.get("y", 0) + bbox.get("height", 0) / 2.0

    payload: Optional[Dict[str, Any]] = None
    try:
        payload = await locator.evaluate(
            """
            (el) => {
                const rect = el.getBoundingClientRect();
                const centerX = rect.left + rect.width / 2;
                const centerY = rect.top + rect.height / 2;
                const topEl = document.elementFromPoint(centerX, centerY);
                const targetStyle = getComputedStyle(el);
                const topStyle = topEl ? getComputedStyle(topEl) : null;
                const isBlocked = !!topEl && topEl !== el && !el.contains(topEl) && !topEl.contains(el) && targetStyle.pointerEvents !== 'none' && (!topStyle || topStyle.pointerEvents !== 'none');
                return {
                    is_blocked: isBlocked,
                    reason: isBlocked ? 'overlay_blocked' : 'not_blocked',
                    target_element: (el.tagName || '').toLowerCase(),
                    target_text: `${(el.innerText || '').trim()}`.slice(0, 140),
                    top_element: topEl ? (topEl.tagName || '').toLowerCase() : '',
                    top_text: topEl ? `${(topEl.innerText || '').trim()}`.slice(0, 140) : '',
                };
            }
            """,
            timeout=_DETECT_INTERCEPTION_TIMEOUT_MS,
        )
    except Exception:
        payload = None

    if payload is None and hasattr(page, "evaluate"):
        try:
            payload = await page.evaluate(
                """
                (args) => {
                    const topEl = document.elementFromPoint(args.x, args.y);
                    const targetStyle = topEl ? getComputedStyle(topEl) : null;
                    const isBlocked = !!topEl && targetStyle && targetStyle.pointerEvents !== 'none' && topEl.tagName !== 'BODY';
                    return {
                        is_blocked: isBlocked,
                        reason: isBlocked ? 'overlay_blocked' : 'not_blocked',
                        target_element: 'unknown',
                        target_text: '',
                        top_element: topEl ? (topEl.tagName || '').toLowerCase() : '',
                        top_text: topEl ? `${(topEl.innerText || '').trim()}`.slice(0, 140) : '',
                    };
                }
                """,
                {"x": center_x, "y": center_y},
            )
        except Exception:
            payload = None

    if isinstance(payload, dict):
        normalized = {
            "is_blocked": bool(payload.get("is_blocked", False)),
            "reason": str(payload.get("reason") or "not_blocked"),
            "target": sanitize_for_storage(str(target), max_len=256),
            "top_element": sanitize_for_storage(str(payload.get("top_element") or ""), max_len=128),
            "top_text": sanitize_for_storage(str(payload.get("top_text") or ""), max_len=256),
            "target_element": sanitize_for_storage(str(payload.get("target_element") or ""), max_len=128),
            "target_text": sanitize_for_storage(str(payload.get("target_text") or ""), max_len=256),
        }
        return normalized

    return base_result


async def collect_failure_context(
    page: Any,
    *,
    step: int,
    action: str,
    target: str,
    error: str,
    dom_limit: int = 2000,
    runtime_errors: Optional[list[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Collect an actionable context bundle for diagnosis when a step fails."""

    dom_context = ""
    try:
        dom_content = await page.content()
        dom_context = sanitize_for_storage(str(dom_content), max_len=max(256, dom_limit))
    except Exception:
        dom_context = ""

    error_text = str(error or "unknown").lower()
    if "sandbox" in error_text or "no-sandbox" in error_text or "launch failed" in error_text:
        failure_category = "sandbox_failure"
    elif "timeout" in error_text or "timed out" in error_text or "step_timeout" in error_text:
        failure_category = "step_timeout"
    elif "overlay" in error_text or "blocked" in error_text or "intercept" in error_text or "modal" in error_text or "frozen" in error_text or "unresponsive" in error_text:
        failure_category = "page_blocked"
    elif "crash" in error_text or "crashed" in error_text:
        failure_category = "app_failure"
    else:
        failure_category = "app_failure"

    runtime_error_entries: list[Dict[str, Any]] = []
    if runtime_errors:
        for item in runtime_errors:
            if isinstance(item, dict):
                runtime_error_entries.append({
                    "type": sanitize_for_storage(str(item.get("type") or "runtime_error"), max_len=128),
                    "message": sanitize_for_storage(str(item.get("message") or item.get("error") or ""), max_len=512),
                })

    return {
        "step": step,
        "last_action": sanitize_for_storage(str(action or "unknown"), max_len=128),
        "last_target": sanitize_for_storage(str(target or ""), max_len=256),
        "error": sanitize_for_storage(str(error or "unknown"), max_len=512),
        "url": sanitize_for_storage(getattr(page, "url", "") or "", max_len=2048),
        "failure_category": failure_category,
        "failure_source": "harness" if failure_category in {"sandbox_failure", "step_timeout", "page_blocked"} else "app",
        "compact_dom_snapshot": dom_context,
        "dom_context": dom_context,
        "runtime_errors": runtime_error_entries,
    }


async def recover_nonresponsive_state(page: Any, settings: Any, *, step: int, action: str, target: str, error: str, reason: str = "unresponsive_page") -> Dict[str, Any]:
    """Attempt a generic recovery sequence when the page appears frozen or blocked."""

    recovery: Dict[str, Any] = {
        "attempted": True,
        "step": step,
        "last_action": sanitize_for_storage(str(action or "unknown"), max_len=128),
        "last_target": sanitize_for_storage(str(target or ""), max_len=256),
        "reason": sanitize_for_storage(str(reason), max_len=256),
        "success": False,
        "strategy": "reload",
        "details": [],
    }

    current_url = getattr(page, "url", "") or getattr(settings, "target_url", "") or ""

    for attempt in range(2):
        try:
            if current_url:
                await page.reload(timeout=15000)
            else:
                await page.goto(str(getattr(settings, "target_url", "")), wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_load_state("networkidle", timeout=5000)
            recovery["success"] = True
            recovery["details"].append(f"reload_attempt_{attempt + 1}:ok")
            return recovery
        except Exception as exc:
            recovery["details"].append(f"reload_attempt_{attempt + 1}:{type(exc).__name__}:{sanitize_for_storage(str(exc), max_len=256)}")
            try:
                await page.goto(current_url or str(getattr(settings, "target_url", "")), wait_until="domcontentloaded", timeout=20000)
                await page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception as fallback_exc:
                recovery["details"].append(f"fallback_attempt_{attempt + 1}:{type(fallback_exc).__name__}:{sanitize_for_storage(str(fallback_exc), max_len=256)}")

    recovery["details"].append(f"error:{sanitize_for_storage(str(error or ''), max_len=512)}")
    return recovery
