"""Backward-compatibility shim for prompts module.

All functionality has been moved to monkeylm/models/prompts/ subdirectory.
This file re-exports everything for existing imports.
"""

from monkeylm.models.prompts.decision import (
    _extract_target_id,
    build_decision_prompt,
    decide_next_action,
    parse_action_plan_response,
)
from monkeylm.models.prompts.discovery import (
    _build_discovery_prompt,
    _parse_testing_strategy,
    run_application_discovery,
)
from monkeylm.models.prompts.payloads import (
    _step_defects_summary,
    generate_form_payload,
)
from monkeylm.models.prompts.antiloop import (
    _break_action_loop,
    _extract_all_target_ids,
    apply_state_aware_policy,
)

__all__ = [
    "_break_action_loop",
    "_build_discovery_prompt",
    "_extract_all_target_ids",
    "_extract_target_id",
    "_parse_testing_strategy",
    "_step_defects_summary",
    "apply_state_aware_policy",
    "build_decision_prompt",
    "decide_next_action",
    "generate_form_payload",
    "parse_action_plan_response",
    "run_application_discovery",
]
