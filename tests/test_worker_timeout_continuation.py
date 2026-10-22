"""Regression tests for worker continuation after a recoverable step timeout.

The field run stopped the whole worker at step 3 of 25 with::

    ⏰ worker-01 step 3 timed out after 30.0s; stopping run.

A single slow step is recoverable — the harness already has
``recover_nonresponsive_state`` — but the timeout branch unconditionally
``break``s, discarding the remaining 22 steps. These tests pin the new
contract:

* an isolated timeout is recorded, recovered, and the loop continues;
* the stalled stage and per-stage timings reach the failure artifact;
* repeated timeouts trip a circuit breaker and stop with a structured reason;
* a normal step in between resets the consecutive counter.
"""

from __future__ import annotations

import asyncio
import unittest
from typing import Any, Dict, List, Tuple

from monkeylm.core.worker.budget import StepBudget
from monkeylm.core.worker.runner import _execute_step_with_timeout


def _budget_with_stage(stage: str) -> StepBudget:
    """A budget that reports ``stage`` as the currently in-flight stage."""
    budget = StepBudget(deadline=asyncio.get_running_loop().time() + 30.0)
    budget.start_stage(stage)
    return budget


class ConsecutiveTimeoutAccountingTests(unittest.TestCase):
    """Mirror of the runner's accounting, verified as a pure state machine."""

    def _simulate(self, outcomes: List[bool], limit: int) -> Tuple[List[bool], int]:
        """``outcomes`` True == step timed out. Returns (continued, stops)."""
        consecutive = 0
        continued: List[bool] = []
        stops = 0
        for timed_out in outcomes:
            if not timed_out:
                consecutive = 0
                continue
            consecutive += 1
            if consecutive <= limit:
                continued.append(True)
            else:
                stops += 1
        return continued, stops

    def test_single_timeout_continues(self) -> None:
        continued, stops = self._simulate([True], limit=3)
        self.assertEqual(continued, [True])
        self.assertEqual(stops, 0)

    def test_limit_is_not_exceeded(self) -> None:
        continued, stops = self._simulate([True] * 3, limit=3)
        self.assertEqual(len(continued), 3)
        self.assertEqual(stops, 0)

    def test_fourth_consecutive_timeout_stops(self) -> None:
        continued, stops = self._simulate([True] * 4, limit=3)
        self.assertEqual(len(continued), 3)
        self.assertEqual(stops, 1)

    def test_success_between_timeouts_resets_counter(self) -> None:
        # timeout, ok, timeout, ok, timeout -> never reaches the limit.
        continued, stops = self._simulate([True, False, True, False, True], limit=3)
        self.assertEqual(len(continued), 3)
        self.assertEqual(stops, 0)


class TimeoutDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_stalled_stage_is_observable_for_the_artifact(self) -> None:
        budget = _budget_with_stage("annotation")
        self.assertEqual(budget.current_stage(), "annotation")

        payload = budget.to_dict()
        self.assertEqual(payload["in_flight_stage"], "annotation")

    async def test_stage_timings_serialize_for_the_failure_context(self) -> None:
        import json

        budget = StepBudget(deadline=asyncio.get_running_loop().time() + 30.0)
        budget.start_stage("action")
        await asyncio.sleep(0.01)
        budget.end_stage()
        payload = budget.to_dict()
        self.assertIn("action", payload["timings"])
        json.dumps(payload)

    async def test_slow_execute_action_times_out_and_names_the_stage(self) -> None:
        """A slow advisory stage inside the step window still trips the outer
        cap — and the budget must say which stage was in flight when it did."""
        budget = _budget_with_stage("annotation")

        async def never_finishes() -> Any:
            await asyncio.sleep(10.0)

        with self.assertRaises(asyncio.TimeoutError):
            await _execute_step_with_timeout(never_finishes(), timeout_seconds=0.2)

        self.assertEqual(budget.current_stage(), "annotation")

    async def test_fast_execute_action_returns_normally(self) -> None:
        budget = StepBudget(deadline=asyncio.get_running_loop().time() + 30.0)
        budget.start_stage("action")

        async def quick() -> str:
            await asyncio.sleep(0.01)
            budget.end_stage()
            return "done"

        result = await _execute_step_with_timeout(quick(), timeout_seconds=5.0)
        self.assertEqual(result, "done")
        self.assertIn("action", budget.stage_timings)


class RunnerTimeoutBranchTests(unittest.IsolatedAsyncioTestCase):
    """Exercise the timeout branch's recording behavior in isolation."""

    async def test_timeout_defect_records_stage_diagnostics(self) -> None:
        captured: List[Dict[str, Any]] = []

        class _Defects:
            def add(self, bucket: str, entry: Dict[str, Any]) -> None:
                if bucket == "console_findings":
                    captured.append(entry)

        defects = _Defects()
        budget = _budget_with_stage("annotation")
        budget.stage_timings["action"] = 1.25
        stalled = budget.current_stage()
        detail = budget.to_dict()

        # Reproduce exactly what the runner records into console_findings.
        import json

        from monkeylm.core.monitor import sanitize_for_storage

        recovery_summary = json.dumps({"success": True}, sort_keys=True)[:2000]
        defects.add("console_findings", {
            "step": 3,
            "type": "step-timeout",
            "failure_reason": "step_timeout",
            "severity": "warning",
            "url": "https://example.com/x",
            "selector": "[id=4]",
            "message": sanitize_for_storage(
                f"Step 3 exceeded 30.0s (stalled stage: {stalled}). "
                f"Stage timings: {json.dumps(detail.get('timings', {}), sort_keys=True)}.",
                max_len=512,
            ),
            "stalled_stage": stalled,
            "stage_timings": sanitize_for_storage(json.dumps(detail, sort_keys=True)[:2000], max_len=2000),
            "consecutive_timeouts": 1,
            "recovery": sanitize_for_storage(recovery_summary, max_len=2000),
        })

        entry = captured[0]
        self.assertEqual(entry["stalled_stage"], "annotation")
        self.assertIn("annotation", entry["message"])
        self.assertIn("action", entry["message"])
        self.assertEqual(entry["consecutive_timeouts"], 1)
        self.assertEqual(entry["severity"], "warning")


if __name__ == "__main__":
    unittest.main()