"""Regression tests for resilient form submission."""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from monkeylm.browser.actions.actions import _action_submit_form


class SubmitFormResilienceTests(unittest.IsolatedAsyncioTestCase):
    async def test_submit_form_bails_out_on_slow_payload_fill(self) -> None:
        async def slow_fill(*args: object, **kwargs: object) -> bool:
            await asyncio.sleep(0.3)
            return False

        class FakeElement:
            @property
            def first(self) -> "FakeElement":
                return self

            async def count(self, *args: object, **kwargs: object) -> int:
                return 1

            async def evaluate(self, *args: object, **kwargs: object) -> bool:
                return True

        class FakeFormLocator:
            def locator(self, selector: str) -> object:
                if "button[type='submit']" in selector or "input[type='submit']" in selector:
                    return FakeElement()
                return FakeElement()

            async def evaluate(self, *args: object, **kwargs: object) -> bool:
                return True

        async def fake_resolve_form_boundary(page: object, target: str) -> tuple[object, str]:
            return FakeFormLocator(), "resolved"

        page = SimpleNamespace()
        log_entry: dict[str, object] = {}

        with patch("monkeylm.browser.actions.actions._resolve_form_boundary", side_effect=fake_resolve_form_boundary), patch("monkeylm.browser.actions.actions._locator_for_target_id", new=AsyncMock(return_value=FakeElement())), patch("monkeylm.browser.actions.actions._click_element_resilient", new=AsyncMock(return_value=False)), patch("monkeylm.browser.actions.actions._fill_input_resilient", side_effect=slow_fill):
            await asyncio.wait_for(
                _action_submit_form(
                    page,
                    SimpleNamespace(),
                    "[id=96]",
                    [{"target": "[id=7]", "value": "hello", "reason": "test"}],
                    "default",
                    7,
                    SimpleNamespace(form_controls=[]),
                    None,
                    log_entry,
                ),
                timeout=0.2,
            )

        self.assertEqual(log_entry.get("status"), "PARTIAL_SUCCESS")

    async def test_submit_form_does_not_wait_for_slow_enter_fallback(self) -> None:
        class SlowPressInput:
            async def press(self, key: str) -> None:
                await asyncio.sleep(0.3)

        class FakeInputsLocator:
            def __init__(self) -> None:
                self.last = SlowPressInput()

            async def count(self, *args: object, **kwargs: object) -> int:
                return 1

        class FakeSubmitButtonLocator:
            @property
            def first(self) -> "FakeSubmitButtonLocator":
                return self

            async def count(self, *args: object, **kwargs: object) -> int:
                return 0

        class FakeFormLocator:
            def locator(self, selector: str) -> object:
                if "button[type='submit']" in selector or "input[type='submit']" in selector:
                    return FakeSubmitButtonLocator()
                return FakeInputsLocator()

        async def fake_resolve_form_boundary(page: object, target: str) -> tuple[object, str]:
            return FakeFormLocator(), "resolved"

        page = SimpleNamespace()
        log_entry: dict[str, object] = {}

        with patch("monkeylm.browser.actions.actions._resolve_form_boundary", side_effect=fake_resolve_form_boundary):
            await asyncio.wait_for(
                _action_submit_form(
                    page,
                    SimpleNamespace(),
                    "[id=96]",
                    [],
                    "default",
                    7,
                    SimpleNamespace(form_controls=[]),
                    None,
                    log_entry,
                ),
                timeout=1.0,
            )

        self.assertEqual(log_entry.get("status"), "PARTIAL_SUCCESS")
        self.assertIn(log_entry.get("status"), {"PARTIAL_SUCCESS", "SUCCESS"})


if __name__ == "__main__":
    unittest.main()
