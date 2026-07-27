"""Core dataclasses and type definitions for MonkeyLM.

All structured data types live here so that every module can import them
without creating circular dependencies. No runtime logic or I/O belongs in
this module.
"""

from __future__ import annotations

import json as _json_module
import re as _re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


# ── Testing strategy types ────────────────────────────────────────────────────


@dataclass
class PersonaGoal:
    """A single testing persona with intent and expected reactions."""

    name: str
    description: str
    behaviors: List[str]


@dataclass
class CriticalFlow:
    """A critical user flow to test with persona-driven actions."""

    name: str
    description: str
    steps: List[str]


@dataclass
class TestingStrategy:
    """Application discovery output — LLM-generated testing strategy."""

    app_domain: str
    primary_personas: List[PersonaGoal]
    critical_flows: List[CriticalFlow]
    edge_cases_to_test: List[str]
    security_focus: List[str]
    strategy_summary: str = ""


# ── Runtime configuration ─────────────────────────────────────────────────────


@dataclass
class Settings:
    """Single source of truth for all MonkeyLM runtime configuration."""

    target_url: str = "https://noblequran-85hu2yge.manus.space/"
    ollama_model: str = "minimax-m3:cloud"
    ollama_timeout_seconds: float = 15.0
    vision_model: str = "gemini-3-flash-preview"
    pdf_vision_model: str = "llama3.2-vision"
    pdf_vision_timeout_seconds: float = 30.0

    max_steps: int = 10
    workers: int = 1
    max_steps_per_worker: int = 10
    worker_navigation_retries: int = 2
    worker_qdrant_init_retries: int = 1
    worker_boundary_recovery_retries: int = 1
    retry_base_delay_seconds: float = 0.75

    headless: bool = True
    browser_window_size: str = "1920,1080"
    no_viewport: bool = True
    strict_sandbox: bool = False
    allow_no_sandbox_fallback: bool = False

    postgres_dsn: str = "postgresql://postgres:postgres@latitude:5432/monkeylm"
    redis_url: str = "redis://:LatitudeRedis1407@latitude:6379/0"
    redis_prefix: str = "monkey:"
    redis_path_lock_ttl_seconds: int = 45
    redis_state_ttl_seconds: int = 86400
    strict_persistence: bool = False
    golden_baseline_mode: str = "preexisting"

    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_collection: str = "monkeylm_semantic_memory"
    qdrant_vector_size: int = 256
    qdrant_enable_reads: bool = True
    qdrant_enable_writes: bool = True
    qdrant_embedding_provider: str = "hash"
    qdrant_embedding_model: str = "nomic-embed-text"
    qdrant_embedding_litellm_base_url: str = "http://localhost:11435"
    qdrant_embedding_litellm_api_key: str = ""
    qdrant_rerank_enabled: bool = False
    qdrant_rerank_model: str = "qwen2.5:3b"
    qdrant_candidate_limit: int = 20
    qdrant_admin_action: str = ""

    pdf_generate: bool = False

    active_seed: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now().strftime("%Y%m%d_%H%M%S"))

    @property
    def output_dir(self) -> str:
        import os
        override = getattr(self, "_output_dir_override", None)
        if override is not None:
            return override
        return os.path.abspath(f"reports/testrun_{self.timestamp}")

    @property
    def user_data_root(self) -> str:
        import os
        return os.path.abspath("./playwright_user_data")

    @property
    def run_user_data_dir(self) -> str:
        import os
        return os.path.join(self.user_data_root, f"session_{self.timestamp}")


# ── DOM / page state types ────────────────────────────────────────────────────


@dataclass
class FormControlRecord:
    """Structured metadata for a single form control extracted from the DOM."""

    control_id: int
    form_id: Optional[str]
    tag_name: str
    input_type: str
    name_attr: str
    id_attr: str
    placeholder: str
    aria_label: str
    aria_labelledby: str
    required: bool
    disabled: bool
    readonly: bool
    minlength: Optional[int]
    maxlength: Optional[int]
    pattern: str
    min_value: str
    max_value: str
    step: str
    resolved_label: str
    label_confidence: float
    semantic_kind: str
    visible: bool = True
    options: List[str] = field(default_factory=list)


@dataclass
class FormRecord:
    """Structured metadata for a single form and its associated controls."""

    form_id: str
    action: str
    method: str
    control_ids: List[int] = field(default_factory=list)
    submit_candidate_id: Optional[int] = None


@dataclass
class PageSnapshot:
    """Normalized, lightweight representation of page state for diffing and planning."""

    url: str
    title: str
    dom_hash: str
    structure_hash: str
    elements: List[str] = field(default_factory=list)
    layout_anchors: Dict[str, Dict[str, float]] = field(default_factory=dict)
    modal_count: int = 0
    spinner_count: int = 0
    disabled_controls: int = 0
    screenshot_path: str = ""
    timestamp: float = 0.0
    form_controls: List[FormControlRecord] = field(default_factory=list)
    forms: List[FormRecord] = field(default_factory=list)
    is_empty_capture: bool = False


# ── Worker / run result types ─────────────────────────────────────────────────


@dataclass
class WorkerRunResult:
    """Result from a single worker's execution."""

    worker_id: int
    allocated_steps: int
    completed_steps: int
    logs: List[Dict[str, Any]]
    defects: Any
    network_injections: List[Dict[str, Any]]
    launch_info: Dict[str, Any]


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

    def to_dict(self) -> Dict[str, Any]:
        return {
            "defect_uid": self.defect_uid,
            "category": self.category,
            "severity": self.severity,
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


__all__ = [
    "PersonaGoal",
    "CriticalFlow",
    "TestingStrategy",
    "Settings",
    "FormControlRecord",
    "FormRecord",
    "PageSnapshot",
    "WorkerRunResult",
    "DefectTicket",
]
