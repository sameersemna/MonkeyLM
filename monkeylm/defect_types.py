"""Structured defect ticket model and rendering helpers."""

from __future__ import annotations

import json as _json_module
import re as _re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ── Defect ticket ─────────────────────────────────────────────────────────────


@dataclass
class DefectTicket:
    """Structured engineering defect ticket with remediation blueprint.

    Compiles raw defects into actionable cards optimized for both human review
    (scannable Markdown/PDF) and machine ingestion by coding agents
    (structured JSON spec blocks).
    """

    defect_uid: str
    category: str
    severity: str
    title: str
    description: str = ""
    target_url: str = ""
    page_state_name: str = ""
    target_selector: str = ""
    html_snippet: str = ""
    reproduction_steps: List[Dict[str, Any]] = field(default_factory=list)
    before_screenshot: Optional[str] = None
    after_screenshot: Optional[str] = None
    expected_screenshot: Optional[str] = None
    root_cause_analysis: str = ""
    remediation_instruction: str = ""
    raw_defects: List[Dict[str, Any]] = field(default_factory=list)
    impact: str = ""
    discovered_context_url: str = ""
    # "confirmed" when two independent detector families (DOM + CV) fired on
    # the same step/URL; "probable" for single-signal findings.
    confidence: str = "probable"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "defect_uid": self.defect_uid,
            "category": self.category,
            "severity": self.severity,
            "confidence": self.confidence,
            "title": self.title,
            "description": self.description,
            "impact": self.impact,
            "target_url": self.target_url,
            "page_state_name": self.page_state_name,
            "discovered_context_url": self.discovered_context_url,
            "target_selector": self.target_selector,
            "html_snippet": self.html_snippet,
            "reproduction_steps": self.reproduction_steps,
            "before_screenshot": self.before_screenshot,
            "after_screenshot": self.after_screenshot,
            "expected_screenshot": self.expected_screenshot,
            "root_cause_analysis": self.root_cause_analysis,
            "remediation_instruction": self.remediation_instruction,
        }

    @property
    def spec_block(self) -> Dict[str, Any]:
        target_element: Dict[str, Any] = {}
        if self.target_selector:
            target_element["selector"] = self.target_selector
        if self.html_snippet:
            tag_match = _re.search(r"<(\w+)", self.html_snippet[:200])
            if tag_match:
                target_element["tag"] = tag_match.group(1)
            attr_matches = _re.findall(
                r'(\w+)\s*=\s*(?:&quot;|")([^"&]*)?(?:&quot;|")',
                self.html_snippet[:500],
            )
            if attr_matches:
                target_element["attributes"] = dict(attr_matches)

        return {
            "defect_type": self.category.replace("_", ""),
            "severity": self.severity,
            "target_element": target_element,
            "target_url": self.target_url,
            "page_state_name": self.page_state_name,
            "reproduction_sequence": [
                {
                    "step": s.get("step", i + 1),
                    "action": s.get("action", ""),
                    "selector": s.get("target", ""),
                    "value": s.get("value", ""),
                    "url": s.get("url", ""),
                }
                for i, s in enumerate(self.reproduction_steps)
            ],
            "root_cause_analysis": self.root_cause_analysis,
            "remediation_instruction": self.remediation_instruction,
        }

    def to_markdown(self) -> str:
        lines = []
        sev_icon = {"CRITICAL": "\U0001f534", "HIGH": "\U0001f7e0", "MEDIUM": "\u26a0\ufe0f ", "LOW": "\u2139\ufe0f "}.get(
            self.severity, "\u26aa"
        )

        lines.append(f"## [{self.severity}] {sev_icon} {self.defect_uid}: {self.title}")
        if self.impact:
            lines.append(f"- **Impact:** {self.impact}")
        if self.target_selector:
            lines.append(f"- **Target Selector:** `{self.target_selector}`")
        if self.target_url:
            lines.append(f"- **Discovered Context:** {self.target_url}")
        if self.page_state_name:
            lines.append(f"- **Page State:** {self.page_state_name}")

        if self.reproduction_steps:
            lines.append("")
            lines.append("**Reproduction Steps:**")
            for s in self.reproduction_steps:
                step_num = s.get("step", "?")
                action = s.get("action", "")
                target = s.get("target", "")
                value = s.get("value", "")
                url = s.get("url", "")
                line = f"{step_num}. `{action}"
                if target:
                    line += f" on `{target}`"
                if value:
                    line += f" value=`{value[:60]}`"
                line += "`"
                if url:
                    line += f" \u2014 {url}"
                lines.append(f"- {line}")

        if self.root_cause_analysis:
            lines.append("")
            lines.append(f"**Root Cause Analysis:** {self.root_cause_analysis}")

        if self.remediation_instruction:
            lines.append("")
            lines.append(f"**Remediation Instruction:** {self.remediation_instruction}")

        screenshots = []
        for label, path in [
            ("Before", self.before_screenshot),
            ("After", self.after_screenshot),
            ("Expected", self.expected_screenshot),
        ]:
            if path:
                screenshots.append(f"`!{label} [{path}](./{path})`")
        if screenshots:
            lines.append("")
            lines.append("**Visual Proofs:** " + ", ".join(screenshots))

        lines.append("")
        lines.append("```json")
        lines.append(_json_module.dumps(self.spec_block, indent=2))
        lines.append("```")

        return "\n".join(lines)

    def agent_context_block(self) -> Dict[str, Any]:
        action = "click"
        payload_used = ""
        if self.reproduction_steps:
            last_step = self.reproduction_steps[-1]
            action = last_step.get("action", "").lower() or "click"
            if last_step.get("value"):
                payload_used = str(last_step["value"])[:200]
            elif last_step.get("target"):
                payload_used = str(last_step["target"])[:200]

        observed_error = ""
        for rd in self.raw_defects:
            err = rd.get("error", "") or rd.get("message", "") or rd.get("description", "")
            if err:
                observed_error = str(err)[:500]
                break
        if not observed_error and self.description:
            observed_error = self.description[:500]

        action_map = {
            "type": "type", "submit": "submit_form", "press": "click",
            "fill": "type", "check": "click",
        }
        normalized_action = action_map.get(action, action) if action in action_map else (
            "submit_form" if "form" in action or "submit" in action else "click"
        )

        return {
            "agent_context": {
                "defect_type": self.category,
                "severity": self.severity,
                "target_url": self.target_url,
                "target_selector": self.target_selector,
                "reproduction_action": {
                    "action": normalized_action,
                    "payload_used": payload_used,
                },
                "observed_error": observed_error,
                "reremediation_instruction": self.remediation_instruction,
            }
        }
