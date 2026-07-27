"""JSON summary generation for MonkeyLM test runs."""

from __future__ import annotations
import json
import os
from datetime import datetime
from typing import Any, Dict, List

from monkeylm.memory import _secure_atomic_write
from monkeylm.reporting.utils import redact_sensitive_content
from monkeylm.reporting.telemetry import summarize_semantic_memory_telemetry
from monkeylm.reporting.accountability import summarize_vibe_coding_accountability
from monkeylm.reporting.accessibility import _compile_accessibility_violations
from monkeylm.reporting.defects import _compile_defect_tickets


def generate_json_summary(
    settings: Any,
    defects: Any,
    test_logs: List[Dict[str, Any]],
    browser_launch_info: Dict[str, Any],
    network_injections: List[Dict[str, Any]],
    graceful_shutdown_requested: bool,
    start_time: datetime,
    end_time: datetime,
) -> None:
    """Write results.json with full run data."""
    semantic_memory_telemetry = summarize_semantic_memory_telemetry(test_logs)
    accountability = summarize_vibe_coding_accountability(defects)

    summary = {
        "target_url": settings.target_url,
        "model": settings.ollama_model,
        "active_seed": settings.active_seed,
        "workers": settings.workers,
        "max_steps_per_worker": settings.max_steps_per_worker,
        "configured_max_steps": settings.max_steps,
        "ollama_timeout_seconds": settings.ollama_timeout_seconds,
        "redis_path_lock_ttl_seconds": settings.redis_path_lock_ttl_seconds,
        "graceful_shutdown_requested": graceful_shutdown_requested,
        "retry_policy": {
            "worker_navigation_retries": settings.worker_navigation_retries,
            "worker_qdrant_init_retries": settings.worker_qdrant_init_retries,
            "worker_boundary_recovery_retries": settings.worker_boundary_recovery_retries,
            "base_delay_seconds": settings.retry_base_delay_seconds,
        },
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "duration_seconds": (end_time - start_time).total_seconds(),
        "steps": len(test_logs),
        "failed_steps": len([log for log in test_logs if log["status"] != "SUCCESS"]),
        "run_summary_status": accountability.get("run_summary_status"),
        "regression_drift_index": accountability.get("regression_drift_index"),
        "app_defect_count": accountability.get("app_defect_count", 0),
        "browser_launch": browser_launch_info,
        "defects": {
            "security_risks": defects.security_risks,
            "accessibility_violations_raw": defects.accessibility_violations,
            "accessibility_compiled": _compile_accessibility_violations(defects.accessibility_violations),
            "performance_bottlenecks": defects.performance_bottlenecks,
            "visual_regressions": defects.visual_regressions,
            "layout_instability": defects.layout_instability,
            "regression_findings": defects.regression_findings,
            "race_findings": defects.race_findings,
            "console_findings": defects.console_findings,
            "boundary_drift": defects.boundary_drift,
            "context_anomalies": defects.context_anomalies,
            "ux_flow_freezes": defects.ux_flow_freezes,
            "validation_failures": defects.validation_failures,
            "capture_diagnostics": getattr(defects, "capture_diagnostics", []),
        },
        "compiled_defect_tickets": [t.to_dict() for t in _compile_defect_tickets(defects, test_logs)],
        "network_injections": network_injections,
        "semantic_memory_telemetry": semantic_memory_telemetry,
        "vibe_coding_accountability": accountability,
        "logs": test_logs,
    }
    output_path = os.path.join(settings.output_dir, "results.json")
    redacted_summary = redact_sensitive_content(json.dumps(summary, indent=2))
    _secure_atomic_write(output_path, redacted_summary, mode=0o600)
    print(f"📦 JSON summary generated: {output_path}")
