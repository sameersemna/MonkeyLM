"""Form payload generation compatibility exports and defect summaries."""

from __future__ import annotations

from typing import Any

from monkeylm.form_filling import generate_form_payload

__all__ = ["generate_form_payload", "_step_defects_summary"]


def _step_defects_summary(step_num: int, defects: Any) -> list[str]:
    reasons: list[str] = []
    category_names = [
        ("security_risks", "security_risk"),
        ("visual_regressions", "visual_regression"),
        ("layout_instability", "layout_instability"),
        ("accessibility_violations", "a11y_violation"),
        ("performance_bottlenecks", "perf_bottleneck"),
        ("regression_findings", "regression"),
        ("console_findings", "console_finding"),
        ("race_findings", "race_condition"),
        ("boundary_drift", "boundary_drift"),
    ]
    for attr_name, label in category_names:
        for item in getattr(defects, attr_name, []):
            if int(item.get("step", -1)) == step_num:
                dtype = item.get("type", item.get("severity", "unknown"))
                reasons.append(f"{label}:{dtype}")
    return reasons
