"""Anti-loop heuristics — state-aware policy and action loop breaking."""

from __future__ import annotations

import random
import re
from typing import Any, Dict, List, Optional

from monkeylm.config import Settings, STATE_LOOP_THRESHOLD
from monkeylm.core import CURRENT_GLOBAL_STEP


def apply_state_aware_policy(
    settings: Settings,
    action_plan: Dict[str, Any],
    snapshot: Any,
    state_counts: Dict[str, int],
    seen_click_targets: set,
) -> Dict[str, Any]:
    state_key = f"{snapshot.url}::{snapshot.structure_hash}"
    revisit_count = state_counts.get(state_key, 0)
    if revisit_count > STATE_LOOP_THRESHOLD:
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
    loop_state: Optional[Dict[str, Any]] = None,
    blacklist_expiry_steps: int = 5,
) -> Dict[str, Any]:
    current_target = str(action_plan.get("target", ""))
    current_action = action_plan.get("action", "click")

    if loop_state is None:
        loop_state = {"blacklist": {}, "loop_count": 0}
    loop_state["loop_count"] += 1

    loop_state["blacklist"] = {
        tgt: exp for tgt, exp in loop_state["blacklist"].items() if exp > CURRENT_GLOBAL_STEP
    }

    blacklist_key = f"{current_action}:{current_target}"
    loop_state["blacklist"][blacklist_key] = CURRENT_GLOBAL_STEP + blacklist_expiry_steps
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

    fallback = random.choice(["scroll", "random_jump", "restart_target"])
    print(f"   -> 🔄 {worker_label} loop break: no alternative selectors; using {fallback}")
    return {"action": fallback, "target": "", "value": "", "action_strategy": "", "input_payloads": []}
