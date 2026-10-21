"""Eval harness driver: takes fixtures and arms, produces per-arm aggregates.

The driver is deliberately lightweight: no I/O beyond reading
fixture metadata and writing one JSON results file.  Tests assert
the JSON shape; the CLI :mod:`scripts.eval_vision` writes the same
JSON to ``tests/eval/vision_eval/results/{arm}.json``.

Public functions:

* :func:`evaluate_arm` — apply one arm to every fixture, return
  ``(per_record, aggregate)``.
* :func:`evaluate_all_arms` — apply every registered arm to the
  fixtures, write a results file per arm.
* :func:`load_results` — read a previously-written results file
  back (used by the ``--compare`` CLI flag).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from typing import Any, Dict, Iterable, List, Tuple

from .arms import ARMS, run_arm
from .fixtures import EvalFixture, synthetic_fixtures
from .metrics import DetectionMetrics, aggregate, measure


RESULTS_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "results"
)


def evaluate_arm(
    arm_name: str,
    fixtures: Iterable[EvalFixture],
) -> Tuple[List[Dict[str, Any]], Dict[str, float]]:
    """Run ``arm_name`` over every fixture and aggregate the metrics."""
    records: List[Dict[str, Any]] = []
    metric_records: List[DetectionMetrics] = []
    for fixture in fixtures:
        result = run_arm(arm_name, fixture)
        metrics = measure(
            result.box_2d,
            fixture.ground_truth.get("box_2d") if isinstance(fixture.ground_truth, dict) else None,
            predicted_defect_type=result.defect_type,
            ground_truth_defect_type=str(
                fixture.ground_truth.get("defect_type")
            ) if isinstance(fixture.ground_truth, dict) else None,
            predicted_severity=result.severity,
            ground_truth_severity=str(
                fixture.ground_truth.get("severity")
            ) if isinstance(fixture.ground_truth, dict) else None,
        )
        records.append(
            {
                "fixture_id": fixture.id,
                "arm": arm_name,
                "result": asdict(result),
                "metrics": asdict(metrics),
                "fixture_source": fixture.source,
                "fixture_splits": list(fixture.splits),
                "fixture_negative": fixture.negative,
            }
        )
        metric_records.append(metrics)
    return records, aggregate(metric_records)


def evaluate_all_arms(
    fixtures: Iterable[EvalFixture],
    *,
    results_dir: str = RESULTS_DIR,
    arms: Iterable[str] = ARMS.keys(),
) -> Dict[str, Dict[str, float]]:
    """Run every arm, write per-arm results JSON, return per-arm aggregates."""
    os.makedirs(results_dir, exist_ok=True)
    fixtures_list = list(fixtures)
    summary: Dict[str, Dict[str, float]] = {}
    for arm_name in arms:
        records, aggregate_metrics = evaluate_arm(arm_name, fixtures_list)
        out_path = os.path.join(results_dir, f"{arm_name}.json")
        with open(out_path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "arm": arm_name,
                    "fixtures": len(fixtures_list),
                    "records": records,
                    "aggregate": aggregate_metrics,
                },
                handle,
                indent=2,
                default=str,
            )
        summary[arm_name] = aggregate_metrics
    return summary


def load_results(arm_name: str, results_dir: str = RESULTS_DIR) -> Dict[str, Any]:
    """Read a previously-written results file back."""
    path = os.path.join(results_dir, f"{arm_name}.json")
    if not os.path.exists(path):
        return {"arm": arm_name, "aggregate": {}, "records": []}
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def default_fixtures() -> List[EvalFixture]:
    """Return the v1 synthetic fixture set; real-run fixtures appended later."""
    return synthetic_fixtures()
