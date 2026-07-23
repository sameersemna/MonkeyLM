"""Browser actions - backward-compat shim. Re-exports from actions/ subpackage."""

from monkeylm.browser.actions.__init__ import (  # noqa: F401
    _compute_action_path_hash,
    _extract_target_id,
    _fill_select_option,
    _locator_for_target_id,
    _resolve_form_boundary,
    _resolve_interaction_mode,
    execute_action,
)
