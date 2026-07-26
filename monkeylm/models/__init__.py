"""Models module - Ollama client, vision routing, and decision prompts."""

from monkeylm.models.ollama import (
    _ollama_chat_with_retry,
    _safe_json_parse,
    _sanitize_prompt_input,
    _redact_secrets,
    _is_ollama_overload_error,
)
from monkeylm.models.vision import (
    annotate_relevant_screenshot,
    _is_cloud_vision_model,
    _build_vision_annotation_prompt,
    _parse_vision_box,
    _draw_red_box_arrow,
    _wrap_text_to_lines,
)
from monkeylm.models.prompts import (
    decide_next_action,
    run_application_discovery,
    parse_action_plan_response,
    build_decision_prompt,
    generate_form_payload,
    _step_defects_summary,
    apply_state_aware_policy,
    _break_action_loop,
    _extract_all_target_ids,
    _extract_target_id,
)

__all__ = [
    "_ollama_chat_with_retry",
    "_safe_json_parse",
    "_sanitize_prompt_input",
    "_redact_secrets",
    "_is_ollama_overload_error",
    "annotate_relevant_screenshot",
    "_is_cloud_vision_model",
    "_build_vision_annotation_prompt",
    "_parse_vision_box",
    "_draw_red_box_arrow",
    "_wrap_text_to_lines",
    "decide_next_action",
    "run_application_discovery",
    "parse_action_plan_response",
    "build_decision_prompt",
    "generate_form_payload",
    "_step_defects_summary",
    "apply_state_aware_policy",
    "_break_action_loop",
    "_extract_all_target_ids",
    "_extract_target_id",
]
