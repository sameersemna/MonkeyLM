import argparse
import asyncio
import json
import os
import random
import signal
import tempfile
import unittest
from datetime import datetime
from typing import Any, Dict, Optional

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
            vision_model=None,
            ollama_timeout_seconds=None,
            max_steps=None,
            workers=None,
            max_steps_per_worker=None,
            worker_navigation_retries=None,
            worker_qdrant_init_retries=None,
            worker_boundary_recovery_retries=None,
            retry_base_delay_seconds=None,
            headless=None,
            window_size=None,
            no_viewport=None,
            seed=321,
            postgres_dsn=None,
            redis_url=None,
            redis_prefix=None,
            redis_path_lock_ttl_seconds=None,
            golden_baseline_mode=None,
            strict_persistence=None,
            qdrant_url=None,
            qdrant_collection=None,
            qdrant_embedding_provider=None,
            qdrant_embedding_model=None,
            qdrant_disable_reads=False,
            qdrant_disable_writes=False,
            qdrant_read_only=False,
            qdrant_enable_rerank=False,
            qdrant_disable_rerank=False,
            qdrant_rerank_model=None,
            qdrant_candidate_limit=None,
            qdrant_inspect=False,
            qdrant_clear=False,
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
            vision_model=None,
            ollama_timeout_seconds=None,
            max_steps=None,
            workers=None,
            max_steps_per_worker=None,
            worker_navigation_retries=None,
            worker_qdrant_init_retries=None,
            worker_boundary_recovery_retries=None,
            retry_base_delay_seconds=None,
            headless=None,
            window_size=None,
            no_viewport=None,
            seed=None,
            postgres_dsn=None,
            redis_url=None,
            redis_prefix=None,
            redis_path_lock_ttl_seconds=None,
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

    def test_apply_runtime_overrides_retry_policy(self) -> None:
        original_nav_retries = m.WORKER_NAVIGATION_RETRIES
        original_qdrant_retries = m.WORKER_QDRANT_INIT_RETRIES
        original_boundary_retries = m.WORKER_BOUNDARY_RECOVERY_RETRIES
        original_base_delay = m.RETRY_BASE_DELAY_SECONDS

        args = argparse.Namespace(
            target_url=None,
            ollama_model=None,
            vision_model=None,
            ollama_timeout_seconds=None,
            max_steps=None,
            workers=None,
            max_steps_per_worker=None,
            worker_navigation_retries=4,
            worker_qdrant_init_retries=2,
            worker_boundary_recovery_retries=3,
            retry_base_delay_seconds=1.25,
            headless=None,
            window_size=None,
            no_viewport=None,
            seed=None,
            postgres_dsn=None,
            redis_url=None,
            redis_prefix=None,
            redis_path_lock_ttl_seconds=None,
            golden_baseline_mode=None,
            strict_persistence=None,
            qdrant_url=None,
            qdrant_collection=None,
            qdrant_embedding_provider=None,
            qdrant_embedding_model=None,
            qdrant_disable_reads=False,
            qdrant_disable_writes=False,
            qdrant_read_only=False,
            qdrant_enable_rerank=False,
            qdrant_disable_rerank=False,
            qdrant_rerank_model=None,
            qdrant_candidate_limit=None,
            qdrant_inspect=False,
            qdrant_clear=False,
        )

        m.apply_runtime_overrides(args)

        self.assertEqual(m.WORKER_NAVIGATION_RETRIES, 4)
        self.assertEqual(m.WORKER_QDRANT_INIT_RETRIES, 2)
        self.assertEqual(m.WORKER_BOUNDARY_RECOVERY_RETRIES, 3)
        self.assertEqual(m.RETRY_BASE_DELAY_SECONDS, 1.25)

        m.WORKER_NAVIGATION_RETRIES = original_nav_retries
        m.WORKER_QDRANT_INIT_RETRIES = original_qdrant_retries
        m.WORKER_BOUNDARY_RECOVERY_RETRIES = original_boundary_retries
        m.RETRY_BASE_DELAY_SECONDS = original_base_delay

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

    def test_build_decision_prompt_json_example_survives_fstring_interpolation(self) -> None:
        """Regression test for the f-string brace crash in build_decision_prompt.

        The JSON example block contains literal curly braces. When the prompt is
        built as an f-string, those braces must be escaped so Python does not try
        to interpret them as replacement fields / format specifiers.
        """
        page_state = "URL: https://example.com/\nTitle: Example\nElements:\n[id=0] <BUTTON text=\"Save\" />"
        prompt = m.build_decision_prompt(page_state, memory_logs=[])

        # These literals must appear verbatim in the final prompt text.
        self.assertIn('"target": "[id=1]"', prompt)
        self.assertIn('"value": "valid@example.com"', prompt)
        self.assertIn('"reason": "happy_valid_email"', prompt)
        # The outer JSON braces should be rendered as single braces for the LLM.
        self.assertIn('"input_payloads": [', prompt)
        self.assertIn(']', prompt)

    def test_qdrant_parse_rerank_response(self) -> None:
        store = m.QdrantMemoryStore()
        parsed = store._parse_rerank_response('{"ranked_indices": [2, 0, 1]}')
        self.assertEqual(parsed, [2, 0, 1])

    def test_allocate_worker_steps_respects_total_and_cap(self) -> None:
        allocations = m.allocate_worker_steps(total_steps=10, worker_count=3, per_worker_cap=4)
        self.assertEqual(sum(allocations), 10)
        self.assertTrue(all(count <= 4 for count in allocations))

    def test_validate_runtime_configuration_rejects_invalid_per_worker_cap(self) -> None:
        original_max_steps = m.MAX_STEPS
        original_max_steps_per_worker = m.MAX_STEPS_PER_WORKER
        try:
            m.MAX_STEPS = 5
            m.MAX_STEPS_PER_WORKER = 6
            with self.assertRaises(ValueError):
                m.validate_runtime_configuration()
        finally:
            m.MAX_STEPS = original_max_steps
            m.MAX_STEPS_PER_WORKER = original_max_steps_per_worker

    def test_validate_runtime_configuration_rejects_excessive_retry_count(self) -> None:
        original_nav_retries = m.WORKER_NAVIGATION_RETRIES
        try:
            m.WORKER_NAVIGATION_RETRIES = m.MAX_ALLOWED_RETRIES + 1
            with self.assertRaises(ValueError):
                m.validate_runtime_configuration()
        finally:
            m.WORKER_NAVIGATION_RETRIES = original_nav_retries

    def test_validate_runtime_configuration_rejects_excessive_retry_delay(self) -> None:
        original_retry_delay = m.RETRY_BASE_DELAY_SECONDS
        try:
            m.RETRY_BASE_DELAY_SECONDS = m.MAX_ALLOWED_RETRY_BASE_DELAY_SECONDS + 0.5
            with self.assertRaises(ValueError):
                m.validate_runtime_configuration()
        finally:
            m.RETRY_BASE_DELAY_SECONDS = original_retry_delay

    def test_build_worker_user_data_dir_returns_distinct_paths(self) -> None:
        dir_one = m.build_worker_user_data_dir(1)
        dir_two = m.build_worker_user_data_dir(2)

        self.assertNotEqual(dir_one, dir_two)
        self.assertTrue(os.path.isdir(dir_one))
        self.assertTrue(os.path.isdir(dir_two))

    def test_build_redis_key_applies_prefix(self) -> None:
        original_prefix = m.REDIS_PREFIX
        try:
            m.REDIS_PREFIX = "monkey:"
            self.assertEqual(m.build_redis_key("visited"), "monkey:visited")
            m.REDIS_PREFIX = ""
            self.assertEqual(m.build_redis_key("visited"), "visited")
        finally:
            m.REDIS_PREFIX = original_prefix

    def test_apply_runtime_overrides_redis_prefix(self) -> None:
        original_prefix = m.REDIS_PREFIX
        try:
            args = argparse.Namespace(
                target_url=None,
                ollama_model=None,
                vision_model=None,
                ollama_timeout_seconds=None,
                max_steps=None,
                workers=None,
                max_steps_per_worker=None,
                worker_navigation_retries=None,
                worker_qdrant_init_retries=None,
                worker_boundary_recovery_retries=None,
                retry_base_delay_seconds=None,
                redis_url=None,
                redis_prefix="test:",
                redis_path_lock_ttl_seconds=None,
                headless=None,
                window_size=None,
                no_viewport=None,
                seed=None,
                postgres_dsn=None,
                golden_baseline_mode=None,
                strict_persistence=None,
                qdrant_url=None,
                qdrant_collection=None,
                qdrant_embedding_provider=None,
                qdrant_embedding_model=None,
                qdrant_disable_reads=False,
                qdrant_disable_writes=False,
                qdrant_read_only=False,
                qdrant_enable_rerank=False,
                qdrant_disable_rerank=False,
                qdrant_rerank_model=None,
                qdrant_candidate_limit=None,
                qdrant_inspect=False,
                qdrant_clear=False,
            )
            m.apply_runtime_overrides(args)
            self.assertEqual(m.REDIS_PREFIX, "test:")
        finally:
            m.REDIS_PREFIX = original_prefix

    def test_apply_runtime_overrides_ollama_timeout(self) -> None:
        original_timeout = m.OLLAMA_TIMEOUT_SECONDS
        try:
            args = argparse.Namespace(
                target_url=None,
                ollama_model=None,
                vision_model=None,
                ollama_timeout_seconds=30.0,
                max_steps=None,
                workers=None,
                max_steps_per_worker=None,
                worker_navigation_retries=None,
                worker_qdrant_init_retries=None,
                worker_boundary_recovery_retries=None,
                retry_base_delay_seconds=None,
                redis_url=None,
                redis_prefix=None,
                redis_path_lock_ttl_seconds=None,
                headless=None,
                window_size=None,
                no_viewport=None,
                seed=None,
                postgres_dsn=None,
                golden_baseline_mode=None,
                strict_persistence=None,
                qdrant_url=None,
                qdrant_collection=None,
                qdrant_embedding_provider=None,
                qdrant_embedding_model=None,
                qdrant_disable_reads=False,
                qdrant_disable_writes=False,
                qdrant_read_only=False,
                qdrant_enable_rerank=False,
                qdrant_disable_rerank=False,
                qdrant_rerank_model=None,
                qdrant_candidate_limit=None,
                qdrant_inspect=False,
                qdrant_clear=False,
            )
            m.apply_runtime_overrides(args)
            self.assertEqual(m.OLLAMA_TIMEOUT_SECONDS, 30.0)
        finally:
            m.OLLAMA_TIMEOUT_SECONDS = original_timeout

    def test_apply_runtime_overrides_redis_path_lock_ttl(self) -> None:
        original_ttl = m.REDIS_PATH_LOCK_TTL_SECONDS
        try:
            args = argparse.Namespace(
                target_url=None,
                ollama_model=None,
                vision_model=None,
                ollama_timeout_seconds=None,
                max_steps=None,
                workers=None,
                max_steps_per_worker=None,
                worker_navigation_retries=None,
                worker_qdrant_init_retries=None,
                worker_boundary_recovery_retries=None,
                retry_base_delay_seconds=None,
                redis_url=None,
                redis_prefix=None,
                redis_path_lock_ttl_seconds=90,
                headless=None,
                window_size=None,
                no_viewport=None,
                seed=None,
                postgres_dsn=None,
                golden_baseline_mode=None,
                strict_persistence=None,
                qdrant_url=None,
                qdrant_collection=None,
                qdrant_embedding_provider=None,
                qdrant_embedding_model=None,
                qdrant_disable_reads=False,
                qdrant_disable_writes=False,
                qdrant_read_only=False,
                qdrant_enable_rerank=False,
                qdrant_disable_rerank=False,
                qdrant_rerank_model=None,
                qdrant_candidate_limit=None,
                qdrant_inspect=False,
                qdrant_clear=False,
            )
            m.apply_runtime_overrides(args)
            self.assertEqual(m.REDIS_PATH_LOCK_TTL_SECONDS, 90)
        finally:
            m.REDIS_PATH_LOCK_TTL_SECONDS = original_ttl

    def test_validate_runtime_configuration_rejects_invalid_path_lock_ttl(self) -> None:
        original_ttl = m.REDIS_PATH_LOCK_TTL_SECONDS
        try:
            m.REDIS_PATH_LOCK_TTL_SECONDS = 0
            with self.assertRaises(ValueError) as ctx:
                m.validate_runtime_configuration()
            self.assertIn("REDIS_PATH_LOCK_TTL_SECONDS", str(ctx.exception))

            m.REDIS_PATH_LOCK_TTL_SECONDS = 301
            with self.assertRaises(ValueError) as ctx:
                m.validate_runtime_configuration()
            self.assertIn("REDIS_PATH_LOCK_TTL_SECONDS", str(ctx.exception))
        finally:
            m.REDIS_PATH_LOCK_TTL_SECONDS = original_ttl

    def test_compute_action_path_hash_is_deterministic(self) -> None:
        h1 = m._compute_action_path_hash("example.com", "/login", "click", "submit-btn")
        h2 = m._compute_action_path_hash("example.com", "/login", "click", "submit-btn")
        self.assertEqual(h1, h2)
        h3 = m._compute_action_path_hash("example.com", "/login", "click", "cancel-btn")
        self.assertNotEqual(h1, h3)

    def test_is_cloud_vision_model_preview_suffix(self) -> None:
        self.assertTrue(m._is_cloud_vision_model("gemini-3-flash-preview"))

    def test_is_cloud_vision_model_cloud_suffix(self) -> None:
        self.assertTrue(m._is_cloud_vision_model("gemma4:31b-cloud"))

    def test_is_cloud_vision_model_minimax_m3(self) -> None:
        self.assertTrue(m._is_cloud_vision_model("minimax-m3"))
        self.assertTrue(m._is_cloud_vision_model("minimax-m3:cloud"))

    def test_is_cloud_vision_model_local(self) -> None:
        self.assertFalse(m._is_cloud_vision_model("llama3.2-vision"))
        self.assertFalse(m._is_cloud_vision_model(""))

    def test_build_vision_annotation_prompt_contains_box_2d(self) -> None:
        prompt = m._build_vision_annotation_prompt("button missing")
        self.assertIn("box_2d", prompt)
        self.assertIn("[ymin, xmin, ymax, xmax]", prompt)
        self.assertNotIn('"box":', prompt)
        self.assertIn("do not use `box`", prompt)

    def test_apply_runtime_overrides_vision_model(self) -> None:
        original_vision_model = m.VISION_MODEL
        try:
            args = argparse.Namespace(
                target_url=None,
                ollama_model=None,
                vision_model="gemini-3-flash-preview",
                ollama_timeout_seconds=None,
                max_steps=None,
                workers=None,
                max_steps_per_worker=None,
                worker_navigation_retries=None,
                worker_qdrant_init_retries=None,
                worker_boundary_recovery_retries=None,
                retry_base_delay_seconds=None,
                redis_url=None,
                redis_prefix=None,
                redis_path_lock_ttl_seconds=None,
                headless=None,
                window_size=None,
                no_viewport=None,
                seed=None,
                postgres_dsn=None,
                golden_baseline_mode=None,
                strict_persistence=None,
                qdrant_url=None,
                qdrant_collection=None,
                qdrant_embedding_provider=None,
                qdrant_embedding_model=None,
                qdrant_disable_reads=False,
                qdrant_disable_writes=False,
                qdrant_read_only=False,
                qdrant_enable_rerank=False,
                qdrant_disable_rerank=False,
                qdrant_rerank_model=None,
                qdrant_candidate_limit=None,
                qdrant_inspect=False,
                qdrant_clear=False,
            )
            m.apply_runtime_overrides(args)
            self.assertEqual(m.VISION_MODEL, "gemini-3-flash-preview")
        finally:
            m.VISION_MODEL = original_vision_model


class MonkeyAgentAdvancedAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_with_retry_backoff_retries_and_succeeds(self) -> None:
        attempts = {"count": 0}

        async def flaky() -> str:
            attempts["count"] += 1
            if attempts["count"] < 3:
                raise RuntimeError("transient")
            return "ok"

        result = await m.with_retry_backoff(
            "test-flaky",
            flaky,
            retries=3,
            initial_delay_seconds=0.01,
        )
        self.assertEqual(result, "ok")
        self.assertEqual(attempts["count"], 3)

    async def test_performance_snapshot_includes_navigation_telemetry_shape(self) -> None:
        defects = m.DefectTracker()
        perf_monitor = m.PerformanceMonitor(defects)

        async with m.async_playwright() as p:
            user_data_dir = f"{m.RUN_USER_DATA_DIR}/test_perf_nav"
            context, _ = await m.launch_context_with_fallback(
                p,
                user_data_dir=user_data_dir,
                worker_label="test-perf-nav",
            )
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
            user_data_dir = f"{m.RUN_USER_DATA_DIR}/test_boundary_recovery"
            context, _ = await m.launch_context_with_fallback(
                p,
                user_data_dir=user_data_dir,
                worker_label="test-boundary",
            )
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

    async def test_claim_action_path_lock_with_fake_redis(self) -> None:
        defects = m.DefectTracker()
        engine = m.PersistenceEngine(defects, max_workers=2)

        class FakeRedis:
            def __init__(self) -> None:
                self.store: Dict[str, Any] = {}

            async def set(self, key: str, value: str, *, nx: bool = False, ex: Optional[int] = None) -> Optional[str]:
                if nx and key in self.store:
                    return None
                self.store[key] = (value, ex)
                return "OK"

        engine.redis_client = FakeRedis()
        try:
            self.assertTrue(await engine.claim_action_path_lock("abc123"))
            self.assertFalse(await engine.claim_action_path_lock("abc123"))
            self.assertTrue(await engine.claim_action_path_lock("def456"))
        finally:
            engine.redis_client = None

    async def test_graceful_shutdown_event_exists_and_can_be_set(self) -> None:
        self.assertIsInstance(m.SHUTDOWN_EVENT, asyncio.Event)
        self.assertFalse(m.SHUTDOWN_EVENT.is_set())
        m.SHUTDOWN_EVENT.set()
        self.assertTrue(m.SHUTDOWN_EVENT.is_set())
        m.SHUTDOWN_EVENT.clear()
        self.assertFalse(m.SHUTDOWN_EVENT.is_set())

    async def test_request_graceful_shutdown_sets_event_and_flag(self) -> None:
        original_flag = m.GRACEFUL_SHUTDOWN_REQUESTED
        try:
            m.GRACEFUL_SHUTDOWN_REQUESTED = False
            m.SHUTDOWN_EVENT.clear()
            m._request_graceful_shutdown(signal.SIGINT, None)
            self.assertTrue(m.GRACEFUL_SHUTDOWN_REQUESTED)
            # The handler schedules the event set on the running loop; yield so
            # the callback is processed before asserting.
            await asyncio.sleep(0)
            self.assertTrue(m.SHUTDOWN_EVENT.is_set())
        finally:
            m.GRACEFUL_SHUTDOWN_REQUESTED = original_flag
            m.SHUTDOWN_EVENT.clear()


if __name__ == "__main__":
    unittest.main()
