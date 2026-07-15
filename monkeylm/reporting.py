"""Markdown, JSON, and PDF report generators for MonkeyLM test runs."""

from __future__ import annotations
import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional
from monkeylm.config import (
    DefectTicket,
    Image,
    _REPORTLAB_AVAILABLE,
    _local_service_log,
)
from monkeylm.memory import _secure_atomic_write


def redact_sensitive_content(text: str) -> str:
    """Redact sensitive patterns from text before writing to files."""
    # Redact specific patterns mentioned in requirements
    text = re.sub(r'sk-\w+', '[REDACTED]', text, flags=re.IGNORECASE)
    text = re.sub(r'gsk_\w+', '[REDACTED]', text, flags=re.IGNORECASE)
    text = re.sub(r'ollama-\w+', '[REDACTED]', text, flags=re.IGNORECASE)
    
    # Redact common password-related terms
    password_patterns = [
        r'(password|passwd|secret|token|key|credential|api_key|access_key|auth_token|session_token|api_secret|private_key|public_key|auth_secret|jwt_token|access_secret)',
    ]
    
    for pattern in password_patterns:
        text = re.sub(pattern, '[REDACTED]', text, flags=re.IGNORECASE)
    
    return text

# Conditional ReportLab imports
if _REPORTLAB_AVAILABLE:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        Image as RLImage,
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )


def summarize_semantic_memory_telemetry(test_logs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate Qdrant retrieval/write telemetry from test logs."""

    def _avg(values: List[float]) -> float:
        if not values:
            return 0.0
        return float(sum(values) / len(values))

    retrieval_events = [
        log.get("memory_retrieval")
        for log in test_logs
        if isinstance(log.get("memory_retrieval"), dict)
    ]
    write_events = [
        log.get("memory_write")
        for log in test_logs
        if isinstance(log.get("memory_write"), dict)
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
    for evt in retrieval_ok + write_ok:
        provider = str(evt.get("provider_used", "unknown"))
        provider_counts[provider] = provider_counts.get(provider, 0) + 1

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
    }


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
            "impact_score": float,       # critical*4 + serious*1 (weighted)
            "rules": [
                {
                    "id": str,                       # Axe rule id
                    "description": str,              # Human-readable name
                    "help": str,                     # Axe guidance text
                    "helpUrl": str,                  # MDN / docs URL
                    "impact": "critical"|"serious",  # Highest impact seen
                    "severity_distribution": {"critical": N, "serious": M},
                    "occurrence_steps": List[int],   # Steps where first observed
                    "unique_selectors": List[str],   # All distinct CSS chains
                    "html_snippets": List[str],       # Up to 3 unique samples
                    "remediation_advice": str,        # First unique failureSummary
                    "impact_score_contribution": float,
                },
                ...
            ]
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

    # ── Group by dedup key (rule_id + first selector truncated) ─────────
    groups: Dict[str, Dict[str, Any]] = {}

    for v in violations:
        rule_id = v.get("id", "unknown")
        selector = v.get("selector", "(unknown)")
        # Selector key: use the full selector string but truncate long ones
        selector_key = (selector[:100] if selector else "(empty)")[:100]
        dedup_key = f"{rule_id}|||{selector_key}"

        if dedup_key not in groups:
            groups[dedup_key] = {
                "id": rule_id,
                "description": v.get("description", ""),
                "help": v.get("help", ""),
                "helpUrl": v.get("helpUrl", ""),
                "impact": v.get("impact", "serious"),  # Track highest seen
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
            snippet = v["html_snippet"][:300]  # Trim very long snippets
            g["html_snippets"].add(snippet)
        if not g["remediation_advice"] and v.get("remediation"):
            g["remediation_advice"] = v["remediation"]
        if v.get("remediation"):
            g["remediations_seen"].add(v["remediation"])

    # ── Build sorted output ─────────────────────────────────────────────
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
            "unique_selectors": sorted(g["unique_selectors"])[:20],  # Cap at 20
            "html_snippets": sorted(g["html_snippets"])[:3],  # Cap at 3 samples
            "remediation_advice": g["remediation_advice"] or (
                "\n".join(sorted(g["remediations_seen"])) if len(g["remediations_seen"]) <= 5 else ""
            ),
            "impact_score_contribution": round(rule_score, 1),
        })

    # Sort by impact score descending (most severe first)
    compiled_rules.sort(key=lambda r: r["impact_score_contribution"], reverse=True)

    return {
        "total_raw_violations": len(violations),
        "unique_rules_found": len(compiled_rules),
        "severity_totals": severity_totals,
        "impact_score": round(total_impact_score, 1),
        "rules": compiled_rules,
    }


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

    # ── Check for application defects across all categories ──────────
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
        for d in collection:
            severity = _derive_severity(cat, d)
            if severity in ("CRITICAL", "HIGH", "MEDIUM"):
                app_defect_count += 1

    # Override run summary status based on application defects
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


# ── Defect Ticket Compiler Pipeline ───────────────────────────────────────────

# Heuristic severity mapping: defect type → default severity
_SEVERITY_MAP: Dict[str, str] = {
    # High-impact categories
    "security_risks": "CRITICAL",
    "validation_failures": "HIGH",
    "context_anomalies": "MEDIUM",
    # Medium-impact categories
    "ux_flow_freezes": "HIGH",
    "race_findings": "HIGH",
    "boundary_drift": "MEDIUM",
    # Lower-impact but still important
    "console_findings": "MEDIUM",
    "performance_bottlenecks": "LOW",
    "accessibility_violations": "MEDIUM",
    # Visual/UI
    "visual_regressions": "LOW",
    "layout_instability": "LOW",
    "regression_findings": "MEDIUM",
}

# Template-based root cause analysis generators by defect category
_ROOT_CAUSE_TEMPLATES: Dict[str, str] = {
    "security_risks": (
        "Client-side input validation was insufficient to prevent potentially malicious payloads "
        "from reaching the server router backend. The application allows user-supplied data to be "
        "processed without adequate sanitization, creating an attack surface for injection-based exploits."
    ),
    "validation_failures": (
        "The client-side form interface allowed a submission event to resolve to the server router "
        "without validating input parameters. The server returned an unhandled error response, "
        "indicating missing schema validation on the API layer or insufficient error boundary handling."
    ),
    "context_anomalies": (
        "Unhandled browser runtime exception detected during automated user flow execution. "
        "This likely indicates a missing try-catch block around async operations, an unhandled "
        "Promise rejection, or a network request failure that propagates to the console error stream."
    ),
    "ux_flow_freezes": (
        "The application entered a repetitive state loop where user interactions no longer produce "
        "meaningful DOM changes or URL transitions. This suggests an infinite loading state, "
        "a broken state machine transition, or a modal/dialog that cannot be dismissed."
    ),
    "race_findings": (
        "Concurrent async operations executed without proper synchronization, leading to a race condition. "
        "Multiple overlapping requests may have caused inconsistent application state or data corruption."
    ),
    "boundary_drift": (
        "During cross-domain navigation testing, the agent escaped the intended application boundary. "
        "This indicates missing origin validation, an open redirect vulnerability, or overly permissive "
        "iframe/embed configurations that allow unintended page traversal."
    ),
    "console_findings": (
        "JavaScript console errors or warnings were emitted during normal user interaction flows. "
        "These may indicate deprecated API usage, null reference exceptions, or unhandled edge cases "
        "that degrade the reliability of the application."
    ),
    "performance_bottlenecks": (
        "Application performance metrics exceed acceptable thresholds during standard interaction patterns. "
        "This could be caused by excessive DOM operations, unoptimized asset loading, memory leaks, "
        "or blocking synchronous operations on the main thread."
    ),
    "accessibility_violations": (
        "The page structure or interactive elements violate WCAG accessibility guidelines, making the "
        "application unusable for assistive technology users. This indicates missing ARIA attributes, "
        "insufficient color contrast, improper heading hierarchy, or keyboard navigation barriers."
    ),
    "visual_regressions": (
        "Visual comparison against golden baselines reveals pixel-level differences in rendered output. "
        "This may indicate unintended CSS changes, component rendering differences, or layout shifts "
        "that break the expected visual presentation of the application."
    ),
    "layout_instability": (
        "Elements on the page shifted position after initial render, causing unexpected layout instability. "
        "This is typically caused by late-loading assets (images, fonts), dynamic content injection, "
        "or JavaScript-driven DOM mutations that shift the Cumulative Layout Shift (CLS) metric."
    ),
    "regression_findings": (
        "Components present in historical golden baselines are now missing or structurally broken. "
        "This indicates code changes have inadvertently removed or altered core interactive elements, "
        "potentially breaking established user flows or functionality."
    ),
}

# Template-based remediation instructions by defect category
_REMEDIATION_TEMPLATES: Dict[str, str] = {
    "security_risks": (
        "Implement server-side input sanitization using parameterized queries and output encoding. "
        "Add client-side schema validation (e.g., Zod or Yup) to all form fields and API endpoints. "
        "Enforce Content Security Policy (CSP) headers to restrict script execution sources."
    ),
    "validation_failures": (
        "Inject standard input schema validation on affected form fields to block submission when "
        "the container state is invalid. Add HTML5 constraint validation attributes (required, pattern, minlength) "
        "and implement robust error boundary components to catch and display validation errors gracefully."
    ),
    "context_anomalies": (
        "Wrap the failing async operation in a try-catch block with appropriate error handling. "
        "Add global unhandled promise rejection handlers via window.addEventListener('unhandledrejection'). "
        "Implement proper HTTP status code checking for all fetch/XHR responses."
    ),
    "ux_flow_freezes": (
        "Audit the state machine triggering this loop. Ensure that modal and dialog components have "
        "dismissible states (Escape key, close button, backdrop click). Add loading timeout fallbacks "
        "to prevent infinite spinner states. Implement URL-based navigation guards to detect stuck states."
    ),
    "race_findings": (
        "Synchronize concurrent async operations using mutex-like patterns, request cancellation tokens, "
        "or optimistic concurrency control. Ensure that network retry logic includes idempotency checks "
        "to prevent duplicate submissions or conflicting state updates."
    ),
    "boundary_drift": (
        "Enforce strict origin validation on all navigational redirects. Implement SameSite cookie attributes. "
        "Add X-Frame-Options and CSP frame-ancestors headers to prevent clickjacking. Audit and restrict "
        "all external link targets to whitelisted domains."
    ),
    "console_findings": (
        "Identify and fix the JavaScript errors in the console output. Common fixes include: proper null-checks, "
        "async/await error handling, and removing deprecated API calls. Treat console warnings as technical debt."
    ),
    "performance_bottlenecks": (
        "Profile the slow operations using browser DevTools Performance tab. Optimize critical rendering path "
        "by lazy loading non-critical assets, reducing DOM complexity, debouncing frequent handlers, "
        "and virtualizing long lists. Set up Core Web Vitals monitoring."
    ),
    "accessibility_violations": (
        "Fix WCAG violations by adding appropriate ARIA roles, labels, and live regions. Ensure keyboard "
        "navigation follows a logical tab order. Verify color contrast ratios meet WCAG AA standards. "
        "Use axe-core or Lighthouse accessibility audits to validate fixes."
    ),
    "visual_regressions": (
        "Compare current CSS and component rendering against the expected golden baseline. If the visual "
        "difference is intentional, update the golden baseline. If unintentional, revert styling changes "
        "or fix the component rendering logic."
    ),
    "layout_instability": (
        "Reserve explicit width/height dimensions for images and iframes to prevent layout shifts. "
        "Avoid injecting content above existing page elements. Use font-display: swap strategically "
        "and serve web fonts early in the critical rendering path."
    ),
    "regression_findings": (
        "Restore missing components by comparing current code with the version that produced the golden baseline. "
        "Review recent commits for style or structural changes that inadvertently removed interactive elements. "
        "Add component existence checks to your E2E test suite."
    ),
}


def _derive_severity(category: str, defect: Dict[str, Any]) -> str:
    """Derive severity from category defaults and defect-specific signals."""
    base = _SEVERITY_MAP.get(category, "MEDIUM")

    # Override based on content
    msg_parts = []
    for key in ("message", "description", "type", "error"):
        val = defect.get(key, "")
        if val:
            msg_parts.append(str(val).lower())
    combined = " ".join(msg_parts)

    severity_rank = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}

    # Critical signals: stack traces, 5xx errors, injection success
    critical_signals = [
        "stack trace", "stacktrace", "unhandled exception", "500", "internal server error",
        "sql injection", "xss", "drop table", "script>alert", "command injection",
        "remote code execution", "rce", "csrf bypass",
    ]
    for signal in critical_signals:
        if signal in combined:
            return "CRITICAL"

    # High signals: 4xx errors, validation bypass, infinite loop
    high_signals = [
        "400", "401", "403", "404", "502", "503", "validation", "bypass",
        "stuck", "freeze", "loop", "race condition", "cors", "csp violation",
    ]
    if any(sig in combined for sig in high_signals):
        # Elevate to at least HIGH, but preserve CRITICAL if base is already higher
        return max([base, "HIGH"], key=lambda s: severity_rank.get(s, 1))

    return base


def _extract_reproduction_steps(
    test_logs: List[Dict[str, Any]], target_step: int, lookback: int = 5
) -> List[Dict[str, Any]]:
    """Reconstruct reproduction steps from test logs leading up to a defect."""
    if not test_logs or target_step <= 0:
        return []

    steps = []
    start_step = max(1, target_step - lookback)
    for log in test_logs:
        step_num = log.get("step", 0)
        if step_num < start_step:
            continue
        if step_num >= target_step:
            break

        entry: Dict[str, Any] = {
            "step": step_num,
            "action": log.get("action", ""),
            "target": log.get("target", ""),
            "url": log.get("url", ""),
            "status": log.get("status", ""),
        }
        if log.get("value"):
            entry["value"] = str(log["value"])[:200]
        steps.append(entry)

    # Add the failing step itself
    for log in test_logs:
        if log.get("step") == target_step:
            entry = {
                "step": target_step,
                "action": log.get("action", ""),
                "target": log.get("target", ""),
                "url": log.get("url", ""),
                "status": log.get("status", ""),
                "error": log.get("error", ""),
            }
            if log.get("value"):
                entry["value"] = str(log["value"])[:200]
            steps.append(entry)
            break

    return steps


def _group_defects(
    defects: Any, test_logs: List[Dict[str, Any]]
) -> List[DefectTicket]:
    """Group raw defects into consolidated DefectTickets.

    Groups by (category, url_prefix) key — multiple occurrences of the same
    defect type on the same page are merged into one ticket with all reproduction
    steps aggregated.
    """
    import re as _re

    # Collect all defects with their category prefix
    all_defects: List[tuple[str, Dict[str, Any]]] = []
    categories = [
        "security_risks", "context_anomalies", "ux_flow_freezes",
        "validation_failures", "race_findings", "boundary_drift",
        "console_findings", "performance_bottlenecks", "accessibility_violations",
        "visual_regressions", "layout_instability", "regression_findings",
    ]
    for category in categories:
        collection = getattr(defects, category, [])
        if collection:
            for d in collection:
                all_defects.append((category, d))

    if not all_defects:
        return []

    # Build step→log mapping for quick reproduction lookup
    step_to_log: Dict[int, Dict[str, Any]] = {}
    for log in test_logs:
        step_to_log[log.get("step", 0)] = log

    # Group by (category, normalized_url) to merge related defects
    groups: Dict[tuple[str, str], list[tuple[str, Dict[str, Any]]]] = {}
    for category, defect in all_defects:
        url = defect.get("url", "") or ""
        # Normalize URL to domain + first path segment
        try:
            m = _re.match(r"([^?#]+)", url)
            normalized_url = m.group(1) if m else ""
            # Strip trailing slash for grouping
            normalized_url = normalized_url.rstrip("/")
        except Exception:
            normalized_url = url
        key = (category, normalized_url)
        groups.setdefault(key, []).append((category, defect))

    # Compile tickets from groups
    tickets: List[DefectTicket] = []
    ticket_counter = 1

    for (category, url), group_defects in groups.items():
        # Sort by step number for ordered reproduction
        sorted_defects = sorted(
            group_defects, key=lambda x: x[1].get("step", 0) or 0
        )

        primary = sorted_defects[0][1]  # first occurrence as primary context
        target_step = primary.get("step", 0)

        # Title generation
        defect_type = primary.get("type", category)
        if category.startswith("ux_flow"):
            title = f"UX Flow Freeze: {defect_type}"
        elif category == "validation_failures":
            probe = primary.get("probe_name", "") or ""
            target = primary.get("target", "") or ""
            title = f"Validation Failure on {target} ({probe})"
        elif category == "context_anomalies":
            anomaly_type = primary.get("type", "unknown")
            title = f"Context Anomaly: {anomaly_type}"
        elif category == "security_risks":
            title = f"Security Risk: {defect_type}"
        else:
            title = f"{category.replace('_', ' ').title()}: {defect_type}"

        # Extract target selector and HTML snippet from primary defect
        target_selector = (
            primary.get("target", "") or
            primary.get("selector", "") or
            primary.get("element", "") or ""
        )
        html_snippet = (primary.get("html_context", "") or primary.get("html", "") or "")[:1000]

        # Page state name from URL
        page_state_name = url.split("/")[-1] if url else "unknown-page"
        if not page_state_name:
            page_state_name = "root"

        # Reproduction steps
        reproduction_steps = _extract_reproduction_steps(test_logs, target_step)

        # Severity
        severity = _derive_severity(category, primary)

        # Root cause & remediation from templates
        root_cause = _ROOT_CAUSE_TEMPLATES.get(category, "")
        if not root_cause:
            root_cause = (
                f"Defect in category '{category}' detected during automated testing. "
                f"further manual analysis required to determine root cause."
            )

        remediation = _REMEDIATION_TEMPLATES.get(category, "")
        if not remediation:
            remediation = (
                f"Investigate and resolve the root cause of this {category} defect. "
                f"Review code changes, error logs, and user flows associated with the target URL."
            )

        # Enhance description from primary defect
        description = (primary.get("description", "") or primary.get("message", "") or "")[:500]
        if not description:
            description = f"Defect of type '{defect_type}' detected on {url} at step {target_step}."

        # Impact label
        impact_map = {
            "CRITICAL": "Potential Data Corruption / Security Exploit",
            "HIGH": "Functional Breakage / User Experience Degradation",
            "MEDIUM": "Reliability Issue / Partial Feature Failure",
            "LOW": "Visual Glitch / Minor Inconsistency",
        }
        impact = impact_map.get(severity, "")

        # Screenshot extraction from test_logs
        after_screenshot = None
        before_screenshot = None
        if target_step in step_to_log:
            log = step_to_log[target_step]
            if log.get("screenshot"):
                after_screenshot = log["screenshot"]
            if log.get("before_screenshot"):
                before_screenshot = log["before_screenshot"]

        # Build the ticket
        uid = f"DEFECT-{ticket_counter:03d}"
        ticket_counter += 1

        ticket = DefectTicket(
            defect_uid=uid,
            category=category,
            severity=severity,
            title=title,
            description=description,
            target_url=url,
            page_state_name=page_state_name,
            target_selector=target_selector,
            html_snippet=html_snippet,
            reproduction_steps=reproduction_steps,
            before_screenshot=before_screenshot,
            after_screenshot=after_screenshot,
            root_cause_analysis=root_cause,
            remediation_instruction=remediation,
            raw_defects=[d for _, d in sorted_defects],
            impact=impact,
            discovered_context_url=url,
        )
        tickets.append(ticket)

    # Sort tickets by severity (CRITICAL first)
    severity_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    tickets.sort(key=lambda t: severity_order.get(t.severity, 4))

    return tickets


def _compile_defect_tickets(
    defects: Any, test_logs: List[Dict[str, Any]]
) -> List[DefectTicket]:
    """Compile all raw defects into structured DefectTickets with remediation blueprints.

    Cross-references test_logs to reconstruct reproduction sequences for each defect.
    Classifies severity via heuristic type→severity mapping and content analysis.
    Generates root_cause_analysis and remediation_instruction from template matchers.
    """
    return _group_defects(defects, test_logs)


def generate_markdown_report(
    settings: Any,
    defects: Any,
    test_logs: List[Dict[str, Any]],
    browser_launch_info: Dict[str, Any],
    start_time: datetime,
    end_time: datetime,
) -> None:
    """Generate test_report.md in the output directory."""
    duration_seconds = (end_time - start_time).total_seconds()
    total_steps = len(test_logs)

    accountability = summarize_vibe_coding_accountability(defects)

    # Collect step numbers with HIGH/MEDIUM/CRITICAL application defects
    defect_step_numbers: set[int] = set()
    defect_categories_for_steps = [
        "security_risks", "context_anomalies", "ux_flow_freezes",
        "validation_failures", "race_findings", "boundary_drift",
        "console_findings", "accessibility_violations",
    ]
    for cat in defect_categories_for_steps:
        collection = getattr(defects, cat, None)
        if not collection:
            continue
        for d in collection:
            severity = _derive_severity(cat, d)
            if severity in ("CRITICAL", "HIGH", "MEDIUM"):
                step_num = d.get("step")
                if step_num is not None:
                    defect_step_numbers.add(step_num)

    # Failed steps = explicitly FAILED/CRASH + steps with app defects
    failed_steps_set: set[int] = set()
    for log in test_logs:
        if log["status"] in ["FAILED", "CRASH"]:
            failed_steps_set.add(log.get("step", 0))
    failed_steps_set.update(defect_step_numbers)

    failed_steps_count = len(failed_steps_set)
    success_rate = ((total_steps - failed_steps_count) / total_steps * 100) if total_steps > 0 else 0
    failed_steps_list = [log for log in test_logs if log["status"] in ["FAILED", "CRASH"]]

    md_content = f"""# Deep Inspection Monkey Test Report

**Target URL:** {settings.target_url}  
**Date:** {start_time.strftime('%Y-%m-%d %H:%M:%S')}  
**Duration:** {duration_seconds:.2f} seconds  
**Total Steps:** {total_steps}  
**Success Rate:** {success_rate:.2f}%  
**Errors Found:** {failed_steps_count}  
**Application Defects (HIGH/MEDIUM/CRITICAL):** {accountability.get('app_defect_count', 0)}  
**Sandbox Policy:** {"strict" if settings.strict_sandbox else "sandbox-first"}  
**No-Sandbox Fallback:** {"enabled" if settings.allow_no_sandbox_fallback else "disabled"}  
**Browser Launch Mode:** {browser_launch_info.get('mode', 'unknown')}  
**Run Summary Status:** {accountability.get('run_summary_status')}  
**Regression Drift Index:** {accountability.get('regression_drift_index')}%  
**Graceful Shutdown:** {"requested" if browser_launch_info.get('graceful_shutdown_requested') else "not requested"}  
**Output Folder:** `{settings.output_dir}`

## Summary
The agent performed {total_steps} actions using **{settings.ollama_model}**.
Actions included: Clicking, Typing, Form Submission, Modal Handling, and State Escapes.
"""

    if failed_steps_list:
        md_content += "\n## Errors Detected\n"
        for log in failed_steps_list:
            md_content += f"\n### Step {log['step']}: {log['action']} failed\n"
            md_content += f"- **Target:** `{log['target']}`\n"
            md_content += f"- **Error:** `{log['error']}`\n"
            if log["screenshot"]:
                md_content += f"- **Screenshot:** `![Screenshot](./{log['screenshot']})`\n"

    # ── Remediation Blueprints (compiled DefectTickets) ────────────────
    compiled_tickets = _compile_defect_tickets(defects, test_logs)
    if compiled_tickets:
        md_content += "\n---\n\n"
        md_content += "# 🔧 Engineering Defect Tickets — Remediation Blueprints\n\n"

        # Severity summary
        sev_counts: Dict[str, int] = {}
        for t in compiled_tickets:
            sev_counts[t.severity] = sev_counts.get(t.severity, 0) + 1
        sev_line_parts = []
        for sev in ["CRITICAL", "HIGH", "MEDIUM", "LOW"]:
            count = sev_counts.get(sev, 0)
            if count:
                icon = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "⚠️ ", "LOW": "ℹ️ "}[sev]
                sev_line_parts.append(f"{icon} {sev}: {count}")
        md_content += f"**Defect Summary:** {', '.join(sev_line_parts)} (Total: {len(compiled_tickets)})\n\n"

        # Quick-reference table
        md_content += "| UID | Severity | Category | Title | Target URL |\n"
        md_content += "|---|---|---|---|---|\n"
        for t in compiled_tickets:
            title_trunc = t.title[:60] + "..." if len(t.title) > 60 else t.title
            url_trunc = t.target_url[:50] + "..." if len(t.target_url) > 50 else t.target_url
            md_content += (
                f"| `{t.defect_uid}` | {t.severity} | {t.category} | {title_trunc} | {url_trunc} |\n"
            )

        # Detailed ticket cards
        md_content += "\n---\n\n"
        for t in compiled_tickets:
            md_content += t.to_markdown() + "\n\n"
            # Append machine-readable agent_context JSON block for significant defects
            if t.severity in ("CRITICAL", "HIGH", "MEDIUM"):
                md_content += "\n```json\n"
                md_content += json.dumps(t.agent_context_block(), indent=2)
                md_content += "\n```\n"
            md_content += "\n---\n\n"
    else:
        md_content += "\n---\n\n# 🔧 Engineering Defect Tickets\n\n**No defects detected.** ✅\n\n---\n\n"

    md_content += "## Security Risks\n"
    if defects.security_risks:
        for item in defects.security_risks:
            md_content += f"- Step {item['step']}: {item['type']} on `{item.get('target', '')}` at {item['url']}\n"
    else:
        md_content += "- None detected.\n"

    md_content += "\n### ⚠️ Vibe Coding Drift Summary\n"
    md_content += (
        f"- Regression Drift Index: {accountability.get('regression_drift_index', 0.0)}% "
        f"({accountability.get('total_missing_historical_components', 0)} missing / "
        f"{accountability.get('total_expected_baseline_components', 0)} expected historical baseline components)\n"
    )
    md_content += f"- Run Summary Status: {accountability.get('run_summary_status', 'UNKNOWN')}\n"

    drift_details = accountability.get("drift_details", [])
    if drift_details:
        for detail in drift_details:
            route_display = f"{detail.get('domain', '')}{detail.get('page_route', '')}"
            md_content += (
                f"- Route {route_display}: historical golden baseline expected "
                f"{detail.get('expected_baseline_components', 0)} interactive components, "
                f"but {detail.get('missing_count', 0)} have now vanished or broken in the current deployment.\n"
            )

            broken_selectors = detail.get("broken_selectors", [])
            if broken_selectors:
                selector_line = ", ".join([str(x) for x in broken_selectors[:15]])
                md_content += f"  - Broken selectors now failing: {selector_line}\n"

            for missing in detail.get("missing_component_contrast", [])[:15]:
                selector_hint = str(missing.get("selector_hint", ""))
                kind = str(missing.get("kind", ""))
                tag = str(missing.get("tag", ""))
                text = str(missing.get("text", ""))
                md_content += (
                    f"  - Historical component no longer operating: selector={selector_hint or 'n/a'}, "
                    f"kind={kind or 'n/a'}, tag={tag or 'n/a'}, text={text or 'n/a'}\n"
                )
    else:
        md_content += "- No missing historical components were detected against golden baselines in this run.\n"

    # ── NEW: Compiled accessibility violations dashboard ────────────────
    compiled_a11y = _compile_accessibility_violations(defects.accessibility_violations)
    md_content += "\n## Accessibility Violations\n"

    if not compiled_a11y["total_raw_violations"]:
        md_content += "- **None detected.** ✅\n"
    else:
        md_content += "### Summary\n\n"
        md_content += f"- **Total raw violations found:** {compiled_a11y['total_raw_violations']}\n"
        md_content += f"- **Unique rules after deduplication:** {compiled_a11y['unique_rules_found']}\n"
        md_content += f"- **Critical count:** {compiled_a11y['severity_totals']['critical']}\n"
        md_content += f"- **Serious count:** {compiled_a11y['severity_totals']['serious']}\n"
        md_content += f"- **Impact Score (weighted):** {compiled_a11y['impact_score']}\n\n"

    if compiled_a11y["rules"]:
        # ── Quick-reference table ───────────────────────────────────
        md_content += "| Rule ID | Impact | Critical | Serious | Impact Score | First Seen |\n"
        md_content += "|---|---|---|---|---|---|\n"
        for r in compiled_a11y["rules"]:
            first_step = r["occurrence_steps"][0] if r["occurrence_steps"] else "?"
            md_content += (
                f"| `{r['id']}` | **{r['impact'].upper()}** "
                f"| {r['severity_distribution']['critical']} | {r['severity_distribution']['serious']} "
                f"| {r['impact_score_contribution']} | Step {first_step} |\n"
            )

        # ── Detailed per-rule cards ─────────────────────────────────
        md_content += "\n### Detailed Findings\n\n"
        for r in compiled_a11y["rules"]:
            impact_icon = "🔴" if r["impact"] == "critical" else "🟠"
            md_content += f"#### {impact_icon} `{r['id']}` — {r['description']} ({r['impact'].upper()})\n\n"

            md_content += f"- **Description:** {r['description']}\n"
            if r.get("helpUrl"):
                md_content += f"- **Documentation:** [{r['help']}]({r['helpUrl']})\n"
            md_crit = r["severity_distribution"].get("critical", 0)
            md_ser = r["severity_distribution"].get("serious", 0)
            md_content += f"- **Occurrences:** {md_crit} critical, {md_ser} serious\n"

            if r.get("unique_selectors"):
                md_content += f"\n**Affected Selectors ({len(r['unique_selectors'])} unique):**\n\n"
                for sel in r["unique_selectors"][:10]:  # Cap display
                    md_content += f"- `{sel}`\n"

            if r.get("html_snippets"):
                md_content += "\n**Sample HTML (first occurrence):**\n\n"
                for snippet in r["html_snippets"]:
                    md_content += f"```html\n{snippet}\n```\n"
                    break  # Only first sample

            if r.get("remediation_advice"):
                md_content += f"\n**Remediation:** {r['remediation_advice']}\n"

            md_content += "\n---\n\n"

    md_content += "\n## Performance Bottlenecks\n"
    if defects.performance_bottlenecks:
        for item in defects.performance_bottlenecks:
            md_content += f"- Step {item['step']}: {item['type']} ({item.get('duration_ms', item.get('heap_delta_bytes', item.get('fps')) )})\n"
    else:
        md_content += "- None detected.\n"

    md_content += "\n## Visual Regressions\n"
    visual_items = defects.visual_regressions + defects.layout_instability
    if visual_items:
        for item in visual_items:
            md_content += f"- Step {item['step']}: {item['type']} on {item['url']}\n"
    else:
        md_content += "- None detected.\n"

    md_content += "\n## Baseline Regressions\n"
    if defects.regression_findings:
        for item in defects.regression_findings:
            md_content += (
                f"- Step {item['step']}: [{item['severity']}] {item['type']} "
                f"at {item['domain']}{item['page_route']} "
                f"(missing: {len(item.get('missing_components', []))})\n"
            )
    else:
        md_content += "- None detected.\n"

    # ── Smart observation sensor reports ──────────────────────
    md_content += "\n## Context Anomalies\n"
    if defects.context_anomalies:
        for item in defects.context_anomalies[:50]:
            action_ctx = item.get("action", "") or "?"
            step_num = item.get("step", "?")
            anomaly_type = item.get("type", "unknown")
            message = (item.get("message", "") or "")[:200]
            md_content += f"- Step {step_num}: [{anomaly_type}] on action `{action_ctx}` at {item.get('url', '?')}\n"
            if message:
                md_content += f"  - `{message}`\n"
    else:
        md_content += "- None detected.\n"

    md_content += "\n## UX Flow Freezes\n"
    if defects.ux_flow_freezes:
        for item in defects.ux_flow_freezes[:50]:
            step_num = item.get("step", "?")
            desc = (item.get("description", "") or "")[:250]
            md_content += f"- Step {step_num}: `{desc}`\n"
    else:
        md_content += "- None detected.\n"

    md_content += "\n## Validation Failures\n"
    if defects.validation_failures:
        for item in defects.validation_failures[:50]:
            step_num = item.get("step", "?")
            probe = item.get("probe_name", "") or "unknown"
            target = item.get("target", "") or "?"
            fail_type = item.get("type", "unknown")
            desc = (item.get("description", "") or "")[:250]
            md_content += f"- Step {step_num}: [{fail_type}] field `{target}` probe `{probe}` — `{desc}`\n"
    else:
        md_content += "- None detected.\n"

    telemetry = summarize_semantic_memory_telemetry(test_logs)
    retrieval = telemetry.get("retrieval", {})
    write = telemetry.get("write", {})
    providers = telemetry.get("providers", {})

    md_content += "\n## Semantic Memory Telemetry\n"
    md_content += f"- Retrieval events: {retrieval.get('events', 0)} (ok: {retrieval.get('ok', 0)})\n"
    md_content += f"- Avg retrieval total: {retrieval.get('avg_total_ms', 0.0)} ms\n"
    md_content += f"- Avg Qdrant search: {retrieval.get('avg_qdrant_search_ms', 0.0)} ms\n"
    md_content += f"- Avg rerank: {retrieval.get('avg_rerank_ms', 0.0)} ms\n"
    md_content += f"- Avg memories returned: {retrieval.get('avg_returned_count', 0.0)}\n"
    md_content += f"- Rerank applied count: {retrieval.get('rerank_applied_count', 0)}\n"
    md_content += f"- Write events: {write.get('events', 0)} (ok: {write.get('ok', 0)})\n"
    md_content += f"- Avg write total: {write.get('avg_total_ms', 0.0)} ms\n"
    md_content += f"- Avg Qdrant upsert: {write.get('avg_qdrant_upsert_ms', 0.0)} ms\n"
    if providers:
        provider_line = ", ".join([f"{name}: {count}" for name, count in sorted(providers.items())])
        md_content += f"- Providers observed: {provider_line}\n"
    else:
        md_content += "- Providers observed: none\n"

    md_content += "\n## Action Log\n\n| Step | Action | Target | Status |\n|---|---|---|---|\n"
    for log in test_logs:
        icon = "✅" if log["status"] == "SUCCESS" else "❌"
        md_content += f"| {log['step']} | {log['action']} | {log['target'][:30]}... | {icon} |\n"

    report_path = os.path.join(settings.output_dir, "test_report.md")
    redacted_md_content = redact_sensitive_content(md_content)
    _secure_atomic_write(report_path, redacted_md_content, mode=0o640)

    print(f"\n📄 Report generated: {report_path}")
    print(f"💾 All artifacts saved in: {settings.output_dir}")


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
            # Keep raw array for backward compatibility:
            "accessibility_violations_raw": defects.accessibility_violations,
            # New compiled/deduplicated format for CI/CD gates and human reading:
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
        },
        # Compiled Defect Tickets with Remediation Blueprints (for coding agents)
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


def generate_pdf_report(
    settings: Any,
    defects: Any,
    test_logs: List[Dict[str, Any]],
    start_time: datetime,
    end_time: datetime,
) -> None:
    """Build a sleek executive PDF audit report using ReportLab."""
    if not settings.pdf_generate:
        return
    if not _REPORTLAB_AVAILABLE:
        print("⚠️ PDF_GENERATE=true but reportlab is not installed; skipping PDF audit report.")
        return

    try:
        pdf_path = os.path.join(settings.output_dir, "test_execution_audit.pdf")
        doc = SimpleDocTemplate(
            pdf_path,
            pagesize=letter,
            rightMargin=0.6 * inch,
            leftMargin=0.6 * inch,
            topMargin=0.8 * inch,
            bottomMargin=0.8 * inch,
        )
        styles = getSampleStyleSheet()
        story: List[Any] = []

        def _xml_escape(text: str) -> str:
            return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;")

        accountability = summarize_vibe_coding_accountability(defects)
        duration_seconds = (end_time - start_time).total_seconds()
        total_steps = len(test_logs)

        # Collect step numbers with HIGH/MEDIUM/CRITICAL application defects
        defect_step_numbers_pdf: set[int] = set()
        for cat in ["security_risks", "context_anomalies", "ux_flow_freezes",
                     "validation_failures", "race_findings", "boundary_drift",
                     "console_findings", "accessibility_violations"]:
            collection = getattr(defects, cat, None)
            if not collection:
                continue
            for d in collection:
                severity = _derive_severity(cat, d)
                if severity in ("CRITICAL", "HIGH", "MEDIUM"):
                    step_num = d.get("step")
                    if step_num is not None:
                        defect_step_numbers_pdf.add(step_num)

        failed_pdf_set: set[int] = set()
        for log in test_logs:
            if log["status"] in ["FAILED", "CRASH"]:
                failed_pdf_set.add(log.get("step", 0))
        failed_pdf_set.update(defect_step_numbers_pdf)
        failed_steps_count_pdf = len(failed_pdf_set)
        success_rate = ((total_steps - failed_steps_count_pdf) / total_steps * 100) if total_steps > 0 else 0.0

        # Header Block
        story.append(Paragraph("MonkeyLM Executive Quality Audit", styles["Title"]))
        story.append(Spacer(1, 0.15 * inch))
        story.append(Paragraph(f"<b>Target URL:</b> {_xml_escape(settings.target_url)}", styles["Normal"]))
        story.append(Paragraph(f"<b>Execution Seed:</b> {_xml_escape(settings.active_seed or 'none')}", styles["Normal"]))
        story.append(Paragraph(f"<b>Run Date:</b> {start_time.strftime('%Y-%m-%d %H:%M:%S')}", styles["Normal"]))
        story.append(Paragraph(f"<b>Duration:</b> {duration_seconds:.2f} seconds", styles["Normal"]))
        story.append(Spacer(1, 0.2 * inch))

        # Summary metric table
        summary_data = [
            ["Metric", "Value"],
            ["Total Steps", str(total_steps)],
            ["Failed / Crashed Steps", str(failed_steps_count_pdf)],
            ["Success Rate", f"{success_rate:.2f}%"],
            ["Workers", str(settings.workers)],
            ["Regression Drift Index", f"{accountability.get('regression_drift_index', 0.0)}%"],
            ["Run Summary Status", str(accountability.get("run_summary_status", "UNKNOWN"))],
        ]
        summary_table = Table(summary_data, colWidths=[3.0 * inch, 3.0 * inch])
        summary_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                    ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, 0), 11),
                    ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
                    ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#f8f9fa")),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ]
            )
        )
        story.append(summary_table)
        story.append(Spacer(1, 0.3 * inch))

        # ─── Unified Defect Audit Cards with Inline Screenshots ──────────
        card_width = 7.8 * inch

        header_critical_style = ParagraphStyle(
            "auditHeaderCritical", parent=styles["BodyText"], fontName="Helvetica-Bold", textColor=colors.whitesmoke, fontSize=10, leading=13
        )
        header_serious_style = ParagraphStyle(
            "auditHeaderSerious", parent=styles["BodyText"], fontName="Helvetica-Bold", textColor=colors.whitesmoke, fontSize=10, leading=13
        )
        header_warning_style = ParagraphStyle(
            "auditHeaderWarning", parent=styles["BodyText"], fontName="Helvetica-Bold", textColor=colors.whitesmoke, fontSize=10, leading=13
        )
        header_info_style = ParagraphStyle(
            "auditHeaderInfo", parent=styles["BodyText"], fontName="Helvetica-Bold", textColor=colors.whitesmoke, fontSize=10, leading=13
        )
        selector_style = ParagraphStyle(
            "auditSelector", parent=styles["BodyText"], fontName="Courier", fontSize=8, leading=11, textColor=colors.HexColor("#2c3e50")
        )
        code_block_style = ParagraphStyle(
            "auditCodeBlock",
            parent=styles["BodyText"],
            fontName="Courier",
            fontSize=7.5,
            leading=9,
            wordWrap="CJK",
            textColor=colors.HexColor("#333333"),
        )
        remediation_style = ParagraphStyle(
            "auditRemediation", parent=styles["BodyText"], fontName="Helvetica", fontSize=9, leading=12, textColor=colors.HexColor("#27ae60")
        )
        description_style = ParagraphStyle(
            "auditDescription",
            parent=styles["BodyText"],
            fontName="Helvetica-Oblique",
            fontSize=8.5,
            leading=11,
            textColor=colors.HexColor("#555555"),
        )

        def _severity_color(severity: str) -> tuple:
            sev = (severity or "").lower()
            if sev in {"critical", "error"}:
                return header_critical_style, colors.HexColor("#c0392b")
            if sev in {"serious", "high", "failed", "crash"}:
                return header_serious_style, colors.HexColor("#d35400")
            if sev in {"warning", "moderate"}:
                return header_warning_style, colors.HexColor("#f39c12")
            return header_info_style, colors.HexColor("#2c3e50")

        def _build_audit_card(item: Dict[str, Any], category_label: str) -> tuple:
            step = item.get("step", "n/a")
            item_type = item.get("type", "unknown")
            severity = item.get("severity", "info")
            selector = item.get("selector", "(none)")
            html_snippet = item.get("html_snippet", "")
            failure_reason = item.get("failure_reason", "")
            remediation_advice = item.get("remediation_advice", "Manual review required.")
            url = item.get("url", "")
            screenshot_basename = item.get("screenshot_path", "")

            header_style, header_bg = _severity_color(severity)
            header_text = f"[{severity.upper()}] {category_label}: Step {step} — {item_type}"

            row_specs: List[tuple] = []
            row_specs.append((Paragraph(header_text, header_style), header_bg))

            selector_line_parts = [f"Selector: {_xml_escape(selector)}"]
            if url:
                selector_line_parts.append(f"URL: {_xml_escape(url)}")
            row_specs.append((Paragraph(" | ".join(selector_line_parts), selector_style), colors.white))

            if html_snippet:
                truncated_html = _xml_escape(html_snippet[:400])
                if len(html_snippet) > 400:
                    truncated_html += " ... (truncated)"
                row_specs.append((Paragraph(truncated_html, code_block_style), colors.HexColor("#f0f0f0")))

            if not html_snippet and failure_reason:
                row_specs.append(
                    (Paragraph(_xml_escape(failure_reason[:300]), description_style), colors.HexColor("#fafafa"))
                )

            if remediation_advice and remediation_advice != "Manual review required.":
                row_specs.append(
                    (
                        Paragraph(f"\U0001f6e0\ufe0f REMEDIATION TASK: {_xml_escape(remediation_advice)}", remediation_style),
                        colors.HexColor("#f0fff4"),
                    )
                )

            card_rows = [[cell] for cell, _ in row_specs]
            ticket_table = Table(card_rows, colWidths=[card_width], repeatRows=0)

            style_cmds = [
                ("BOX", (0, 0), (-1, -1), 1.0, colors.HexColor("#bdc3c7")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d5dbdb")),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
            for row_idx, (_cell, bg_color) in enumerate(row_specs):
                style_cmds.append(("BACKGROUND", (0, row_idx), (0, row_idx), bg_color))

            ticket_table.setStyle(TableStyle(style_cmds))
            return ticket_table, screenshot_basename

        def _build_scaled_image(basename: str, max_width: float = 6.5) -> Optional[Any]:
            if not basename:
                return None
            image_path = os.path.join(settings.output_dir, basename)
            if not os.path.exists(image_path):
                return None

            try:
                img_width = max_width * inch
                img_height = 4.0 * inch
                if Image is not None:
                    with Image.open(image_path) as img:
                        orig_w, orig_h = img.size
                        aspect = orig_h / max(1, orig_w)
                        img_height = min(img_width * aspect, 3.5 * inch)
                return RLImage(image_path, width=img_width, height=img_height)
            except Exception:
                return None

        embedded_screenshots: set = set()

        all_defect_sections = [
            ("Security Risks", defects.security_risks),
            ("Accessibility Violations", defects.accessibility_violations),
            ("Performance Bottlenecks", defects.performance_bottlenecks),
            ("Baseline Regressions", defects.regression_findings),
            ("Visual Regressions", defects.visual_regressions),
            ("Layout Instability", defects.layout_instability),
            ("Race Findings", defects.race_findings),
            ("Console Findings", defects.console_findings),
            ("Boundary Drift", defects.boundary_drift),
            ("Context Anomalies", defects.context_anomalies),
            ("UX Flow Freezes", defects.ux_flow_freezes),
            ("Validation Failures", defects.validation_failures),
        ]

        any_defects = False
        for category_label, items in all_defect_sections:
            if not items:
                continue
            any_defects = True
            story.append(Paragraph(category_label, styles["Heading3"]))
            story.append(Spacer(1, 0.05 * inch))

            for item in items[:50]:
                ticket_table, screenshot_basename = _build_audit_card(item, category_label)
                story.append(ticket_table)

                if screenshot_basename:
                    embedded_screenshots.add(screenshot_basename)
                    img_flowable = _build_scaled_image(screenshot_basename)
                    if img_flowable is not None:
                        story.append(Spacer(1, 0.05 * inch))
                        story.append(img_flowable)

                story.append(Spacer(1, 0.1 * inch))

            story.append(Spacer(1, 0.15 * inch))

        if not any_defects:
            story.append(Paragraph("Defect Logs", styles["Heading2"]))
            story.append(Spacer(1, 0.1 * inch))
            story.append(Paragraph("No defects detected during this run.", styles["BodyText"]))
            story.append(Spacer(1, 0.2 * inch))

        # ── Visual Proof Plates ──────────────────────────────────────────
        # Only include screenshots for non-SUCCESS status (errors/problems/issues)
        # to keep the audit report focused on items that need attention.
        annotated_logs = [
            log for log in test_logs 
            if (log.get("screenshot_annotated") or str(log.get("screenshot", "")).endswith("_annotated.png"))
            and log.get("status", "UNKNOWN") != "SUCCESS"
        ]
        proof_plate_logs = [log for log in annotated_logs if log.get("screenshot", "") not in embedded_screenshots]

        if proof_plate_logs:
            story.append(PageBreak())
            story.append(Paragraph("Visual Proof Plates", styles["Heading2"]))
            story.append(Spacer(1, 0.1 * inch))

            for log in proof_plate_logs:
                screenshot_name = log.get("screenshot", "")
                if not screenshot_name:
                    continue
                image_path = os.path.join(settings.output_dir, screenshot_name)
                if not os.path.exists(image_path):
                    continue

                step = log.get("step", "n/a")
                status = log.get("status", "UNKNOWN")
                action = log.get("action", "")
                target = log.get("target", "")
                error = log.get("error", "")
                story.append(
                    Paragraph(f"Step {step}: {_xml_escape(action)} on '{_xml_escape(target)}' — status {status}", styles["Heading3"])
                )
                if error:
                    story.append(Paragraph(f"<font color='red'>Error:</font> {_xml_escape(error[:200])}", styles["BodyText"]))

                img_flowable = _build_scaled_image(screenshot_name)
                if img_flowable is not None:
                    story.append(img_flowable)
                else:
                    story.append(Paragraph("⚠️ Screenshot not available", styles["BodyText"]))
                story.append(Spacer(1, 0.15 * inch))

        doc.build(story)
        # Enforce restrictive file permissions for PDF output
        os.chmod(pdf_path, 0o640)
        print(f"📄 PDF audit report generated: {pdf_path}")
    except Exception as exc:
        print(f"⚠️ PDF generation failed: {exc}")


def generate_interactive_html_report(
    settings: Any,
    defects: Any,
    test_logs: List[Dict[str, Any]],
    start_time: datetime,
    end_time: datetime,
) -> str | None:
    """Generate interactive single-file HTML accessibility dashboard with embedded CSS/JS.

    Returns file path written or None if no violations exist.
    Features: metric cards grid, quick-reference table, collapsible rule cards,
             inline vanilla JS for toggle functionality (no external deps).
    """
    if not getattr(defects, "accessibility_violations", None):
        return None

    compiled = _compile_accessibility_violations(defects.accessibility_violations)
    rules = compiled.get("rules", [])
    if not rules:
        return None

    # Escape HTML for safe embedding
    def esc(text: str) -> str:
        return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

    # Collect metadata from test_logs for target URL
    target_url = "unknown"
    for log in reversed(test_logs):
        if isinstance(log, dict) and log.get("url"):
            target_url = log["url"]
            break

    timestamp_str = end_time.strftime("%Y-%m-%d %H:%M:%S")
    critical_count = compiled.get("severity_totals", {}).get("critical", 0)
    serious_count = compiled.get("severity_totals", {}).get("serious", 0)
    impact_score = compiled.get("impact_score", 0)
    total_raw = compiled.get("total_raw_violations", 0)
    unique_rules = compiled.get("unique_rules_found", 0)

    # Build collapsible rule cards HTML
    rules_html_parts = []
    for idx, rule in enumerate(rules):
        rule_id = esc(str(rule.get("id", "unknown")))
        description = esc(rule.get("description", "")) or "No description available."
        help_url = str(rule.get("helpUrl", "") or "")
        impact = esc(str(rule.get("impact", "N/A")))
        severity_dist = rule.get("severity_distribution", {})
        critical_rule = severity_dist.get("critical", 0)
        serious_rule = severity_dist.get("serious", 0)
        moderate_rule = severity_dist.get("moderate", 0)
        minor_rule = severity_dist.get("minor", 0)
        rule_score = rule.get("impact_score_contribution", 0)
        # occurrence_steps is List[int] from _compile_accessibility_violations
        first_seen = rule.get("occurrence_steps", [])[0] if rule.get("occurrence_steps") else "?"

        # Build selectors list
        selectors_html = ""
        for sel in rule.get("unique_selectors", [])[:10]:  # Limit to 10 selectors
            selectors_html += f'<li style="margin-bottom:4px; font-family:monospace; background:#f5f5f5; padding:4px 8px; border-radius:3px;">{esc(sel)}</li>\n        '

        # Build HTML snippets
        snippets_html = ""
        for snippet in rule.get("html_snippets", [])[:5]:  # Limit to 5 snippets
            snippets_html += f'<div style="background:#fff3cd; padding:6px 10px; border-radius:4px; margin-bottom:6px; font-family:monospace; font-size:12px; overflow-x:auto;">{esc(snippet)}</div>\n        '

        remediation = esc(rule.get("remediation_advice", "No specific guidance available."))

        card_html = f'''
    <div class="rule-card">
      <div class="rule-header" onclick="toggleRule({idx})" style="cursor:pointer;">
        <span class="rule-title">
          <span class="impact-badge impact-{str(rule.get("impact", "")).lower()}">{impact}</span>
          {rule_id}
        </span>
        <span class="rule-meta">Critical: {critical_rule} | Serious: {serious_rule} | Score: {rule_score:.0f}</span>
        <span class="toggle-icon" id="toggle-{idx}">▼</span>
      </div>
      <div class="rule-body" id="rule-body-{idx}" style="display:none;">
        <p><strong>Description:</strong> {description}</p>
        {'<p><a href="' + help_url + '" target="_blank" rel="noopener noreferrer" style="color:#0066cc;">📖 View WCAG Documentation</a></p>' if help_url else ''}
        <div class="detail-section">
          <h4>Affected Selectors ({len(rule.get("unique_selectors", []))})</h4>
          <ul>{selectors_html}</ul>
        </div>
        {'<div class="detail-section"><h4>HTML Snippets</h4>' + snippets_html + '</div>' if snippets_html else ''}
        <div class="detail-section">
          <h4>Remediation Advice</h4>
          <p>{remediation}</p>
        </div>
        <p style="color:#666; font-size:12px;"><strong>First Seen:</strong> Step {first_seen}</p>
      </div>
    </div>'''
        rules_html_parts.append(card_html)

    rules_html = "\n".join(rules_html_parts)

    # Build quick-reference table rows
    table_rows = ""
    for rule in rules:
        rule_id = esc(str(rule.get("id", "unknown")))
        impact = esc(str(rule.get("impact", "N/A")))
        severity_dist = rule.get("severity_distribution", {})
        critical_rule = severity_dist.get("critical", 0)
        serious_rule = severity_dist.get("serious", 0)
        rule_score = rule.get("impact_score_contribution", 0)
        # occurrence_steps is List[int] from _compile_accessibility_violations
        first_seen = rule.get("occurrence_steps", [])[0] if rule.get("occurrence_steps") else "?"

        table_rows += f'''<tr>
          <td><code>{rule_id}</code></td>
          <td><span class="impact-badge impact-{impact.lower()}">{impact}</span></td>
          <td style="text-align:center; font-weight:bold; color:#dc3545 if critical_rule > 0 else '#666';">{critical_rule}</td>
          <td style="text-align:center;">{serious_rule}</td>
          <td style="text-align:center; font-weight:bold;">{rule_score:.0f}</td>
          <td>{first_seen}</td>
        </tr>\n'''

    html_content = f'''<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>MonkeyLM Accessibility Audit Report</title>
  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      background: #f5f7fa;
      color: #333;
      line-height: 1.6;
    }}
    .container {{
      max-width: 1200px;
      margin: 0 auto;
      padding: 20px;
    }}
    header {{
      background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
      color: white;
      padding: 30px;
      border-radius: 8px;
      margin-bottom: 20px;
    }}
    header h1 {{ font-size: 28px; margin-bottom: 10px; }}
    header p {{ opacity: 0.9; font-size: 14px; }}
    header a {{ color: #ffd700; text-decoration: none; }}
    .metrics-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 15px;
      margin-bottom: 20px;
    }}
    .metric-card {{
      background: white;
      padding: 20px;
      border-radius: 8px;
      box-shadow: 0 2px 4px rgba(0,0,0,0.1);
      text-align: center;
    }}
    .metric-card .value {{
      font-size: 36px;
      font-weight: bold;
      color: #667eea;
    }}
    .metric-card .label {{
      font-size: 12px;
      color: #666;
      text-transform: uppercase;
      letter-spacing: 1px;
    }}
    .metric-card.critical .value {{ color: #dc3545; }}
    .metric-card.serious .value {{ color: #ffc107; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: white;
      border-radius: 8px;
      overflow: hidden;
      box-shadow: 0 2px 4px rgba(0,0,0,0.1);
      margin-bottom: 20px;
    }}
    th, td {{
      padding: 12px 15px;
      text-align: left;
      border-bottom: 1px solid #eee;
    }}
    th {{
      background: #667eea;
      color: white;
      font-weight: 600;
      text-transform: uppercase;
      font-size: 12px;
    }}
    .impact-badge {{
      display: inline-block;
      padding: 3px 8px;
      border-radius: 12px;
      font-size: 11px;
      font-weight: bold;
      color: white;
    }}
    .impact-critical {{ background: #dc3545; }}
    .impact-serious {{ background: #ffc107; color: #333; }}
    .impact-moderate {{ background: #fd7e14; }}
    .impact-minor {{ background: #28a745; }}
    .rule-card {{
      background: white;
      border-radius: 8px;
      margin-bottom: 10px;
      box-shadow: 0 2px 4px rgba(0,0,0,0.1);
      overflow: hidden;
    }}
    .rule-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 15px 20px;
    }}
    .rule-title {{
      font-weight: bold;
      font-size: 16px;
    }}
    .rule-meta {{ color: #666; font-size: 13px; }}
    .toggle-icon {{ transition: transform 0.3s; }}
    .detail-section {{
      margin-top: 12px;
      padding: 12px;
      background: #f8f9fa;
      border-radius: 6px;
    }}
    .detail-section h4 {{
      font-size: 13px;
      color: #667eea;
      margin-bottom: 8px;
    }}
    footer {{
      text-align: center;
      padding: 20px;
      color: #666;
      font-size: 12px;
    }}
    @media print {{
      .rule-card .rule-body {{ display: block !important; }}
      .toggle-icon {{ display: none; }}
    }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <h1>🔍 MonkeyLM Accessibility Audit Report</h1>
      <p><strong>Target URL:</strong> <a href="{esc(target_url)}" target="_blank">{esc(target_url)}</a></p>
      <p><strong>Generated:</strong> {timestamp_str} | <strong>Total Steps Tested:</strong> {len(test_logs) if test_logs else 0}</p>
    </header>

    <div class="metrics-grid">
      <div class="metric-card">
        <div class="value">{total_raw}</div>
        <div class="label">Raw Violations</div>
      </div>
      <div class="metric-card">
        <div class="value">{unique_rules}</div>
        <div class="label">Unique Rules</div>
      </div>
      <div class="metric-card critical">
        <div class="value">{critical_count}</div>
        <div class="label">Critical</div>
      </div>
      <div class="metric-card serious">
        <div class="value">{serious_count}</div>
        <div class="label">Serious</div>
      </div>
      <div class="metric-card">
        <div class="value">{impact_score:.0f}</div>
        <div class="label">Impact Score</div>
      </div>
    </div>

    <h2 style="margin-bottom:15px;">📊 Quick Reference Table</h2>
    <table>
      <thead>
        <tr>
          <th>Rule ID</th>
          <th>Impact</th>
          <th style="text-align:center;">Critical</th>
          <th style="text-align:center;">Serious</th>
          <th style="text-align:center;">Score</th>
          <th>First Seen</th>
        </tr>
      </thead>
      <tbody>
{table_rows}
      </tbody>
    </table>

    <h2 style="margin-bottom:15px;">📋 Detailed Rule Analysis (Click to expand)</h2>
    {rules_html}

    <footer>
      <p>Generated by MonkeyLM | Accessibility Audit Dashboard</p>
    </footer>
  </div>

  <script>
    function toggleRule(index) {{
      var body = document.getElementById("rule-body-" + index);
      var icon = document.getElementById("toggle-" + index);
      if (body.style.display === "none") {{
        body.style.display = "block";
        icon.textContent = "▲";
      }} else {{
        body.style.display = "none";
        icon.textContent = "▼";
      }}
    }}
  </script>
</body>
</html>'''

    # Write to file with redaction and secure permissions
    output_dir = getattr(settings, "output_dir", None) or os.getcwd()
    report_path = os.path.join(output_dir, "accessibility_report.html")
    redacted_html = redact_sensitive_content(html_content)
    _secure_atomic_write(report_path, redacted_html, mode=0o640)

    print(f"🌐 Interactive HTML report generated: {report_path}")
    return report_path
