"""Regression tests for the vision-annotation step-window fix.

Root cause these tests lock down: ``resolve_stage_budget`` clamps the
annotation stage to the *remaining* step window, and the work window
(``step_timeout_seconds``, 30s default) is smaller than real vision-model
latency (~58.5s measured against ``free-failsafe-vision`` on 2026-10-22).
Without reserving the configured annotation budget in the step window, the
clamp starved every annotation run below model latency, the calls timed out,
and the reports shipped with no annotated screenshots, no red boxes or
arrows, and no vision descriptions (the Oct 21, 2026 regression).

Covered here:

* :func:`vision_annotation_allowance` returns the configured annotation
  budget exactly when annotation can run (PDF on + a vision model present).
* :func:`step_window_seconds` adds the allowance to the work window.
* ``resolve_stage_budget`` grants the *full* configured budget once the
  window includes the allowance; the clamp bites only on genuine overrun.
* ``execute_action`` completes an annotation successfully when the step
  window covers the configured budget (mirrors the field failure mode in
  reverse: ``annotation_source == "vision"``, not ``"none"``).
* The cross-report summary counts each annotation outcome.
"""

from __future__ import annotations

import asyncio
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from monkeylm.browser.actions.executor import execute_action
from monkeylm.core.worker.budget import (
    StepBudget,
    resolve_stage_budget,
    step_window_seconds,
    vision_annotation_allowance,
)
from monkeylm.reporting.utils import summarize_vision_annotation


class AllowanceTests(unittest.TestCase):
    def _settings(self, **overrides) -> SimpleNamespace:
        base = {
            "pdf_generate": True,
            "vision_model": "free-failsafe-vision",
            "pdf_vision_model": "",
            "step_timeout_seconds": 30.0,
            "vision_annotation_timeout_seconds": 120.0,
        }
        base.update(overrides)
        return SimpleNamespace(**base)

    def test_allowance_is_zero_when_pdf_generation_disabled(self) -> None:
        self.assertEqual(
            vision_annotation_allowance(self._settings(pdf_generate=False)), 0.0
        )

    def test_allowance_is_zero_without_a_vision_model(self) -> None:
        self.assertEqual(
            vision_annotation_allowance(self._settings(vision_model="", pdf_vision_model="")),
            0.0,
        )

    def test_allowance_matches_configured_annotation_budget(self) -> None:
        self.assertEqual(vision_annotation_allowance(self._settings()), 120.0)

    def test_allowance_pdf_vision_model_counts_as_configured(self) -> None:
        self.assertEqual(
            vision_annotation_allowance(
                self._settings(vision_model="", pdf_vision_model="qwen3-vl:30b")
            ),
            120.0,
        )

    def test_window_adds_allowance_to_work_window(self) -> None:
        self.assertEqual(step_window_seconds(self._settings()), 150.0)

    def test_window_equals_work_window_when_annotation_disabled(self) -> None:
        self.assertEqual(
            step_window_seconds(self._settings(pdf_generate=False)), 30.0
        )

    def test_window_tolerates_non_numeric_step_timeout(self) -> None:
        # A non-numeric work window falls back to the 30s default and the
        # annotation allowance is still applied on top.
        self.assertEqual(
            step_window_seconds(self._settings(step_timeout_seconds="bad")), 150.0
        )


class ClampHonoursAllowanceTests(unittest.TestCase):
    def test_full_budget_granted_when_window_covers_allowance(self) -> None:
        # 150s window (30 work + 120 annotation), 10s already consumed ->
        # 136s usable -> the configured 120s ask must pass through untouched.
        deadline = time.monotonic() + (150.0 - 10.0)
        budget = StepBudget(deadline=deadline)
        self.assertEqual(resolve_stage_budget(120.0, budget), 120.0)

    def test_clamp_still_degrades_on_genuine_overrun(self) -> None:
        # 150s window with only 20s left -> the ask is clamped to what fits.
        deadline = time.monotonic() + 20.0
        budget = StepBudget(deadline=deadline)
        resolved = resolve_stage_budget(120.0, budget)
        self.assertLessEqual(resolved, 16.0)

    def test_exhausted_window_still_refuses_to_start(self) -> None:
        budget = StepBudget(deadline=time.monotonic() - 1.0)
        self.assertEqual(resolve_stage_budget(120.0, budget), 0.0)


class DummyPage:
    url = "https://example.com"

    async def wait_for_load_state(self, *args, **kwargs) -> None:
        return None

    async def evaluate(self, *args, **kwargs):
        return 0

    async def screenshot(self, path: str) -> None:
        with open(path, "wb") as handle:
            handle.write(b"img")


class DummySnapshot:
    is_empty_capture = False
    url = "https://example.com"
    structure_hash = "abc"
    dom_hash = "def"
    screenshot_path = "screenshot.png"
    elements: list = []
    forms: list = []
    form_controls: list = []


class AnnotateWithinWindowTests(unittest.IsolatedAsyncioTestCase):
    def _settings(self, output_dir: str) -> SimpleNamespace:
        return SimpleNamespace(
            output_dir=output_dir,
            pdf_generate=True,
            vision_model="free-failsafe-vision",
            pdf_vision_model="free-failsafe-vision",
            step_timeout_seconds=30.0,
            vision_annotation_timeout_seconds=120.0,
            vision_annotation_max_attempts=1,
        )

    def _monitors(self) -> tuple:
        perf_monitor = SimpleNamespace(
            snapshot=AsyncMock(return_value=None),
            detect_bottlenecks=AsyncMock(return_value=[]),
        )
        network_monitor = SimpleNamespace(detect_zombie_ui=AsyncMock(return_value=None))
        defects = SimpleNamespace(add=lambda *args, **kwargs: None)
        fuzzer = SimpleNamespace(next_payload=lambda: "")
        return perf_monitor, network_monitor, defects, fuzzer

    async def test_annotation_succeeds_when_step_window_covers_budget(self) -> None:
        """The window now reserves the annotation allowance, so the vision
        stage gets its configured budget and records a real annotation."""

        async def fake_get_page_state(*args, **kwargs):
            return DummySnapshot()

        async def working_annotation(*args, **kwargs):
            await asyncio.sleep(0.2)
            return "step_007_annotated.png", "button clipped offscreen", {
                "box_2d": [0.15, 0.35, 0.45, 0.60],
                "defect_type": "rendering_defects",
                "severity": "high",
                "verdict": "FAIL",
            }

        settings = self._settings(tempfile.mkdtemp(prefix="monkeylm-window-"))
        perf_monitor, network_monitor, defects, fuzzer = self._monitors()
        log_entries: list = []

        with patch(
            "monkeylm.browser.actions.executor.get_page_state",
            side_effect=fake_get_page_state,
        ), patch(
            "monkeylm.browser.actions.executor.compare_screenshots_pixelmatch",
            return_value={
                "diff_ratio": 0.0,
                "diff_pixels": 0,
                "engine": "pixelmatch",
                "diff_image": "",
            },
        ), patch(
            "monkeylm.models._step_defects_summary",
            return_value=["layout-instability:max_shift_px=582"],
        ), patch(
            "monkeylm.models.annotate_relevant_screenshot",
            new=AsyncMock(side_effect=working_annotation),
        ):
            reserve = 0.5
            deadline = time.monotonic() + (30.0 + 120.0)
            budget = StepBudget(deadline=deadline, reserve_seconds=reserve)
            snapshot, entry = await execute_action(
                page=DummyPage(),
                settings=settings,
                action_plan={"action": "scroll", "target": "", "value": ""},
                step_num=7,
                fuzzer=fuzzer,
                defects=defects,
                network_monitor=network_monitor,
                perf_monitor=perf_monitor,
                log_sink=log_entries,
                persistence_engine=None,
                worker_id=1,
                validation_prober=None,
                step_budget=budget,
            )

        self.assertIsNotNone(snapshot)
        self.assertEqual(entry.get("annotation_source"), "vision")
        self.assertTrue(entry.get("screenshot_annotated"))
        self.assertEqual(entry.get("screenshot_description"), "button clipped offscreen")
        self.assertEqual(entry.get("screenshot_verdict", {}).get("box_2d"), [0.15, 0.35, 0.45, 0.60])


class SummarizeVisionAnnotationTests(unittest.TestCase):
    def _settings(self) -> SimpleNamespace:
        return SimpleNamespace(
            vision_model="free-failsafe-vision",
            pdf_vision_model="",
            vision_annotation_timeout_seconds=120.0,
            vision_annotation_max_attempts=2,
        )

    def test_counts_each_outcome_exactly_once(self) -> None:
        logs = [
            {"annotation_source": "vision", "screenshot_annotated": True},
            {"annotation_source": "cv", "screenshot_annotated": True},
            {"annotation_source": "none", "annotation_error": "timeout"},
            {"annotation_source": "none", "annotation_error": "skipped_no_step_budget"},
            {"annotation_source": "none"},
            {"annotation_source": "none", "annotation_error": "RuntimeError: boom"},
            {"annotation_source": ""},
        ]
        summary = summarize_vision_annotation(logs, self._settings())
        self.assertEqual(summary["attempted"], 6)
        self.assertEqual(summary["annotated"], 2)
        self.assertEqual(summary["annotated_by_vision"], 1)
        self.assertEqual(summary["annotated_by_cv"], 1)
        self.assertEqual(summary["no_region_found"], 1)
        self.assertEqual(summary["timeouts"], 1)
        self.assertEqual(summary["skipped_no_step_budget"], 1)
        self.assertEqual(summary["errors"], 1)
        self.assertEqual(summary["total_steps"], 7)
        self.assertEqual(summary["budget_seconds"], 120.0)
        self.assertEqual(summary["max_attempts"], 2)
        self.assertEqual(summary["vision_model"], "free-failsafe-vision")

    def test_empty_logs_and_missing_settings_fields_are_safe(self) -> None:
        summary = summarize_vision_annotation(None or [], None)
        self.assertEqual(summary["attempted"], 0)
        self.assertEqual(summary["annotated"], 0)
        self.assertIn("vision_model", summary)


if __name__ == "__main__":
    unittest.main()