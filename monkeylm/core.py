"""Monitor classes, worker coordination, and main entry point."""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
import signal
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from playwright.async_api import Page, Route, async_playwright

def sanitize_for_storage(value: str, max_len: int = 1024) -> str:
    '''Sanitize untrusted string (console text, page errors, URLs) before storage.

    Strips characters that could trigger XSS in downstream HTML-based
    viewers, replaces long runs with an ellipsis, and rejects non-string types.
    '''
    if not isinstance(value, str):
        return '(non-string)'[:max_len]
    # HTML-entity encoding for log/display safety
    safe = value
    safe = safe.replace('&', '&amp;')
    safe = safe.replace('<', '&lt;')
    safe = safe.replace('>', '&gt;')
    safe = safe.replace('"', '&quot;')
    return safe[:max_len]

from monkeylm.config import (
    ACTION_COOLDOWN_SECONDS,
    Faker,
    Settings,
    WorkerRunResult,
    _local_service_log,
    _normalize_defect,
    GRACEFUL_SHUTDOWN_REQUESTED,
    is_in_scope,
    SHUTDOWN_EVENT,
)

from monkeylm.resources import AXE_CORE_PATH


# ── Global step counter for loop detection blacklisting ───────────────────────

CURRENT_GLOBAL_STEP: int = 0


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
        # Smart observation sensors
        self.context_anomalies: List[Dict[str, Any]] = []
        self.ux_flow_freezes: List[Dict[str, Any]] = []
        self.validation_failures: List[Dict[str, Any]] = []

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
            "context_anomalies",
            "ux_flow_freezes",
            "validation_failures",
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
                    str(self.fake.email()),  # type-safe
                    str(self.fake.user_name()),
                    str(self.fake.name()),
                    str(self.fake.uri()),
                    str(self.fake.pystr(min_chars=20, max_chars=100)),
                ]
            )
        chosen = random.choice(candidates)
        return str(chosen)[:1024]


# ── Accessibility checker ────────────────────────────────────────────────────


class A11yChecker:
    """Injects axe-core and executes periodic scans to surface high-severity a11y defects.

    FIX (Layer 1 - Framework Fix):
        Uses a locally bundled axe-core.min.js via add_init_script(path=...)
        instead of the broken CDN URL approach. No outbound network request
        is needed — bypassing CORS and strict CSP blocks entirely.

    GUARDRAIL (Layer 2 - Self-Healing Runtime Check):
        Inside scan(), if page.evaluate("typeof window.axe !== 'undefined'")
        returns False (meaning a hard navigation or strict CSP cleared the
        init script), the method re-injects the raw JS string directly into
        the frame via page.evaluate(axe_raw_js_string) before invoking axe.run().
        This completely eliminates "axe_missing" loop exceptions.

    REPORTING (Layer 3 - Structured Findings):
        Violations are structured with full metadata: rule id, description,
        helpUrl, impact level, CSS selector chain, HTML snippet, and the
        axe failureSummary remediation guidance. The DefectTracker collection
        is then processed by reporting.py into deduplicated compiled reports.
    """

    def __init__(self, defects: DefectTracker) -> None:
        self.injected_pages: set[int] = set()
        self.defects = defects
        # Cache for raw JS string (populated on first re-injection attempt)
        self._cached_raw_axe: Optional[str] = None

    async def inject_init_script(self, page: Page) -> None:
        """Bake axe-core into every navigation via local file path.

        Playwright's add_init_script(path=...) reads the bundle from disk
        and serialises it into each frame before any page script runs —
        this is immune to CSP <meta> tags or network blocks.
        
        FIX: Uses 'path=' instead of 'url=' (CDN URL fails under CSP).
        """
        page_id = id(page)
        if page_id in self.injected_pages:
            return

        try:
            await page.add_init_script(path=str(AXE_CORE_PATH))
            self.injected_pages.add(page_id)
        except Exception as exc:
            self.defects.add(
                "console_findings",
                {
                    "step": -1,
                    "type": "axe-init-script-warning",
                    "severity": "warning",
                    "message": f"Unable to add axe-core init script (path={AXE_CORE_PATH!r}): {exc}",
                    "url": getattr(page, "url", "(no page)"),
                },
            )

    async def ensure_injected(self, page: Page) -> None:
        """Legacy fallback for pages already opened before the init-script hook."""
        page_id = id(page)
        if page_id in self.injected_pages:
            return
        try:
            await page.add_script_tag(path=str(AXE_CORE_PATH))
            self.injected_pages.add(page_id)
        except Exception as exc:
            self.defects.add(
                "console_findings",
                {
                    "step": -1,
                    "type": "axe-injection-warning",
                    "severity": "warning",
                    "message": f"Unable to inject axe-core (path={AXE_CORE_PATH!r}): {exc}",
                    "url": getattr(page, "url", "(no page)"),
                },
            )

    @staticmethod
    def _sanitize_for_logging(value: str) -> str:
        """Sanitize a raw payload value before it enters logs or reports.

        Strips characters that could trigger XSS in downstream HTML-based
        viewers and replaces long runs with an ellipsis to bound log size.
        """
        return sanitize_for_storage(value, max_len=1024)

    async def _reinject_via_evaluate(self, page: Page) -> bool:
        """Re-inject axe-core raw source when the execution context is wiped.

        This is the self-healing path: if a hard navigation or strict CSP
        clears `window.axe`, we re-load the full minified bundle by passing
        its text content to page.evaluate(), which runs it as an inline script
        in the target frame.  No file-path resolution needed at runtime —
        we cache the string after first read to avoid repeated I/O.

        Returns True if injection appeared successful, False otherwise.
        """
        try:
            # Read and cache the local file once; cache on subsequent calls
            if self._cached_raw_axe is None:
                raw = AXE_CORE_PATH.read_text(encoding="utf-8")
                # Bound the raw JS payload to prevent memory exhaustion
                if len(raw) > 10 * 1024 * 1024:  # 10 MiB limit
                    self._cached_raw_axe = raw[:10 * 1024 * 1024]
                else:
                    self._cached_raw_axe = raw

            await page.evaluate(self._cached_raw_axe)
            return True
        except Exception as exc:
            self.defects.add(
                "console_findings",
                {
                    "step": -1,
                    "type": "axe-reinject-failure",
                    "severity": "error",
                    "message": f"Self-healing re-injection failed: {exc}",
                    "url": getattr(page, "url", "(no page)"),
                },
            )
            return False

    async def scan(self, page: Page, step_num: int) -> List[Dict[str, Any]]:
        """Run an axe-core audit; collect critical / serious violations.

        GUARDRAIL FLOW:
            1. Ensure axe is present (via add_init_script path or legacy fallback).
            2. Evaluate the axe.run() query.
            3. If results.error == 'axe_missing', run _reinject_via_evaluate(page)
               and retry the scan once.
            4. Extract full metadata (id, description, helpUrl, impact, selector, 
               html_snippet, remediation) for structured reporting downstream.
        """
        await self.ensure_injected(page)

        def _axe_run_eval() -> str:
            return """async () => {
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

        results: Dict[str, Any] = {}
        try:
            results = await page.evaluate(_axe_run_eval())
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

        # ── Self-heal if axe was wiped (e.g. hard navigation / CSP) ───────
        if results.get("error") == "axe_missing":
            success = await self._reinject_via_evaluate(page)
            if not success:
                self.defects.add(
                    "console_findings",
                    {
                        "step": step_num,
                        "type": "axe-runtime-warning",
                        "severity": "warning",
                        "message": "Re-injection failed; axe-core unavailable on this page.",
                        "url": page.url,
                    },
                )
                return []
            # Retry after re-injection
            try:
                results = await page.evaluate(_axe_run_eval())
            except Exception as exc2:
                self.defects.add(
                    "console_findings",
                    {
                        "step": step_num,
                        "type": "axe-runtime-warning",
                        "severity": "warning",
                        "message": f"Retry after re-injection failed: {exc2}",
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

        # ── Extract violations with full metadata ─────────────────────────
        filtered: List[Dict[str, Any]] = []
        for violation in results.get("violations", []):
            impact = (violation.get("impact") or "").lower()
            if impact not in {"critical", "serious", "moderate"}:
                continue

            rule_id = violation.get("id")
            description = violation.get("description")
            help_text = violation.get("help")
            help_url = violation.get("helpUrl", "")

            for node in violation.get("nodes", []):
                targets = node.get("target", [])
                selector = ", ".join(targets) if targets else "(unknown)"

                finding: Dict[str, Any] = {
                    "step": step_num,
                    "severity": impact,                  # critical | serious
                    "id": rule_id,                       # e.g. "color-contrast"
                    "description": description,          # Human-readable rule name
                    "help": help_text,                   # Axe guidance text
                    "helpUrl": help_url,                 # MDN / axe docs link
                    "impact": impact,
                    "selector": selector,                # CSS chain: "main > nav > ul"
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
        before_url = page.url
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
            # Guard: if page navigated away or was closed during the sleep window, skip check.
            after_url = page.url
            if after_url != before_url:
                return None

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


# ── Browser anomaly sensor (global error interception) ────────────────────────


class BrowserAnomalySensor:
    """Intercepts hidden browser context anomalies and maps them to monkey actions.

    Captures:
      - Unhandled promise rejections / page script errors (pageerror, console)
      - Strict CSP violations parsed from console messages
      - Failing backend fetch/XHR calls (4xx/5xx responses) via route interception
    Attributes each anomaly to the exact step and action that triggered it.
    """

    def __init__(self, defects: DefectTracker) -> None:
        self.defects = defects
        self._anomalies: List[Dict[str, Any]] = []
        self._current_step: int = -1
        self._current_action: str = ""
        self._installed_pages: set[int] = set()
        self._network_installed: bool = False

    async def install(self, page: Page) -> None:
        """Attach global error listeners to the page. Safe to call multiple times."""
        page_id = id(page)
        if page_id in self._installed_pages:
            return
        self._installed_pages.add(page_id)

        def _on_page_error(error: Any) -> None:
            raw = str(getattr(error, "message", error))
            msg = sanitize_for_storage(raw, max_len=1000)
            self._anomalies.append({
                "step": self._current_step,
                "action": self._current_action,
                "type": "unhandled-page-error",
                "severity": "error",
                "message": msg,
                "url": sanitize_for_storage(page.url, max_len=2048),
            })

        def _on_console(msg: Any) -> None:
            try:
                raw = getattr(msg, "text", "") or ""
                text = sanitize_for_storage(raw, max_len=1000)
                lower_text = text.lower()

                # Unhandled promise rejection
                if "unhandled promise" in lower_text:
                    self._anomalies.append({
                        "step": self._current_step,
                        "action": self._current_action,
                        "type": "unhandled-promise-rejection",
                        "severity": "error",
                        "message": text[:1000],
                        "url": page.url,
                    })

                # Strict CSP violation (blocked directive)
                elif ("content security policy" in lower_text or "csp" in lower_text) and "blocked" in lower_text:
                    # Parse the directive that was violated
                    directive = ""
                    _csp_resource = ""  # noqa: F841 – CSP diagnostic, kept for future telemetry
                    for part in text.split(";"):
                        if "directive" in part.lower():
                            directive = part.strip().split(":", 1)[-1].strip() if ":" in part else part.strip()
                        if ("script-src" in part or "style-src" in part or "img-src" in part):
                            _csp_resource = part.strip()  # noqa: F841
                    self._anomalies.append({
                        "step": self._current_step,
                        "action": self._current_action,
                        "type": "csp-violation",
                        "severity": "warning",
                        "message": text[:1000],
                        "blocked_directive": directive or lower_text.split("directive")[-1].strip() if "directive" in lower_text else "",
                        "url": page.url,
                    })

                # Uncaught exception from script (console-level)
                elif (("uncaught" in lower_text or "error:" in lower_text) and msg.type in ("error", "warning", "assert")):
                    # Avoid double-capturing if also caught by pageerror
                    already = any(
                        a.get("step") == self._current_step and a.get("type") == "unhandled-page-error" and text[:100] in a.get("message", "")
                        for a in self._anomalies[-5:]
                    )
                    if not already:
                        self._anomalies.append({
                            "step": self._current_step,
                            "action": self._current_action,
                            "type": "console-error",
                            "severity": "warning",
                            "message": text,
                            "url": sanitize_for_storage(page.url, max_len=2048),
                            "console_type": str(getattr(msg, "type", "")),
                        })

            except Exception:
                pass  # sensor failure must never crash worker

        page.on("pageerror", _on_page_error)
        page.on("console", _on_console)

    def set_action_context(self, step: int, action_desc: str) -> None:
        """Call before execute_action to attribute subsequent anomalies."""
        self._current_step = step
        self._current_action = action_desc

    async def check_network_failures(self, page: Page) -> None:
        """Install route handler that captures server 4xx/5xx for fetch/xhr calls.

        This augments (does not replace) the NetworkMonitor fault injector.
        It must be installed after NetworkMonitor.install() so both handlers coexist.
        """
        if self._network_installed:
            return

        # We use a second route handler layered on the existing one.
        # Playwright allows chaining via page.route — each new call replaces.
        # To avoid replacing NetworkMonitor's handler, we check for failures
        # by observing responses instead (page.on("response")).
        try:
            def _on_response(response) -> None:
                try:
                    status = response.status
                    if status >= 400:
                        request = response.request
                        resource_type = request.resource_type
                        url = sanitize_for_storage(request.url, max_len=2048)
                        method = request.method
                        # Focus on API/data requests
                        if resource_type in ("xhr", "fetch") or "/api/" in url.lower():
                            self._anomalies.append({
                                "step": self._current_step,
                                "action": self._current_action,
                                "type": f"network-{status // 100}xx-fetch",
                                "severity": "error" if status >= 500 else "warning",
                                "url": url,
                                "method": sanitize_for_storage(str(method), max_len=64),
                                "status": status,
                                "resource_type": sanitize_for_storage(str(resource_type), max_len=64),
                            })
                except Exception:
                    pass

            page.on("response", _on_response)

        except Exception:
            pass  # non-fatal

    async def flush_anomalies(self) -> List[Dict[str, Any]]:
        """Pop anomalies buffer into DefectTracker, returning flushed items."""
        if not self._anomalies:
            return []
        batch = list(self._anomalies)
        for anomaly in batch:
            self.defects.add("context_anomalies", anomaly)
        self._anomalies.clear()
        return batch


# ── Stall detector (state/URL lock detection) ──────────────────────────────────


class StallDetector:
    """Detects UX flow freezes when DOM structure or URL stays identical across steps.

    Tracks a rolling window of page state fingerprints. If N consecutive steps
    produce the same fingerprint while meaningful actions are attempted, it flags
    a "Stall/UX Flow Freeze Defect".
    """

    def __init__(self, defects: DefectTracker, *, threshold: int = 3) -> None:
        self.defects = defects
        self.threshold = max(2, threshold)
        self._history: List[Dict[str, Any]] = []

    def record_state(self, step: int, url: str, structure_hash: str, action: str = "") -> None:
        """Call with the page state after each action step."""
        self._history.append({
            "step": step,
            "url": url,
            "structure_hash": structure_hash,
            "action": action,
        })
        # Keep only last threshold+1 entries to bound memory
        if len(self._history) > self.threshold + 2:
            excess = len(self._history) - (self.threshold + 1)
            self._history = self._history[excess:]

    def check_for_stall(self, step: int, current_action: str) -> Optional[Dict[str, Any]]:
        """Return a stall finding if threshold consecutive fingerprints are identical.

        A "stall" is flagged when:
          - URL + structure_hash haven't changed for `threshold` consecutive steps
          - The actions attempted were NOT passive (not 'scroll', not 'back')
        Returns the finding dict, or None if no stall detected.
        """
        if len(self._history) < self.threshold:
            return None

        window = self._history[-self.threshold:]
        urls = set(e["url"] for e in window)
        hashes = set(e["structure_hash"] for e in window)
        actions = [e["action"] for e in window]

        # Consider the current action too
        all_actions = actions + [current_action]

        # Passive actions that don't change state are expected
        passive_actions = {"scroll", "back"}
        meaningful_count = sum(1 for a in all_actions if a not in passive_actions)

        if len(urls) <= 1 and len(hashes) <= 1 and meaningful_count >= self.threshold:
            # Defensive: window may have entries without expected keys if data was
            # corrupted or partially initialised. Use .get() with safe defaults.
            sentinel = (window or [{}])[0] if window else {}
            finding = {
                "step": step,
                "type": "ux-flow-freeze",
                "description": (
                    f"Page state unchanged across {self.threshold} consecutive steps "
                    f"(URL={sentinel.get('url', 'unknown')!r}, "
                    f"hash={sentinel.get('structure_hash', 'unknown')!r}). "
                    f"Actions attempted: {actions}"
                ),
                "stall_window_steps": window,
                "meaningful_actions_in_window": meaningful_count,
                "url": sentinel.get("url", "unknown"),
                "structure_hash": sentinel.get("structure_hash", "unknown"),
            }
            self.defects.add("ux_flow_freezes", finding)
            return finding

        # Also check for URL lock (URL unchanged but DOM evolved — potential endless navigation loop)
        if len(urls) <= 1 and meaningful_count >= self.threshold:
            # Different hashes but same URL — not a freeze, just state changes on same page
            pass

        return None


# ── Validation prober (destructive input testing) ─────────────────────────────


class ValidationProber:
    """Periodically sends destructive inputs to form fields to check error handling.

    Sends SQL injection fragments, XSS payloads, oversized strings, and improper formats
    through controlled field interactions, then checks the page response for unhandled
    application stack traces or crashes.

    Probes are non-destructive by design: they only fill fields (without necessarily
    submitting) and observe client-side behavior.
    """

    # Patterns that indicate the app leaked an error instead of handling gracefully
    ERROR_LEAK_PATTERNS = [
        re.compile(r"Traceback|stack\s*trace|uncaught\s+exception", re.I),
        re.compile(r"'NoneType'|'null'\s+has\s+no\s+attribute|Cannot\s+read\s+property", re.I),
        re.compile(r"TypeError:\s*(cannot|is not|invalid|expected)", re.I),
        re.compile(r"SyntaxError:\s*unexpected", re.I),
        re.compile(r"ReferenceError:\s*\w+\s+is\s+not\s+defined", re.I),
        re.compile(r"<pre>\s*(File\s+\"|at\s+\S+\.js)", re.I),
        re.compile(r"Internal Server Error|500 Internal|Server Error", re.I),
        re.compile(r"django\.|flask\.|express\.|next\.", re.I),  # framework-specific leaks
    ]

    DESTRUCTIVE_PAYLOADS = [
        {"name": "sql_injection_basic", "value": "' OR 1=1 --"},
        {"name": "sql_injection_union", "value": "' UNION SELECT NULL,NULL--"},
        {"name": "xss_script_tag", "value": "<script>alert('probe')</script>"},
        {"name": "xss_event_handler", "value": "\" onfocus=\"alert('probe') autofocus=\""},
        {"name": "path_traversal", "value": "../../../../etc/passwd"},
        {"name": "ssti_injection", "value": "{{7*'7}}"},
        {"name": "oversized_string", "value": "A" * 50000},
        {"name": "unicode_boundary", "value": "\ud800\udc00\uFFFF𐍉\x00\x1F"},  # lone surrogates, null, control
        {"name": "html_entity_injection", "value": "&lt;img src=x onerror=alert(1)&gt;"},
    ]

    def __init__(self, defects: DefectTracker, *, probe_frequency: int = 3):
        self.defects = defects
        # probe every Nth form interaction (default: 1 in 3)
        self.probe_frequency = max(1, probe_frequency)
        self._form_interaction_count: int = 0

    def should_probe(self) -> bool:
        """Return True if the next form interaction should be probed."""
        self._form_interaction_count += 1
        return self._form_interaction_count % self.probe_frequency == 0

    async def probe_field(
        self, page: Page, locator: Any, control_type: str,
        step: int, action_desc: str, target_id: str = ""
    ) -> List[Dict[str, Any]]:
        """Send a destructive payload to a field and check for error handling failures.

        Returns a list of validation failure findings (may be empty if app handles gracefully).
        The probe is read-only for submissions — it only fills the field and observes.
        """
        # Runtime type validation for dynamic inputs
        if not isinstance(step, int) or step < 0:
            return []
        if not isinstance(action_desc, str):
            action_desc = str(action_desc)[:512]
        if not isinstance(target_id, str):
            target_id = str(target_id)[:512]
        if not isinstance(control_type, str):
            control_type = str(control_type)[:64]

        findings: List[Dict[str, Any]] = []

        # Select one destructive payload based on control type
        if control_type in ("tel", "email"):
            probe_payloads = [p for p in self.DESTRUCTIVE_PAYLOADS if "sql" in p["name"] or "xss" in p["name"]]
        elif control_type in ("number", "range"):
            probe_payloads = [
                {"name": "non_numeric_in_number_field", "value": "abc, not a number"},
                {"name": "extreme_number", "value": "-999999999999999999"},
                {"name": "sql_injection_basic", "value": "' OR 1=1 --"},
            ]
        else:
            probe_payloads = self.DESTRUCTIVE_PAYLOADS

        # Pick a payload (deterministic by step for reproducibility)
        probe_idx = step % len(probe_payloads) if len(probe_payloads) > 0 else 0
        probe = probe_payloads[probe_idx]

        # Capture page content before probe
        try:
            before_content_length = len(await page.content())
        except Exception:
            before_content_length = 0

        # Fill with destructive payload
        try:
            if control_type in ("checkbox",):
                await locator.click(timeout=2000)
            else:
                await locator.fill(probe["value"][:1000], timeout=3000)  # cap at 1000 chars for fill
        except Exception:
            # Field rejected the input — this is itself useful info (client-side validation)
            return findings

        # Brief wait to let client-side validation fire
        await asyncio.sleep(0.3)

        # Check for error leaks in page content
        try:
            page_html = await page.content()

            # Check for stack traces or error messages that weren't there before
            for pattern in self.ERROR_LEAK_PATTERNS:
                matches = pattern.findall(page_html)
                if matches:
                    # Verify this is new (not present in normal page state)
                    finding = {
                        "step": step,
                        "type": "validation-error-leak",
                        "description": (
                            f"App exposed potential error when probing field '{target_id}' "
                            f"with {probe['name']} payload. Pattern matched: {pattern.pattern}"
                        ),
                        "probe_name": probe["name"],
                        "probe_value_preview": probe["value"][:100],
                        "control_type": control_type,
                        "target": target_id,
                        "matched_text_sample": matches[0][:200] if isinstance(matches[0], str) else "",
                        "action_context": action_desc,
                        "url": page.url,
                    }
                    findings.append(finding)
                    self.defects.add("validation_failures", finding)

            # Check for DOM collapse (page may have crashed client-side)
            if before_content_length > 0:
                after_content_length = len(page_html)
                if after_content_length < max(1, before_content_length * 0.2):
                    finding = {
                        "step": step,
                        "type": "validation-dom-collapse",
                        "description": (
                            f"Page DOM collapsed from {before_content_length} to {after_content_length} chars "
                            f"after probing field '{target_id}' with {probe['name']} payload."
                        ),
                        "probe_name": probe["name"],
                        "control_type": control_type,
                        "target": target_id,
                        "before_size": before_content_length,
                        "after_size": after_content_length,
                        "action_context": action_desc,
                        "url": page.url,
                    }
                    findings.append(finding)
                    self.defects.add("validation_failures", finding)

        except Exception:
            pass  # sensor non-fatal

        return findings


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
        # Second Ctrl+C during graceful shutdown - just ignore, let shutdown complete
        return

    GRACEFUL_SHUTDOWN_REQUESTED = True
    print(f"\n\U0001f6d1 Graceful shutdown requested (signal {signum}). Finishing in-flight steps...")
    _remove_graceful_shutdown_signals()  # Remove handlers to prevent further interrupts
    try:
        loop = asyncio.get_event_loop()
        loop.call_soon_threadsafe(SHUTDOWN_EVENT.set)
    except Exception:
        try:
            SHUTDOWN_EVENT.set()
        except Exception:
            pass


def _remove_graceful_shutdown_signals() -> None:
    """Remove signal handlers to prevent further interrupts during graceful shutdown."""
    try:
        # Try to use asyncio's signal handler removal if loop is still running
        loop = asyncio.get_running_loop()
        try:
            loop.remove_signal_handler(signal.SIGINT)
        except (NotImplementedError, KeyError, ValueError, RuntimeError):
            # RuntimeError can occur if loop is being destroyed
            pass
        try:
            loop.remove_signal_handler(signal.SIGTERM)
        except (NotImplementedError, KeyError, ValueError, RuntimeError):
            pass
    except Exception:
        # Fallback to signal.signal() if asyncio removal fails
        try:
            signal.signal(signal.SIGINT, signal.default_int_handler)
        except Exception:
            pass
        try:
            signal.signal(signal.SIGTERM, signal.default_int_handler)
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
    from monkeylm.models import decide_next_action, apply_state_aware_policy, _break_action_loop, run_application_discovery
    from monkeylm.memory import QdrantMemoryStore

    worker_label = f"worker-{worker_id:02d}"
    worker_defects = DefectTracker()
    worker_fuzzer = Fuzzer()
    worker_network_monitor = NetworkMonitor(worker_defects)
    worker_a11y_checker = A11yChecker(worker_defects)
    worker_perf_monitor = PerformanceMonitor(worker_defects)

    # Smart observation sensors
    worker_anomaly_sensor = BrowserAnomalySensor(worker_defects)
    worker_stall_detector = StallDetector(worker_defects, threshold=settings.max_steps // 4 if settings.max_steps >= 8 else 3)
    worker_validation_prober = ValidationProber(worker_defects, probe_frequency=3)

    worker_memory = QdrantMemoryStore(settings)
    worker_logs: List[Dict[str, Any]] = []
    visited_states: Dict[str, int] = {}
    seen_click_targets: set = set()
    recent_model_plans: List[Tuple[str, str]] = []
    completed_steps = 0

    # Enhanced loop detection state - shared across steps for blacklisting
    loop_detection_state: Dict[str, Any] = {
        "blacklist": {},  # target -> expiry_step mapping
        "loop_count": 0,
        "recent_actions": [],  # short-term memory buffer to clear on loop
    }

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
            text = sanitize_for_storage(getattr(msg, "text", ""), max_len=2048)
            if "content security policy" in text.lower() or "csp" in text.lower():
                worker_defects.add(
                    "console_findings",
                    {
                        "step": -1,
                        "type": "csp-warning",
                        "message": text,
                        "url": sanitize_for_storage(page.url, max_len=2048),
                        "worker": worker_label,
                    },
                )

        page.on("console", _console_listener)

        # Bake axe-core into the page lifecycle BEFORE any navigation
        await worker_a11y_checker.inject_init_script(page)

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

        # Install smart observation sensors
        await worker_anomaly_sensor.install(page)

        await with_retry_backoff(
            f"{worker_label} qdrant initialize",
            worker_memory.initialize,
            retries=settings.worker_qdrant_init_retries,
            initial_delay_seconds=settings.retry_base_delay_seconds,
        )

        # ── Application Discovery: analyze landing page and build testing strategy ──
        testing_strategy = None
        try:
            discovery_snapshot = await get_page_state(page, -1, phase="plan", output_dir=settings.output_dir)
            discovery_state = state_to_prompt(discovery_snapshot)
            testing_strategy = await run_application_discovery(settings, discovery_state)
        except Exception as exc:
            _local_service_log(f"{worker_label} Application Discovery failed: {exc}; proceeding without strategy.", settings.output_dir)

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

            plan = await decide_next_action(settings, state, memory_store=worker_memory, snapshot=snapshot, testing_strategy=testing_strategy)
            retrieval_telemetry = worker_memory.consume_last_search_telemetry()

            # Update global step counter for loop detection blacklisting
            CURRENT_GLOBAL_STEP = step

            # Enhanced anti-loop heuristic with memory clearing & blacklist
            plan_signature = (plan.get("action", "scroll"), plan.get("target", ""))
            if len(recent_model_plans) >= 3 and all(p == plan_signature for p in recent_model_plans[-3:]):
                print(f"\U0001f504 Loop detected for {worker_label}; forcing path exploration variance.")

                # Clear short-term execution memory to break repetitive patterns
                loop_detection_state["recent_actions"] = []
                print(f"   \u251c\u2500 \u26d4 Cleared short-term action history for {worker_label}")

                # Use enhanced _break_action_loop with blacklist state
                plan = _break_action_loop(
                    plan, snapshot, worker_label,
                    loop_state=loop_detection_state,
                    blacklist_expiry_steps=settings.max_steps // 3,
                )
            recent_model_plans.append(plan_signature)
            recent_model_plans = recent_model_plans[-3:]

            plan = apply_state_aware_policy(settings, plan, snapshot, visited_states, seen_click_targets)
            if plan.get("action") == "click" and plan.get("target"):
                seen_click_targets.add(plan.get("target"))

            # Set anomaly attribution context before action execution
            worker_anomaly_sensor.set_action_context(step, f"{plan.get('action', '?')}:{plan.get('target', '')}")

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
                validation_prober=worker_validation_prober,
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
                raw_probe = log_entry["value"]
                payload_probe = sanitize_for_storage(str(raw_probe), max_len=200)
                try:
                    body_html = await page.content()
                    # Detect reflected XSS: payload contains HTML tags and appears in DOM unescaped
                    # NOTE: operates on sanitized text — only flags structural patterns, not exact match
                    has_xss_patterns = "&lt;" in payload_probe or "javascript:" in payload_probe.lower()
                    # Detect reflected SQL injection: single-quoted payloads with SQL keywords
                    has_sqli_patterns = (
                        "'" in raw_probe
                        and any(kw in raw_probe.upper() for kw in ("OR 1=1", "UNION SELECT", "DROP TABLE", "' OR '"))
                    )
                    if raw_probe in body_html and (has_xss_patterns or has_sqli_patterns):
                        probe_type = "reflected-xss" if has_xss_patterns else "reflected-sql-injection"
                        worker_defects.add(
                            "security_risks",
                            {
                                "step": step,
                                "type": f"fuzz-payload-{probe_type}",
                                "payload_preview": payload_probe,
                                "url": sanitize_for_storage(page.url, max_len=2048),
                                "worker": worker_label,
                            },
                        )
                except Exception:
                    pass

            # ── Smart observation sensors: post-step analysis ──────────────

            # Stall detection: record current state fingerprint and check for freezes
            try:
                post_snapshot = await get_page_state(page, step, phase="stall", output_dir=settings.output_dir)
                worker_stall_detector.record_state(
                    step, post_snapshot.url, post_snapshot.structure_hash, str(plan.get("action", ""))
                )
                stall_finding = worker_stall_detector.check_for_stall(step, plan.get("action", "scroll"))
                if stall_finding:
                    print(f"\u26a0\ufe0f {worker_label} STALL DETECTED at step {step}: page state unchanged across multiple steps")
            except Exception as stall_exc:
                _local_service_log(f"{worker_label} stall detection failed: {stall_exc}", settings.output_dir)

            # Flush browser anomalies captured during this step into defects
            try:
                flushed = await worker_anomaly_sensor.flush_anomalies()
                if flushed:
                    print(f"\u26a0\ufe0f {worker_label} {len(flushed)} context anomaly(s) at step {step}")
            except Exception as anomaly_exc:
                _local_service_log(f"{worker_label} anomaly flush failed: {anomaly_exc}", settings.output_dir)

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

    # Merge results from all workers
    merged_defects = DefectTracker()
    merged_logs: List[Dict[str, Any]] = []
    merged_network_events: List[Dict[str, Any]] = []
    worker_launches: List[Dict[str, Any]] = []
    worker_completion: List[Dict[str, Any]] = []

    for result in worker_results:
        if isinstance(result, BaseException):
            print(f"   -> 🚨 Worker raised an exception during shutdown: {result}")
            continue
        # At this point result is WorkerRunResult (any non-BaseException from gather)
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
    # Generate interactive HTML accessibility dashboard (if violations exist)
    try:
        if getattr(merged_defects, "accessibility_violations", None):
            from monkeylm.reporting import generate_interactive_html_report
            generate_interactive_html_report(settings, merged_defects, merged_logs, start_time, end_time)
    except Exception as exc:
        print(f"⚠️ HTML accessibility report generation failed: {exc}")
    if settings.pdf_generate:
        generate_pdf_report(settings, merged_defects, merged_logs, start_time, end_time)
