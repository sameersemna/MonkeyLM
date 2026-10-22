"""Regression tests for submit-form failure handling and annotation retries.

Two failure modes the field run could not distinguish from a harness bug:

* a ``submit_form`` that blows its internal 4s deadline must degrade to
  ``PARTIAL_SUCCESS`` with a named error, not raise and kill the step;
* a vision-annotation transport failure must be retried a bounded number of
  times and then surface a diagnosable error rather than hanging.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from monkeylm.browser.actions.actions import _action_submit_form
from monkeylm.models.vision._legacy import annotate_relevant_screenshot


class _HangingForm:
    """Form locator whose submit-button lookup never returns."""

    def locator(self, selector: str) -> object:
        raise TimeoutError("submit button never appeared")

    async def evaluate(self, *args: object, **kwargs: object) -> bool:
        return True


class SubmitFormFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_internal_deadline_degrades_to_partial_success(self) -> None:
        """A submit that exceeds its own budget must not raise."""

        async def hanging_resolve(*args, **kwargs):
            return _HangingForm(), "resolved"

        log_entry: dict = {}
        with patch(
            "monkeylm.browser.actions.actions._resolve_form_boundary",
            side_effect=hanging_resolve,
        ):
            await _action_submit_form(
                SimpleNamespace(),
                SimpleNamespace(),
                "[id=4]",
                [],
                "default",
                3,
                SimpleNamespace(form_controls=[]),
                None,
                log_entry,
            )

        self.assertEqual(log_entry.get("status"), "PARTIAL_SUCCESS")
        self.assertEqual(log_entry.get("error"), "submit_form_internal_timeout")

    async def test_missing_form_is_recorded_not_raised(self) -> None:
        log_entry: dict = {}

        async def no_form(*args, **kwargs):
            return None, "missing"

        with patch(
            "monkeylm.browser.actions.actions._resolve_form_boundary",
            side_effect=no_form,
        ):
            await _action_submit_form(
                SimpleNamespace(),
                SimpleNamespace(),
                "[id=99]",
                [],
                "default",
                4,
                SimpleNamespace(form_controls=[]),
                None,
                log_entry,
            )

        self.assertIn("status", log_entry)
        error = str(log_entry.get("error") or "")
        self.assertNotEqual(log_entry.get("status"), "SUCCESS")


class AnnotationRetryTests(unittest.IsolatedAsyncioTestCase):
    def _settings(self) -> SimpleNamespace:
        return SimpleNamespace(
            output_dir=tempfile.mkdtemp(prefix="monkeylm-retry-"),
            vision_model="qwen3-vl:30b",
            pdf_vision_model="qwen3-vl:30b",
            vision_annotation_timeout_seconds=5.0,
            vision_annotation_max_tokens=2048,
            pdf_vision_timeout_seconds=20.0,
        )

    async def _image(self) -> str:
        path = Path(self._settings().output_dir) / "shot.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\n")
        return str(path)

    async def test_transient_failure_is_retried_and_can_succeed(self) -> None:
        settings = self._settings()
        image = await self._image()
        calls: list = []

        def flaky(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise ConnectionError("upstream reset")
            return {"choices": [{"message": {"content": ""}}]}

        with patch(
            "monkeylm.models.openai_client.chat_completion",
            side_effect=flaky,
        ), patch("monkeylm.models.openai_client.prepare_image_for_vlm", return_value=(b"x", 10, 10, "image/jpeg")), patch(
            "monkeylm.models.openai_client.build_vision_message_bytes", return_value={}
        ):
            await annotate_relevant_screenshot(settings, image, "issue", step_num=1, max_attempts=2)

        self.assertEqual(len(calls), 2)

    async def test_retry_budget_is_bounded(self) -> None:
        settings = self._settings()
        image = await self._image()
        calls: list = []

        def always_fails(*args, **kwargs):
            calls.append(1)
            raise ConnectionError("upstream reset")

        with patch(
            "monkeylm.models.openai_client.chat_completion",
            side_effect=always_fails,
        ), patch("monkeylm.models.openai_client.prepare_image_for_vlm", return_value=(b"x", 10, 10, "image/jpeg")), patch(
            "monkeylm.models.openai_client.build_vision_message_bytes", return_value={}
        ):
            result = await annotate_relevant_screenshot(
                settings, image, "issue", step_num=1, max_attempts=3
            )

        self.assertEqual(len(calls), 3)
        # The failure must degrade to an unannotated copy, not raise. The
        # copy-fallback path returns the `_annotated` twin with no box.
        self.assertTrue(result[0].endswith("_annotated.png"))
        self.assertIsNone(result[1])
        self.assertEqual(result[2].get("box_2d"), [0.0, 0.0, 0.0, 0.0])

    async def test_on_retry_callback_is_invoked(self) -> None:
        settings = self._settings()
        image = await self._image()
        events: list = []

        def always_fails(*args, **kwargs):
            raise ConnectionError("down")

        with patch(
            "monkeylm.models.openai_client.chat_completion",
            side_effect=always_fails,
        ), patch("monkeylm.models.openai_client.prepare_image_for_vlm", return_value=(b"x", 10, 10, "image/jpeg")), patch(
            "monkeylm.models.openai_client.build_vision_message_bytes", return_value={}
        ):
            await annotate_relevant_screenshot(
                settings,
                image,
                "issue",
                step_num=1,
                max_attempts=2,
                on_retry=lambda attempt, total, exc: events.append((attempt, total)),
            )

        self.assertEqual(events, [(1, 2)])

    async def test_single_attempt_never_retries(self) -> None:
        settings = self._settings()
        image = await self._image()
        calls: list = []

        def always_fails(*args, **kwargs):
            calls.append(1)
            raise ConnectionError("down")

        with patch(
            "monkeylm.models.openai_client.chat_completion",
            side_effect=always_fails,
        ), patch("monkeylm.models.openai_client.prepare_image_for_vlm", return_value=(b"x", 10, 10, "image/jpeg")), patch(
            "monkeylm.models.openai_client.build_vision_message_bytes", return_value={}
        ):
            await annotate_relevant_screenshot(settings, image, "issue", step_num=1, max_attempts=1)

        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()