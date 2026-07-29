"""Regression tests for executor annotation timeouts."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from monkeylm.browser.actions.executor import execute_action


class ExecutorAnnotationTimeoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_execute_action_does_not_wait_for_slow_annotation(self) -> None:
        class DummyPage:
            url = "https://example.com"

            async def wait_for_load_state(self, *args, **kwargs) -> None:
                return None

            async def screenshot(self, path: str) -> None:
                with open(path, "wb") as handle:
                    handle.write(b"img")

        class DummySnapshot:
            is_empty_capture = False
            url = "https://example.com"
            structure_hash = "abc"
            dom_hash = "def"
            screenshot_path = "screenshot.png"
            elements = []
            forms = []
            form_controls = []

        async def fake_get_page_state(*args, **kwargs):
            return DummySnapshot()

        async def slow_annotation(*args, **kwargs):
            await asyncio.sleep(3.0)
            return "annotated.png"

        settings = SimpleNamespace(
            output_dir=tempfile.mkdtemp(prefix="monkeylm-test-"),
            pdf_generate=True,
            vision_model="qwen3-vl:30b",
            pdf_vision_model="qwen3-vl:30b",
            step_timeout_seconds=30.0,
        )

        perf_monitor = SimpleNamespace(
            snapshot=AsyncMock(return_value=None),
            detect_bottlenecks=AsyncMock(return_value=[]),
        )
        network_monitor = SimpleNamespace(detect_zombie_ui=AsyncMock(return_value=None))
        defects = SimpleNamespace(add=lambda *args, **kwargs: None)
        fuzzer = SimpleNamespace(next_payload=lambda: "")

        with patch("monkeylm.browser.actions.executor.get_page_state", side_effect=fake_get_page_state), \
             patch("monkeylm.browser.actions.executor.compare_screenshots_pixelmatch", return_value={"diff_ratio": 0.0, "diff_pixels": 0, "engine": "pixelmatch", "diff_image": ""}), \
             patch("monkeylm.models._step_defects_summary", return_value=[]), \
             patch("monkeylm.models.annotate_relevant_screenshot", new=AsyncMock(side_effect=slow_annotation)):
            started = asyncio.get_running_loop().time()
            await execute_action(
                page=DummyPage(),
                settings=settings,
                action_plan={"action": "scroll", "target": "", "value": ""},
                step_num=1,
                fuzzer=fuzzer,
                defects=defects,
                network_monitor=network_monitor,
                perf_monitor=perf_monitor,
                log_sink=None,
                persistence_engine=None,
                worker_id=1,
                validation_prober=None,
            )
            elapsed = asyncio.get_running_loop().time() - started

        self.assertLess(elapsed, 2.5)


if __name__ == "__main__":
    unittest.main()
