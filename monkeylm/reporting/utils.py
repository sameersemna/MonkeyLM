"""Utility functions for MonkeyLM reporting."""

from __future__ import annotations
import re
from typing import Any, Dict, List


def redact_sensitive_content(text: str) -> str:
    """Redact sensitive patterns from text before writing to files."""
    text = re.sub(r'sk-\w+', '[REDACTED]', text, flags=re.IGNORECASE)
    text = re.sub(r'gsk_\w+', '[REDACTED]', text, flags=re.IGNORECASE)
    text = re.sub(r'ollama-\w+', '[REDACTED]', text, flags=re.IGNORECASE)

    password_patterns = [
        r'(password|passwd|secret|token|key|credential|api_key|access_key|auth_token|session_token|api_secret|private_key|public_key|auth_secret|jwt_token|access_secret)',
    ]

    for pattern in password_patterns:
        text = re.sub(pattern, '[REDACTED]', text, flags=re.IGNORECASE)

    return text


def summarize_vision_annotation(
    test_logs: List[Dict[str, Any]],
    settings: Any,
) -> Dict[str, Any]:
    """Count vision-LLM screenshot checks from per-step log entries.

    Every step log records the outcome of the annotation stage in
    ``annotation_source`` and (when the stage degraded) ``annotation_error``.
    This summary aggregates those outcomes so the Markdown, JSON and PDF
    reports can each answer "did the vision LLM actually check the
    screenshots this run?" — a run that silently stops annotating (the
    Oct 21, 2026 step-budget regression) is then obvious instead of
    merely an absent section.
    """
    counts = {
        "vision": 0,
        "cv": 0,
        "no_region": 0,
        "timeout": 0,
        "error": 0,
        "skipped_no_step_budget": 0,
    }
    for log in test_logs or []:
        source = str(log.get("annotation_source") or "")
        annotation_error = str(log.get("annotation_error") or "")
        annotated = bool(log.get("screenshot_annotated"))
        if annotated and source == "cv":
            counts["cv"] += 1
        elif annotated and source == "vision":
            counts["vision"] += 1
        elif annotation_error == "timeout":
            counts["timeout"] += 1
        elif annotation_error == "skipped_no_step_budget":
            counts["skipped_no_step_budget"] += 1
        elif annotation_error:
            counts["error"] += 1
        elif source == "none":
            counts["no_region"] += 1

    attempted = sum(counts.values())
    return {
        "total_steps": len(test_logs or []),
        "attempted": attempted,
        "annotated": counts["vision"] + counts["cv"],
        "annotated_by_vision": counts["vision"],
        "annotated_by_cv": counts["cv"],
        "no_region_found": counts["no_region"],
        "timeouts": counts["timeout"],
        "errors": counts["error"],
        "skipped_no_step_budget": counts["skipped_no_step_budget"],
        "vision_model": getattr(settings, "vision_model", "") or "",
        "pdf_vision_model": getattr(settings, "pdf_vision_model", "") or "",
        "budget_seconds": getattr(settings, "vision_annotation_timeout_seconds", None),
        "max_attempts": getattr(settings, "vision_annotation_max_attempts", None),
    }
