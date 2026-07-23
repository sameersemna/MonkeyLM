"""Defect ticket compilation pipeline for MonkeyLM."""

from __future__ import annotations
import re
from typing import Any, Dict, List, Tuple
from monkeylm.types import DefectTicket


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
    """Group raw defects into consolidated DefectTickets."""
    all_defects: List[Tuple[str, Dict[str, Any]]] = []
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

    step_to_log: Dict[int, Dict[str, Any]] = {}
    for log in test_logs:
        step_to_log[log.get("step", 0)] = log

    groups: Dict[Tuple[str, str], List[Tuple[str, Dict[str, Any]]]] = {}
    for category, defect in all_defects:
        url = defect.get("url", "") or ""
        try:
            m = re.match(r"([^?#]+)", url)
            normalized_url = m.group(1) if m else ""
            normalized_url = normalized_url.rstrip("/")
        except Exception:
            normalized_url = url
        key = (category, normalized_url)
        groups.setdefault(key, []).append((category, defect))

    tickets: List[DefectTicket] = []
    ticket_counter = 1

    for (category, url), group_defects in groups.items():
        sorted_defects = sorted(
            group_defects, key=lambda x: x[1].get("step", 0) or 0
        )

        primary = sorted_defects[0][1]
        target_step = primary.get("step", 0)

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

        target_selector = (
            primary.get("target", "") or
            primary.get("selector", "") or
            primary.get("element", "") or ""
        )
        html_snippet = (primary.get("html_context", "") or primary.get("html", "") or "")[:1000]

        page_state_name = url.split("/")[-1] if url else "unknown-page"
        if not page_state_name:
            page_state_name = "root"

        reproduction_steps = _extract_reproduction_steps(test_logs, target_step)

        severity = _derive_severity(category, primary)

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

        description = (primary.get("description", "") or primary.get("message", "") or "")[:500]
        if not description:
            description = f"Defect of type '{defect_type}' detected on {url} at step {target_step}."

        impact_map = {
            "CRITICAL": "Potential Data Corruption / Security Exploit",
            "HIGH": "Functional Breakage / User Experience Degradation",
            "MEDIUM": "Reliability Issue / Partial Feature Failure",
            "LOW": "Visual Glitch / Minor Inconsistency",
        }
        impact = impact_map.get(severity, "")

        after_screenshot = None
        before_screenshot = None
        if target_step in step_to_log:
            log = step_to_log[target_step]
            if log.get("screenshot"):
                after_screenshot = log["screenshot"]
            if log.get("before_screenshot"):
                before_screenshot = log["before_screenshot"]

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

    severity_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    tickets.sort(key=lambda t: severity_order.get(t.severity, 4))

    return tickets


def _compile_defect_tickets(
    defects: Any, test_logs: List[Dict[str, Any]]
) -> List[DefectTicket]:
    """Compile all raw defects into structured DefectTickets with remediation blueprints."""
    return _group_defects(defects, test_logs)
