"""Browser snapshot - DOM extraction, page state capture, component manifests, and visual diffing."""

from __future__ import annotations

from monkeylm.browser.snapshot.dom import capture_dom_and_layout
from monkeylm.browser.snapshot.manifest import (
    _normalize_manifest_text,
    _manifest_component_key,
    diff_component_manifests,
    extract_component_manifest,
)
from monkeylm.browser.snapshot.state import (
    _normalize_form_control_raw,
    _sanitize_filename,
    get_page_state,
    state_to_prompt,
)
from monkeylm.browser.snapshot.visual import compute_max_layout_shift, compare_screenshots_pixelmatch

__all__ = [
    "capture_dom_and_layout",
    "_normalize_manifest_text",
    "_manifest_component_key",
    "diff_component_manifests",
    "extract_component_manifest",
    "_normalize_form_control_raw",
    "_sanitize_filename",
    "get_page_state",
    "state_to_prompt",
    "compute_max_layout_shift",
    "compare_screenshots_pixelmatch",
]
