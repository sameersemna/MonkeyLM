"""Telemetry aggregation for MonkeyLM semantic memory operations."""

from __future__ import annotations
from typing import Any, Dict, List


def summarize_v1m_shadow_telemetry(test_logs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate native V1M shadow outcomes without changing execution policy."""
    events = [
        log.get("v1m_shadow")
        for log in test_logs
        if isinstance(log.get("v1m_shadow"), dict)
        and log.get("v1m_shadow", {}).get("status") != "skipped"
    ]
    evaluated = [event for event in events if event.get("status") == "evaluated"]
    fallback = [event for event in events if event.get("status") == "fallback"]
    latencies = [
        float(event.get("latency_ms", 0.0))
        for event in events
        if isinstance(event.get("latency_ms"), (int, float))
    ]
    outcomes: Dict[str, int] = {}
    for event in events:
        outcome = str(event.get("outcome", "unknown"))
        outcomes[outcome] = outcomes.get(outcome, 0) + 1

    model_changed_count = len([
        event for event in events
        if event.get("model_changed") is True
    ])
    permutation_checks = [
        event.get("permutation_check")
        for event in events
        if isinstance(event.get("permutation_check"), dict)
        and event["permutation_check"].get("checked") is True
    ]
    permutation_flips = len([
        check for check in permutation_checks
        if check.get("flip") is True
    ])
    baseline_executed_changes = len([
        event for event in events
        if isinstance(event.get("baseline_plan"), dict)
        and isinstance(event.get("executed_plan"), dict)
        and event["baseline_plan"] != event["executed_plan"]
    ])
    loop_context_count = len([
        event for event in events
        if event.get("loop_context") is True
        or (
            isinstance(event.get("permutation_check"), dict)
            and event["permutation_check"].get("checked") is not None
        )
    ])
    risk_counts: Dict[str, int] = {}
    for event in events:
        risk = str(event.get("action_risk") or "")
        if risk:
            risk_counts[risk] = risk_counts.get(risk, 0) + 1

    return {
        "events": len(events),
        "evaluated": len(evaluated),
        "fallback": len(fallback),
        "rerank_applied": len([
            event for event in events
            if event.get("rerank_applied") is True
        ]),
        "rerank_blocked": len([
            event for event in events
            if event.get("rerank_blocked_reason")
        ]),
        "model_changed": model_changed_count,
        "permutation_checked": len(permutation_checks),
        "permutation_flips": permutation_flips,
        "baseline_executed_changes": baseline_executed_changes,
        "loop_context_events": loop_context_count,
        "risk_counts": risk_counts,
        "outcomes": outcomes,
        "avg_latency_ms": round(
            sum(latencies) / len(latencies),
            3,
        ) if latencies else 0.0,
    }


def summarize_semantic_memory_telemetry(test_logs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate Qdrant retrieval/write telemetry from test logs."""

    def _avg(values: List[float]) -> float:
        if not values:
            return 0.0
        return float(sum(values) / len(values))

    retrieval_events: List[Dict[str, Any]] = [
        val for log in test_logs
        for val in [log.get("memory_retrieval")]
        if isinstance(val, dict)
    ]
    write_events: List[Dict[str, Any]] = [
        val for log in test_logs
        for val in [log.get("memory_write")]
        if isinstance(val, dict)
    ]

    retrieval_ok = [evt for evt in retrieval_events if evt.get("status") == "ok"]
    retrieval_returned = [int(evt.get("returned_count", 0)) for evt in retrieval_ok]
    retrieval_total_ms = [float(evt.get("total_ms", 0.0)) for evt in retrieval_events]
    retrieval_search_ms = [float(evt.get("qdrant_search_ms", 0.0)) for evt in retrieval_ok]
    retrieval_rerank_ms = [float(evt.get("rerank_ms", 0.0)) for evt in retrieval_ok]

    write_ok = [evt for evt in write_events if evt.get("status") == "ok"]
    write_total_ms = [float(evt.get("total_ms", 0.0)) for evt in write_events]
    write_upsert_ms = [float(evt.get("qdrant_upsert_ms", 0.0)) for evt in write_ok]

    provider_counts: Dict[str, int] = {}
    fallback_count = 0
    for evt in retrieval_ok + write_ok:
        provider = str(evt.get("provider_used", "unknown"))
        provider_counts[provider] = provider_counts.get(provider, 0) + 1
        if evt.get("fallback_used"):
            fallback_count += 1

    rerank_applied_count = len([evt for evt in retrieval_ok if evt.get("rerank_applied")])

    return {
        "retrieval": {
            "events": len(retrieval_events),
            "ok": len(retrieval_ok),
            "avg_total_ms": round(_avg(retrieval_total_ms), 3),
            "avg_qdrant_search_ms": round(_avg(retrieval_search_ms), 3),
            "avg_rerank_ms": round(_avg(retrieval_rerank_ms), 3),
            "avg_returned_count": round(_avg([float(x) for x in retrieval_returned]), 3),
            "rerank_applied_count": rerank_applied_count,
        },
        "write": {
            "events": len(write_events),
            "ok": len(write_ok),
            "avg_total_ms": round(_avg(write_total_ms), 3),
            "avg_qdrant_upsert_ms": round(_avg(write_upsert_ms), 3),
        },
        "providers": provider_counts,
        # `providers` alone can't tell you whether "hash" means "hash was the
        # configured default" or "ollama was requested and silently fell
        # back on every call." fallback_count is the direct signal: it's only
        # nonzero when a real embedding attempt failed and got downgraded.
        "fallback_count": fallback_count,
    }
