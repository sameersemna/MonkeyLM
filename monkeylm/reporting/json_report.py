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


def _build_runtime_preflight_payload(browser_launch_info: Dict[str, Any]) -> Dict[str, Any]:
    payload = browser_launch_info.get("runtime_preflight")
    if isinstance(payload, dict):
        return payload
    return {}


def generate_json_summary(
    settings: Any,
    defects: Any,
    test_logs: List[Dict[str, Any]],
    browser_launch_info: Dict[str, Any],
    network_injections: List[Dict[str, Any]],
    graceful_shutdown_requested: bool,
    start_time: datetime,
    end_time: datetime,
    *,
    discovery_strategy: Any = None,
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
        "qdrant_config": {
            # Recorded so "providers: {hash: N}" in semantic_memory_telemetry
            # can be read correctly: it's expected/correct when
            # embedding_provider_configured == "hash", and only a real
            # problem (silent fallback) when it's "ollama" but the observed
            # provider or telemetry fallback_count says otherwise.
            "embedding_provider_configured": settings.qdrant_embedding_provider,
            "embedding_model_configured": settings.qdrant_embedding_model,
            "rerank_enabled_configured": settings.qdrant_rerank_enabled,
        },
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
        "worker_failures": browser_launch_info.get("worker_failures", []),
        "runtime_preflight": _build_runtime_preflight_payload(browser_launch_info),
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
        "application_discovery": {
            "app_domain": getattr(discovery_strategy, "app_domain", None),
            "strategy_summary": getattr(discovery_strategy, "strategy_summary", None),
            "primary_personas": [
                {
                    "name": getattr(persona, "name", None),
                    "description": getattr(persona, "description", None),
                    "behaviors": list(getattr(persona, "behaviors", []) or []),
                }
                for persona in getattr(discovery_strategy, "primary_personas", []) or []
            ],
            "critical_flows": [
                {
                    "name": getattr(flow, "name", None),
                    "description": getattr(flow, "description", None),
                    "steps": list(getattr(flow, "steps", []) or []),
                }
                for flow in getattr(discovery_strategy, "critical_flows", []) or []
            ],
            "edge_cases_to_test": list(getattr(discovery_strategy, "edge_cases_to_test", []) or []),
            "security_focus": list(getattr(discovery_strategy, "security_focus", []) or []),
        } if discovery_strategy is not None else None,
        "network_injections": network_injections,
        "semantic_memory_telemetry": semantic_memory_telemetry,
        "vibe_coding_accountability": accountability,
        "logs": test_logs,
        "failure_context_samples": [
            {
                "step": log.get("step"),
                "action": log.get("action"),
                "error": log.get("error"),
                "failure_context": log.get("failure_context"),
            }
            for log in test_logs
            if log.get("status") != "SUCCESS" and log.get("failure_context")
        ],
    }
    output_path = os.path.join(settings.output_dir, "results.json")
    redacted_summary = redact_sensitive_content(json.dumps(summary, indent=2))
    _secure_atomic_write(output_path, redacted_summary, mode=0o600)
    print(f"📦 JSON summary generated: {output_path}")
