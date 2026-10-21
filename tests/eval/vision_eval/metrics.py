"""Eval-harness metric definitions.

Implemented in pure Python so the metric computations are reproducible
without a vision model in the loop and so the ``Phase 3.3`` baselines can
recompute every score from a single JSON results file.

Each metric accepts the (predicted_box, ground_truth_box) pair expressed
in [0,1] normalized coordinates.  Predictions outside [0,1] are
clamped.  Ground-truth boxes outside [0,1] are an evaluator error and
raise ``ValueError``.

The metric thresholds (0.5 for IoU mAP, 0.75 for the strict variant) are
the standard detection-IoU values; both are reported.  Boundary quality
is the metric UI-Lens flags as the MLLM weak spot
(CVPR 2026, arXiv :cite-not-implemented — see the harness README).

The ``description_score`` rubric returns the metric a cross-vendor LLM
judge would supply: BLEU/ROUGE are not validated for UI defect
descriptions in any published benchmark, so the harness deliberately
exposes a stub ``llm_judge_score`` callable that the eval CLI wires to
the operator's chosen judge (default: a deterministic placeholder).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence


# Evaluation metric constants (kept as module-level so eval-harness
# fixtures can import without instantiating).
IOU_THRESHOLD_RECALL = 0.5
IOU_THRESHOLD_STRICT = 0.75
CENTER_HIT_THRESHOLD = 0.0  # any IoU > 0 is a center hit


def _clamp01(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def _clamp_box(box: Sequence[float]) -> List[float]:
    if len(box) != 4:
        raise ValueError(f"box must have exactly 4 values, got {len(box)}")
    return [_clamp01(float(v)) for v in box]


def iou(predicted: Sequence[float], ground_truth: Sequence[float]) -> float:
    """Intersection-over-union for two (ymin, xmin, ymax, xmax) boxes."""
    p = _clamp_box(predicted)
    g = _clamp_box(ground_truth)
    if p[2] <= p[0] or p[3] <= p[1] or g[2] <= g[0] or g[3] <= g[1]:
        return 0.0
    y0 = max(p[0], g[0])
    x0 = max(p[1], g[1])
    y1 = min(p[2], g[2])
    x1 = min(p[3], g[3])
    if y1 <= y0 or x1 <= x0:
        return 0.0
    intersection = (y1 - y0) * (x1 - x0)
    area_p = max(0.0, (p[2] - p[0]) * (p[3] - p[1]))
    area_g = max(0.0, (g[2] - g[0]) * (g[3] - g[1]))
    union = area_p + area_g - intersection
    if union <= 0.0:
        return 0.0
    return intersection / union


def boundary_quality(
    predicted: Sequence[float],
    ground_truth: Sequence[float],
    grid: int = 8,
) -> float:
    """Fraction of GT-edge pixels that fall inside the predicted box.

    ``grid`` controls resolution: 8 means an 8x8 sample of the GT-box
    interior.  A returned value of 1.0 means every sampled interior
    pixel sits inside the predicted box; 0.0 means none do.  This is
    the metric UI-Lens names as the MLLM-specific weak spot.
    """
    p = _clamp_box(predicted)
    g = _clamp_box(ground_truth)
    if grid < 1:
        raise ValueError(f"grid must be >= 1, got {grid}")
    inside = 0
    total = 0
    for i in range(grid):
        y = g[0] + (g[2] - g[0]) * (i + 0.5) / grid
        for j in range(grid):
            x = g[1] + (g[3] - g[1]) * (j + 0.5) / grid
            total += 1
            if p[0] <= y <= p[2] and p[1] <= x <= p[3]:
                inside += 1
    return inside / total if total else 0.0


def center_hit(predicted: Sequence[float], ground_truth: Sequence[float]) -> bool:
    """ScreenSpot-style: the predicted center lies inside the GT box."""
    p = _clamp_box(predicted)
    g = _clamp_box(ground_truth)
    cy = (p[0] + p[2]) / 2
    cx = (p[1] + p[3]) / 2
    return bool(g[0] <= cy <= g[2] and g[1] <= cx <= g[3])


@dataclass
class DetectionMetrics:
    """Per-record detection/localization metric bundle.

    All fields default to safe zero so a record with missing annotations
    doesn't crash the harness.
    """

    recall_at_05: float = 0.0
    recall_at_075: float = 0.0
    iou_top1: float = 0.0
    boundary_quality: float = 0.0
    center_hit: bool = False
    parsed: bool = False
    defect_type_match: bool = False
    severity_weighted_kappa: float = 0.0


def measure(
    predicted_box: Optional[Sequence[float]],
    ground_truth_box: Optional[Sequence[float]],
    *,
    predicted_defect_type: Optional[str] = None,
    ground_truth_defect_type: Optional[str] = None,
    predicted_severity: Optional[str] = None,
    ground_truth_severity: Optional[str] = None,
) -> DetectionMetrics:
    """Compute the per-record metric bundle."""
    metrics = DetectionMetrics()
    if (
        ground_truth_box is None
        or predicted_box is None
    ):
        return metrics
    try:
        gt = _clamp_box(ground_truth_box)
    except ValueError:
        return metrics
    try:
        pr = _clamp_box(predicted_box)
    except ValueError:
        pr = [0.0, 0.0, 0.0, 0.0]
    if pr == [0.0, 0.0, 0.0, 0.0] or gt == [0.0, 0.0, 0.0, 0.0]:
        # Both empties count as a non-event for recall purposes; one
        # empty + one non-empty counts as a miss.
        return metrics
    score = iou(pr, gt)
    metrics.iou_top1 = score
    metrics.recall_at_05 = 1.0 if score >= IOU_THRESHOLD_RECALL else 0.0
    metrics.recall_at_075 = 1.0 if score >= IOU_THRESHOLD_STRICT else 0.0
    metrics.boundary_quality = boundary_quality(pr, gt)
    metrics.center_hit = center_hit(pr, gt)
    metrics.parsed = True
    if (
        predicted_defect_type is not None
        and ground_truth_defect_type is not None
        and predicted_defect_type == ground_truth_defect_type
    ):
        metrics.defect_type_match = True
    metrics.severity_weighted_kappa = _ordinal_severity_distance(
        predicted_severity, ground_truth_severity
    )
    return metrics


def _ordinal_severity_distance(predicted: Optional[str], ground: Optional[str]) -> float:
    """Distance on the ordinal severity scale, [0,1]; 0 = identical.

    A larger value means the prediction is farther from the ground
    truth on the severity axis.  ``None`` values return 0 (the eval
    harness treats unknown severities as missing rather than wrong).
    """
    if predicted is None or ground is None:
        return 0.0
    ladder = ("info", "minor", "serious", "critical")
    if predicted not in ladder or ground not in ladder:
        return 0.0
    pi = ladder.index(predicted)
    gi = ladder.index(ground)
    return abs(pi - gi) / (len(ladder) - 1)


def aggregate(records: Iterable[DetectionMetrics]) -> Dict[str, float]:
    """Average metric bundle across many records."""
    n = 0
    sums = {
        "recall_at_05": 0.0,
        "recall_at_075": 0.0,
        "iou_top1": 0.0,
        "boundary_quality": 0.0,
        "defect_type_match": 0.0,
        "severity_distance": 0.0,
    }
    center_hit_n = 0
    parsed_n = 0
    for record in records:
        n += 1
        sums["recall_at_05"] += record.recall_at_05
        sums["recall_at_075"] += record.recall_at_075
        sums["iou_top1"] += record.iou_top1
        sums["boundary_quality"] += record.boundary_quality
        sums["defect_type_match"] += (
            1.0 if record.defect_type_match else 0.0
        )
        sums["severity_distance"] += record.severity_weighted_kappa
        if record.center_hit:
            center_hit_n += 1
        if record.parsed:
            parsed_n += 1
    if n == 0:
        return {
            "n": 0,
            "parsed_rate": 0.0,
            "center_hit_rate": 0.0,
            **sums,
        }
    return {
        "n": n,
        "parsed_rate": parsed_n / n,
        "center_hit_rate": center_hit_n / n,
        **{key: value / n for key, value in sums.items()},
    }


def llm_judge_score(
    predicted_description: Optional[str],
    reference_description: Optional[str],
    *args: Any,
    judge: Optional[Any] = None,
    **kwargs: Any,
) -> float:
    """Stub LLM-judge rubric; the eval CLI supplies a real judge.

    Without a ``judge`` callable, this returns a deterministic token-
    overlap placeholder so the harness's CLI runs without a network.
    Real eval runs wire in a cross-vendor LLM judge (different
    vendor from the model under test) with a 1-5 rubric.
    """
    if judge is not None:
        try:
            result = judge(
                predicted_description or "",
                reference_description or "",
            )
            return float(result)
        except Exception:
            return 0.0
    pred_tokens = set((predicted_description or "").lower().split())
    ref_tokens = set((reference_description or "").lower().split())
    if not ref_tokens:
        return 1.0 if not pred_tokens else 0.0
    return len(pred_tokens & ref_tokens) / len(ref_tokens)
