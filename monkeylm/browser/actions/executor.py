"""Action execution - orchestrator that dispatches to individual action handlers."""

from __future__ import annotations

import hashlib
import os
from typing import Any, Dict, List, Optional, Tuple

from playwright.async_api import Page

from monkeylm.config import (
    LAYOUT_SHIFT_THRESHOLD_PX,
    VISUAL_DIFF_THRESHOLD_RATIO,
    _local_service_log,
)
from monkeylm.core.monitor import sanitize_for_storage
from monkeylm.browser.snapshot import (
    get_page_state,
    compute_max_layout_shift,
    compare_screenshots_pixelmatch,
)
from monkeylm.browser.lifecycle import wait_for_page_ready
from monkeylm.types import PageSnapshot

from .helpers import _locator_for_target_id
from .actions import (
    _action_scroll,
    _action_back,
    _action_restart_target,
    _action_random_jump,
    _action_handle_modal,
    _action_submit_form,
    _action_click,
    _action_type,
)


async def execute_action(
    page: Page,
    settings: Any,
    action_plan: Dict[str, Any],
    step_num: int,
    fuzzer: Any,
    defects: Any,
    network_monitor: Any,
    perf_monitor: Any,
    log_sink: Optional[List[Dict[str, Any]]] = None,
    persistence_engine: Any = None,
    worker_id: int = 0,
    validation_prober: Any = None,
) -> Tuple[Optional[PageSnapshot], Dict[str, Any]]:
    from monkeylm.models import annotate_relevant_screenshot, _step_defects_summary

    action = action_plan.get("action", "scroll")
    target = action_plan.get("target", "")
    value = action_plan.get("value", "")
    action_strategy = action_plan.get("action_strategy", "")
    input_payloads = action_plan.get("input_payloads", [])
    if not isinstance(input_payloads, list):
        input_payloads = []

    before_snapshot = await get_page_state(page, step_num, phase="before", output_dir=settings.output_dir)
    perf_before = await perf_monitor.snapshot(page)

    safe_page_url = sanitize_for_storage(page.url, max_len=1024)
    log_entry = {
        "step": step_num,
        "action": action,
        "target": target,
        "value": value if action == "type" else None,
        "action_strategy": action_strategy,
        "input_payloads": input_payloads,
        "status": "SUCCESS",
        "error": None,
        "screenshot": None,
        "url": safe_page_url,
    }

    worker_label = f"worker-{worker_id:02d}"
    if persistence_engine is not None and target.strip() and action in {"click", "type", "submit_form", "handle_modal"}:
        from monkeylm.config import split_domain_and_route
        domain, route = split_domain_and_route(page.url)
        path_hash = _compute_action_path_hash(domain, route, action, target)
        if not await persistence_engine.claim_action_path_lock(path_hash, worker_label):
            print(f"🤖 Step {step_num}: action path '{action}/{target}' already claimed by another worker; skipping.")
            action = "scroll"
            target = ""
            log_entry["action"] = action
            log_entry["target"] = target
            log_entry["status"] = "SKIPPED_PATH"

    print(f"🤖 Step {step_num}: Executing {action} on '{target}'")

    try:
        if action == "scroll":
            await _action_scroll(page)
        elif action == "back":
            await _action_back(page, settings)
        elif action == "restart_target":
            await _action_restart_target(page, settings)
        elif action == "random_jump":
            await _action_random_jump(page)
        elif action == "handle_modal":
            await _action_handle_modal(page)
        elif action == "submit_form":
            await _action_submit_form(page, settings, target, input_payloads, action_strategy, step_num, before_snapshot, validation_prober, log_entry)
        elif action == "click":
            await _action_click(page, target)
        elif action == "type":
            await _action_type(page, settings, target, value, action_strategy, input_payloads, step_num, before_snapshot, fuzzer, defects, validation_prober, log_entry)

        try:
            await page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
        log_entry["url"] = sanitize_for_storage(page.url, max_len=1024)

        after_snapshot = await get_page_state(page, step_num, phase="after", output_dir=settings.output_dir)
        perf_after = await perf_monitor.snapshot(page)

        max_shift = compute_max_layout_shift(before_snapshot, after_snapshot)
        if before_snapshot.url == after_snapshot.url and max_shift > LAYOUT_SHIFT_THRESHOLD_PX:
            defects.add("layout_instability", {"step": step_num, "type": "layout-instability", "max_shift_px": max_shift, "url": sanitize_for_storage(after_snapshot.url, max_len=1024), "before_hash": before_snapshot.structure_hash, "after_hash": after_snapshot.structure_hash})

        if before_snapshot.url == after_snapshot.url and len(after_snapshot.elements) < max(1, int(len(before_snapshot.elements) * 0.5)):
            defects.add("layout_instability", {"step": step_num, "type": "dom-collapse", "before_elements": len(before_snapshot.elements), "after_elements": len(after_snapshot.elements), "url": sanitize_for_storage(after_snapshot.url, max_len=1024)})

        visual_diff = compare_screenshots_pixelmatch(before_snapshot.screenshot_path, after_snapshot.screenshot_path, step_num, output_dir=settings.output_dir)
        if visual_diff.get("diff_ratio", 0.0) > VISUAL_DIFF_THRESHOLD_RATIO and before_snapshot.url == after_snapshot.url:
            defects.add("visual_regressions", {"step": step_num, "type": "visual-diff", "diff_ratio": visual_diff.get("diff_ratio"), "diff_pixels": visual_diff.get("diff_pixels"), "engine": visual_diff.get("engine"), "diff_image": os.path.basename(visual_diff.get("diff_image", "")), "url": sanitize_for_storage(after_snapshot.url, max_len=1024)})

        perf_findings = await perf_monitor.detect_bottlenecks(perf_before, perf_after, step_num, action, sanitize_for_storage(after_snapshot.url, max_len=1024))
        log_entry["performance_findings"] = len(perf_findings)

        zombie = await network_monitor.detect_zombie_ui(page, step_num)
        if zombie:
            log_entry["zombie_ui"] = zombie["type"]

        log_entry["before_dom_hash"] = before_snapshot.dom_hash
        log_entry["after_dom_hash"] = after_snapshot.dom_hash
        log_entry["visual_diff_ratio"] = visual_diff.get("diff_ratio", 0.0)
        log_entry["screenshot"] = os.path.basename(after_snapshot.screenshot_path)

    except Exception as e:
        error_msg = str(e)
        log_entry["status"] = "FAILED"
        log_entry["error"] = error_msg
        print(f"💥 Error: {error_msg}")

        screenshot_name = f"error_step_{step_num}.png"
        try:
            await page.screenshot(path=os.path.join(settings.output_dir, screenshot_name))
            log_entry["screenshot"] = screenshot_name
        except Exception:
            pass

        action_remediation: Dict[str, str] = {
            "click": "Ensure the target element is visible, not obscured by overlays, and has a stable selector.",
            "type": "Verify the input field is enabled and accepts keyboard events.",
            "submit_form": "Ensure form wrappers properly enclose all input targets and submit buttons are visible.",
            "handle_modal": "Confirm the modal/dialog is present in the DOM and accessible.",
            "scroll": "Check that the page body has sufficient height to scroll.",
            "back": "Verify browser history has a previous entry.",
            "random_jump": "Ensure the target URL is reachable and returns a valid HTTP response.",
            "restart_target": "Confirm the target URL is valid and the server is responding.",
        }
        remediation_text = action_remediation.get(action, "Investigate the step failure context. Review the error message and annotated screenshot.")

        html_context = ""
        try:
            if target.strip():
                locator = await _locator_for_target_id(page, target)
                if locator:
                    html_context = await locator.evaluate("el => el.outerHTML", timeout=2000) or ""
        except Exception:
            pass

        defects.add("console_findings", {"step": step_num, "type": f"functional-failure:{action}", "severity": "error", "selector": sanitize_for_storage(target, max_len=256) if target.strip() else "(none)", "html_snippet": sanitize_for_storage(html_context[:500], max_len=512) if html_context else "", "failure_reason": sanitize_for_storage(error_msg[:300], max_len=512), "remediation_advice": remediation_text, "screenshot_path": screenshot_name if log_entry.get("screenshot") == screenshot_name else "", "url": sanitize_for_storage(page.url, max_len=1024)})

    if log_entry.get("screenshot") and settings.pdf_generate:
        status = log_entry.get("status", "")
        defect_reasons = _step_defects_summary(step_num, defects)
        if status in {"FAILED", "CRASH"} or defect_reasons:
            context_issue = f"status={status}"
            if defect_reasons:
                context_issue += "; " + "; ".join(defect_reasons)
            if log_entry.get("error"):
                context_issue += f"; error={log_entry['error'][:120]}"
            try:
                active_vision_model = settings.vision_model or settings.pdf_vision_model
                print(f"   └─ 📸 Annotating screenshot with {active_vision_model}...")
                original_path = os.path.join(settings.output_dir, log_entry["screenshot"])
                annotated_path = await annotate_relevant_screenshot(settings, original_path, context_issue, step_num=step_num)
                if annotated_path != original_path:
                    log_entry["screenshot"] = os.path.basename(annotated_path)
                    log_entry["screenshot_annotated"] = True
            except Exception as exc:
                _local_service_log(f"Annotation hook failed at step {step_num}: {exc}", settings.output_dir)

    if log_sink is None:
        from monkeylm.core import test_logs
        test_logs.append(log_entry)
    else:
        log_sink.append(log_entry)

    try:
        return await get_page_state(page, step_num, phase="final", output_dir=settings.output_dir), log_entry
    except Exception:
        return None, log_entry


def _compute_action_path_hash(domain: str, route: str, action: str, target: str) -> str:
    raw = f"{domain}|{route}|{action}|{target}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()
