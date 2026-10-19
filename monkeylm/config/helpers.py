"""Small configuration helpers shared across runtime modules."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, List
from urllib.parse import urlparse

from .defaults import ALLOWED_ACTIONS


def normalize_action_plan(raw_plan: Any) -> Dict[str, Any]:
    if not isinstance(raw_plan, dict):
        return {"action": "scroll", "target": "", "value": "", "action_strategy": "", "input_payloads": []}

    action = str(raw_plan.get("action", "scroll")).strip().lower()
    if action not in ALLOWED_ACTIONS:
        action = "scroll"

    target = raw_plan.get("target", "")
    value = raw_plan.get("value", "")
    if target is None:
        target = ""
    if value is None:
        value = ""

    strategy = str(raw_plan.get("action_strategy", "")).strip().upper()
    if strategy not in {"HAPPY_UPSERT", "EDGE_CASE_FUZZ"}:
        strategy = ""

    payloads = raw_plan.get("input_payloads", [])
    if not isinstance(payloads, list):
        payloads = []
    normalized_payloads = []
    for p in payloads:
        if isinstance(p, dict):
            normalized_payloads.append(
                {"target": str(p.get("target", "")), "value": str(p.get("value", "")), "reason": str(p.get("reason", ""))}
            )

    persona_intent = raw_plan.get("persona_intent", "")
    if persona_intent is None:
        persona_intent = ""
    expected_reaction = raw_plan.get("expected_reaction", "")
    if expected_reaction is None:
        expected_reaction = ""

    reasoning_obj = raw_plan.get("reasoning", {})
    if not isinstance(reasoning_obj, dict):
        reasoning_obj = {}
    reasoning_intent = reasoning_obj.get("intent", "")
    if reasoning_intent is None:
        reasoning_intent = ""
    reasoning_strategy_ref = reasoning_obj.get("strategy_reference", "")
    if reasoning_strategy_ref is None:
        reasoning_strategy_ref = ""
    reasoning_target_justification = reasoning_obj.get("target_justification", "")
    if reasoning_target_justification is None:
        reasoning_target_justification = ""

    return {
        "action": action,
        "target": str(target),
        "value": str(value),
        "action_strategy": strategy,
        "input_payloads": normalized_payloads,
        "persona_intent": str(persona_intent),
        "expected_reaction": str(expected_reaction),
        "reasoning_intent": str(reasoning_intent),
        "reasoning_strategy_reference": str(reasoning_strategy_ref),
        "reasoning_target_justification": str(reasoning_target_justification),
    }


def is_in_scope(current_url: str, target_url: str) -> bool:
    try:
        current = urlparse(current_url)
        target = urlparse(target_url)
    except Exception:
        return False

    if not current.netloc or not target.netloc:
        return False

    return current.netloc.lower() == target.netloc.lower()


def build_redis_key(redis_prefix: str, base_key: str) -> str:
    if redis_prefix:
        return f"{redis_prefix}{base_key}"
    return base_key


# ── Defect normalization (used by core.py DefectTracker) ─────────────────────


def _normalize_defect(payload: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(payload)

    if "selector" not in out or not out["selector"]:
        for candidate_key in ("target", "broken_selectors"):
            val = out.get(candidate_key)
            if isinstance(val, list) and val:
                out["selector"] = ", ".join(str(v) for v in val[:5])
                break
            elif isinstance(val, str) and val.strip():
                out["selector"] = val.strip()
                break
    if not out.get("selector"):
        out["selector"] = "(none)"

    if "html_snippet" not in out or not out["html_snippet"]:
        for candidate_key in ("payload_preview",):
            val = out.get(candidate_key)
            if isinstance(val, str) and val.strip():
                out["html_snippet"] = val.strip()
                break
    if not out.get("html_snippet"):
        out["html_snippet"] = ""

    if "failure_reason" not in out or not out["failure_reason"]:
        for candidate_key in ("description", "message", "error"):
            val = out.get(candidate_key)
            if isinstance(val, str) and val.strip():
                out["failure_reason"] = val.strip()
                break
    if not out.get("failure_reason"):
        parts: List[str] = []
        if out.get("type"):
            parts.append(str(out["type"]))
        if out.get("severity"):
            parts.append(str(out["severity"]))
        out["failure_reason"] = ": ".join(parts) if parts else "defect detected"

    if "remediation_advice" not in out or not out["remediation_advice"]:
        for candidate_key in ("remediation", "help", "failureSummary"):
            val = out.get(candidate_key)
            if isinstance(val, str) and val.strip():
                out["remediation_advice"] = val.strip()
                break
    if not out.get("remediation_advice"):
        out["remediation_advice"] = "Manual review required."

    if "screenshot_path" not in out or not out["screenshot_path"]:
        for candidate_key in ("diff_image",):
            val = out.get(candidate_key)
            if isinstance(val, str) and val.strip():
                out["screenshot_path"] = val.strip()
                break
    if not out.get("screenshot_path"):
        out["screenshot_path"] = ""

    return out


# ── Service logging helper ─────────────────────────────────────────────────────


def _local_service_log(message: str, output_dir: str = "") -> None:
    timestamp = datetime.now().isoformat(timespec="seconds")
    log_line = f"[{timestamp}] {message}"
    print("\u26a0\ufe0f", log_line)
    if output_dir:
        try:
            log_path = os.path.join(output_dir, "service_connectivity.log")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(log_line + "\n")
        except Exception:
            pass


def split_domain_and_route(url: str) -> tuple[str, str]:
    try:
        parsed = urlparse(url)
    except Exception:
        return "", "/"

    domain = (parsed.netloc or "").lower().strip()
    route = parsed.path or "/"
    if parsed.query:
        route = f"{route}?{parsed.query}"
    return domain, route


__all__ = [
    "normalize_action_plan",
    "is_in_scope",
    "build_redis_key",
    "_normalize_defect",
    "_local_service_log",
    "split_domain_and_route",
]
