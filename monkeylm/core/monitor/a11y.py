"""Accessibility scanning via axe-core."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from playwright.async_api import Page

from monkeylm.resources import AXE_CORE_PATH

from .defects import DefectTracker, sanitize_for_storage


class A11yChecker:
    """Injects axe-core and executes periodic scans to surface high-severity a11y defects."""

    def __init__(self, defects: DefectTracker) -> None:
        self.injected_pages: set[int] = set()
        self.defects = defects
        self._cached_raw_axe: Optional[str] = None

    async def inject_init_script(self, page: Page) -> None:
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
        return sanitize_for_storage(value, max_len=1024)

    async def _reinject_via_evaluate(self, page: Page) -> bool:
        try:
            if self._cached_raw_axe is None:
                raw = AXE_CORE_PATH.read_text(encoding="utf-8")
                if len(raw) > 10 * 1024 * 1024:
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
                    "severity": impact,
                    "id": rule_id,
                    "description": description,
                    "help": help_text,
                    "helpUrl": help_url,
                    "impact": impact,
                    "selector": selector,
                    "html_snippet": node.get("html", ""),
                    "remediation": node.get("failureSummary", ""),
                    "url": page.url,
                }
                filtered.append(finding)
        for finding in filtered:
            self.defects.add("accessibility_violations", finding)
        return filtered
