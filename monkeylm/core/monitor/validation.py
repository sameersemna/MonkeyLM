"""Destructive input testing for form validation."""

from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, List

from playwright.async_api import Page

from .defects import DefectTracker


class ValidationProber:
    """Periodically sends destructive inputs to form fields to check error handling."""

    ERROR_LEAK_PATTERNS = [
        re.compile(r"Traceback|stack\s*trace|uncaught\s+exception", re.I),
        re.compile(r"'NoneType'|'null'\s+has\s+no\s+attribute|Cannot\s+read\s+property", re.I),
        re.compile(r"TypeError:\s*(cannot|is not|invalid|expected)", re.I),
        re.compile(r"SyntaxError:\s*unexpected", re.I),
        re.compile(r"ReferenceError:\s*\w+\s+is\s+not\s+defined", re.I),
        re.compile(r"<pre>\s*(File\s+\"|at\s+\S+\.js)", re.I),
        re.compile(r"Internal Server Error|500 Internal|Server Error", re.I),
        re.compile(r"django\.|flask\.|express\.|next\.", re.I),
    ]

    DESTRUCTIVE_PAYLOADS = [
        {"name": "sql_injection_basic", "value": "' OR 1=1 --"},
        {"name": "sql_injection_union", "value": "' UNION SELECT NULL,NULL--"},
        {"name": "xss_script_tag", "value": "<script>alert('probe')</script>"},
        {"name": "xss_event_handler", "value": "\" onfocus=\"alert('probe') autofocus=\""},
        {"name": "path_traversal", "value": "../../../../etc/passwd"},
        {"name": "ssti_injection", "value": "{{7*'7}}"},
        {"name": "oversized_string", "value": "A" * 50000},
        {"name": "unicode_boundary", "value": "\ud800\udc00\uFFFF𐍉\x00\x1F"},
        {"name": "html_entity_injection", "value": "&lt;img src=x onerror=alert(1)&gt;"},
    ]

    def __init__(self, defects: DefectTracker, *, probe_frequency: int = 3):
        self.defects = defects
        self.probe_frequency = max(1, probe_frequency)
        self._form_interaction_count: int = 0

    def should_probe(self) -> bool:
        self._form_interaction_count += 1
        return self._form_interaction_count % self.probe_frequency == 0

    async def probe_field(
        self, page: Page, locator: Any, control_type: str,
        step: int, action_desc: str, target_id: str = ""
    ) -> List[Dict[str, Any]]:
        if not isinstance(step, int) or step < 0:
            return []
        if not isinstance(action_desc, str):
            action_desc = str(action_desc)[:512]
        if not isinstance(target_id, str):
            target_id = str(target_id)[:512]
        if not isinstance(control_type, str):
            control_type = str(control_type)[:64]

        findings: List[Dict[str, Any]] = []

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

        probe_idx = step % len(probe_payloads) if len(probe_payloads) > 0 else 0
        probe = probe_payloads[probe_idx]

        try:
            before_content_length = len(await page.content())
        except Exception:
            before_content_length = 0

        try:
            if control_type in ("checkbox",):
                await locator.click(timeout=2000)
            else:
                await locator.fill(probe["value"][:1000], timeout=3000)
        except Exception:
            return findings

        await asyncio.sleep(0.3)

        try:
            page_html = await page.content()
            for pattern in self.ERROR_LEAK_PATTERNS:
                matches = pattern.findall(page_html)
                if matches:
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
            pass

        return findings
