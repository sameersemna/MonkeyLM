"""Worker execution - the main worker loop that runs steps in a browser context."""

from __future__ import annotations

import asyncio
import os
import json
from datetime import datetime
from typing import Any, Dict, List, Tuple

from monkeylm.config import (
    ACTION_COOLDOWN_SECONDS,
    Settings,
    _local_service_log,
    is_in_scope,
    SHUTDOWN_EVENT,
)
from monkeylm.core.monitor import sanitize_for_storage
from monkeylm.core.monitor import (
    DefectTracker,
    Fuzzer,
    A11yChecker,
    NetworkMonitor,
    PerformanceMonitor,
    BrowserAnomalySensor,
    StallDetector,
    ValidationProber,
    sanitize_for_storage,
)
from monkeylm.types import WorkerRunResult

from .helpers import build_worker_user_data_dir, with_retry_backoff
from monkeylm.browser.actions.interaction import recover_nonresponsive_state


async def _execute_step_with_timeout(coro: Any, *, timeout_seconds: float) -> Any:
    return await asyncio.wait_for(coro, timeout=timeout_seconds)


def classify_runtime_failure(exc: Exception | None) -> str | None:
    if exc is None:
        return None
    error_text = str(exc).lower()
    if "sandbox" in error_text or "no-sandbox" in error_text or "launch failed" in error_text:
        return "sandbox"
    if "timeout" in error_text or "timed out" in error_text:
        return "timeout"
    return None


def write_failure_debug_artifact(
    settings: Settings,
    *,
    step: int,
    failure_reason: str,
    failure_context: Dict[str, Any] | None,
    recent_history: List[Dict[str, Any]] | None = None,
    launch_info: Dict[str, Any] | None = None,
) -> str:
    artifact_name = f"failure_debug_step_{step}.json"
    artifact_path = os.path.join(settings.output_dir, artifact_name)
    payload = {
        "step": step,
        "failure_reason": failure_reason,
        "failure_context": failure_context or {},
        "recent_history": recent_history or [],
        "launch_info": launch_info or {},
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }
    with open(artifact_path, "w", encoding="utf-8") as artifact_file:
        json.dump(payload, artifact_file, indent=2)
    return artifact_path


async def _run_worker_with_limit(
    settings: Settings,
    worker_semaphore: asyncio.Semaphore,
    *,
    playwright_instance: Any,
    worker_id: int,
    allocated_steps: int,
    start_step: int,
    persistence_engine: Any,
) -> WorkerRunResult:
    async with worker_semaphore:
        return await run_worker(
            settings=settings,
            playwright_instance=playwright_instance,
            worker_id=worker_id,
            allocated_steps=allocated_steps,
            start_step=start_step,
            persistence_engine=persistence_engine,
        )


async def run_worker(
    *,
    settings: Settings,
    playwright_instance: Any,
    worker_id: int,
    allocated_steps: int,
    start_step: int,
    persistence_engine: Any,
) -> WorkerRunResult:
    from monkeylm.browser import (
        get_page_state,
        state_to_prompt,
        wait_for_page_ready,
        launch_context_with_fallback,
        handle_dialog,
        execute_action,
    )
    from monkeylm.models import decide_next_action, apply_state_aware_policy, _break_action_loop, run_application_discovery
    from monkeylm.memory import QdrantMemoryStore

    worker_label = f"worker-{worker_id:02d}"
    os.makedirs(settings.output_dir, exist_ok=True)
    worker_defects = DefectTracker()
    worker_fuzzer = Fuzzer()
    worker_network_monitor = NetworkMonitor(worker_defects)
    worker_a11y_checker = A11yChecker(worker_defects)
    worker_perf_monitor = PerformanceMonitor(worker_defects)
    worker_anomaly_sensor = BrowserAnomalySensor(worker_defects)
    # Waiting for a quarter of the whole run to elapse before declaring a freeze
    # burns a huge chunk of the step/time budget sitting on a state that was
    # already distinguishable much earlier. Cap the window so freezes surface
    # quickly regardless of how many total steps the run is configured for.
    worker_stall_detector = StallDetector(worker_defects, threshold=max(3, settings.stuck_state_threshold))
    worker_validation_prober = ValidationProber(worker_defects, probe_frequency=3)

    worker_memory = QdrantMemoryStore(settings)
    worker_logs: List[Dict[str, Any]] = []
    visited_states: Dict[str, int] = {}
    seen_click_targets: set = set()
    recent_model_plans: List[Tuple[str, str]] = []
    recent_state_history: List[Dict[str, Any]] = []
    completed_steps = 0
    failure_reason: str | None = None
    failure_artifact: str | None = None
    failure_context: Dict[str, Any] | None = None

    loop_detection_state: Dict[str, Any] = {"blacklist": {}, "loop_count": 0, "recent_actions": []}
    worker_data_dir = build_worker_user_data_dir(settings, worker_id)

    context = None
    launch_info: Dict[str, Any] = {"worker": worker_label, "mode": "not-started", "user_data_dir": worker_data_dir}

    try:
        context, launch_info = await launch_context_with_fallback(playwright_instance, settings=settings, user_data_dir=worker_data_dir, worker_label=worker_label)
        page = context.pages[0] if context.pages else await context.new_page()
        page.on("dialog", handle_dialog)

        def _console_listener(msg: Any) -> None:
            text = sanitize_for_storage(getattr(msg, "text", ""), max_len=2048)
            if "content security policy" in text.lower() or "csp" in text.lower():
                worker_defects.add("console_findings", {"step": -1, "type": "csp-warning", "message": text, "url": sanitize_for_storage(page.url, max_len=2048), "worker": worker_label})

        page.on("console", _console_listener)
        await worker_a11y_checker.inject_init_script(page)

        print(f"\U0001f680 Starting {worker_label} on {settings.target_url} with {allocated_steps} steps...")
        await with_retry_backoff(f"{worker_label} initial navigation", lambda: page.goto(settings.target_url, wait_until="domcontentloaded", timeout=45000), retries=settings.worker_navigation_retries, initial_delay_seconds=settings.retry_base_delay_seconds)
        await wait_for_page_ready(page, f"{worker_label}-initial-navigation")

        await worker_network_monitor.install(page)
        await worker_perf_monitor.install(page)
        await worker_anomaly_sensor.install(page)

        await with_retry_backoff(f"{worker_label} qdrant initialize", worker_memory.initialize, retries=settings.worker_qdrant_init_retries, initial_delay_seconds=settings.retry_base_delay_seconds)

        testing_strategy = None
        try:
            discovery_snapshot = await get_page_state(page, -1, phase="plan", output_dir=settings.output_dir)
            discovery_state = state_to_prompt(discovery_snapshot)
            testing_strategy = await run_application_discovery(settings, discovery_state)
        except Exception as exc:
            _local_service_log(f"{worker_label} Application Discovery failed: {exc}; proceeding without strategy.", settings.output_dir)

        for idx in range(allocated_steps):
            if SHUTDOWN_EVENT.is_set():
                print(f"\n\U0001f6d1 {worker_label} stopping early due to graceful shutdown request.")
                break

            step = start_step + idx
            print(f"\n--- {worker_label} step {step}/{settings.max_steps} ---")

            try:
                snapshot = await get_page_state(page, step, phase="plan", output_dir=settings.output_dir)
                # dom_hash (includes element text), not structure_hash (text
                # stripped) -- otherwise two screens with the same element
                # *shape* but different content (e.g. an onboarding carousel
                # that's always "two buttons," different labels each slide)
                # collide onto the same state_key and get treated as revisits
                # of an identical state.
                state_key = f"{snapshot.url}::{snapshot.dom_hash}"
                local_count = visited_states.get(state_key, 0) + 1
                redis_count = await persistence_engine.increment_visited_state(state_key)
                visited_states[state_key] = redis_count if redis_count is not None else local_count
                state = state_to_prompt(snapshot)
            except Exception as exc:
                print(f"   -> \U0001f6a8 {worker_label} failed to get state: {exc}. Skipping step.")
                continue

            plan = await decide_next_action(settings, state, memory_store=worker_memory, snapshot=snapshot, testing_strategy=testing_strategy)
            retrieval_telemetry = worker_memory.consume_last_search_telemetry()

            plan_signature = (plan.get("action", "scroll"), plan.get("target", ""))
            loop_break_applied = False
            if len(recent_model_plans) >= 3 and all(p == plan_signature for p in recent_model_plans[-3:]):
                print(f"\U0001f504 Loop detected for {worker_label}; forcing path exploration variance.")
                loop_detection_state["recent_actions"] = []
                print(f"   \u251c\u2500 \u26d4 Cleared short-term action history for {worker_label}")
                plan = _break_action_loop(plan, snapshot, worker_label, step, loop_state=loop_detection_state, blacklist_expiry_steps=settings.max_steps // 3)
                loop_break_applied = True
            recent_model_plans.append(plan_signature)
            recent_model_plans = recent_model_plans[-3:]

            plan = apply_state_aware_policy(settings, plan, snapshot, visited_states, seen_click_targets, loop_break_applied=loop_break_applied)
            if plan.get("action") == "click" and plan.get("target"):
                seen_click_targets.add(plan.get("target"))

            recent_state_history.append({
                "step": step,
                "action": str(plan.get("action", "")),
                "target": str(plan.get("target", "")),
                "url": sanitize_for_storage(snapshot.url, max_len=2048),
                "dom_hash": snapshot.dom_hash,
            })
            if len(recent_state_history) > 8:
                recent_state_history = recent_state_history[-8:]

            worker_anomaly_sensor.set_action_context(step, f"{plan.get('action', '?')}:{plan.get('target', '')}")

            try:
                _, log_entry = await _execute_step_with_timeout(
                    execute_action(
                        page,
                        settings,
                        plan,
                        step,
                        worker_fuzzer,
                        worker_defects,
                        worker_network_monitor,
                        worker_perf_monitor,
                        log_sink=worker_logs,
                        persistence_engine=persistence_engine,
                        worker_id=worker_id,
                        validation_prober=worker_validation_prober,
                    ),
                    timeout_seconds=settings.step_timeout_seconds,
                )
            except asyncio.TimeoutError as timeout_exc:
                failure_reason = "step_timeout"
                failure_context = {
                    "step": step,
                    "action": plan.get("action", ""),
                    "target": plan.get("target", ""),
                    "timeout_seconds": settings.step_timeout_seconds,
                    "url": sanitize_for_storage(page.url, max_len=2048),
                }
                print(f"⏰ {worker_label} step {step} timed out after {settings.step_timeout_seconds}s; stopping run.")
                await page.screenshot(path=os.path.join(settings.output_dir, f"timeout_step_{step}.png"))
                failure_artifact = os.path.basename(write_failure_debug_artifact(
                    settings,
                    step=step,
                    failure_reason="step_timeout",
                    failure_context=failure_context,
                    recent_history=recent_state_history,
                    launch_info=launch_info,
                ))
                try:
                    recovery = await recover_nonresponsive_state(page, settings, step=step, action=plan.get("action", ""), target=plan.get("target", ""), error="step_timeout")
                    failure_context["recovery"] = recovery
                except Exception:
                    pass
                worker_defects.add("console_findings", {
                    "step": step,
                    "type": "step-timeout",
                    "failure_reason": "step_timeout",
                    "url": sanitize_for_storage(page.url, max_len=2048),
                    "selector": sanitize_for_storage(plan.get("target", "") or "(none)", max_len=256),
                    "remediation_advice": "Increase the step timeout or investigate a frozen or blocked target page.",
                    "recovery": sanitize_for_storage(json.dumps(failure_context.get("recovery", {}), sort_keys=True)[:2000], max_len=2000),
                })
                break
            except Exception as exc:
                runtime_failure = classify_runtime_failure(exc)
                if runtime_failure == "sandbox":
                    failure_reason = "sandbox"
                    failure_context = {"step": step, "error": str(exc), "url": sanitize_for_storage(page.url, max_len=2048)}
                    failure_artifact = os.path.basename(write_failure_debug_artifact(
                        settings,
                        step=step,
                        failure_reason="sandbox",
                        failure_context=failure_context,
                        recent_history=recent_state_history,
                        launch_info=launch_info,
                    ))
                    print(f"🧱 {worker_label} sandbox failure at step {step}: {exc}")
                    break
                raise

            log_entry["worker_id"] = worker_id
            log_entry["memory_retrieval"] = retrieval_telemetry

            if step % 5 == 0:
                violations = await worker_a11y_checker.scan(page, step)
                if violations:
                    print(f"   -> \u267f {worker_label} a11y findings at step {step}: {len(violations)}")

            await wait_for_page_ready(page, f"{worker_label}-post-step-{step}")

            current_url = page.url
            if not is_in_scope(current_url, settings.target_url):
                worker_defects.add("boundary_drift", {"step": step, "type": "Boundary Drift", "current_url": current_url, "target_url": settings.target_url, "worker": worker_label})
                await with_retry_backoff(f"{worker_label} boundary recovery navigation", lambda: page.goto(settings.target_url, wait_until="domcontentloaded", timeout=45000), retries=settings.worker_boundary_recovery_retries, initial_delay_seconds=settings.retry_base_delay_seconds)
                await wait_for_page_ready(page, f"{worker_label}-boundary-recovery-{step}")

            try:
                baseline_snapshot = await get_page_state(page, step, phase="baseline", output_dir=settings.output_dir)
                await persistence_engine.analyze_route_regression(page, baseline_snapshot, step)
            except Exception as exc:
                _local_service_log(f"{worker_label} post-step baseline analysis failed at step {step}: {exc}", settings.output_dir)

            regression_hits = [finding for finding in worker_defects.regression_findings if int(finding.get("step", -1)) == step]
            outcome_bits = [f"status={log_entry.get('status', 'UNKNOWN')}"]
            if log_entry.get("error"):
                outcome_bits.append(f"error={log_entry['error'][:180]}")
            if regression_hits:
                outcome_bits.append(f"regressions={len(regression_hits)} tag=Vibe-Code-Regression-Missing-Component")

            await worker_memory.add_step_memory(page_state=state, action=str(plan.get("action", "scroll")), outcome="; ".join(outcome_bits), url=page.url, step=step)
            log_entry["memory_write"] = worker_memory.consume_last_write_telemetry()

            if log_entry.get("value"):
                raw_probe = log_entry["value"]
                payload_probe = sanitize_for_storage(str(raw_probe), max_len=200)
                try:
                    body_html = await page.content()
                    has_xss_patterns = "&lt;" in payload_probe or "javascript:" in payload_probe.lower()
                    has_sqli_patterns = "'" in raw_probe and any(kw in raw_probe.upper() for kw in ("OR 1=1", "UNION SELECT", "DROP TABLE", "' OR '"))
                    if raw_probe in body_html and (has_xss_patterns or has_sqli_patterns):
                        probe_type = "reflected-xss" if has_xss_patterns else "reflected-sql-injection"
                        worker_defects.add("security_risks", {"step": step, "type": f"fuzz-payload-{probe_type}", "payload_preview": payload_probe, "url": sanitize_for_storage(page.url, max_len=2048), "worker": worker_label})
                except Exception:
                    pass

            try:
                post_snapshot = await get_page_state(page, step, phase="stall", output_dir=settings.output_dir)
                if post_snapshot.is_empty_capture:
                    # Don't let an empty capture (no real page content) masquerade as
                    # an "unchanged state" freeze \u2014 it's a capture problem, not a UX
                    # flow issue. capture_diagnostics already got a finding for this
                    # step via execute_action; skip feeding the stall detector so it
                    # doesn't declare (and keep re-declaring) a bogus freeze.
                    pass
                else:
                    worker_stall_detector.record_state(step, post_snapshot.url, post_snapshot.dom_hash, str(plan.get("action", "")))
                    stall_finding = worker_stall_detector.check_for_stall(step, plan.get("action", "scroll"))
                    if stall_finding:
                        failure_reason = stall_finding.get("reason", "stuck_state_detected")
                        failure_context = {
                            "step": step,
                            "action": plan.get("action", ""),
                            "target": plan.get("target", ""),
                            "url": sanitize_for_storage(post_snapshot.url, max_len=2048),
                            "dom_hash": post_snapshot.dom_hash,
                            "threshold": settings.stuck_state_threshold,
                        }
                        failure_artifact = os.path.basename(write_failure_debug_artifact(
                            settings,
                            step=step,
                            failure_reason=failure_reason,
                            failure_context={
                                **failure_context,
                                "stall_window_steps": [entry.get("step") for entry in stall_finding.get("stall_window_steps", [])],
                                "url": sanitize_for_storage(post_snapshot.url, max_len=2048),
                                "dom_hash": post_snapshot.dom_hash,
                            },
                            recent_history=recent_state_history,
                            launch_info=launch_info,
                        ))
                        print(f"⚠️ {worker_label} STALL DETECTED at step {step}: page state unchanged across multiple steps")
                        break
            except Exception as stall_exc:
                _local_service_log(f"{worker_label} stall detection failed: {stall_exc}", settings.output_dir)

            try:
                flushed = await worker_anomaly_sensor.flush_anomalies()
                if flushed:
                    print(f"\u26a0\ufe0f {worker_label} {len(flushed)} context anomaly(s) at step {step}")
            except Exception as anomaly_exc:
                _local_service_log(f"{worker_label} anomaly flush failed: {anomaly_exc}", settings.output_dir)

            completed_steps += 1
            await asyncio.sleep(ACTION_COOLDOWN_SECONDS)
    finally:
        await worker_memory.close()
        if context is not None:
            await context.close()

    return WorkerRunResult(
        worker_id=worker_id,
        allocated_steps=allocated_steps,
        completed_steps=completed_steps,
        logs=worker_logs,
        defects=worker_defects,
        network_injections=list(worker_network_monitor.injected_events),
        launch_info=launch_info,
        failure_reason=failure_reason,
        failure_artifact=failure_artifact,
        failure_context=failure_context,
    )
