"""Regression tests for resilient click handling."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from monkeylm.browser.actions.actions import _action_click


class ClickResilienceTests(unittest.IsolatedAsyncioTestCase):
    async def test_action_click_falls_back_when_click_times_out(self) -> None:
        class FlakyLocator:
            async def scroll_into_view_if_needed(self, **kwargs: object) -> None:
                return None

            async def wait_for(self, **kwargs: object) -> None:
                return None

            async def click(self, **kwargs: object) -> None:
                raise TimeoutError("outside viewport")

            async def evaluate(self, *args: object, **kwargs: object) -> bool:
                return True

        async def fake_locator_for_target_id(page: object, target: str) -> object:
            return FlakyLocator()

        with patch("monkeylm.browser.actions.actions._locator_for_target_id", side_effect=fake_locator_for_target_id):
            await _action_click(SimpleNamespace(), "[id=0]")


if __name__ == "__main__":
    unittest.main()
