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
