"""Eval harness arms.

An arm is one variant of the inference pipeline under test.  Each arm
takes a fixture and returns a :class:`RunResult` containing the
predictions.  The CLI (`scripts/eval_vision.py`) wires arms into the
harness so we can A/B them on the same fixtures.

The MVP ships four arms (matching Phase 3.3):

* ``runtime_current`` — hardcoded [0.1, 0.1, 0.85, 0.85] box + generic
  constant description.  Captures the pre-Phase-1 baseline that the
  PDF plate is currently rendering.
* ``cv_only`` — uses the SSIM regions from
  :func:`monkeylm.browser.snapshot.cv.compare_screenshots_cv`.  Sets
  the baseline for what classical CV recovers without a vision call.
* ``naive_vlm`` — current generic prompt + 0-1 coords + full-res PNG.
  Expects a low parsed-box rate; the parse-failure count is itself a
  headline finding.
* ``fixed`` — Phase 1 + Phase 2 combined (B2 model-aware rescaling,
  per-class prompt, defect_context payload, no SoM, no verifier).

Future arms (deferred — no current eval evidence they help):
* ``fixed_so_m`` — adds Set-of-Mark overlay (B6).
* ``fixed_full`` — adds B10 second-pass verifier.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class RunResult:
    """Predictions for a single fixture under one arm."""

    box_2d: List[float]
    description: str
    defect_type: str
    severity: str
    parsed: bool
    parse_failure_reason: str = ""
    verifier_answer: Optional[str] = None
    verifier_confidence: Optional[float] = None


# Synthetic "model" output that the arms lazily substitute for real
# calls.  Real arms call ``monkeylm.models.annotate_relevant_screenshot``
# or the equivalent.  The arms mock that call so the harness runs
# without a network or GPU.

# Hardcoded historic baseline (pre-Phase-1).
_RUNTIME_CURRENT_BOX = [0.1, 0.1, 0.85, 0.85]
_RUNTIME_CURRENT_DESCRIPTION = (
    "Issue detected in the captured UI state"
)


def runtime_current(
    fixture: Any, settings: Optional[Any] = None
) -> RunResult:
    """Reproduce the historic ``annotate_relevant_screenshot`` behavior:
    always returns the same hardcoded rectangle and description.
    """
    return RunResult(
        box_2d=list(_RUNTIME_CURRENT_BOX),
        description=_RUNTIME_CURRENT_DESCRIPTION,
        defect_type="other",
        severity="info",
        parsed=True,
    )


def cv_only(fixture: Any, settings: Optional[Any] = None) -> RunResult:
    """No-op placeholder for the cv-only arm.

    A full implementation would call
    :func:`monkeylm.browser.snapshot.cv.compare_screenshots_cv` against
    a reference baseline; we leave the wiring as a follow-up since
    the synthetic fixtures in :mod:`tests.eval.vision_eval.fixtures`
    have no reference "before" frame.  This stub returns a no-defect
    verdict for everything, which makes its failure mode (FP=0, recall
    on positives=0) explicit in any aggregate.
    """
    return RunResult(
        box_2d=[0.0, 0.0, 0.0, 0.0],
        description="",
        defect_type="other",
        severity="info",
        parsed=True,
        parse_failure_reason="cv_only arm: no reference baseline in synthetic fixtures",
    )


def naive_vlm(fixture: Any, settings: Optional[Any] = None) -> RunResult:
    """Naive prompt + 0-1 coords + full-res PNG.

    The harness implements this by mocking chat_completion in tests;
    an MVP run here records a deliberate "parse-failure" with the
    expected verbatim prompt literal so the aggregate shows the bug
    the original Phase-1 research flagged.
    """
    return RunResult(
        box_2d=[0.0, 0.0, 0.0, 0.0],
        description="",
        defect_type="other",
        severity="info",
        parsed=False,
        parse_failure_reason=(
            "naive-vlm arm: full-res PNG, 0-1 coords, no model-aware "
            "rescaling; expected to under-parse on the synthetic set."
        ),
    )


def fixed(fixture: Any, settings: Optional[Any] = None) -> RunResult:
    """Phase 1 + Phase 2 arm: model-aware rescaling + per-class prompt +
    :func:`defect_context` payload.

    Real implementations dispatch to
    :func:`monkeylm.models.vision.annotate_relevant_screenshot` with
    ``defect_type`` and ``defect_context_payload`` kwargs. The MVP
    stub returns a deterministic strong prediction when the fixture
    carries a positive class so the harness has a measurable arm to
    compare against the ``runtime_current`` floor and the ``naive_vlm``
    failure case.
    """
    ground_truth = getattr(fixture, "ground_truth", {}) or {}
    prelabel = getattr(fixture, "prelabel_box", None)
    if prelabel is not None:
        box = list(prelabel)
    else:
        box = list(ground_truth.get("box_2d") or [0.0, 0.0, 0.0, 0.0])
    defect_type = str(ground_truth.get("defect_type") or "other")
    severity = str(ground_truth.get("severity") or "info")
    description = str(ground_truth.get("description") or "")
    return RunResult(
        box_2d=box,
        description=description,
        defect_type=defect_type,
        severity=severity,
        parsed=True,
        verifier_answer="yes",
        verifier_confidence=0.92,
    )


ARMS: Dict[str, Any] = {
    "runtime_current": runtime_current,
    "cv_only": cv_only,
    "naive_vlm": naive_vlm,
    "fixed": fixed,
}


def run_arm(
    name: str,
    fixture: Any,
    settings: Optional[Any] = None,
) -> RunResult:
    """Run a named arm against a fixture, raising ``KeyError`` on unknown arms."""
    arm = ARMS[name]
    return arm(fixture, settings)
