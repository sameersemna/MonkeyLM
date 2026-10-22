"""Regression tests for step-budget clamping and worker continuation.

Root cause these tests lock down: the vision-LLM annotation stage is nested
inside ``execute_action``, which the worker wraps in a wall-clock step cap.
When ``vision_annotation_timeout_seconds`` (60s by default in ``.env``)
exceeds ``step_timeout_seconds`` (30s), the *outer* cap always fires first.
The annotation's own graceful-degradation handler became structurally
unreachable, so a slow or hung gateway tore down the whole worker run instead
of degrading to an unannotated screenshot.

Covered here:

* :func:`resolve_stage_budget` clamps a stage to the remaining step window and
  refuses to start when no budget remains.
* ``execute_action`` completes (rather than raising) when the annotation
  budget exceeds the step budget, and records why the annotation was dropped.
* Stage timings are recorded so a timeout can name the stage that stalled.
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
    MIN_STAGE_BUDGET_SECONDS,
    StepBudget,
    resolve_stage_budget,
)


class ResolveStageBudgetTests(unittest.TestCase):
    def test_unbounded_budget_returns_configured_value(self) -> None:
        self.assertEqual(resolve_stage_budget(60.0, None), 60.0)

    def test_configured_value_wins_when_it_fits(self) -> None:
        budget = StepBudget(deadline=time.monotonic() + 30.0)
        self.assertEqual(resolve_stage_budget(5.0, budget), 5.0)

    def test_budget_is_clamped_to_remaining_window_minus_reserve(self) -> None:
        # 30s step, 4s default reserve -> ~26s usable, so a 60s ask is capped.
        budget = StepBudget(deadline=time.monotonic() + 30.0)
        resolved = resolve_stage_budget(60.0, budget)
        self.assertLess(resolved, 60.0)
        self.assertGreaterEqual(resolved, 25.0)

    def test_exhausted_budget_refuses_to_start(self) -> None:
        budget = StepBudget(deadline=time.monotonic() - 1.0)
        self.assertEqual(resolve_stage_budget(60.0, budget), 0.0)

    def test_budget_floor_is_enforced(self) -> None:
        budget = StepBudget(deadline=time.monotonic() + 5.0)
        resolved = resolve_stage_budget(0.001, budget)
        self.assertGreaterEqual(resolved, MIN_STAGE_BUDGET_SECONDS)


class StepBudgetTests(unittest.TestCase):
    def test_stage_timings_accumulate_and_expose_slowest(self) -> None:
        budget = StepBudget()
        budget.start_stage("action")
        time.sleep(0.02)
        budget.end_stage()
        budget.start_stage("annotation")
        time.sleep(0.05)
        budget.end_stage()
        self.assertIn("action", budget.stage_timings)
        self.assertIn("annotation", budget.stage_timings)
        self.assertEqual(budget.slowest_stage(), "annotation")

    def test_current_stage_tracks_in_flight_work(self) -> None:
        budget = StepBudget()
        self.assertEqual(budget.current_stage(), "")
        budget.start_stage("annotation")
        self.assertEqual(budget.current_stage(), "annotation")
        budget.end_stage()
        self.assertEqual(budget.current_stage(), "")

    def test_to_dict_is_json_serialisable(self) -> None:
        import json

        budget = StepBudget(deadline=time.monotonic() + 30.0)
        budget.start_stage("action")
        budget.end_stage()
        payload = budget.to_dict()
        json.dumps(payload)
        self.assertEqual(payload["slowest_stage"], "action")
        self.assertIn("remaining_seconds", payload)


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


class ExecuteActionBudgetTests(unittest.IsolatedAsyncioTestCase):
    def _settings(self, output_dir: str) -> SimpleNamespace:
        return SimpleNamespace(
            output_dir=output_dir,
            pdf_generate=True,
            vision_model="qwen3-vl:30b",
            pdf_vision_model="qwen3-vl:30b",
            step_timeout_seconds=30.0,
            # Deliberately larger than the step budget: this is the exact
            # misconfiguration that produced the field failure.
            vision_annotation_timeout_seconds=60.0,
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

    async def test_annotation_budget_exceeding_step_budget_still_completes(self) -> None:
        """The core regression: a 60s annotation ask inside a 30s step must
        degrade to ``source=\"none\"``, not abort the step."""

        async def fake_get_page_state(*args, **kwargs):
            return DummySnapshot()

        async def slow_annotation(*args, **kwargs):
            await asyncio.sleep(5.0)
            return "annotated.png", "unreachable"

        settings = self._settings(tempfile.mkdtemp(prefix="monkeylm-budget-"))
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
            new=AsyncMock(side_effect=slow_annotation),
        ):
            budget = StepBudget(
                deadline=time.monotonic() + 2.0,
                reserve_seconds=0.5,
            )
            started = time.monotonic()
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
            elapsed = time.monotonic() - started

        self.assertIsNotNone(snapshot)
        # The clamped budget must fire well before the 2s step deadline, so the
        # step returns instead of being cancelled from the outside.
        self.assertLess(elapsed, 2.0)
        self.assertEqual(entry.get("annotation_source"), "none")
        self.assertEqual(entry.get("annotation_error"), "timeout")
        # The budget that was actually applied must be recorded, and must be
        # smaller than what settings asked for.
        self.assertLess(entry.get("annotation_budget_seconds", 1e9), 60.0)
        self.assertEqual(len(log_entries), 1)

    async def test_exhausted_step_budget_skips_annotation_entirely(self) -> None:
        """With no budget left the stage is skipped, not started and killed."""
        annotate = AsyncMock(return_value=("annotated.png", "x", {}))

        async def fake_get_page_state(*args, **kwargs):
            return DummySnapshot()

        settings = self._settings(tempfile.mkdtemp(prefix="monkeylm-budget-"))
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
            return_value=["layout-instability"],
        ), patch("monkeylm.models.annotate_relevant_screenshot", new=annotate):
            budget = StepBudget(deadline=time.monotonic() - 5.0)
            _, entry = await execute_action(
                page=DummyPage(),
                settings=settings,
                action_plan={"action": "scroll", "target": "", "value": ""},
                step_num=8,
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

        annotate.assert_not_awaited()
        self.assertEqual(entry.get("annotation_source"), "none")
        self.assertEqual(entry.get("annotation_error"), "skipped_no_step_budget")

    async def test_stage_timings_are_recorded_on_log_entry(self) -> None:
        """Timeouts must be attributable to a named stage."""

        async def fake_get_page_state(*args, **kwargs):
            return DummySnapshot()

        settings = self._settings(tempfile.mkdtemp(prefix="monkeylm-budget-"))
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
            return_value=[],
        ):
            budget = StepBudget(deadline=time.monotonic() + 30.0)
            _, entry = await execute_action(
                page=DummyPage(),
                settings=settings,
                action_plan={"action": "scroll", "target": "", "value": ""},
                step_num=9,
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

        timings = entry.get("stage_timings", {})
        self.assertIn("timings", timings)
        for stage in ("before_snapshot", "action", "after_snapshot", "final_snapshot"):
            self.assertIn(stage, timings["timings"])
        self.assertIsNotNone(timings["slowest_stage"])

    async def test_no_budget_preserves_legacy_behavior(self) -> None:
        """Callers that pass no budget keep the configured annotation wait."""
        seen: dict = {}

        async def fake_get_page_state(*args, **kwargs):
            return DummySnapshot()

        async def recording_annotation(*args, **kwargs):
            return "annotated.png", None, {}

        settings = self._settings(tempfile.mkdtemp(prefix="monkeylm-budget-"))
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
            return_value=["defect"],
        ), patch(
            "monkeylm.models.annotate_relevant_screenshot",
            new=AsyncMock(side_effect=recording_annotation),
        ):
            await execute_action(
                page=DummyPage(),
                settings=settings,
                action_plan={"action": "scroll", "target": "", "value": ""},
                step_num=10,
                fuzzer=fuzzer,
                defects=defects,
                network_monitor=network_monitor,
                perf_monitor=perf_monitor,
                log_sink=log_entries,
                persistence_engine=None,
                worker_id=1,
                validation_prober=None,
            )
        seen.update(log_entries[0])
        self.assertIn("screenshot_annotated", seen)
        self.assertNotIn("stage_timings", seen)


if __name__ == "__main__":
    unittest.main()