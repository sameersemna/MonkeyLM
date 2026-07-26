#!/usr/bin/env python3
"""Backfill annotated screenshots for a completed test run.

A previous bug shipped where every ``*_annotated.png`` was a byte-for-byte
copy of the source screenshot because the Pillow optional-import helper
returned ``None`` for ``Image`` / ``ImageDraw`` on Pillow 11+ and the drawer
silently exited early. The drawer has been fixed, but the artifacts in
completed test runs are still the empty copies.

This script:
  1. Walks ``results.json`` for the given run directory.
  2. For every log entry that has ``screenshot_annotated: True``:
       a. Locates the source screenshot (stripping the ``_annotated`` suffix).
       b. Re-invokes :func:`monkeylm.models.annotate_relevant_screenshot` with
          the step's defect context.
       c. Overwrites the existing ``*_annotated.png`` with the freshly drawn
          one (now containing a real red box + arrow).
  3. Regenerates the executive PDF so the embedded proof plates are also fixed.

Usage:
    # Re-annotate every annotated screenshot in the run (serial, 1 LLM call at a time).
    python3 scripts/backfill_annotations.py reports/testrun_YYYYMMDD_HHMMSS/

    # Test on a single step first to make sure the model + drawer are happy.
    python3 scripts/backfill_annotations.py reports/testrun_YYYYMMDD_HHMMSS/ --single 18

    # Add a cool-down between calls (recommended on hot laptops / small GPUs).
    python3 scripts/backfill_annotations.py reports/testrun_YYYYMMDD_HHMMSS/ --cooldown 5

    # Only re-annotate the first 3 steps and skip PDF regeneration.
    python3 scripts/backfill_annotations.py reports/testrun_YYYYMMDD_HHMMSS/ --limit 3 --skip-pdf

Safety notes:
    This script invokes the configured vision model once per annotated
    screenshot, *serially* (no concurrency). On a hot laptop or a small GPU
    it is still advisable to leave a few seconds of cool-down between calls
    via ``--cooldown``. To validate the pipeline end-to-end without any LLM
    cost, pass ``--limit 0``: that mode verifies the drawer against a
    synthetic box and never contacts Ollama.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

# Make the project importable when this script is run directly.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from monkeylm.config import Settings  # noqa: E402


def _load_settings_for_run(run_dir: str) -> Settings:
    """Build a Settings object that the annotation helpers can use.

    Uses :func:`monkeylm.config.load_settings` so that ``.env`` values
    (``VISION_MODEL``, ``PDF_VISION_MODEL``, ``PDF_VISION_TIMEOUT_SECONDS``,
    etc.) are honored, then redirects the run directory to the backfill target.
    """
    from monkeylm.config import load_settings

    settings = load_settings()
    # ``Settings.output_dir`` is a property with a private override slot.
    # Set the override directly to redirect the run dir to the backfill target.
    settings._output_dir_override = os.path.abspath(run_dir)
    settings.pdf_generate = os.path.exists(
        os.path.join(settings.output_dir, "test_execution_audit.pdf")
    )
    return settings


def _find_source_screenshot(annotated_name: str, run_dir: str) -> Optional[str]:
    """Given ``error_step_1_annotated.png``, return the path to the source."""
    base = annotated_name.replace("_annotated.png", ".png")
    candidates = [base]
    stem = base[:-4]
    for suffix in ("_before", "_after", "_final", "_baseline", "_plan", "_stall"):
        candidates.append(f"{stem}{suffix}.png")
    for c in candidates:
        p = os.path.join(run_dir, c)
        if os.path.exists(p):
            return p
    return None


def _build_context_issue(log: Dict[str, Any]) -> str:
    """Reconstruct the issue text that should be sent to the vision model."""
    parts: List[str] = []
    status = log.get("status", "UNKNOWN")
    action = log.get("action", "")
    target = log.get("target", "")
    error = log.get("error", "")
    parts.append(f"Status: {status}")
    if action:
        parts.append(f"Action: {action}")
    if target:
        parts.append(f"Target: {target}")
    if error:
        parts.append(f"Error: {error[:500]}")
    url = log.get("url", "")
    if url:
        parts.append(f"URL: {url}")
    return "\n".join(parts)


async def _backfill_one(
    settings: Settings, log: Dict[str, Any], run_dir: str
) -> Dict[str, Any]:
    """Re-annotate a single log entry.

    Returns a small dict with the outcome and (when available) the
    vision-model ``description`` string so the caller can persist it
    back into ``results.json``. The PDF regenerator then renders the
    description as the explanatory text above each proof plate.
    """
    annotated_name = log.get("screenshot", "")
    if not annotated_name:
        return {"outcome": "no-source"}
    annotated_path = os.path.join(run_dir, annotated_name)
    if not os.path.exists(annotated_path):
        return {"outcome": "no-source"}

    src_path = _find_source_screenshot(annotated_name, run_dir)
    if not src_path:
        return {"outcome": "no-source"}

    context = _build_context_issue(log)
    step_num = log.get("step")
    result_path, description = await _annotate_with_description(
        settings, src_path, context, step_num=step_num
    )

    if not description:
        return {"outcome": "no-box"}

    if result_path == src_path:
        return {"outcome": "no-box"}

    if os.path.abspath(result_path) != os.path.abspath(annotated_path):
        import shutil

        shutil.copy2(result_path, annotated_path)

    return {"outcome": "ok", "description": description, "annotated_path": annotated_path}


async def _annotate_with_description(
    settings: Settings,
    image_path: str,
    context_issue: str,
    step_num: Optional[int] = None,
) -> tuple:
    """Re-invoke the vision model, draw the annotation, and return both the
    output path and the parsed ``description`` string. Identical to
    :func:`monkeylm.models.annotate_relevant_screenshot` but exposes the
    description so the backfill can persist it for the PDF plate caption.
    """
    from monkeylm.models import (
        _build_vision_annotation_prompt,
        _is_cloud_vision_model,
        _parse_vision_box,
        _safe_json_parse,
        _draw_red_box_arrow,
        _local_service_log,
    )
    import asyncio
    import base64
    import shutil
    import ollama

    active_model = settings.vision_model or settings.pdf_vision_model
    cloud = _is_cloud_vision_model(active_model)
    prompt_text = _build_vision_annotation_prompt(context_issue)

    with open(image_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode("utf-8")

    output_path = image_path.replace(".png", "_annotated.png").replace(".jpg", "_annotated.jpg")
    chat_kwargs = {
        "model": active_model,
        "messages": [
            {"role": "user", "content": prompt_text, "images": [img_b64]},
        ],
        "format": "json",
        "options": {"temperature": 0.0},
    }

    route_label = "Cloud" if cloud else "Local"
    try:
        response = await asyncio.wait_for(
            asyncio.to_thread(ollama.chat, **chat_kwargs),
            timeout=settings.pdf_vision_timeout_seconds,
        )
        content = response.get("message", {}).get("content", "")
    except Exception as exc:
        _local_service_log(f"{route_label} vision model failed: {exc}", settings.output_dir)
        return image_path, None

    box = None
    description = None
    if isinstance(content, str):
        box = _parse_vision_box(content)
        try:
            parsed = _safe_json_parse(content)
            if isinstance(parsed, dict) and isinstance(parsed.get("description"), str):
                description = parsed["description"].strip()
        except Exception:
            description = None

    if box and len(box) == 4:
        ok = _draw_red_box_arrow(
            image_path,
            box,
            context_issue,
            output_path,
            description=description,
            step_num=step_num,
        )
        if ok:
            return output_path, description

    # Fallback: copy original as annotated and report no description.
    try:
        shutil.copy2(image_path, output_path)
    except Exception:
        return image_path, None
    return output_path, description


async def backfill_async(
    run_dir: str,
    skip_pdf: bool = False,
    single_step: Optional[int] = None,
    limit: Optional[int] = None,
    cooldown_seconds: float = 0.0,
) -> Dict[str, int]:
    results_json = os.path.join(run_dir, "results.json")
    if not os.path.exists(results_json):
        raise SystemExit(f"results.json not found in {run_dir}")

    with open(results_json) as f:
        run_data = json.load(f)

    logs = run_data.get("logs", [])
    annotated_logs = [log for log in logs if log.get("screenshot_annotated")]
    if not annotated_logs:
        print(f"No annotated screenshots found in {run_dir}")
        return {"total": 0, "annotated": 0, "redrew": 0, "no_box": 0, "failed": 0}

    if single_step is not None:
        annotated_logs = [log for log in annotated_logs if log.get("step") == single_step]
        if not annotated_logs:
            raise SystemExit(f"No annotated screenshot for step {single_step}")
    if limit is not None:
        annotated_logs = annotated_logs[: max(0, int(limit))]

    settings = _load_settings_for_run(run_dir)
    print(f"Using vision model:   {settings.vision_model or settings.pdf_vision_model or settings.ollama_model}")
    print(f"Run directory:        {run_dir}")
    print(f"Annotated entries:    {len(annotated_logs)} (after filters)")
    print(f"Output dir (in cfg):  {settings.output_dir}")
    if cooldown_seconds > 0:
        print(f"Cool-down between calls: {cooldown_seconds:.1f}s")
    print()

    if not annotated_logs:
        return {"total": 0, "annotated": 0, "redrew": 0, "no_box": 0, "failed": 0}

    redrew = 0
    no_box = 0
    failed = 0
    descriptions_captured = 0

    # Build a quick step→log mapping so we can write descriptions back to
    # results.json in place once the run completes.
    by_step: Dict[int, Dict[str, Any]] = {
        entry.get("step"): entry for entry in run_data.get("logs", []) if entry.get("step") is not None
    }

    for idx, log in enumerate(annotated_logs):
        annotated_name = log.get("screenshot", "")
        try:
            result = await _backfill_one(settings, log, run_dir)
        except Exception as exc:  # noqa: BLE001
            print(f"  [step {log.get('step')}] {annotated_name} FAILED: {exc}")
            failed += 1
            continue

        outcome = result.get("outcome")
        if outcome == "ok":
            redrew += 1
            desc = result.get("description") or ""
            step_num = log.get("step")
            if step_num is not None and step_num in by_step:
                by_step[step_num]["screenshot_description"] = desc
                descriptions_captured += 1
            print(f"  [step {log.get('step')}] {annotated_name} redrew (description captured: {bool(desc)})")
        elif outcome == "no-source":
            print(f"  [step {log.get('step')}] {annotated_name} skipped (no source screenshot)")
            failed += 1
        else:  # no-box
            no_box += 1
            print(f"  [step {log.get('step')}] {annotated_name} no-box from vision model")

        if cooldown_seconds > 0 and idx < len(annotated_logs) - 1:
            time.sleep(cooldown_seconds)

    print(
        f"\nSummary: redrew {redrew}, no-box {no_box}, failed {failed}, "
        f"descriptions captured {descriptions_captured}, total {len(annotated_logs)}"
    )

    # Persist any captured descriptions back to results.json so the
    # regenerated PDF can use them as plate captions.
    if descriptions_captured > 0:
        results_json_path = os.path.join(run_dir, "results.json")
        try:
            with open(results_json_path, "w") as f:
                json.dump(run_data, f, indent=2)
            print(f"  Updated results.json with {descriptions_captured} descriptions")
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠️ Failed to update results.json: {exc}")

    if not skip_pdf and settings.pdf_generate and redrew > 0:
        print("\nRegenerating executive PDF...")
        _regenerate_pdf(run_data, run_dir, settings)

    return {
        "total": len(annotated_logs),
        "annotated": len(annotated_logs),
        "redrew": redrew,
        "no_box": no_box,
        "failed": failed,
        "descriptions_captured": descriptions_captured,
    }


def _regenerate_pdf(run_data: Dict[str, Any], run_dir: str, settings: Settings) -> None:
    """Rebuild ``test_execution_audit.pdf`` so the proof plates use the new marks."""
    from datetime import datetime
    from monkeylm.reporting import generate_pdf_report

    defects = run_data.get("defects", {})
    test_logs = run_data.get("logs", [])
    try:
        start_time = datetime.fromisoformat(run_data.get("start_time", ""))
        end_time = datetime.fromisoformat(run_data.get("end_time", ""))
    except ValueError:
        start_time = end_time = datetime.now()
    generate_pdf_report(settings, defects, test_logs, start_time, end_time)
    pdf_path = os.path.join(run_dir, "test_execution_audit.pdf")
    if os.path.exists(pdf_path):
        print(f"  PDF written: {pdf_path} ({os.path.getsize(pdf_path)} bytes)")
    else:
        print("  PDF generation did not produce a file (check reportlab availability).")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_dir", help="Path to a testrun_* directory containing results.json")
    parser.add_argument("--skip-pdf", action="store_true", help="Do not regenerate the PDF audit report")
    parser.add_argument(
        "--single",
        type=int,
        default=None,
        help="Only re-annotate the entry whose 'step' number matches this value (e.g. --single 18).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap the number of entries re-annotated (0 means: do not contact any LLM, only validate the drawer).",
    )
    parser.add_argument(
        "--cooldown",
        type=float,
        default=0.0,
        help="Seconds to sleep between vision-model calls. Recommended on hot laptops / small GPUs.",
    )
    args = parser.parse_args()
    if args.limit == 0:
        # LLM-free smoke test path: validate the drawer + parser without touching Ollama.
        from tests.test_screenshot_annotation import _run_offline_drawer_smoke
        _run_offline_drawer_smoke()
        return 0
    asyncio.run(
        backfill_async(
            args.run_dir,
            skip_pdf=args.skip_pdf,
            single_step=args.single,
            limit=args.limit,
            cooldown_seconds=args.cooldown,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
