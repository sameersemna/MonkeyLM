import argparse
import json
import random
import tempfile
import unittest
from datetime import datetime

import monkey_agent_advanced as m


class MonkeyAgentAdvancedTests(unittest.TestCase):
    def test_split_domain_and_route(self) -> None:
        domain, route = m.split_domain_and_route("https://example.com/account/settings?tab=profile")
        self.assertEqual(domain, "example.com")
        self.assertEqual(route, "/account/settings?tab=profile")

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

    def test_apply_runtime_overrides_qdrant_toggles(self) -> None:
        original_reads = m.QDRANT_ENABLE_READS
        original_writes = m.QDRANT_ENABLE_WRITES
        original_provider = m.QDRANT_EMBEDDING_PROVIDER
        original_model = m.QDRANT_EMBEDDING_MODEL
        original_admin = m.QDRANT_ADMIN_ACTION
        original_rerank_enabled = m.QDRANT_RERANK_ENABLED
        original_rerank_model = m.QDRANT_RERANK_MODEL
        original_candidate_limit = m.QDRANT_CANDIDATE_LIMIT

        args = argparse.Namespace(
            target_url=None,
            ollama_model=None,
            max_steps=None,
            headless=None,
            window_size=None,
            no_viewport=None,
            seed=None,
            postgres_dsn=None,
            redis_url=None,
            golden_baseline_mode=None,
            strict_persistence=None,
            qdrant_url=None,
            qdrant_collection=None,
            qdrant_embedding_provider="ollama",
            qdrant_embedding_model="nomic-embed-text",
            qdrant_disable_reads=False,
            qdrant_disable_writes=False,
            qdrant_read_only=True,
            qdrant_enable_rerank=True,
            qdrant_disable_rerank=False,
            qdrant_rerank_model="qwen2.5:3b",
            qdrant_candidate_limit=25,
            qdrant_inspect=True,
            qdrant_clear=False,
        )

        m.apply_runtime_overrides(args)

        self.assertEqual(m.QDRANT_EMBEDDING_PROVIDER, "ollama")
        self.assertEqual(m.QDRANT_EMBEDDING_MODEL, "nomic-embed-text")
        self.assertTrue(m.QDRANT_ENABLE_READS)
        self.assertFalse(m.QDRANT_ENABLE_WRITES)
        self.assertTrue(m.QDRANT_RERANK_ENABLED)
        self.assertEqual(m.QDRANT_RERANK_MODEL, "qwen2.5:3b")
        self.assertEqual(m.QDRANT_CANDIDATE_LIMIT, 25)
        self.assertEqual(m.QDRANT_ADMIN_ACTION, "inspect")

        m.QDRANT_ENABLE_READS = original_reads
        m.QDRANT_ENABLE_WRITES = original_writes
        m.QDRANT_EMBEDDING_PROVIDER = original_provider
        m.QDRANT_EMBEDDING_MODEL = original_model
        m.QDRANT_ADMIN_ACTION = original_admin
        m.QDRANT_RERANK_ENABLED = original_rerank_enabled
        m.QDRANT_RERANK_MODEL = original_rerank_model
        m.QDRANT_CANDIDATE_LIMIT = original_candidate_limit

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

    def test_generate_json_summary_includes_semantic_memory_telemetry(self) -> None:
        original_output_dir = m.OUTPUT_DIR
        original_seed = m.ACTIVE_SEED
        original_logs = m.test_logs
        original_defects = m.DEFECTS
        original_network_monitor = m.NETWORK_MONITOR

        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                m.OUTPUT_DIR = tmp_dir
                m.ACTIVE_SEED = "123"
                m.test_logs = [
                    {
                        "step": 1,
                        "status": "SUCCESS",
                        "memory_retrieval": {
                            "status": "ok",
                            "provider_used": "ollama",
                            "returned_count": 3,
                            "total_ms": 30.0,
                            "qdrant_search_ms": 12.0,
                            "rerank_ms": 8.0,
                            "rerank_applied": True,
                        },
                        "memory_write": {
                            "status": "ok",
                            "provider_used": "ollama",
                            "total_ms": 16.0,
                            "qdrant_upsert_ms": 9.0,
                        },
                    }
                ]
                m.DEFECTS = m.DefectTracker()
                m.NETWORK_MONITOR = m.NetworkMonitor(m.DEFECTS)

                now = datetime.now()
                m.generate_json_summary(now, now)

                with open(f"{tmp_dir}/results.json", "r", encoding="utf-8") as f:
                    data = json.load(f)

                telemetry = data.get("semantic_memory_telemetry", {})
                self.assertIn("retrieval", telemetry)
                self.assertIn("write", telemetry)
                self.assertEqual(telemetry["retrieval"].get("ok"), 1)
                self.assertEqual(telemetry["write"].get("ok"), 1)
                self.assertEqual(telemetry.get("providers", {}).get("ollama"), 2)
        finally:
            m.OUTPUT_DIR = original_output_dir
            m.ACTIVE_SEED = original_seed
            m.test_logs = original_logs
            m.DEFECTS = original_defects
            m.NETWORK_MONITOR = original_network_monitor

    def test_vibe_coding_accountability_sets_failed_on_drift(self) -> None:
        original_defects = m.DEFECTS
        try:
            m.DEFECTS = m.DefectTracker()
            m.DEFECTS.add(
                "regression_findings",
                {
                    "step": 2,
                    "domain": "example.com",
                    "page_route": "/checkout",
                    "missing_components": [
                        {"selector_hint": "#submit", "kind": "button", "tag": "BUTTON", "text": "Submit"},
                        {"selector_hint": "input[name=email]", "kind": "form", "tag": "INPUT", "text": ""},
                    ],
                    "broken_selectors": ["#submit", "input[name=email]"],
                    "expected_baseline_components": 8,
                },
            )

            accountability = m.summarize_vibe_coding_accountability()

            self.assertEqual(accountability.get("total_missing_historical_components"), 2)
            self.assertEqual(accountability.get("total_expected_baseline_components"), 8)
            self.assertEqual(accountability.get("regression_drift_index"), 25.0)
            self.assertEqual(accountability.get("run_summary_status"), "FAILED: Structural Drift Detected")
        finally:
            m.DEFECTS = original_defects

    def test_generate_markdown_report_includes_semantic_memory_telemetry(self) -> None:
        original_output_dir = m.OUTPUT_DIR
        original_logs = m.test_logs
        original_defects = m.DEFECTS

        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                m.OUTPUT_DIR = tmp_dir
                m.test_logs = [
                    {
                        "step": 1,
                        "action": "click",
                        "target": "[id=0]",
                        "status": "SUCCESS",
                        "memory_retrieval": {
                            "status": "ok",
                            "provider_used": "hash",
                            "returned_count": 2,
                            "total_ms": 14.0,
                            "qdrant_search_ms": 6.0,
                            "rerank_ms": 0.0,
                            "rerank_applied": False,
                        },
                        "memory_write": {
                            "status": "ok",
                            "provider_used": "hash",
                            "total_ms": 10.0,
                            "qdrant_upsert_ms": 5.0,
                        },
                    }
                ]
                m.DEFECTS = m.DefectTracker()
                m.DEFECTS.add(
                    "regression_findings",
                    {
                        "step": 3,
                        "type": "Vibe-Code-Regression-Missing-Component",
                        "severity": "high",
                        "domain": "example.com",
                        "page_route": "/settings",
                        "missing_components": [
                            {
                                "selector_hint": "#save-settings",
                                "kind": "button",
                                "tag": "BUTTON",
                                "text": "Save Settings",
                            }
                        ],
                        "broken_selectors": ["#save-settings"],
                        "expected_baseline_components": 5,
                    },
                )

                now = datetime.now()
                m.generate_markdown_report(now, now)

                with open(f"{tmp_dir}/test_report.md", "r", encoding="utf-8") as f:
                    report = f.read()

                self.assertIn("## Semantic Memory Telemetry", report)
                self.assertIn("Retrieval events", report)
                self.assertIn("Avg Qdrant upsert", report)
                self.assertIn("### ⚠️ Vibe Coding Drift Summary", report)
                self.assertIn("FAILED: Structural Drift Detected", report)
                self.assertIn("#save-settings", report)
        finally:
            m.OUTPUT_DIR = original_output_dir
            m.test_logs = original_logs
            m.DEFECTS = original_defects

    def test_diff_component_manifests_detects_missing_components(self) -> None:
        golden = [
            {"kind": "button", "tag": "BUTTON", "text": "Save", "selector_hint": "#save"},
            {"kind": "form", "tag": "FORM", "text": "profile", "selector_hint": "form#profile"},
        ]
        current = [
            {"kind": "form", "tag": "FORM", "text": "profile", "selector_hint": "form#profile"},
        ]

        missing, broken = m.diff_component_manifests(golden, current)

        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0]["text"], "Save")
        self.assertEqual(broken, ["#save"])

    def test_build_decision_prompt_includes_memory_logs_header(self) -> None:
        page_state = "URL: https://example.com/\nTitle: Example\nElements:\n[id=0] <BUTTON text=\"Save\" />"
        memory_logs = [
            {
                "layout_summary": "URL: https://example.com/\\n[id=0] <BUTTON text=Save />",
                "action": "type",
                "outcome": "status=SUCCESS; regressions=1 tag=Vibe-Code-Regression-Missing-Component",
                "url": "https://example.com/",
                "score": 0.93,
            }
        ]

        prompt = m.build_decision_prompt(page_state, memory_logs)

        self.assertIn("## Memory Logs of Previous Vibe Changes", prompt)
        self.assertIn("Vibe-Code-Regression-Missing-Component", prompt)

    def test_qdrant_parse_rerank_response(self) -> None:
        store = m.QdrantMemoryStore()
        parsed = store._parse_rerank_response('{"ranked_indices": [2, 0, 1]}')
        self.assertEqual(parsed, [2, 0, 1])


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
