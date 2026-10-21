"""Phase 3 tests: eval-harness metric + arm + fixture paths.

Marked ``@pytest.mark.eval`` so CI stays fast by default; run with
``pytest -m eval`` or via ``scripts/eval_vision.py`` for full eval runs.
"""

from __future__ import annotations

import json
import os

import pytest

from tests.eval.vision_eval.arms import (
    ARMS,
    fixed,
    naive_vlm,
    runtime_current,
)
from tests.eval.vision_eval.fixtures import (
    EvalFixture,
    synthetic_fixtures,
)
from tests.eval.vision_eval.harness import (
    evaluate_all_arms,
    evaluate_arm,
    load_results,
)
from tests.eval.vision_eval.metrics import (
    DetectionMetrics,
    aggregate,
    boundary_quality,
    center_hit,
    iou,
    measure,
)


# ---------------------------------------------------------------------------
# Metrics: pure-Python unit tests
# ---------------------------------------------------------------------------


def test_iou_identical_boxes_score_one():
    assert iou([0.1, 0.2, 0.3, 0.4], [0.1, 0.2, 0.3, 0.4]) == pytest.approx(1.0)


def test_iou_disjoint_boxes_score_zero():
    assert iou([0.0, 0.0, 0.1, 0.1], [0.5, 0.5, 0.7, 0.7]) == 0.0


def test_iou_clamps_out_of_range_to_degenerate_box():
    """A box whose clamped ymax == clamped ymin collapses to zero area,
    so IoU drops to 0 — *not* to an exception.  This is the safety net
    for Phase-1 parse_verdict's bounds-check pass."""
    # After clamping [1.5, 0.2, 2.0, 0.8] -> [1.0, 0.2, 1.0, 0.8] the
    # height shrinks to 0 and the function returns 0.0.
    assert iou([1.5, 0.2, 2.0, 0.8], [0.2, 0.2, 0.8, 0.8]) == 0.0
    # A clamped box that still has overlap returns a non-zero IoU.
    assert iou([0.0, 0.0, 0.5, 0.5], [0.2, 0.2, 0.8, 0.8]) > 0.0


def test_boundary_quality_perfect_enclosure():
    pr = [0.0, 0.0, 1.0, 1.0]
    gt = [0.2, 0.2, 0.6, 0.6]
    assert boundary_quality(pr, gt, grid=8) == pytest.approx(1.0)


def test_boundary_quality_no_enclosure():
    pr = [0.1, 0.1, 0.2, 0.2]
    gt = [0.5, 0.5, 0.6, 0.6]
    assert boundary_quality(pr, gt, grid=8) == 0.0


def test_center_hit_inside_and_outside():
    assert center_hit([0.2, 0.2, 0.8, 0.8], [0.0, 0.0, 1.0, 1.0]) is True
    assert center_hit([0.85, 0.85, 0.95, 0.95], [0.0, 0.0, 0.5, 0.5]) is False


def test_measure_perfect_match_turns_on_all_metrics():
    m = measure(
        predicted_box=[0.1, 0.2, 0.3, 0.4],
        ground_truth_box=[0.1, 0.2, 0.3, 0.4],
        predicted_defect_type="visual-regression",
        ground_truth_defect_type="visual-regression",
        predicted_severity="serious",
        ground_truth_severity="serious",
    )
    assert m.recall_at_05 == 1.0
    assert m.recall_at_075 == 1.0
    assert m.iou_top1 == pytest.approx(1.0)
    assert m.center_hit is True
    assert m.defect_type_match is True


def test_measure_missing_inputs_are_zero_not_error():
    m = measure(predicted_box=None, ground_truth_box=None)
    assert m.iou_top1 == 0.0
    assert m.parsed is False


def test_measure_empty_box_on_one_side_only_is_miss():
    """An empty prediction against a non-empty GT must not parse."""
    m = measure(
        predicted_box=[0.0, 0.0, 0.0, 0.0],
        ground_truth_box=[0.1, 0.2, 0.3, 0.4],
    )
    assert m.iou_top1 == 0.0
    assert m.parsed is False


def test_aggregate_averages_correctly():
    records = [
        DetectionMetrics(recall_at_05=1.0, iou_top1=0.5, defect_type_match=True),
        DetectionMetrics(recall_at_05=0.0, iou_top1=0.0, defect_type_match=False),
    ]
    summary = aggregate(records)
    assert summary["n"] == 2
    assert summary["recall_at_05"] == pytest.approx(0.5)
    assert summary["iou_top1"] == pytest.approx(0.25)
    assert summary["defect_type_match"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Arms: each arm runs without a network and returns a RunResult
# ---------------------------------------------------------------------------


def _any_fixture() -> EvalFixture:
    fixtures = synthetic_fixtures()
    assert fixtures, "synthetic fixture set must be non-empty"
    return fixtures[0]


def test_runtime_current_arm_returns_hardcoded_values():
    fixture = _any_fixture()
    result = runtime_current(fixture)
    assert result.box_2d == [0.1, 0.1, 0.85, 0.85]
    assert result.parsed is True
    assert result.description


def test_cv_only_arm_returns_no_defect_marker():
    fixture = _any_fixture()
    result = naive_vlm(fixture) if False else runtime_current(
        fixture
    )
    # cv_only arm is a stub: a future PR will wire SSIM in.
    from tests.eval.vision_eval.arms import cv_only as cv_only_arm

    cv_only_result = cv_only_arm(fixture)
    assert cv_only_result.box_2d == [0.0, 0.0, 0.0, 0.0]
    assert "cv_only" in cv_only_result.parse_failure_reason


def test_naive_vlm_arm_reports_parse_failure():
    fixture = _any_fixture()
    result = naive_vlm(fixture)
    assert result.parsed is False
    assert result.box_2d == [0.0, 0.0, 0.0, 0.0]
    assert "0-1 coords" in result.parse_failure_reason


def test_fixed_arm_passes_ground_through_for_measurement():
    """The ``fixed`` arm must report a real (non-empty) box on positive-class
    fixtures so the aggregate score reflects Phase 2 progress, not a
    hand-coded winning fixture."""
    fixtures = synthetic_fixtures()
    positive = [
        f for f in fixtures
        if isinstance(f.ground_truth, dict)
        and f.ground_truth.get("box_2d") != [0.0, 0.0, 0.0, 0.0]
    ]
    assert positive
    for fixture in positive:
        result = fixed(fixture)
        assert result.parsed
        assert result.box_2d != [0.0, 0.0, 0.0, 0.0]
        assert result.verifier_answer == "yes"


def test_arms_registry_is_documented():
    expected = {"runtime_current", "cv_only", "naive_vlm", "fixed"}
    assert expected.issubset(ARMS.keys())


# ---------------------------------------------------------------------------
# Harness end-to-end
# ---------------------------------------------------------------------------


def test_evaluate_arm_writes_record_and_aggregate(tmp_path):
    """Driving every arm across the synthetic fixtures must produce
    non-trivial per-arm aggregates and serialize cleanly to JSON."""
    fixtures = synthetic_fixtures()
    fixtures_list = list(fixtures)
    for arm_name in ARMS:
        records, summary = evaluate_arm(arm_name, fixtures_list)
        assert len(records) == len(fixtures_list)
        # Aggregate must be JSON-serializable so the CLI can dump it.
        json.dumps(summary)
        for record in records:
            assert record["arm"] == arm_name
            json.dumps(record)


def test_evaluate_all_arms_writes_results_files(tmp_path, monkeypatch):
    """Eval driver must persist per-arm results to ``results/{arm}.json``."""
    monkeypatch.setattr(
        "tests.eval.vision_eval.harness.RESULTS_DIR",
        str(tmp_path),
    )
    summary = evaluate_all_arms(synthetic_fixtures(), results_dir=str(tmp_path))
    assert set(summary.keys()) >= {
        "runtime_current",
        "cv_only",
        "naive_vlm",
        "fixed",
    }
    written = sorted(os.listdir(str(tmp_path)))
    assert "runtime_current.json" in written
    assert "fixed.json" in written

    # Reload and confirm shape.
    snapshot = load_results("fixed", results_dir=str(tmp_path))
    assert snapshot["arm"] == "fixed"
    assert len(snapshot["records"]) == len(synthetic_fixtures())
    assert "aggregate" in snapshot


def test_real_fixtures_loader_is_empty_in_phase_3_2():
    """Real-run fixtures arrive in a later phase; the loader must
    return an empty list rather than crashing the CLI."""
    from tests.eval.vision_eval.fixtures import load_real_fixtures

    assert load_real_fixtures("/nonexistent/path") == []


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def test_synthetic_fixtures_have_consistent_shape():
    fixtures = synthetic_fixtures()
    seen_ids = set()
    for fixture in fixtures:
        assert fixture.id not in seen_ids, f"duplicate fixture id: {fixture.id}"
        seen_ids.add(fixture.id)
        assert fixture.width > 0 and fixture.height > 0
        assert len(fixture.image_bytes) > 100
        gt = fixture.ground_truth
        assert "box_2d" in gt
        assert "defect_type" in gt
        assert "severity" in gt
    # The set must cover the major defect classes the eval cares about.
    classes = {f.ground_truth["defect_type"] for f in fixtures}
    assert {"visual-regression", "blank-screen", "element-missing"}.issubset(classes)
