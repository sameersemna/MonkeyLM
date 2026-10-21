"""B5 — ``DefectVerdict`` structured output schema for the vision pipeline.

The model is asked for ``{verdict, box_2d, defect_type, severity,
description, remediation, action_url}``. The verdict dataclass captures
exactly that shape so downstream consumers (PDF plate captions, defect
deduplication, eval harness) can rely on a typed object rather than
re-parsing free-form JSON.

Evidence motivating the schema choices:

* Closed ``defect_type`` enum aligns with the strings already emitted
  in ``monkeylm/browser/actions/executor.py``: ``visual-diff-region``,
  ``blank-screen``, ``ocr-low-contrast``, ``ocr-error-text``, plus
  ``layout-shift`` and ``element-missing`` from snapshot diffing, and
  the catch-all ``other``.
* ``verdict: accept | refine | reject`` lets the model disagree with
  the CV anchor (Rex-Thinker, arXiv 2506.04034: hints give
  ``+13.2 Recall, +11.7 Precision, +10.8 Density F1``; the reject
  branch keeps anchoring from degenerating into rubber-stamping).
* ``severity`` is one of ``info | minor | serious | critical`` so the
  PDF plate ranking and the eval harness's ``weighted-kappa`` metric
  have a finite, documented label set.
* ``remediation`` + ``action_url`` round out the AutoVisQA-style
  pairing of structured fields with a narrated explanation
  (``researchsquare rs-9383307``).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from monkeylm.models.ollama import _safe_json_parse


# Closed enums — closed by intent, but every consumer must guard
# against drift by mapping unknown values to ``"other"`` /
# ``"info"`` / ``"accept"`` fall-backs rather than raising.
DEFECT_TYPES: Tuple[str, ...] = (
    "visual-regression",
    "blank-screen",
    "ocr-error-text",
    "low-contrast",
    "layout-shift",
    "element-missing",
    "other",
)
SEVERITIES: Tuple[str, ...] = ("info", "minor", "serious", "critical")
VERDICTS: Tuple[str, ...] = ("accept", "refine", "reject")


@dataclass
class DefectVerdict:
    """Structured vision-model output for a single annotated screenshot."""

    # Localized bounding box in [0,1] (ymin, xmin, ymax, xmax). Empty
    # box ``[0.0, 0.0, 0.0, 0.0]`` is the documented "no defect" signal.
    box_2d: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    # Coarse classification of the defect. One of ``DEFECT_TYPES``.
    defect_type: str = "other"
    # One of ``SEVERITIES``. Defaults to "info" so missing values do
    # not silently upgrade a step's risk.
    severity: str = "info"
    # Verdict on the CV-supplied anchor: "accept" (anchor already
    # correct), "refine" (box is different from anchor), "reject"
    # (anchor is wrong — bounding box is set independently).
    verdict: str = "accept"
    # Free-text sentence describing the issue.
    description: str = ""
    # Short remediation hint shown in the PDF plate if non-empty.
    remediation: str = ""
    # Optional URL the operator can open for more context.
    action_url: str = ""
    # Confidence in [0,1]. Used by B10's second-pass verifier and the
    # eval harness's confidence-vs-accuracy curve.
    confidence: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def empty_verdict() -> DefectVerdict:
    """Return a no-defect verdict with the empty-box signal set."""
    return DefectVerdict(box_2d=[0.0, 0.0, 0.0, 0.0])


def _coerce_enum(value: Any, allowed: Tuple[str, ...], default: str) -> str:
    """Map ``value`` to one of ``allowed``; fall back to ``default``."""
    if not isinstance(value, str):
        return default
    value = value.strip().lower()
    return value if value in allowed else default


def parse_verdict(
    content: Any,
    *,
    model: Optional[str] = None,
    image_size: Optional[Tuple[int, int]] = None,
) -> DefectVerdict:
    """Parse a model reply into a :class:`DefectVerdict`.

    Tolerates the legacy ``{box_2d, description}`` schema (Phase 1
    output shape) by populating only the box and description and
    defaulting every other field.  Tolerates prose, partial JSON,
    and non-string content.

    The bbox is parsed through :func:`monkeylm.models.vision._parse_vision_box`
    so model-aware coordinate scaling (0-1000 for Gemini/Qwen, px for
    llama with ``image_size``) is applied.  Direct callers that have
    already run the parser can pass ``model=None`` and ``image_size=None``
    to preserve the legacy clamp-only behavior.
    """
    if isinstance(content, DefectVerdict):
        return content
    if isinstance(content, dict):
        source: Dict[str, Any] = content
        raw_text: Optional[str] = None
    else:
        if not isinstance(content, str) or not content:
            return empty_verdict()
        raw_text = content
        parsed = _safe_json_parse(content)
        if not isinstance(parsed, dict):
            from monkeylm.models.vision import _extract_description, _parse_vision_box

            box = _parse_vision_box(
                content, model=model, image_size=image_size
            )
            description = _extract_description(content) or ""
            if box is None:
                return DefectVerdict(description=description)
            return DefectVerdict(box_2d=box, description=description)
        source = parsed

    # Locating the bbox correctly is the whole point of Phase 1.2 (B2).
    # The :func:`monkeylm.models.vision._parse_vision_box` helper honors
    # the model-specific coordinate scale and the empty-box escape.
    from monkeylm.models.vision import _parse_vision_box

    box: List[float]
    if raw_text is not None:
        parsed_box = _parse_vision_box(
            raw_text, model=model, image_size=image_size
        )
    else:
        # ``source`` is the dict; extract the raw tuple and clamp.
        box_raw = source.get("box_2d")
        if isinstance(box_raw, (list, tuple)) and len(box_raw) == 4:
            try:
                floats = [float(v) for v in box_raw]
                bounded: List[float] = []
                for v in floats:
                    bounded.append(max(0.0, min(1.0, v)))
                parsed_box = bounded
            except (TypeError, ValueError):
                parsed_box = [0.0, 0.0, 0.0, 0.0]
        else:
            parsed_box = None
    if parsed_box is None:
        box = [0.0, 0.0, 0.0, 0.0]
    else:
        box = list(parsed_box)

    description = source.get("description")
    if not isinstance(description, str):
        description = ""
    description = description.strip()

    defect_type = _coerce_enum(source.get("defect_type"), DEFECT_TYPES, "other")
    severity = _coerce_enum(source.get("severity"), SEVERITIES, "info")
    verdict_action = _coerce_enum(source.get("verdict"), VERDICTS, "accept")

    remediation_raw = source.get("remediation") or source.get("remediation_advice")
    if not isinstance(remediation_raw, str):
        remediation_raw = ""
    action_url_raw = source.get("action_url")
    if not isinstance(action_url_raw, str):
        action_url_raw = ""

    confidence_raw = source.get("confidence")
    try:
        confidence = float(confidence_raw)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    return DefectVerdict(
        box_2d=box,
        defect_type=defect_type,
        severity=severity,
        verdict=verdict_action,
        description=description,
        remediation=remediation_raw[:512],
        action_url=action_url_raw[:512],
        confidence=confidence,
    )


# Convenience: structured type alias for callers that want a positional
# verb-style contract instead of the dataclass import.
VerdictAction = str
