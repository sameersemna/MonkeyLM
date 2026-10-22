"""Vision-related building blocks.

This subpackage holds the Phase 2 work (B4-B7, B10) that grew past what
fits in a single ~400-LOC module under M2.  The historic
``monkeylm.models.vision`` file's symbols (``_draw_red_box_arrow``,
``_parse_vision_box``, ``_build_vision_annotation_prompt``, …) are
re-exported here for backward compatibility.
"""

from __future__ import annotations

# Always-available helpers from the legacy file (defined in
# ``_legacy.py`` to keep this __init__ short).
from ._legacy import (
    annotate_relevant_screenshot,
    _build_vision_annotation_prompt,
    _coord_scale,
    _draw_red_box_arrow,
    _extract_box_from_prose,
    _extract_description,
    _is_cloud_vision_model,
    _parse_vision_box,
    _resolve_label_font,
    _wrap_text_to_lines,
)

# Phase 2 modules.
from .verdict import (
    DEFECT_TYPES,
    SEVERITIES,
    DefectVerdict,
    VerdictAction,
    empty_verdict,
    parse_verdict,
)
from .prompts import (
    build_defect_class_prompt,
    build_prompt_for_defect_type,
    normalize_defect_type,
)
from .overlays import (
    render_numbered_overlay,
    regions_to_som,
    som_prompt_intro,
)
from .callouts import render_callouts
from .context import defect_context
from .verifier import (
    DEFAULT_VERIFIER_CONFIDENCE_THRESHOLD,
    DEFAULT_VERIFIER_PROMPT,
    run_verification,
)

__all__ = [
    # Phase 1 legacy surface (backward-compatible).
    "annotate_relevant_screenshot",
    "_build_vision_annotation_prompt",
    "_coord_scale",
    "_draw_red_box_arrow",
    "_extract_box_from_prose",
    "_extract_description",
    "_is_cloud_vision_model",
    "_parse_vision_box",
    "_resolve_label_font",
    "_wrap_text_to_lines",
    # B5 verdict schema.
    "DEFECT_TYPES",
    "SEVERITIES",
    "DefectVerdict",
    "VerdictAction",
    "empty_verdict",
    "parse_verdict",
    # B7 per-class prompt dispatcher.
    "build_defect_class_prompt",
    "build_prompt_for_defect_type",
    "normalize_defect_type",
    # B6 Set-of-Mark overlay.
    "render_numbered_overlay",
    "regions_to_som",
    "som_prompt_intro",
    # Multi-region numbered callouts.
    "render_callouts",
    # B3 CV evidence normalizer.
    "defect_context",
    # B10 second-pass verifier.
    "DEFAULT_VERIFIER_CONFIDENCE_THRESHOLD",
    "DEFAULT_VERIFIER_PROMPT",
    "run_verification",
]
