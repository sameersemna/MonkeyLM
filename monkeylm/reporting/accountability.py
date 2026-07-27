"""Vibe coding accountability and regression drift analysis."""

from __future__ import annotations
from typing import Any, Dict, List

from monkeylm.reporting.dedup import dedupe_findings


def summarize_vibe_coding_accountability(defects: Any) -> Dict[str, Any]:
    """Compute regression drift index and details from the DefectTracker."""
    findings = defects.regression_findings

    total_missing = 0
    total_expected = 0
    drift_details: List[Dict[str, Any]] = []

    for item in findings:
        missing_components = item.get("missing_components", [])
        if not isinstance(missing_components, list):
            missing_components = []
        missing_count = len(missing_components)

        expected_components = item.get("expected_baseline_components", missing_count)
        try:
            expected_components = int(expected_components)
        except Exception:
            expected_components = missing_count
        expected_components = max(expected_components, missing_count)

        total_missing += missing_count
        total_expected += expected_components

        component_contrast: List[Dict[str, str]] = []
        for component in missing_components[:50]:
            if not isinstance(component, dict):
                continue
            component_contrast.append(
                {
                    "selector_hint": str(component.get("selector_hint", "")),
                    "kind": str(component.get("kind", "")),
                    "tag": str(component.get("tag", "")),
                    "text": str(component.get("text", "")),
                }
            )

        broken_selectors = item.get("broken_selectors", [])
        if not isinstance(broken_selectors, list):
            broken_selectors = []

        drift_details.append(
            {
                "step": item.get("step"),
                "domain": item.get("domain", ""),
                "page_route": item.get("page_route", ""),
                "missing_count": missing_count,
                "expected_baseline_components": expected_components,
                "broken_selectors": [str(x) for x in broken_selectors],
                "missing_component_contrast": component_contrast,
            }
        )

    drift_index = (float(total_missing) / float(total_expected) * 100.0) if total_expected > 0 else 0.0

    defect_categories = [
        "security_risks", "context_anomalies", "ux_flow_freezes",
        "validation_failures", "race_findings", "boundary_drift",
        "console_findings", "performance_bottlenecks", "accessibility_violations",
    ]
    app_defect_count = 0
    for cat in defect_categories:
        collection = getattr(defects, cat, None)
        if not collection:
            continue
        # Dedupe before counting: the same root cause (a stuck freeze, a static
        # a11y violation on an unchanging page, etc.) is re-observed and logged
        # on every step it persists across, which would otherwise inflate the
        # headline defect count by 1-2 orders of magnitude without reflecting
        # any additional distinct app issues.
        for d in dedupe_findings(collection):
            severity = _derive_severity(cat, d)
            if severity in ("CRITICAL", "HIGH", "MEDIUM"):
                app_defect_count += 1

    if app_defect_count > 0:
        run_summary_status = f"FAILED: {app_defect_count} Application Defects Detected"
    elif drift_index > 0.0:
        run_summary_status = "FAILED: Structural Drift Detected"
    else:
        run_summary_status = "PASSED: No Issues Detected"

    return {
        "regression_drift_index": round(drift_index, 3),
        "total_missing_historical_components": total_missing,
        "total_expected_baseline_components": total_expected,
        "run_summary_status": run_summary_status,
        "drift_details": drift_details,
        "app_defect_count": app_defect_count,
    }


def _derive_severity(category: str, defect: Dict[str, Any]) -> str:
    """Derive severity from category defaults and defect-specific signals."""
    _SEVERITY_MAP: Dict[str, str] = {
        "security_risks": "CRITICAL",
        "validation_failures": "HIGH",
        "context_anomalies": "MEDIUM",
        "ux_flow_freezes": "HIGH",
        "race_findings": "HIGH",
        "boundary_drift": "MEDIUM",
        "console_findings": "MEDIUM",
        "performance_bottlenecks": "LOW",
        "accessibility_violations": "MEDIUM",
        "visual_regressions": "LOW",
        "layout_instability": "LOW",
        "regression_findings": "MEDIUM",
    }

    base = _SEVERITY_MAP.get(category, "MEDIUM")

    msg_parts = []
    for key in ("message", "description", "type", "error"):
        val = defect.get(key, "")
        if val:
            msg_parts.append(str(val).lower())
    combined = " ".join(msg_parts)

    severity_rank = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}

    critical_signals = [
        "stack trace", "stacktrace", "unhandled exception", "500", "internal server error",
        "sql injection", "xss", "drop table", "script>alert", "command injection",
        "remote code execution", "rce", "csrf bypass",
    ]
    for signal in critical_signals:
        if signal in combined:
            return "CRITICAL"

    high_signals = [
        "400", "401", "403", "404", "502", "503", "validation", "bypass",
        "stuck", "freeze", "loop", "race condition", "cors", "csp violation",
    ]
    if any(sig in combined for sig in high_signals):
        return max([base, "HIGH"], key=lambda s: severity_rank.get(s, 1))

    return base
