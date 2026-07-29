"""Anti-loop heuristics — state-aware policy and action loop breaking."""

from __future__ import annotations

import random
import re
from typing import Any, Dict, List, Optional

from monkeylm.config import Settings, STATE_LOOP_THRESHOLD


def apply_state_aware_policy(
    settings: Settings,
    action_plan: Dict[str, Any],
    snapshot: Any,
    state_counts: Dict[str, int],
    seen_click_targets: set,
    *,
    loop_break_applied: bool = False,
) -> Dict[str, Any]:
    # dom_hash (includes element text) rather than structure_hash (text
    # stripped for layout-only comparisons elsewhere) -- see runner.py's
    # matching state_key construction for why.
    state_key = f"{snapshot.url}::{snapshot.dom_hash}"
    revisit_count = state_counts.get(state_key, 0)
    # `state_counts` only ever increases (it's a running visit tally for the
    # whole run), so once any route has been seen more than STATE_LOOP_THRESHOLD
    # times it stays "over threshold" forever. If `_break_action_loop` already
    # ran this step, it already picked a fresh, unblacklisted target specifically
    # to escape a stale state -- forcing random_jump/restart_target right after
    # would silently discard that choice every time, which in practice means a
    # handful of popular routes get permanently locked out of real click/type
    # interaction for the rest of the run. Trust the loop-breaker's choice
    # instead of immediately overriding it.
    if revisit_count > STATE_LOOP_THRESHOLD and not loop_break_applied:
        forced = random.choice(["random_jump", "restart_target"])
        return {"action": forced, "target": "", "value": ""}

    action = action_plan.get("action", "scroll")
    if action == "click" and action_plan.get("target") in seen_click_targets:
        clickable = [x for x in snapshot.elements if "<BUTTON" in x or "<A" in x]
        unseen = [x for x in clickable if x not in seen_click_targets]
        if unseen:
            pick = random.choice(unseen)
            id_match = re.search(r"\[id=(\d+)\]", pick)
            if id_match:
                action_plan["target"] = f"[id={id_match.group(1)}]"
    return action_plan


def _extract_all_target_ids(elements: List[str]) -> List[str]:
    ids: List[str] = []
    for el in elements:
        for match in re.finditer(r"\[id=(\d+)\]", el):
            ids.append(f"[id={match.group(1)}]")
    return ids


def _break_action_loop(
    action_plan: Dict[str, Any],
    snapshot: Any,
    worker_label: str,
    current_step: int,
    loop_state: Optional[Dict[str, Any]] = None,
    blacklist_expiry_steps: int = 5,
) -> Dict[str, Any]:
    current_target = str(action_plan.get("target", ""))
    current_action = action_plan.get("action", "click")

    if loop_state is None:
        loop_state = {"blacklist": {}, "loop_count": 0}
    loop_state["loop_count"] += 1

    loop_state["blacklist"] = {
        tgt: exp for tgt, exp in loop_state["blacklist"].items() if exp > current_step
    }

    blacklist_key = f"{current_action}:{current_target}"
    loop_state["blacklist"][blacklist_key] = current_step + blacklist_expiry_steps
    print(f"   └─ ⛓ Blacklisted '{blacklist_key}' for {blacklist_expiry_steps} steps (loop #{loop_state['loop_count']})")

    all_targets = _extract_all_target_ids(snapshot.elements)
    blacklisted_targets = set()
    for bl_key in loop_state["blacklist"]:
        parts = bl_key.split(":", 1)
        if len(parts) == 2:
            _, tgt = parts
            if tgt in all_targets:
                blacklisted_targets.add(tgt)

    alternatives = [t for t in all_targets if t != current_target and t not in blacklisted_targets]

    if alternatives:
        chosen_target = random.choice(alternatives)
        chosen_element = next((el for el in snapshot.elements if chosen_target in el), "")
        chosen_action = (
            "type"
            if any(tag in chosen_element.upper() for tag in {"<INPUT", "<TEXTAREA", "<SELECT"})
            else "click"
        )
        print(f"   -> 🔄 {worker_label} loop break: switching to {chosen_action} on {chosen_target} (excluding {len(blacklisted_targets)} blacklisted targets)")
        return {"action": chosen_action, "target": chosen_target, "value": "", "action_strategy": "", "input_payloads": []}

    fallback = "scroll"
    print(f"   -> 🔄 {worker_label} loop break: no alternative selectors; using {fallback}")
    return {"action": fallback, "target": "", "value": "", "action_strategy": "", "input_payloads": []}
