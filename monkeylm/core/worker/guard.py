"""Per-worker guard for plans that already failed against the same DOM state."""

from __future__ import annotations

from typing import Any, Dict, Set, Tuple


PlanSignature = Tuple[str, str, str, str]


def build_plan_signature(url: str, dom_hash: str, plan: Dict[str, Any]) -> PlanSignature:
    return (
        url or "",
        dom_hash or "",
        str(plan.get("action", "")),
        str(plan.get("target", "")),
    )


class FailedPlanGuard:
    """Prevents retrying an identical action/target on an unchanged DOM state."""

    def __init__(self) -> None:
        self._failed: Set[PlanSignature] = set()

    def should_skip(self, signature: PlanSignature) -> bool:
        return signature in self._failed

    def record_failure(self, signature: PlanSignature) -> None:
        self._failed.add(signature)

    def clear(self) -> None:
        self._failed.clear()

    def failed_targets(self, url: str, dom_hash: str) -> set:
        targets = set()
        for failed_url, failed_dom_hash, _action, target in self._failed:
            if failed_url == (url or "") and failed_dom_hash == (dom_hash or ""):
                if target:
                    targets.add(target)
        return targets
