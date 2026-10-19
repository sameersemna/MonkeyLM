"""Action dispatch - individual action implementations for browser automation."""

from __future__ import annotations

import asyncio
import json
import random
import re
from typing import Any, Dict, List, Optional

from playwright.async_api import Page

from monkeylm.config import _local_service_log
from monkeylm.browser.lifecycle import resilient_page_goto, wait_for_page_ready
from monkeylm.types import PageSnapshot

from .helpers import (
    _click_element_resilient,
    _extract_target_id,
    _fill_input_resilient,
    _fill_select_option,
    _is_typeable_control,
    _locator_for_target_id,
    _resolve_form_boundary,
    _resolve_interaction_mode,
)


async def _safe_count(locator: Any) -> int:
    """Return the current match count; Playwright's count() takes no timeout."""
    try:
        return await locator.count()
    except Exception:
        return 0


def _locator_candidates_for_control(page: Page, control: Any) -> List[Any]:
    candidates: List[Any] = []
    tag_name = (getattr(control, "tag_name", "") or "input").lower()
    id_attr = getattr(control, "id_attr", "") or ""
    name_attr = getattr(control, "name_attr", "") or ""
    placeholder = getattr(control, "placeholder", "") or ""
    aria_label = getattr(control, "aria_label", "") or ""
    if id_attr:
        candidates.append(page.locator(f"{tag_name}[id={json.dumps(id_attr)}]").first)
    if name_attr:
        candidates.append(page.locator(f"{tag_name}[name={json.dumps(name_attr)}]").first)
    if placeholder:
        candidates.append(page.get_by_placeholder(placeholder).first)
    if aria_label:
        candidates.append(page.get_by_label(aria_label).first)
    return candidates


async def _resolve_type_locator(
    page: Page,
    target: str,
    before_snapshot: PageSnapshot,
) -> tuple[Optional[Any], str, Optional[Any]]:
    """Resolve a type target, retrying with stable selectors before giving up."""
    parsed_id = _extract_target_id(target)
    control = next(
        (fc for fc in before_snapshot.form_controls if fc.control_id == parsed_id),
        None,
    )
    locator = await _locator_for_target_id(page, target)
    if locator is not None:
        try:
            await locator.wait_for(state="visible", timeout=3000)
            if await locator.is_enabled():
                mode = await _resolve_interaction_mode(locator)
                if mode != "unsupported":
                    return locator, "positional", control
        except Exception:
            pass

    candidates: List[Any] = []
    if control is not None and _is_typeable_control(control):
        candidates.extend(_locator_candidates_for_control(page, control))
    candidates.extend([
        page.locator("input[type='search']:visible").first,
        page.locator("input:visible, textarea:visible, [contenteditable='true']:visible").first,
    ])
    for candidate in candidates:
        try:
            await candidate.wait_for(state="visible", timeout=2000)
            if not await candidate.is_enabled():
                continue
            mode = await _resolve_interaction_mode(candidate)
            if mode != "unsupported":
                return candidate, "semantic-fallback", control
        except Exception:
            continue
    return None, "none", control


async def _action_scroll(page: Page) -> None:
    await page.evaluate("delta => window.scrollBy(0, delta)", random.choice([-500, 500]))


async def _action_back(page: Page, settings: Any) -> None:
    history_length = await page.evaluate("() => window.history.length")
    if page.url == "about:blank" or history_length <= 2:
        await resilient_page_goto(page, settings.target_url, wait_until="domcontentloaded", timeout=45000, phase="back-recovery")
        await wait_for_page_ready(page, "back-recovery")
    else:
        previous_page = await page.go_back(timeout=5000)
        if page.url == "about:blank" or previous_page is None:
            await resilient_page_goto(page, settings.target_url, wait_until="domcontentloaded", timeout=45000, phase="back-recovery-fallback")
            await wait_for_page_ready(page, "back-recovery")


async def _action_restart_target(page: Page, settings: Any) -> None:
    await resilient_page_goto(page, settings.target_url, wait_until="domcontentloaded", timeout=45000, phase="restart-target")
    await wait_for_page_ready(page, "restart-target")


async def _action_random_jump(page: Page) -> None:
    """Navigate to a random link, button, or scroll position to escape a stuck state.

    Tries in order:
    1. Click a random visible link (a[href]:visible)
    2. Click a random visible button
    3. Click any random clickable element
    4. Scroll to a random position on the page
    """
    # Strategy 1: visible links
    links = page.locator("a[href]:visible")
    link_count = await _safe_count(links)
    if link_count > 0:
        idx = random.randint(0, min(link_count - 1, 10))
        await links.nth(idx).click(timeout=3000)
        return

    # Strategy 2: visible buttons
    buttons = page.locator("button:visible, [role='button']:visible, input[type='button']:visible, input[type='submit']:visible")
    button_count = await _safe_count(buttons)
    if button_count > 0:
        idx = random.randint(0, min(button_count - 1, 5))
        await buttons.nth(idx).click(timeout=3000)
        return

    # Strategy 3: any clickable element (spans, divs with onclick, etc.)
    clickables = page.locator("[onclick]:visible, [data-action]:visible, [data-click]:visible, .clickable:visible")
    clickable_count = await _safe_count(clickables)
    if clickable_count > 0:
        idx = random.randint(0, min(clickable_count - 1, 3))
        await clickables.nth(idx).click(timeout=3000)
        return

    # Strategy 4: random scroll as last resort
    scroll_amount = random.choice([-800, -400, 400, 800])
    await page.evaluate("delta => window.scrollBy(0, delta)", scroll_amount)


async def _action_handle_modal(page: Page) -> None:
    close_btn = page.locator("button[aria-label='Close'], .close, [title='Close']").first
    if await _safe_count(close_btn) > 0:
        try:
            await close_btn.click(timeout=2000)
            return
        except Exception:
            pass
    cancel_btn = page.get_by_role("button", name=re.compile("cancel|close|no|dismiss", re.I)).first
    if await _safe_count(cancel_btn) > 0:
        try:
            await cancel_btn.click(timeout=2000)
            return
        except Exception:
            pass
    await page.keyboard.press("Escape")
    print("   -> Sent Escape key to close modal")


async def _action_submit_form(page: Page, settings: Any, target: str, input_payloads: List[Dict[str, Any]], action_strategy: str, step_num: int, before_snapshot: PageSnapshot, validation_prober: Any, log_entry: Dict[str, Any]) -> None:
    async def _run_submit_form() -> None:
        filled_payloads = []
        form_locator, form_reason = await _resolve_form_boundary(page, target)
        if form_locator is None:
            fallback_locator = await _locator_for_target_id(page, target)
            if fallback_locator is not None:
                try:
                    await fallback_locator.click(timeout=3000)
                    log_entry["status"] = "FALLBACK_CLICK"
                    log_entry["action"] = "click"
                    log_entry["error"] = None
                    print(f"   -> submit_form target has no form ancestor; falling back to click (target='{target}')")
                except Exception as click_exc:
                    log_entry["status"] = "SKIPPED_NOT_FORM"
                    log_entry["error"] = f"form_boundary_not_resolved: {form_reason}; fallback click failed: {click_exc}"
                    print(f"   ⚠️ Step {step_num}: submit_form skipped — {form_reason} (target='{target}')")
            else:
                log_entry["status"] = "SKIPPED_NOT_FORM"
                log_entry["error"] = f"form_boundary_not_resolved: {form_reason}"
                print(f"   ⚠️ Step {step_num}: submit_form skipped — {form_reason} (target='{target}')")
        else:
            for payload in input_payloads:
                payload_target = payload.get("target", "")
                payload_value = payload.get("value", "")
                payload_reason = payload.get("reason", "")
                locator = await _locator_for_target_id(page, payload_target)
                if locator:
                    mode = await _resolve_interaction_mode(locator)
                    control_options: List[str] = []
                    parsed_id = _extract_target_id(payload_target)
                    if parsed_id is not None:
                        control = next((fc for fc in before_snapshot.form_controls if fc.control_id == parsed_id), None)
                        if control is not None:
                            control_options = control.options
                    try:
                        if mode == "text_input":
                            filled = await _fill_input_resilient(page, locator, payload_value, target=payload_target, timeout_ms=2200)
                            if filled:
                                filled_payloads.append({"target": payload_target, "value": payload_value[:120], "reason": payload_reason})
                                if validation_prober and validation_prober.should_probe():
                                    try:
                                        control_type = await locator.evaluate("el => el.type || 'text'", timeout=2000)
                                        probe_findings = await validation_prober.probe_field(page, locator, control_type, step_num, f"submit_form:{payload_target}", payload_target)
                                        if probe_findings:
                                            print(f"   ⚠️ Validation probe found {len(probe_findings)} issue(s) on form field '{payload_target}'")
                                    except Exception:
                                        pass
                            else:
                                _local_service_log(f"Step {step_num}: unable to mutate {payload_target} with guarded fill")
                        elif mode == "select":
                            chosen, reason = await _fill_select_option(page, locator, payload_value, control_options, action_strategy)
                            filled_payloads.append({"target": payload_target, "value": chosen[:120], "reason": reason})
                        elif mode == "checkbox_radio":
                            await locator.check(timeout=2200)
                            filled_payloads.append({"target": payload_target, "value": "checked", "reason": payload_reason or "happy_checkbox_radio_check"})
                    except Exception as fill_exc:
                        _local_service_log(f"Step {step_num}: failed to mutate {payload_target}: {fill_exc}")
            log_entry["input_payloads"] = filled_payloads
            submit_btn = form_locator.locator("button[type='submit'], input[type='submit']").first
            submit_btn_count = await _safe_count(submit_btn)
            if submit_btn_count > 0:
                clicked = await _click_element_resilient(page, submit_btn, target=target, timeout_ms=2200)
                if not clicked:
                    _local_service_log(f"Step {step_num}: submit button for '{target}' could not be clicked reliably")
                    try:
                        await asyncio.wait_for(
                            form_locator.evaluate(
                                """
                                (form) => {
                                    if (!form) return false;
                                    form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
                                    if (typeof form.requestSubmit === 'function') {
                                        form.requestSubmit();
                                    }
                                    return true;
                                }
                                """,
                                timeout=2000,
                            ),
                            timeout=2.0,
                        )
                        log_entry["status"] = "PARTIAL_SUCCESS"
                        log_entry["error"] = "submit_fallback_dispatched"
                    except Exception as fallback_exc:
                        _local_service_log(f"Step {step_num}: submit fallback failed: {fallback_exc}")
                        log_entry["status"] = "PARTIAL_SUCCESS"
                        log_entry["error"] = f"submit_fallback_failed:{fallback_exc}"
            else:
                inputs = form_locator.locator("input:visible, textarea:visible")
                inputs_count = await _safe_count(inputs)
                if inputs_count > 0:
                    try:
                        await asyncio.wait_for(inputs.last.press("Enter"), timeout=2.0)
                        log_entry["status"] = "PARTIAL_SUCCESS"
                        log_entry["error"] = None
                    except Exception as enter_exc:
                        _local_service_log(f"Step {step_num}: Enter fallback failed: {enter_exc}")
                        log_entry["status"] = "PARTIAL_SUCCESS"
                        log_entry["error"] = f"enter_fallback_failed:{enter_exc}"
                else:
                    raise Exception("Form found but no inputs or submit button")

    try:
        await asyncio.wait_for(_run_submit_form(), timeout=4.0)
    except asyncio.TimeoutError:
        _local_service_log(f"Step {step_num}: submit_form exceeded internal deadline after 4.0s")
        log_entry["status"] = "PARTIAL_SUCCESS"
        log_entry["error"] = "submit_form_internal_timeout"
    except asyncio.CancelledError:
        _local_service_log(f"Step {step_num}: submit_form was cancelled before completion")
        log_entry["status"] = "PARTIAL_SUCCESS"
        log_entry["error"] = "submit_form_cancelled"


async def _action_press_key(page: Page, key: str) -> None:
    normalized_key = (key or "Enter").strip()
    if not normalized_key:
        normalized_key = "Enter"
    await page.keyboard.press(normalized_key)


async def _action_click(page: Page, target: str) -> None:
    locator = await _locator_for_target_id(page, target)
    if locator:
        clicked = await _click_element_resilient(page, locator, target=target, timeout_ms=2500)
        if not clicked:
            raise Exception(f"Click target '{target}' could not be completed reliably")
    else:
        raise Exception(f"Element '{target}' not found")


async def _action_type(page: Page, settings: Any, target: str, value: str, action_strategy: str, input_payloads: List[Dict[str, Any]], step_num: int, before_snapshot: PageSnapshot, fuzzer: Any, defects: Any, validation_prober: Any, log_entry: Dict[str, Any], action: str = "type") -> None:
    from monkeylm.core.monitor import sanitize_for_storage

    payload_value = value
    payload_reason = action_strategy
    for payload_entry in input_payloads:
        if payload_entry.get("target") == target:
            payload_value = payload_entry.get("value", value)
            payload_reason = payload_entry.get("reason", action_strategy)
            break

    def _record_recovery(reason: str) -> None:
        defects.add("console_findings", {
            "step": step_num,
            "type": "recovered-missing-target:type",
            "severity": "warning",
            "selector": sanitize_for_storage(str(target), max_len=256),
            "failure_reason": sanitize_for_storage(reason, max_len=512),
            "url": sanitize_for_storage(page.url, max_len=1024),
        })

    locator, resolved_source, control = await _resolve_type_locator(page, target, before_snapshot)
    log_entry["resolved_source"] = resolved_source
    if locator is None:
        log_entry["status"] = "RECOVERED_MISSING_TARGET"
        log_entry["error"] = f"type_target_unresolved:{target}"
        _record_recovery(log_entry["error"])
        print(f"   -> ↩️ No typeable target for '{target}'; falling back to scroll")
        await _action_scroll(page)
        return

    mode = await _resolve_interaction_mode(locator)
    if mode == "unsupported":
        log_entry["status"] = "RECOVERED_MISSING_TARGET"
        log_entry["error"] = f"type_target_not_typeable:{target}"
        _record_recovery(log_entry["error"])
        print(f"   -> ↩️ Target '{target}' is not typeable; falling back to scroll")
        await _action_scroll(page)
        return

    type_control_options: List[str] = control.options if control is not None else []
    payload = payload_value or fuzzer.next_payload()
    if mode == "select":
        chosen, reason = await _fill_select_option(page, locator, payload, type_control_options, action_strategy)
        log_entry["value"] = chosen[:120]
        log_entry["input_payloads"] = [{"target": target, "value": chosen[:120], "reason": reason}]
    elif mode == "checkbox_radio":
        await locator.check(timeout=2200)
        log_entry["value"] = "checked"
        log_entry["input_payloads"] = [{"target": target, "value": "checked", "reason": payload_reason or "happy_checkbox_radio_check"}]
    else:
        filled = await _fill_input_resilient(page, locator, payload, target=target, timeout_ms=2200)
        if filled:
            log_entry["value"] = payload[:120]
            log_entry["input_payloads"] = [{"target": target, "value": payload[:120], "reason": payload_reason or "fallback_fuzzer"}]
            if any(marker in payload.lower() for marker in ["<script", "onerror", " or 1=1", "drop table"]):
                defects.add("security_risks", {"step": step_num, "type": "fuzz-payload-injected", "target": sanitize_for_storage(str(target), max_len=256), "payload_preview": sanitize_for_storage(payload[:200], max_len=256), "url": sanitize_for_storage(page.url, max_len=1024)})
            if validation_prober and validation_prober.should_probe():
                try:
                    control_type = await locator.evaluate("el => el.type || (el.tagName === 'TEXTAREA' ? 'textarea' : '')", timeout=2000)
                    probe_findings = await validation_prober.probe_field(page, locator, control_type or "text", step_num, f"{action}:{target}", target)
                    if probe_findings:
                        print(f"   ⚠️ Validation probe found {len(probe_findings)} issue(s) on '{target}'")
                except Exception as probe_exc:
                    _local_service_log(f"Step {step_num}: validation probe failed for '{target}': {probe_exc}")
        else:
            log_entry["status"] = "RECOVERED_MISSING_TARGET"
            log_entry["error"] = f"input_fill_failed:{target}"
            _record_recovery(log_entry["error"])
            _local_service_log(f"Step {step_num}: unable to fill target '{target}' reliably; falling back to scroll")
            await _action_scroll(page)
