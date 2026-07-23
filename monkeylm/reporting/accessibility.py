"""Accessibility violation compilation for MonkeyLM reports."""

from __future__ import annotations
from typing import Any, Dict, List


def _compile_accessibility_violations(
    violations: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Aggregate & deduplicate raw Axe findings into an actionable summary.

    Deduplication strategy:
        Violations sharing the same (rule_id, selector_key) across multiple
        steps are merged into a single entry.  selector_key is the first CSS
        target in the chain, truncated to 100 chars, so that repeated hits on
        the exact same DOM element collapse cleanly.

    Returns a dict structured for both CI/CD machine parsing and developer
    scannability:

        {
            "total_raw_violations": int,
            "unique_rules_found": int,
            "severity_totals": {"critical": N, "serious": M},
            "impact_score": float,
            "rules": [...]
        }
    """

    if not violations:
        return {
            "total_raw_violations": 0,
            "unique_rules_found": 0,
            "severity_totals": {"critical": 0, "serious": 0},
            "impact_score": 0.0,
            "rules": [],
        }

    groups: Dict[str, Dict[str, Any]] = {}

    for v in violations:
        rule_id = v.get("id", "unknown")
        selector = v.get("selector", "(unknown)")
        selector_key = (selector[:100] if selector else "(empty)")[:100]
        dedup_key = f"{rule_id}|||{selector_key}"

        if dedup_key not in groups:
            groups[dedup_key] = {
                "id": rule_id,
                "description": v.get("description", ""),
                "help": v.get("help", ""),
                "helpUrl": v.get("helpUrl", ""),
                "impact": v.get("impact", "serious"),
                "severity_distribution": {"critical": 0, "serious": 0},
                "occurrence_steps": [],
                "unique_selectors": set(),
                "html_snippets": set(),
                "remediation_advice": "",
                "remediations_seen": set(),
            }

        g = groups[dedup_key]
        g["severity_distribution"][v.get("severity", "serious")] = \
            g["severity_distribution"].get(v.get("severity", "serious"), 0) + 1
        g["occurrence_steps"].append(v.get("step", -1))
        if selector and selector != "(unknown)":
            g["unique_selectors"].add(selector)
        if v.get("html_snippet"):
            snippet = v["html_snippet"][:300]
            g["html_snippets"].add(snippet)
        if not g["remediation_advice"] and v.get("remediation"):
            g["remediation_advice"] = v["remediation"]
        if v.get("remediation"):
            g["remediations_seen"].add(v["remediation"])

    SEVERITY_WEIGHT = {"critical": 4.0, "serious": 1.0}

    compiled_rules: List[Dict[str, Any]] = []
    severity_totals = {"critical": 0, "serious": 0}
    total_impact_score = 0.0

    for g in groups.values():
        sev_dist = g["severity_distribution"]
        rule_score = (sev_dist.get("critical", 0) * SEVERITY_WEIGHT["critical"]
                      + sev_dist.get("serious", 0) * SEVERITY_WEIGHT["serious"])
        total_impact_score += rule_score
        severity_totals["critical"] += sev_dist.get("critical", 0)
        severity_totals["serious"] += sev_dist.get("serious", 0)

        compiled_rules.append({
            "id": g["id"],
            "description": g["description"],
            "help": g["help"],
            "helpUrl": g["helpUrl"],
            "impact": g["impact"],
            "severity_distribution": dict(sev_dist),
            "occurrence_steps": sorted(set(g["occurrence_steps"])),
            "unique_selectors": sorted(g["unique_selectors"])[:20],
            "html_snippets": sorted(g["html_snippets"])[:3],
            "remediation_advice": g["remediation_advice"] or (
                "\n".join(sorted(g["remediations_seen"])) if len(g["remediations_seen"]) <= 5 else ""
            ),
            "impact_score_contribution": round(rule_score, 1),
        })

    compiled_rules.sort(key=lambda r: r["impact_score_contribution"], reverse=True)

    return {
        "total_raw_violations": len(violations),
        "unique_rules_found": len(compiled_rules),
        "severity_totals": severity_totals,
        "impact_score": round(total_impact_score, 1),
        "rules": compiled_rules,
    }
