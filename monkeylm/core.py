"""Monitor classes, worker coordination, and main entry point."""

from __future__ import annotations

import asyncio
import json
import os
import random
import signal
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from playwright.async_api import Page, Route, async_playwright

from monkeylm.config import (
    ACTION_COOLDOWN_SECONDS,
    AXE_CDN_URL,
    Faker,
    Settings,
    WorkerRunResult,
    _local_service_log,
    _normalize_defect,
    GRACEFUL_SHUTDOWN_REQUESTED,
    is_in_scope,
    SHUTDOWN_EVENT,
)


# ── Defect tracker ────────────────────────────────────────────────────────────


class DefectTracker:
    """Centralized defect tracker to keep reporting deterministic and CI-friendly."""

    def __init__(self) -> None:
        self.layout_instability: List[Dict[str, Any]] = []
        self.visual_regressions: List[Dict[str, Any]] = []
        self.regression_findings: List[Dict[str, Any]] = []
        self.security_risks: List[Dict[str, Any]] = []
        self.accessibility_violations: List[Dict[str, Any]] = []
        self.performance_bottlenecks: List[Dict[str, Any]] = []
        self.console_findings: List[Dict[str, Any]] = []
        self.race_findings: List[Dict[str, Any]] = []
        self.boundary_drift: List[Dict[str, Any]] = []

    def add(self, category: str, payload: Dict[str, Any]) -> None:
        collection = getattr(self, category, None)
        if collection is not None:
            collection.append(_normalize_defect(payload))

    def merge_from(self, other: "DefectTracker") -> None:
        categories = [
            "layout_instability",
            "visual_regressions",
            "regression_findings",
            "security_risks",
            "accessibility_violations",
            "performance_bottlenecks",
            "console_findings",
            "race_findings",
            "boundary_drift",
        ]
        for category in categories:
            own_collection = getattr(self, category)
            own_collection.extend(getattr(other, category, []))


# ── Fuzzer ────────────────────────────────────────────────────────────────────


class Fuzzer:
    """Produces mixed benign and malicious payloads for resilience and security testing."""

    def __init__(self) -> None:
        self.fake = Faker() if Faker else None
        self.owasp_payloads: List[str] = [
            "' OR 1=1 --",
            '" OR "1"="1" --',
            "<script>alert('xss')</script>",
            "<img src=x onerror=alert(1)>",
            "javascript:alert(1)",
            "../../../../etc/passwd",
            "${7*7}",
            "{{7*7}}",
            "%0d%0aSet-Cookie:evil=true",
            "'; DROP TABLE users; --",
            "A" * 12000,
        ]

    def next_payload(self) -> str:
        candidates = list(self.owasp_payloads)
        if self.fake:
            candidates.extend(
                [
                    self.fake.email(),
                    self.fake.user_name(),
                    self.fake.name(),
                    self.fake.uri(),
                    self.fake.pystr(min_chars=20, max_chars=100),
                ]
            )
        return random.choice(candidates)


# ── Accessibility checker ────────────────────────────────────────────────────


class A11yChecker:
    """Injects axe-core and executes periodic scans to surface high-severity a11y defects."""

    def __init__(self, defects: DefectTracker) -> None:
        self.injected_pages: set[int] = set()
        self.defects = defects

    async def ensure_injected(self, page: Page) -> None:
        page_id = id(page)
        if page_id in self.injected_pages:
            return
        try:
            await page.add_script_tag(url=AXE_CDN_URL)
            self.injected_pages.add(page_id)
        except Exception as exc:
            self.defects.add(
                "console_findings",
                {
                    "step": -1,
                    "type": "axe-injection-warning",
                    "severity": "warning",
                    "message": f"Unable to inject axe-core (likely CSP/network): {exc}",
                    "url": page.url,
                },
            )

    async def scan(self, page: Page, step_num: int) -> List[Dict[str, Any]]:
        await self.ensure_injected(page)
        try:
            results = await page.evaluate(
                """async () => {
                    try {
                        if (!window.axe) return { error: 'axe_missing', violations: [] };
                        const result = await window.axe.run(document, {
                            resultTypes: ['violations'],
                            runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'best-practice'] }
                        });
                        return result;
                    } catch (err) {
                        return {
                            error: 'axe_runtime_error',
                            errorMessage: String(err || 'unknown axe error'),
                            violations: []
                        };
                    }
                }"""
            )
        except Exception as exc:
            self.defects.add(
                "console_findings",
                {
                    "step": step_num,
                    "type": "axe-runtime-warning",
                    "severity": "warning",
                    "message": str(exc),
                    "url": page.url,
                },
            )
            return []

        if results.get("error"):
            self.defects.add(
                "console_findings",
                {
                    "step": step_num,
                    "type": "axe-runtime-warning",
                    "severity": "warning",
                    "message": results.get("errorMessage", results.get("error")),
                    "url": page.url,
                },
            )
            return []

        filtered: List[Dict[str, Any]] = []
        for violation in results.get("violations", []):
            impact = (violation.get("impact") or "").lower()
            if impact not in {"critical", "serious"}:
                continue
            rule_id = violation.get("id")
            description = violation.get("description")
            help_text = violation.get("help")
            for node in violation.get("nodes", []):
                targets = node.get("target", [])
                selector = ", ".join(targets) if targets else "(unknown)"
                finding: Dict[str, Any] = {
                    "step": step_num,
                    "severity": impact,
                    "id": rule_id,
                    "description": description,
                    "help": help_text,
                    "selector": selector,
                    "html_snippet": node.get("html", ""),
                    "remediation": node.get("failureSummary", ""),
                    "url": page.url,
                }
                filtered.append(finding)
        for finding in filtered:
            self.defects.add("accessibility_violations", finding)
        return filtered


# ── Network monitor ───────────────────────────────────────────────────────────


class NetworkMonitor:
    """Intercepts API calls to inject realistic latency/failure and monitors stale loading UI."""

    def __init__(self, defects: DefectTracker) -> None:
        self.defects = defects
        self.injected_events: List[Dict[str, Any]] = []
        self.route_enabled = False

    async def install(self, page: Page) -> None:
        if self.route_enabled:
            return

        async def _route_handler(route: Route) -> None:
            request = route.request
            resource_type = request.resource_type
            url = request.url
            if resource_type in {"xhr", "fetch"} or "/api/" in url:
                roll = random.random()
                if roll < 0.15:
                    delay_seconds = random.randint(2, 5)
                    self.injected_events.append(
                        {
                            "type": "delay",
                            "url": url,
                            "delay_seconds": delay_seconds,
                            "timestamp": time.time(),
                        }
                    )
                    await asyncio.sleep(delay_seconds)
                    await route.continue_()
                    return
                if roll < 0.25:
                    status_code = random.choice([500, 503])
                    self.injected_events.append(
                        {
                            "type": "http_error",
                            "url": url,
                            "status": status_code,
                            "timestamp": time.time(),
                        }
                    )
                    await route.fulfill(
                        status=status_code,
                        content_type="application/json",
                        body=json.dumps({"error": "injected fault", "status": status_code}),
                    )
                    return
            await route.continue_()

        await page.route("**/*", _route_handler)
        self.route_enabled = True

    async def detect_zombie_ui(self, page: Page, step_num: int) -> Optional[Dict[str, Any]]:
        try:
            before = await page.evaluate(
                """() => {
                    const spinnerSel = '[aria-busy="true"], .spinner, .loading, [data-testid*="spinner" i]';
                    const spinnerCount = document.querySelectorAll(spinnerSel).length;
                    const disabledCount = document.querySelectorAll('button:disabled, input:disabled, select:disabled, textarea:disabled').length;
                    return { spinnerCount, disabledCount };
                }"""
            )
            await asyncio.sleep(3.0)
            after = await page.evaluate(
                """() => {
                    const spinnerSel = '[aria-busy="true"], .spinner, .loading, [data-testid*="spinner" i]';
                    const spinnerCount = document.querySelectorAll(spinnerSel).length;
                    const disabledCount = document.querySelectorAll('button:disabled, input:disabled, select:disabled, textarea:disabled').length;
                    return { spinnerCount, disabledCount };
                }"""
            )
        except Exception:
            return None

        if before.get("spinnerCount", 0) > 0 and after.get("spinnerCount", 0) >= before.get("spinnerCount", 0):
            finding = {
                "step": step_num,
                "type": "zombie-ui",
                "description": "Potential zombie UI: spinners persisted for >3s after action.",
                "before": before,
                "after": after,
                "url": page.url,
            }
            self.defects.add("race_findings", finding)
            return finding

        if before.get("disabledCount", 0) > 0 and after.get("disabledCount", 0) >= before.get("disabledCount", 0):
            finding = {
                "step": step_num,
                "type": "disabled-stuck",
                "description": "Potential zombie UI: disabled controls persisted for >3s after action.",
                "before": before,
                "after": after,
                "url": page.url,
            }
            self.defects.add("race_findings", finding)
            return finding
        return None


# ── Performance monitor ───────────────────────────────────────────────────────


class PerformanceMonitor:
    """Collects long-task and memory telemetry through CDP and in-page observers."""

    def __init__(self, defects: DefectTracker) -> None:
        self.defects = defects
        self.cdp: Any = None

    async def install(self, page: Page) -> None:
        if self.cdp is not None:
            return
        self.cdp = await page.context.new_cdp_session(page)
        await self.cdp.send("Performance.enable")
        try:
            await self.cdp.send("Page.enable")
        except Exception:
            pass
        await page.add_init_script(
            """
            () => {
                window.__deepLongTasks = [];
                try {
                    const obs = new PerformanceObserver(list => {
                        for (const entry of list.getEntries()) {
                            window.__deepLongTasks.push({
                                startTime: entry.startTime,
                                duration: entry.duration,
                                name: entry.name || 'longtask'
                            });
                        }
                    });
                    obs.observe({ type: 'longtask', buffered: true });
                } catch (e) {}
            }
            """
        )

    async def snapshot(self, page: Page) -> Dict[str, Any]:
        metrics = await self.cdp.send("Performance.getMetrics") if self.cdp else {"metrics": []}
        navigation: Dict[str, Any] = {"entries": []}
        if self.cdp:
            try:
                history = await self.cdp.send("Page.getNavigationHistory")
                entries = history.get("entries", [])
                navigation = {
                    "current_index": history.get("currentIndex", -1),
                    "entries": [
                        {
                            "id": item.get("id"),
                            "url": item.get("url"),
                            "title": item.get("title"),
                            "transition_type": item.get("transitionType"),
                        }
                        for item in entries
                    ],
                }
            except Exception as exc:
                navigation = {"entries": [], "error": str(exc)}
        memory = await page.evaluate(
            """() => {
                const mem = performance.memory || {};
                return {
                    usedJSHeapSize: mem.usedJSHeapSize || 0,
                    totalJSHeapSize: mem.totalJSHeapSize || 0,
                    jsHeapSizeLimit: mem.jsHeapSizeLimit || 0,
                };
            }"""
        )
        long_tasks = await page.evaluate("() => window.__deepLongTasks || []")
        fps = await page.evaluate(
            """() => new Promise(resolve => {
                const start = performance.now();
                let frames = 0;
                function tick(now) {
                    frames += 1;
                    if (now - start >= 600) {
                        const fps = frames / ((now - start) / 1000);
                        resolve({ fps });
                        return;
                    }
                    requestAnimationFrame(tick);
                }
                requestAnimationFrame(tick);
            })"""
        )
        return {
            "metrics": metrics.get("metrics", []),
            "navigation": navigation,
            "memory": memory,
            "long_tasks": long_tasks,
            "fps": fps,
        }

    async def detect_bottlenecks(
        self,
        before: Dict[str, Any],
        after: Dict[str, Any],
        step_num: int,
        action: str,
        url: str,
    ) -> List[Dict[str, Any]]:
        findings: List[Dict[str, Any]] = []

        before_heap = before.get("memory", {}).get("usedJSHeapSize", 0)
        after_heap = after.get("memory", {}).get("usedJSHeapSize", 0)
        heap_delta = after_heap - before_heap
        if heap_delta > 30_000_000:
            findings.append(
                {
                    "step": step_num,
                    "type": "memory-spike",
                    "action": action,
                    "heap_delta_bytes": heap_delta,
                    "url": url,
                }
            )

        before_long_count = len(before.get("long_tasks", []))
        new_long_tasks = after.get("long_tasks", [])[before_long_count:]
        for task in new_long_tasks:
            duration = task.get("duration", 0)
            if duration > 50:
                findings.append(
                    {
                        "step": step_num,
                        "type": "long-task",
                        "action": action,
                        "duration_ms": duration,
                        "url": url,
                    }
                )
            if duration > 2000:
                findings.append(
                    {
                        "step": step_num,
                        "type": "main-thread-blocked",
                        "action": action,
                        "duration_ms": duration,
                        "url": url,
                    }
                )

        fps = after.get("fps", {}).get("fps", 60)
        if fps < 20:
            findings.append(
                {
                    "step": step_num,
                    "type": "fps-drop",
                    "action": action,
                    "fps": fps,
                    "url": url,
                }
            )

        for finding in findings:
            self.defects.add("performance_bottlenecks", finding)
        return findings


# ── Worker helpers ────────────────────────────────────────────────────────────


def build_worker_user_data_dir(settings: Settings, worker_id: int) -> str:
    """Create a per-worker sub-directory under the run's user data dir."""
    worker_label = f"worker-{worker_id:02d}"
    worker_data_dir = os.path.join(settings.run_user_data_dir, worker_label)
    os.makedirs(worker_data_dir, exist_ok=True)
    return worker_data_dir


async def with_retry_backoff(
    operation_name: str,
    operation,
    *,
    retries: int = 2,
    initial_delay_seconds: float = 0.75,
) -> Any:
    attempts = max(1, retries + 1)
    delay = max(0.1, float(initial_delay_seconds))
    last_exc: Optional[Exception] = None

    for attempt in range(1, attempts + 1):
        try:
            result = operation()
            if asyncio.iscoroutine(result):
                return await result
            return result
        except Exception as exc:
            last_exc = exc
            if attempt >= attempts:
                break
            jitter = random.uniform(0.0, 0.2)
            sleep_for = delay + jitter
            _local_service_log(
                f"{operation_name} failed on attempt {attempt}/{attempts}; "
                f"retrying in {sleep_for:.2f}s: {exc}"
            )
            await asyncio.sleep(sleep_for)
            delay *= 2.0

    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"{operation_name} failed without exception details")


def allocate_worker_steps(total_steps: int, worker_count: int, per_worker_cap: int) -> List[int]:
    worker_count = max(1, worker_count)
    remaining = max(0, total_steps)
    cap = max(1, per_worker_cap)
    allocations = [0 for _ in range(worker_count)]

    while remaining > 0:
        progressed = False
        for idx in range(worker_count):
            if remaining <= 0:
                break
            if allocations[idx] >= cap:
                continue
            allocations[idx] += 1
            remaining -= 1
            progressed = True
        if not progressed:
            break

    if remaining > 0:
        _local_service_log(
            f"Step allocation exhausted per-worker caps. "
            f"Unallocated steps={remaining}, workers={worker_count}, cap={cap}.",
            output_dir="",
        )
    return allocations


# ── Graceful shutdown helpers ─────────────────────────────────────────────────


def _request_graceful_shutdown(signum: int, frame: Optional[Any]) -> None:
    """Signal handler that requests a graceful shutdown."""
    global GRACEFUL_SHUTDOWN_REQUESTED
    if GRACEFUL_SHUTDOWN_REQUESTED:
        signal.default_int_handler(signum, frame)
        return

    GRACEFUL_SHUTDOWN_REQUESTED = True
    print(f"\n\U0001f6d1 Graceful shutdown requested (signal {signum}). Finishing in-flight steps...")
    try:
        loop = asyncio.get_event_loop()
        loop.call_soon_threadsafe(SHUTDOWN_EVENT.set)
    except Exception:
        try:
            SHUTDOWN_EVENT.set()
        except Exception:
            pass


def _register_graceful_shutdown_signals() -> None:
    """Register SIGINT/SIGTERM handlers for graceful shutdown."""
    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGINT, lambda: _request_graceful_shutdown(signal.SIGINT, None))
        loop.add_signal_handler(signal.SIGTERM, lambda: _request_graceful_shutdown(signal.SIGTERM, None))
    except NotImplementedError:
        signal.signal(signal.SIGINT, _request_graceful_shutdown)
        try:
            signal.signal(signal.SIGTERM, _request_graceful_shutdown)
        except Exception:
            pass


# ── Worker lifecycle ──────────────────────────────────────────────────────────


async def _run_worker_with_limit(
    settings: Settings,
    worker_semaphore: asyncio.Semaphore,
    *,
    playwright_instance: Any,
    worker_id: int,
    allocated_steps: int,
    start_step: int,
    persistence_engine: Any,  # PersistenceEngine
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
    persistence_engine: Any,  # PersistenceEngine
) -> WorkerRunResult:
    """Execute a single worker's step allocation with full monitor lifecycle."""
    from monkeylm.browser import (
        get_page_state,
        state_to_prompt,
        wait_for_page_ready,
        launch_context_with_fallback,
        handle_dialog,
        execute_action,
    )
    from monkeylm.models import decide_next_action, apply_state_aware_policy, _break_action_loop
    from monkeylm.memory import QdrantMemoryStore

    worker_label = f"worker-{worker_id:02d}"
    worker_defects = DefectTracker()
    worker_fuzzer = Fuzzer()
    worker_network_monitor = NetworkMonitor(worker_defects)
    worker_a11y_checker = A11yChecker(worker_defects)
    worker_perf_monitor = PerformanceMonitor(worker_defects)
    worker_memory = QdrantMemoryStore(settings)
    worker_logs: List[Dict[str, Any]] = []
    visited_states: Dict[str, int] = {}
    seen_click_targets: set = set()
    recent_model_plans: List[Tuple[str, str]] = []
    completed_steps = 0

    worker_data_dir = build_worker_user_data_dir(settings, worker_id)

    context = None
    launch_info: Dict[str, Any] = {
        "worker": worker_label,
        "mode": "not-started",
        "user_data_dir": worker_data_dir,
    }

    try:
        context, launch_info = await launch_context_with_fallback(
            playwright_instance,
            settings=settings,
            user_data_dir=worker_data_dir,
            worker_label=worker_label,
        )

        page = context.pages[0] if context.pages else await context.new_page()
        page.on("dialog", handle_dialog)

        def _console_listener(msg: Any) -> None:
            text = msg.text
            if "content security policy" in text.lower() or "csp" in text.lower():
                worker_defects.add(
                    "console_findings",
                    {
                        "step": -1,
                        "type": "csp-warning",
                        "message": text,
                        "url": page.url,
                        "worker": worker_label,
                    },
                )

        page.on("console", _console_listener)

        print(f"\U0001f680 Starting {worker_label} on {settings.target_url} with {allocated_steps} steps...")
        await with_retry_backoff(
            f"{worker_label} initial navigation",
            lambda: page.goto(settings.target_url, wait_until="domcontentloaded", timeout=45000),
            retries=settings.worker_navigation_retries,
            initial_delay_seconds=settings.retry_base_delay_seconds,
        )
        await wait_for_page_ready(page, f"{worker_label}-initial-navigation")

        await worker_network_monitor.install(page)
        await worker_perf_monitor.install(page)
        await with_retry_backoff(
            f"{worker_label} qdrant initialize",
            worker_memory.initialize,
            retries=settings.worker_qdrant_init_retries,
            initial_delay_seconds=settings.retry_base_delay_seconds,
        )

        for idx in range(allocated_steps):
            if SHUTDOWN_EVENT.is_set():
                print(f"\n\U0001f6d1 {worker_label} stopping early due to graceful shutdown request.")
                break

            step = start_step + idx
            print(f"\n--- {worker_label} step {step}/{settings.max_steps} ---")

            try:
                snapshot = await get_page_state(page, step, phase="plan", output_dir=settings.output_dir)
                state_key = f"{snapshot.url}::{snapshot.structure_hash}"
                local_count = visited_states.get(state_key, 0) + 1
                redis_count = await persistence_engine.increment_visited_state(state_key)
                visited_states[state_key] = redis_count if redis_count is not None else local_count
                state = state_to_prompt(snapshot)
            except Exception as exc:
                print(f"   -> \U0001f6a8 {worker_label} failed to get state: {exc}. Skipping step.")
                continue

            plan = await decide_next_action(settings, state, memory_store=worker_memory)
            retrieval_telemetry = worker_memory.consume_last_search_telemetry()

            # Anti-loop heuristic
            plan_signature = (plan.get("action", "scroll"), plan.get("target", ""))
            if len(recent_model_plans) >= 3 and all(p == plan_signature for p in recent_model_plans[-3:]):
                print(f"\U0001f504 Loop detected for {worker_label}; forcing path exploration variance.")
                plan = _break_action_loop(plan, snapshot, worker_label)
            recent_model_plans.append(plan_signature)
            recent_model_plans = recent_model_plans[-3:]

            plan = apply_state_aware_policy(settings, plan, snapshot, visited_states, seen_click_targets)
            if plan.get("action") == "click" and plan.get("target"):
                seen_click_targets.add(plan.get("target"))

            _, log_entry = await execute_action(
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
            )
            log_entry["worker_id"] = worker_id
            log_entry["memory_retrieval"] = retrieval_telemetry

            if step % 5 == 0:
                violations = await worker_a11y_checker.scan(page, step)
                if violations:
                    print(f"   -> \u267f {worker_label} a11y findings at step {step}: {len(violations)}")

            await wait_for_page_ready(page, f"{worker_label}-post-step-{step}")

            current_url = page.url
            if not is_in_scope(current_url, settings.target_url):
                worker_defects.add(
                    "boundary_drift",
                    {
                        "step": step,
                        "type": "Boundary Drift",
                        "current_url": current_url,
                        "target_url": settings.target_url,
                        "worker": worker_label,
                    },
                )
                await with_retry_backoff(
                    f"{worker_label} boundary recovery navigation",
                    lambda: page.goto(settings.target_url, wait_until="domcontentloaded", timeout=45000),
                    retries=settings.worker_boundary_recovery_retries,
                    initial_delay_seconds=settings.retry_base_delay_seconds,
                )
                await wait_for_page_ready(page, f"{worker_label}-boundary-recovery-{step}")

            try:
                baseline_snapshot = await get_page_state(page, step, phase="baseline", output_dir=settings.output_dir)
                await persistence_engine.analyze_route_regression(page, baseline_snapshot, step)
            except Exception as exc:
                _local_service_log(f"{worker_label} post-step baseline analysis failed at step {step}: {exc}", settings.output_dir)

            regression_hits = [
                finding
                for finding in worker_defects.regression_findings
                if int(finding.get("step", -1)) == step
            ]
            outcome_bits = [f"status={log_entry.get('status', 'UNKNOWN')}"]
            if log_entry.get("error"):
                outcome_bits.append(f"error={log_entry['error'][:180]}")
            if regression_hits:
                outcome_bits.append(
                    f"regressions={len(regression_hits)} tag=Vibe-Code-Regression-Missing-Component"
                )

            await worker_memory.add_step_memory(
                page_state=state,
                action=str(plan.get("action", "scroll")),
                outcome="; ".join(outcome_bits),
                url=page.url,
                step=step,
            )
            log_entry["memory_write"] = worker_memory.consume_last_write_telemetry()

            if log_entry.get("value"):
                payload_probe = log_entry["value"]
                try:
                    body_html = await page.content()
                    if payload_probe in body_html and "<" in payload_probe:
                        worker_defects.add(
                            "security_risks",
                            {
                                "step": step,
                                "type": "possible-reflected-input",
                                "payload_preview": payload_probe[:200],
                                "url": page.url,
                                "worker": worker_label,
                            },
                        )
                except Exception:
                    pass

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
    )


# ── Global test logs (populated by main) ─────────────────────────────────────

test_logs: List[Dict[str, Any]] = []


# ── Main entry point ──────────────────────────────────────────────────────────


async def main(settings: Settings) -> None:
    """Orchestrate the full monkey test run."""
    from monkeylm.browser import launch_context_with_fallback
    from monkeylm.memory import PersistenceEngine, QdrantMemoryStore
    from monkeylm.models import _is_cloud_vision_model
    from monkeylm.reporting import generate_markdown_report, generate_json_summary, generate_pdf_report

    start_time = datetime.now()

    # Qdrant admin modes (early exit)
    if settings.qdrant_admin_action in {"inspect", "clear"}:
        qmem = QdrantMemoryStore(settings)
        await qmem.initialize(for_admin=True)
        try:
            if settings.qdrant_admin_action == "inspect":
                info = await qmem.inspect_collection()
                print("🧠 Qdrant Inspect:")
                print(json.dumps(info, indent=2))
            else:
                info = await qmem.clear_collection()
                print("🧹 Qdrant Clear:")
                print(json.dumps(info, indent=2))
        finally:
            await qmem.close()
        return

    # Initialize defect tracker and persistence
    defects = DefectTracker()
    persistence_engine = PersistenceEngine(settings, defects, max_workers=settings.workers)

    print(
        "💡 Ollama throughput tip: set OLLAMA_NUM_PARALLEL="
        + str(settings.workers)
        + " (or higher) and "
        "OLLAMA_KV_CACHE_TYPE=q4_0 for lower-latency batch inference under concurrent workers."
    )

    active_vision_model = settings.vision_model or settings.pdf_vision_model
    vision_tier = "cloud" if _is_cloud_vision_model(active_vision_model) else "local"
    print(f"📸 Visual Auditor initialized with: {active_vision_model} ({vision_tier} tier)")

    allocations = allocate_worker_steps(settings.max_steps, settings.workers, settings.max_steps_per_worker)
    active_allocations = [(idx + 1, count) for idx, count in enumerate(allocations) if count > 0]
    if not active_allocations:
        _local_service_log("No steps allocated for execution. Exiting run early.", settings.output_dir)
        return

    async with async_playwright() as p:
        await persistence_engine.initialize()
        try:
            worker_semaphore = asyncio.Semaphore(settings.workers)
            worker_tasks: List[asyncio.Task] = []
            next_start_step = 1
            for worker_id, allocated_steps in active_allocations:
                worker_tasks.append(
                    asyncio.create_task(
                        _run_worker_with_limit(
                            settings,
                            worker_semaphore,
                            playwright_instance=p,
                            worker_id=worker_id,
                            allocated_steps=allocated_steps,
                            start_step=next_start_step,
                            persistence_engine=persistence_engine,
                        )
                    )
                )
                next_start_step += allocated_steps

            _register_graceful_shutdown_signals()
            worker_results = await asyncio.gather(*worker_tasks, return_exceptions=True)
        finally:
            await persistence_engine.close()

    # Merge results from all workers
    merged_defects = DefectTracker()
    merged_logs: List[Dict[str, Any]] = []
    merged_network_events: List[Dict[str, Any]] = []
    worker_launches: List[Dict[str, Any]] = []
    worker_completion: List[Dict[str, Any]] = []

    for result in worker_results:
        if isinstance(result, Exception):
            print(f"   -> 🚨 Worker raised an exception during shutdown: {result}")
            continue
        merged_defects.merge_from(result.defects)
        merged_logs.extend(result.logs)
        merged_network_events.extend(result.network_injections)
        worker_launches.append(result.launch_info)
        worker_completion.append(
            {
                "worker_id": result.worker_id,
                "allocated_steps": result.allocated_steps,
                "completed_steps": result.completed_steps,
            }
        )

    merged_logs.sort(key=lambda entry: int(entry.get("step", 0)))
    global test_logs
    test_logs = merged_logs

    browser_launch_info: Dict[str, Any] = {
        "mode": "multi-worker" if len(worker_launches) > 1 else "single-worker",
        "workers": worker_launches,
        "worker_completion": worker_completion,
        "window_size": settings.browser_window_size,
        "no_viewport": settings.no_viewport,
        "headless": settings.headless,
        "root_user_data_dir": settings.run_user_data_dir,
        "graceful_shutdown_requested": GRACEFUL_SHUTDOWN_REQUESTED,
    }

    end_time = datetime.now()

    # Generate reports
    generate_markdown_report(settings, merged_defects, merged_logs, browser_launch_info, start_time, end_time)
    generate_json_summary(settings, merged_defects, merged_logs, browser_launch_info, [], GRACEFUL_SHUTDOWN_REQUESTED, start_time, end_time)
    if settings.pdf_generate:
        generate_pdf_report(settings, merged_defects, merged_logs, start_time, end_time)
