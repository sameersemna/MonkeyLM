"""Regression tests for bounded select-option fallback."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from monkeylm.browser.actions.helpers import _fill_select_option


class SelectFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_fill_select_option_returns_fast_when_select_option_times_out(self) -> None:
        class FlakyLocator:
            async def select_option(self, **kwargs: object) -> None:
                raise TimeoutError("option disabled")

        locator = FlakyLocator()
        chosen, reason = await _fill_select_option(
            SimpleNamespace(),
            locator,
            "alpha",
            ["alpha", "beta"],
            "default",
        )

        self.assertEqual(chosen, "alpha")
        self.assertEqual(reason, "select_model_provided_option_failed")


if __name__ == "__main__":
    unittest.main()
