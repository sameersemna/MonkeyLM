"""Scheduler - main entry point, global state, and orchestration."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any, Dict, List

from playwright.async_api import async_playwright

from monkeylm.config import (
    Settings,
    _local_service_log,
    GRACEFUL_SHUTDOWN_REQUESTED,
    _register_graceful_shutdown_signals,
)
from monkeylm.core.monitor import DefectTracker, NetworkMonitor, PerformanceMonitor
from monkeylm.core.worker import _run_worker_with_limit, allocate_worker_steps


test_logs: List[Dict[str, Any]] = []
DEFECTS: DefectTracker = DefectTracker()
NETWORK_MONITOR: NetworkMonitor = NetworkMonitor(DEFECTS)
PERF_MONITOR: PerformanceMonitor = PerformanceMonitor(DEFECTS)


async def main(settings: Settings) -> None:
    from monkeylm.memory import PersistenceEngine, QdrantMemoryStore
    from monkeylm.models import _is_cloud_vision_model
    from monkeylm.reporting import generate_markdown_report, generate_json_summary, generate_pdf_report

    start_time = datetime.now()

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
    print(f"   └─ Vision model (settings.vision_model): {settings.vision_model}")
    print(f"   └─ PDF vision model (settings.pdf_vision_model): {settings.pdf_vision_model}")

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

    merged_defects = DefectTracker()
    merged_logs: List[Dict[str, Any]] = []
    merged_network_events: List[Dict[str, Any]] = []
    worker_launches: List[Dict[str, Any]] = []
    worker_completion: List[Dict[str, Any]] = []

    for result in worker_results:
        if isinstance(result, BaseException):
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
                "failure_reason": result.failure_reason,
                "failure_artifact": result.failure_artifact,
                "failure_context": result.failure_context,
            }
        )

    merged_logs.sort(key=lambda entry: int(entry.get("step", 0)))
    global test_logs
    test_logs = merged_logs

    browser_launch_info: Dict[str, Any] = {
        "mode": "multi-worker" if len(worker_launches) > 1 else "single-worker",
        "workers": worker_launches,
        "worker_completion": worker_completion,
        "worker_failures": [entry for entry in worker_completion if entry.get("failure_reason")],
        "window_size": settings.browser_window_size,
        "no_viewport": settings.no_viewport,
        "headless": settings.headless,
        "root_user_data_dir": settings.run_user_data_dir,
        "graceful_shutdown_requested": GRACEFUL_SHUTDOWN_REQUESTED,
    }

    end_time = datetime.now()

    generate_markdown_report(settings, merged_defects, merged_logs, browser_launch_info, start_time, end_time)
    generate_json_summary(settings, merged_defects, merged_logs, browser_launch_info, [], GRACEFUL_SHUTDOWN_REQUESTED, start_time, end_time)
    try:
        if getattr(merged_defects, "accessibility_violations", None):
            from monkeylm.reporting import generate_interactive_html_report
            generate_interactive_html_report(settings, merged_defects, merged_logs, start_time, end_time)
    except Exception as exc:
        print(f"⚠️ HTML accessibility report generation failed: {exc}")
    if settings.pdf_generate:
        generate_pdf_report(settings, merged_defects, merged_logs, start_time, end_time)
