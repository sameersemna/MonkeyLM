"""Generic deduplication of raw defect findings for reporting.

A single underlying root cause (e.g. a stuck freeze, or a static accessibility
violation on a page that never changes) can generate many raw finding entries
across a run — one per step it's re-observed. Reported *counts* and per-item
report sections should reflect distinct issues, not raw observation events.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _dedup_key(item: Dict[str, Any]) -> tuple:
    selector = (
        item.get("selector")
        or item.get("target")
        or item.get("element")
        or item.get("url")
        or ""
    )
    content_hash = (
        item.get("content_hash")
        or item.get("state_hash")
        or item.get("structure_hash")
        or item.get("dom_hash")
        or item.get("hash")
        or item.get("after_hash")
        or item.get("before_hash")
        or ""
    )
    finding_type = item.get("type") or item.get("id") or ""

    if content_hash:
        # A content hash is a strong, stable identity signal for "this is the
        # same underlying page state/issue" — prefer it over the free-text
        # message, which often embeds per-occurrence details (e.g. a rolling
        # window of recent actions) that make every occurrence's message
        # technically unique even though the underlying finding is identical.
        return (finding_type, str(selector), str(content_hash))

    message = (
        item.get("message")
        or item.get("description")
        or ""
    )
    return (finding_type, str(selector), str(message)[:200])


def dedupe_findings(items: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Collapse findings sharing type+selector+message+content-hash into one.

    Each returned entry is a shallow copy of the first occurrence, annotated
    with `occurrence_count` (how many raw findings collapsed into it) and
    `step_range` ([min_step, max_step] across all occurrences, when steps are
    present).
    """
    if not items:
        return []

    groups: Dict[tuple, Dict[str, Any]] = {}
    order: List[tuple] = []

    for item in items:
        key = _dedup_key(item)
        if key not in groups:
            merged = dict(item)
            merged["occurrence_count"] = 1
            steps = [item["step"]] if item.get("step") is not None else []
            merged["_steps"] = steps
            groups[key] = merged
            order.append(key)
        else:
            g = groups[key]
            g["occurrence_count"] += 1
            if item.get("step") is not None:
                g["_steps"].append(item["step"])

    deduped: List[Dict[str, Any]] = []
    for key in order:
        g = groups[key]
        steps = g.pop("_steps", [])
        if steps:
            g["step_range"] = [min(steps), max(steps)]
        deduped.append(g)
    return deduped
