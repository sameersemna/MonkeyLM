"""Backward compatibility shim - reporting functions now live in reporting/ submodules.

DEPRECATED: This module is maintained for backward compatibility only.
Import from monkeylm.reporting instead.
"""

from monkeylm.reporting import (
    redact_sensitive_content,
    summarize_semantic_memory_telemetry,
    summarize_vibe_coding_accountability,
    _compile_accessibility_violations,
    _compile_defect_tickets,
    _group_defects,
    _derive_severity,
    _extract_reproduction_steps,
    _SEVERITY_MAP,
    _ROOT_CAUSE_TEMPLATES,
    _REMEDIATION_TEMPLATES,
    generate_markdown_report,
    generate_json_summary,
    generate_pdf_report,
    generate_interactive_html_report,
)

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
