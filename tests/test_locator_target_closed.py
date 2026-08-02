"""Regression tests for graceful handling of closed page/locator futures.

The MonkeyLM step loop can hit a race during cleanup: the harness closes
the Playwright context while a locator iteration is still in flight. The
underlying Playwright Future then raises ``TargetClosedError`` and, if no
one is awaiting it, surfaces as ``Future exception was never retrieved``.

These tests pin the contract that ``_locator_for_target_id``:
  * returns ``None`` (not raises) when the page is already closed;
  * returns ``None`` (not raises) when ``page.evaluate`` raises a
    Playwright ``Error`` (e.g. ``TargetClosedError``);
  * caps the underlying ``evaluate`` round-trip with
    ``asyncio.wait_for`` so a single slow call cannot eat the whole
    step-timeout budget;
  * returns a real ``Locator`` (the ``page.locator(...).nth(...)`` chain)
    for the Nth *visible* interactive element on a healthy page.

The implementation is intentionally a single ``page.evaluate`` round-trip
to avoid creating one orphaned Playwright ``Future`` per DOM index — the
old Python-side loop did that, and those orphan futures produced the
shutdown warning.
"""

from __future__ import annotations

import asyncio
import unittest
from typing import Any
from unittest.mock import patch

try:
    from playwright.async_api import Error as PlaywrightError
except Exception:  # pragma: no cover - import path flexibility
    PlaywrightError = Exception  # type: ignore[misc,assignment]

from monkeylm.browser.actions.helpers import (
    _BOUNDING_BOX_ITER_TIMEOUT_SECONDS,
    _locator_for_target_id,
)
from monkeylm.browser.snapshot.selectors import INTERACTIVE_ELEMENTS_SELECTOR


class _ClosedPage:
    """Stub page that reports itself closed before any locator call."""

    def is_closed(self) -> bool:
        return True

    def locator(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("locator() should not be called on a closed page")

    async def evaluate(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("evaluate() should not be called on a closed page")


class _EvaluateErrorPage:
    """Stub page where ``evaluate`` raises a Playwright ``Error`` (e.g. closed)."""

    def is_closed(self) -> bool:
        return False

    async def evaluate(self, *_args: object, **_kwargs: object) -> object:
        raise PlaywrightError("Target page, context or browser has been closed")

    def locator(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("locator() should not be called when evaluate fails")


class _SlowEvaluatePage:
    """Stub page where ``evaluate`` blocks past the per-iteration cap."""

    def is_closed(self) -> bool:
        return False

    async def evaluate(self, *_args: object, **_kwargs: object) -> object:
        await asyncio.sleep(5)
        return -1  # never reached in practice

    def locator(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("locator() should not be called when evaluate times out")


class _HealthyPage:
    """Stub page that returns a deterministic DOM index from ``evaluate``."""

    def __init__(self, dom_index: int) -> None:
        self._dom_index = dom_index
        self.evaluate_calls = 0

    def is_closed(self) -> bool:
        return False

    async def evaluate(self, _expression: str, _arg: Any = None) -> int:
        self.evaluate_calls += 1
        return self._dom_index

    def locator(self, selector: str) -> "_HealthyLocator":
        return _HealthyLocator(selector)


class _HealthyLocator:
    def __init__(self, selector: str) -> None:
        self._selector = selector
        self.nth_calls: list[int] = []

    def nth(self, idx: int) -> "_HealthyLocator":
        self.nth_calls.append(idx)
        return self


class LocatorTargetClosedTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_none_when_page_already_closed(self) -> None:
        result = await _locator_for_target_id(_ClosedPage(), "[id=0]")
        self.assertIsNone(result)

    async def test_returns_none_when_evaluate_raises_target_closed(self) -> None:
        result = await _locator_for_target_id(_EvaluateErrorPage(), "[id=0]")
        self.assertIsNone(result)

    async def test_returns_none_when_evaluate_times_out(self) -> None:
        # Pin the failure mode that motivated the per-iteration cap: a single
        # round-trip that stalls must not block forever.
        start = asyncio.get_event_loop().time()
        result = await _locator_for_target_id(_SlowEvaluatePage(), "[id=0]")
        elapsed = asyncio.get_event_loop().time() - start
        self.assertIsNone(result)
        # Should return roughly when the cap elapses, not after the 5s sleep.
        self.assertLess(elapsed, _BOUNDING_BOX_ITER_TIMEOUT_SECONDS + 1.0)

    async def test_evaluate_round_trip_cap_is_short(self) -> None:
        # Sanity-check the cap is small enough to fail fast inside a step.
        self.assertLessEqual(_BOUNDING_BOX_ITER_TIMEOUT_SECONDS, 2.0)

    async def test_returns_real_locator_on_healthy_page(self) -> None:
        page = _HealthyPage(dom_index=7)
        result = await _locator_for_target_id(page, "[id=3]")
        # The result is a Locator built from the canonical selector.
        self.assertIsInstance(result, _HealthyLocator)
        self.assertEqual(result._selector, INTERACTIVE_ELEMENTS_SELECTOR)
        self.assertEqual(result.nth_calls, [7])
        # Exactly one evaluate call (no Python-side iteration).
        self.assertEqual(page.evaluate_calls, 1)

    async def test_returns_none_when_target_visible_index_not_found(self) -> None:
        page = _HealthyPage(dom_index=-1)  # JS signals "not enough visible"
        result = await _locator_for_target_id(page, "[id=42]")
        self.assertIsNone(result)
        # We did not fall through to the locator() factory.
        self.assertEqual(page.evaluate_calls, 1)

    async def test_evaluate_call_is_wrapped_in_wait_for(self) -> None:
        # Pin the implementation contract: the evaluate() call must be
        # wrapped in ``asyncio.wait_for`` with the configured cap.
        captured: list[float | int | str | None] = []
        real_wait_for = asyncio.wait_for

        async def spy_wait_for(awaitable, *, timeout=None, **kwargs):  # type: ignore[no-untyped-def]
            captured.append(timeout)
            return await real_wait_for(awaitable, timeout=timeout, **kwargs)

        page = _HealthyPage(dom_index=2)
        with patch("monkeylm.browser.actions.helpers.asyncio.wait_for", side_effect=spy_wait_for):
            await _locator_for_target_id(page, "[id=0]")

        self.assertTrue(captured, "expected at least one wait_for call")
        for timeout in captured:
            self.assertEqual(timeout, _BOUNDING_BOX_ITER_TIMEOUT_SECONDS)

    async def test_does_not_issue_multiple_round_trips(self) -> None:
        # Regression guard: the old Python-side loop issued one
        # ``candidates.nth(idx).bounding_box()`` per DOM index, producing
        # one orphan Playwright Future per index. The new implementation
        # must do exactly one ``evaluate`` round-trip.
        page = _HealthyPage(dom_index=4)
        await _locator_for_target_id(page, "[id=0]")
        self.assertEqual(page.evaluate_calls, 1)


if __name__ == "__main__":
    unittest.main()
