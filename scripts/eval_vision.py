"""Vision eval harness CLI.

Phase 3.2 driver.  Runs each registered arm across the synthetic
fixture set (and any appended real-run fixtures) and writes one
results JSON per arm to ``tests/eval/vision_eval/results/{arm}.json``.

Example::

    .venv/bin/python scripts/eval_vision.py --arms fixed,naive_vlm
    .venv/bin/python scripts/eval_vision.py --print-summary

The arm stubs under ``tests.eval.vision_eval.arms`` are intentionally
deterministic so the harness runs without a GPU or network.  A future
PR will add ``--end-to-end`` that wires each arm through the real
``annotate_relevant_screenshot`` path; until then this CLI's job is to
freeze the *measurement surface* (metric definitions, results file
shape, fixture format) so every subsequent arm can be diffed against
the same baseline rows.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tests.eval.vision_eval.arms import ARMS
from tests.eval.vision_eval.harness import (
    RESULTS_DIR,
    default_fixtures,
    evaluate_all_arms,
    load_results,
)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Run the vision-eval harness across one or more arms.",
    )
    parser.add_argument(
        "--arms",
        default=",".join(ARMS.keys()),
        help=(
            "Comma-separated list of arm names to run.  Defaults to "
            "every registered arm.  Available: " + ", ".join(ARMS.keys())
        ),
    )
    parser.add_argument(
        "--results-dir",
        default=RESULTS_DIR,
        help="Directory to write per-arm results JSON files into.",
    )
    parser.add_argument(
        "--print-summary",
        action="store_true",
        help="Print the per-arm aggregate summary after running.",
    )
    parser.add_argument(
        "--load-only",
        default=None,
        help=(
            "Skip running; instead read the named arm's results file "
            "and print its aggregate.  Useful for quick inspection "
            "between edits."
        ),
    )
    args = parser.parse_args(argv)

    if args.load_only:
        snapshot = load_results(args.load_only, args.results_dir)
        print(json.dumps(snapshot.get("aggregate", {}), indent=2))
        return 0

    arms = [
        name.strip() for name in args.arms.split(",") if name.strip()
    ]
    for arm in arms:
        if arm not in ARMS:
            print(f"Unknown arm: {arm}.  Known: {sorted(ARMS.keys())}")
            return 2

    fixtures = default_fixtures()
    summary = evaluate_all_arms(
        fixtures, results_dir=args.results_dir, arms=arms
    )

    if args.print_summary:
        for arm in arms:
            print(f"=== {arm} ===")
            print(json.dumps(summary.get(arm, {}), indent=2))
            print()

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
