"""Regression tests for resilient input handling."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from monkeylm.browser.actions.helpers import _fill_input_resilient


class InputRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_fill_input_resilient_retries_detached_elements(self) -> None:
        class FlakyLocator:
            def __init__(self) -> None:
                self.calls = 0

            async def scroll_into_view_if_needed(self, **kwargs: object) -> None:
                return None

            async def fill(self, value: str, timeout: int | None = None) -> None:
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("element was detached from the DOM")

        locator = FlakyLocator()
        page = SimpleNamespace()

        succeeded = await _fill_input_resilient(page, locator, "hello", target="[id=7]", timeout_ms=100)

        self.assertTrue(succeeded)
        self.assertEqual(locator.calls, 2)


if __name__ == "__main__":
    unittest.main()
