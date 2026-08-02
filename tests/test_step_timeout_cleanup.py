"""Regression tests for the harness's step-timeout cleanup path.

The original implementation used ``asyncio.wait_for(coro, timeout=...)``,
which cancels the inner coroutine on timeout. That cancellation is
cooperative: any Playwright internal ``Future`` already in flight survives
the cancellation, and when the page eventually closes (or the Future's own
30s action timeout fires), that Future's exception has no consumer, so
asyncio logs ``Future exception was never retrieved`` at shutdown.

The new implementation shields the inner task from cancellation and gives
it a short grace window to either finish cleanly or fail with a
predictable exception. If the grace window expires, we cancel the inner
task explicitly and await the cancellation so the task is fully torn down
before the caller proceeds.

These tests pin:
  * happy path: a fast inner coroutine still returns its result on time;
  * timeout path: when the inner coroutine exceeds the step timeout, the
    caller still receives ``asyncio.TimeoutError``;
  * grace path: when the inner coroutine is just slightly slower than the
    step timeout, the grace window lets it finish — no orphan task is
    left running in the background;
  * hard-cancel path: when the inner coroutine ignores the grace window
    entirely, we cancel it and the task ends in the cancelled state.
"""

from __future__ import annotations

import asyncio
import unittest

from monkeylm.core.worker.runner import _execute_step_with_timeout


class StepTimeoutCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_fast_inner_returns_result(self) -> None:
        async def fast() -> str:
            await asyncio.sleep(0.01)
            return "ok"

        result = await _execute_step_with_timeout(fast(), timeout_seconds=0.5)
        self.assertEqual(result, "ok")

    async def test_slow_inner_raises_asyncio_timeout_error(self) -> None:
        async def slow() -> None:
            await asyncio.sleep(2.0)

        with self.assertRaises(asyncio.TimeoutError):
            await _execute_step_with_timeout(slow(), timeout_seconds=0.1)

    async def test_just_slow_inner_finishes_in_grace_window(self) -> None:
        # Step timeout = 0.1s, but the inner coroutine finishes in 0.2s.
        # The old implementation would have raised TimeoutError; the new
        # implementation gives a 0.5s grace window and lets it finish.
        async def slow_but_finishing() -> str:
            await asyncio.sleep(0.2)
            return "completed"

        result = await _execute_step_with_timeout(slow_but_finishing(), timeout_seconds=0.1)
        self.assertEqual(result, "completed")

    async def test_pathologically_slow_inner_is_cancelled(self) -> None:
        # The inner coroutine ignores the grace window and would block
        # forever. The harness must cancel it and the task must end in the
        # cancelled state.
        cancellation_observed = asyncio.Event()
        finished_normally = asyncio.Event()

        async def pathologically_slow() -> None:
            try:
                await asyncio.sleep(10.0)
                finished_normally.set()
            except asyncio.CancelledError:
                cancellation_observed.set()
                raise

        with self.assertRaises(asyncio.TimeoutError):
            await _execute_step_with_timeout(pathologically_slow(), timeout_seconds=0.1)

        # The task should have observed the cancellation during the
        # hard-cancel phase.
        self.assertTrue(cancellation_observed.is_set())
        # The task should NOT have completed normally.
        self.assertFalse(finished_normally.is_set())


if __name__ == "__main__":
    unittest.main()
