"""Defect tracking and fuzzing utilities."""

from __future__ import annotations

import random
from typing import Any, Dict, List

from monkeylm.config import Faker, _normalize_defect


def sanitize_for_storage(value: str, max_len: int = 1024) -> str:
    if not isinstance(value, str):
        return '(non-string)'[:max_len]
    safe = value
    safe = safe.replace('&', '&amp;')
    safe = safe.replace('<', '&lt;')
    safe = safe.replace('>', '&gt;')
    safe = safe.replace('"', '&quot;')
    return safe[:max_len]


def _observation_count(item: Dict[str, Any]) -> int:
    try:
        return max(1, int(item.get("occurrences", item.get("occurrence_count", 1))))
    except (TypeError, ValueError):
        return 1


def _observation_steps(item: Dict[str, Any]) -> List[int]:
    steps = item.get("occurrence_steps")
    if isinstance(steps, list):
        parsed = []
        for step in steps:
            try:
                parsed.append(int(step))
            except (TypeError, ValueError):
                continue
        if parsed:
            return parsed
    step = item.get("step")
    if step is None:
        return []
    try:
        return [int(step)]
    except (TypeError, ValueError):
        return []


def _dedup_key(category: str, item: Dict[str, Any]) -> tuple:
    if category == "accessibility_violations":
        return (item.get("id"), item.get("selector"), item.get("url"))
    if category == "context_anomalies":
        message = str(item.get("message") or item.get("failure_reason") or "")[:200]
        return (item.get("type"), item.get("action"), item.get("url"), message)
    return ()


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
        self.context_anomalies: List[Dict[str, Any]] = []
        self.ux_flow_freezes: List[Dict[str, Any]] = []
        self.validation_failures: List[Dict[str, Any]] = []
        self.capture_diagnostics: List[Dict[str, Any]] = []
        self.rendering_defects: List[Dict[str, Any]] = []

    def add(self, category: str, payload: Dict[str, Any]) -> None:
        collection = getattr(self, category, None)
        if collection is None:
            return
        normalized = _normalize_defect(payload)
        dedup_key = _dedup_key(category, normalized)
        if dedup_key:
            for existing in collection:
                if _dedup_key(category, existing) != dedup_key:
                    continue
                incoming_count = _observation_count(normalized)
                existing_count = _observation_count(existing) + incoming_count
                existing["occurrences"] = existing_count
                existing["occurrence_count"] = existing_count
                steps = sorted(set(_observation_steps(existing) + _observation_steps(normalized)))
                if steps:
                    existing["occurrence_steps"] = steps
                    existing["step_range"] = [steps[0], steps[-1]]
                if normalized.get("step") is not None:
                    existing["last_seen_step"] = normalized.get("step")
                return
            count = _observation_count(normalized)
            normalized["occurrences"] = count
            normalized["occurrence_count"] = count
            steps = _observation_steps(normalized)
            if steps:
                normalized["occurrence_steps"] = steps
                normalized["step_range"] = [steps[0], steps[-1]]
            normalized["last_seen_step"] = normalized.get("step")
        collection.append(normalized)

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
            "capture_diagnostics",
            "rendering_defects",
        ]
        for category in categories:
            for item in list(getattr(other, category, [])):
                self.add(category, item)


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
                    str(self.fake.email()),
                    str(self.fake.user_name()),
                    str(self.fake.name()),
                    str(self.fake.uri()),
                    str(self.fake.pystr(min_chars=20, max_chars=100)),
                ]
            )
        chosen = random.choice(candidates)
        return str(chosen)[:1024]
