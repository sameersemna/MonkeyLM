"""Browser module - snapshot, lifecycle, and action execution."""

try:
    from monkeylm.browser.snapshot import (
        capture_dom_and_layout,
        get_page_state,
        state_to_prompt,
        compute_max_layout_shift,
        compare_screenshots_pixelmatch,
        extract_component_manifest,
        diff_component_manifests,
    )
except Exception:  # pragma: no cover - optional dependency path
    capture_dom_and_layout = get_page_state = state_to_prompt = compute_max_layout_shift = compare_screenshots_pixelmatch = extract_component_manifest = diff_component_manifests = None

try:
    from monkeylm.browser.lifecycle import (
        wait_for_page_ready,
        launch_context_with_fallback,
        handle_dialog,
        resilient_page_goto,
        _validate_navigation_url,
    )
except Exception:  # pragma: no cover - optional dependency path
    wait_for_page_ready = launch_context_with_fallback = handle_dialog = resilient_page_goto = _validate_navigation_url = None

try:
    from monkeylm.browser.actions import (
        execute_action,
        _extract_target_id,
        _locator_for_target_id,
        _resolve_interaction_mode,
        _fill_select_option,
        _resolve_form_boundary,
        _compute_action_path_hash,
    )
except Exception:  # pragma: no cover - optional dependency path
    execute_action = _extract_target_id = _locator_for_target_id = _resolve_interaction_mode = _fill_select_option = _resolve_form_boundary = _compute_action_path_hash = None

try:
    from monkeylm.browser.auth import attempt_login_with_target_credentials, infer_login_field_targets
except Exception:  # pragma: no cover - optional dependency path
    attempt_login_with_target_credentials = infer_login_field_targets = None

__all__ = [
    "capture_dom_and_layout",
    "get_page_state",
    "state_to_prompt",
    "compute_max_layout_shift",
    "compare_screenshots_pixelmatch",
    "extract_component_manifest",
    "diff_component_manifests",
    "wait_for_page_ready",
    "launch_context_with_fallback",
    "handle_dialog",
    "resilient_page_goto",
    "_validate_navigation_url",
    "execute_action",
    "attempt_login_with_target_credentials",
    "infer_login_field_targets",
    "_extract_target_id",
    "_locator_for_target_id",
    "_resolve_interaction_mode",
    "_fill_select_option",
    "_resolve_form_boundary",
    "_compute_action_path_hash",
]
