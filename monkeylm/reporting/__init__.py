"""MonkeyLM reporting submodules - re-exports for backward compatibility."""

from monkeylm.reporting.utils import redact_sensitive_content
from monkeylm.reporting.telemetry import summarize_semantic_memory_telemetry
from monkeylm.reporting.accessibility import _compile_accessibility_violations
from monkeylm.reporting.accountability import summarize_vibe_coding_accountability, _derive_severity
from monkeylm.reporting.defects import (
    _compile_defect_tickets,
    _group_defects,
    _derive_severity as _defects_derive_severity,
    _extract_reproduction_steps,
    _SEVERITY_MAP,
    _ROOT_CAUSE_TEMPLATES,
    _REMEDIATION_TEMPLATES,
)
from monkeylm.reporting.markdown import generate_markdown_report
from monkeylm.reporting.json_report import generate_json_summary
from monkeylm.reporting.pdf import generate_pdf_report
from monkeylm.reporting.html import generate_interactive_html_report

__all__ = [
    "redact_sensitive_content",
    "summarize_semantic_memory_telemetry",
    "summarize_vibe_coding_accountability",
    "_compile_accessibility_violations",
    "_compile_defect_tickets",
    "_group_defects",
    "_derive_severity",
    "_extract_reproduction_steps",
    "_SEVERITY_MAP",
    "_ROOT_CAUSE_TEMPLATES",
    "_REMEDIATION_TEMPLATES",
    "generate_markdown_report",
    "generate_json_summary",
    "generate_pdf_report",
    "generate_interactive_html_report",
]
