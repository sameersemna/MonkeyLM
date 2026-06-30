import argparse
import json
import random
import tempfile
import unittest
from datetime import datetime

import monkey_agent_advanced as m


class MonkeyAgentAdvancedTests(unittest.TestCase):
    def test_is_in_scope_netloc_matching(self) -> None:
        self.assertTrue(
            m.is_in_scope(
                "https://noblequran-85hu2yge.manus.space/bookmarked-verses",
                "https://noblequran-85hu2yge.manus.space/",
            )
        )
        self.assertFalse(
            m.is_in_scope(
                "https://example.com/",
                "https://noblequran-85hu2yge.manus.space/",
            )
        )
        self.assertFalse(m.is_in_scope("about:blank", "https://noblequran-85hu2yge.manus.space/"))

    def test_apply_runtime_overrides_sets_seed(self) -> None:
        previous_seed = m.ACTIVE_SEED

        args = argparse.Namespace(
            target_url=None,
            ollama_model=None,
            max_steps=None,
            headless=None,
            window_size=None,
            no_viewport=None,
            seed=321,
        )

        m.apply_runtime_overrides(args)

        expected_rng = random.Random(321)
        self.assertEqual(m.ACTIVE_SEED, "321")
        self.assertAlmostEqual(random.random(), expected_rng.random())

        m.ACTIVE_SEED = previous_seed

    def test_generate_json_summary_contains_seed_and_boundary_drift(self) -> None:
        original_output_dir = m.OUTPUT_DIR
        original_seed = m.ACTIVE_SEED
        original_logs = m.test_logs
        original_defects = m.DEFECTS
        original_network_monitor = m.NETWORK_MONITOR

        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                m.OUTPUT_DIR = tmp_dir
                m.ACTIVE_SEED = "999"
                m.test_logs = []
                m.DEFECTS = m.DefectTracker()
                m.NETWORK_MONITOR = m.NetworkMonitor(m.DEFECTS)

                m.DEFECTS.add(
                    "boundary_drift",
                    {
                        "step": 1,
                        "type": "Boundary Drift",
                        "current_url": "https://example.com/",
                        "target_url": "https://noblequran-85hu2yge.manus.space/",
                    },
                )

                now = datetime.now()
                m.generate_json_summary(now, now)

                with open(f"{tmp_dir}/results.json", "r", encoding="utf-8") as f:
                    data = json.load(f)

                self.assertEqual(data.get("active_seed"), "999")
                self.assertIn("boundary_drift", data.get("defects", {}))
                self.assertEqual(len(data["defects"]["boundary_drift"]), 1)
        finally:
            m.OUTPUT_DIR = original_output_dir
            m.ACTIVE_SEED = original_seed
            m.test_logs = original_logs
            m.DEFECTS = original_defects
            m.NETWORK_MONITOR = original_network_monitor


class MonkeyAgentAdvancedAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_performance_snapshot_includes_navigation_telemetry_shape(self) -> None:
        defects = m.DefectTracker()
        perf_monitor = m.PerformanceMonitor(defects)

        async with m.async_playwright() as p:
            context = await m.launch_context_with_fallback(p)
            page = context.pages[0]

            await page.goto(m.TARGET_URL, wait_until="domcontentloaded", timeout=45000)
            await m.wait_for_page_ready(page, "test-nav-telemetry")

            await perf_monitor.install(page)
            snapshot = await perf_monitor.snapshot(page)

            self.assertIn("navigation", snapshot)
            navigation = snapshot["navigation"]
            self.assertIn("entries", navigation)
            self.assertIsInstance(navigation["entries"], list)

            if "current_index" in navigation:
                self.assertIsInstance(navigation["current_index"], int)

            if navigation["entries"]:
                first_entry = navigation["entries"][0]
                self.assertIn("url", first_entry)
                self.assertIn("id", first_entry)
                self.assertIn("title", first_entry)
                self.assertIn("transition_type", first_entry)

            await context.close()

    async def test_boundary_drift_recovery_with_real_navigation(self) -> None:
        local_defects = m.DefectTracker()

        async with m.async_playwright() as p:
            context = await m.launch_context_with_fallback(p)
            page = context.pages[0]

            await page.goto(m.TARGET_URL, wait_until="domcontentloaded", timeout=45000)
            await m.wait_for_page_ready(page, "test-initial")

            await page.goto("https://example.com/", wait_until="domcontentloaded", timeout=45000)
            await m.wait_for_page_ready(page, "test-out-of-scope")

            self.assertFalse(m.is_in_scope(page.url, m.TARGET_URL))

            if not m.is_in_scope(page.url, m.TARGET_URL):
                local_defects.add(
                    "boundary_drift",
                    {
                        "step": 1,
                        "type": "Boundary Drift",
                        "current_url": page.url,
                        "target_url": m.TARGET_URL,
                    },
                )
                await page.goto(m.TARGET_URL, wait_until="domcontentloaded", timeout=45000)
                await m.wait_for_page_ready(page, "test-boundary-recovery")

            self.assertTrue(m.is_in_scope(page.url, m.TARGET_URL))
            self.assertEqual(len(local_defects.boundary_drift), 1)
            self.assertEqual(local_defects.boundary_drift[0]["type"], "Boundary Drift")

            await context.close()


if __name__ == "__main__":
    unittest.main()
