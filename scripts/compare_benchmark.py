"""Compare two MonkeyLM benchmark runs (CV on vs CV off).

Usage: python3 scripts/compare_benchmark.py <run_a_dir> <run_b_dir>
Prints a side-by-side table of defect counts, detection signals, and timing.
"""

from __future__ import annotations

import json
import os
import sys


def _load(run_dir: str) -> dict:
    path = os.path.join(run_dir, "results.json")
    with open(path) as fh:
        return json.load(fh)


def _defect_counts(data: dict) -> dict:
    defects = data.get("defects", {})
    return {k: len(v) for k, v in defects.items() if isinstance(v, list) and v}


def _visual_signals(data: dict) -> dict:
    signals = {"visual_phash_freezes": 0, "dom_hash_freezes": 0}
    for item in data.get("defects", {}).get("ux_flow_freezes", []):
        if item.get("detection_signal") == "visual_phash":
            signals["visual_phash_freezes"] += 1
        else:
            signals["dom_hash_freezes"] += 1
    return signals


def main() -> None:
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    a_dir, b_dir = sys.argv[1], sys.argv[2]
    a, b = _load(a_dir), _load(b_dir)

    a_counts, b_counts = _defect_counts(a), _defect_counts(b)
    categories = sorted(set(a_counts) | set(b_counts))

    print(f"\n{'Category':<32} {'A (CV on)':>10} {'B (CV off)':>10} {'Delta':>8}")
    print("-" * 64)
    for cat in categories:
        delta = a_counts.get(cat, 0) - b_counts.get(cat, 0)
        print(f"{cat:<32} {a_counts.get(cat, 0):>10} {b_counts.get(cat, 0):>10} {delta:>+8}")

    a_sig, b_sig = _visual_signals(a), _visual_signals(b)
    print("-" * 64)
    print(f"{'ux freezes via visual_phash':<32} {a_sig['visual_phash_freezes']:>10} {b_sig['visual_phash_freezes']:>10}")
    print(f"{'ux freezes via dom_hash':<32} {a_sig['dom_hash_freezes']:>10} {b_sig['dom_hash_freezes']:>10}")

    a_steps = len(a.get("logs", []))
    b_steps = len(b.get("logs", []))
    print("-" * 64)
    print(f"{'steps executed':<32} {a_steps:>10} {b_steps:>10}")

    a_dur = a.get("duration_seconds") or a.get("run_duration_seconds")
    b_dur = b.get("duration_seconds") or b.get("run_duration_seconds")
    if a_dur and b_dur:
        overhead = ((float(a_dur) - float(b_dur)) / float(b_dur)) * 100.0
        print(f"{'wall-clock seconds':<32} {float(a_dur):>10.1f} {float(b_dur):>10.1f} {overhead:>+7.1f}%")

    # CV-specific artifacts
    a_regions = sum(1 for log in a.get("logs", []) if log.get("cv_diff_regions"))
    a_blank = sum(1 for log in a.get("logs", []) if log.get("blank_screen"))
    print("-" * 64)
    print(f"{'steps with CV diff regions':<32} {a_regions:>10} {'n/a':>10}")
    print(f"{'steps flagged blank-screen':<32} {a_blank:>10} {'n/a':>10}")
    print()


if __name__ == "__main__":
    main()
