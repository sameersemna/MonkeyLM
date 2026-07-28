"""Browser actions - element interaction, form handling, and action execution."""

from .helpers import (
    _extract_target_id,
    _fill_select_option,
    _locator_for_target_id,
    _resolve_form_boundary,
    _resolve_interaction_mode,
)
from .interaction import (
    collect_failure_context,
    detect_click_interception,
    recover_nonresponsive_state,
)
from .executor import (
    _compute_action_path_hash,
    execute_action,
)

__all__ = [
    "_compute_action_path_hash",
    "_extract_target_id",
    "_fill_select_option",
    "_locator_for_target_id",
    "_resolve_form_boundary",
    "_resolve_interaction_mode",
    "execute_action",
]
