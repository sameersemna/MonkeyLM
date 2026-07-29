"""Tests for the MonkeyLM package.

All imports reference canonical module paths directly.
Mutable globals are accessed via their module to ensure mutations propagate.
"""

import argparse
import asyncio
import json
import os
import random
import signal
import sys
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime
from typing import Any, Dict, Optional

import monkeylm.config as config
import monkeylm.core as core_module
from monkeylm.config import (
    DEFAULT_TARGET_URL,
    MAX_ALLOWED_RETRIES,
    MAX_ALLOWED_RETRY_BASE_DELAY_SECONDS,
    apply_runtime_overrides,
    build_redis_key,
    inspect_optional_runtime_dependencies,
    is_in_scope,
    load_settings,
    normalize_action_plan,
    parse_cli_args,
    split_domain_and_route,
    validate_runtime_configuration,
    SHUTDOWN_EVENT,
    _request_graceful_shutdown,
)
from monkeylm.core import (
    DefectTracker,
    NetworkMonitor,
    PerformanceMonitor,
    allocate_worker_steps,
    build_worker_user_data_dir,
    with_retry_backoff,
    test_logs,
)
from monkeylm.core.monitor import StallDetector
from monkeylm.core.worker.runner import _execute_step_with_timeout, classify_runtime_failure, write_failure_debug_artifact
from monkeylm.models import (
    _build_vision_annotation_prompt,
    _is_cloud_vision_model,
    build_decision_prompt,
)
from monkeylm.models.prompts.antiloop import _break_action_loop
from monkeylm.browser import (
    _compute_action_path_hash,
    diff_component_manifests,
    launch_context_with_fallback,
)
from monkeylm.browser.actions.interaction import (
    collect_failure_context,
    detect_click_interception,
    recover_nonresponsive_state,
)
from monkeylm.memory import PersistenceEngine, QdrantMemoryStore
from monkeylm.reporting import (
    generate_json_summary,
    generate_markdown_report,
    summarize_vibe_coding_accountability,
)
from monkeylm.reporting.json_report import generate_json_summary as generate_json_summary_direct


class MonkeyLMTests(unittest.TestCase):
    def test_split_domain_and_route(self) -> None:
        domain, route = split_domain_and_route("https://example.com/account/settings?tab=profile")
        self.assertEqual(domain, "example.com")
        self.assertEqual(route, "/account/settings?tab=profile")

    def test_is_in_scope_netloc_matching(self) -> None:
        self.assertTrue(
            is_in_scope(
                "https://noblequran-85hu2yge.manus.space/bookmarked-verses",
                "https://noblequran-85hu2yge.manus.space/",
            )
        )
        self.assertFalse(
            is_in_scope(
                "https://example.com/",
                "https://noblequran-85hu2yge.manus.space/",
            )
        )
        self.assertFalse(is_in_scope("about:blank", "https://noblequran-85hu2yge.manus.space/"))

    def test_apply_runtime_overrides_sets_seed(self) -> None:
        previous_seed = config.ACTIVE_SEED

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

        apply_runtime_overrides(args)

        expected_rng = random.Random(321)
        self.assertEqual(config.ACTIVE_SEED, "321")
        self.assertAlmostEqual(random.random(), expected_rng.random())

        config.ACTIVE_SEED = previous_seed

    def test_apply_runtime_overrides_qdrant_toggles(self) -> None:
        original_reads = config.QDRANT_ENABLE_READS
        original_writes = config.QDRANT_ENABLE_WRITES
        original_provider = config.QDRANT_EMBEDDING_PROVIDER
        original_model = config.QDRANT_EMBEDDING_MODEL
        original_admin = config.QDRANT_ADMIN_ACTION
        original_rerank_enabled = config.QDRANT_RERANK_ENABLED
        original_rerank_model = config.QDRANT_RERANK_MODEL
        original_candidate_limit = config.QDRANT_CANDIDATE_LIMIT

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

        apply_runtime_overrides(args)

        self.assertEqual(config.QDRANT_EMBEDDING_PROVIDER, "ollama")
        self.assertEqual(config.QDRANT_EMBEDDING_MODEL, "nomic-embed-text")
        self.assertTrue(config.QDRANT_ENABLE_READS)
        self.assertFalse(config.QDRANT_ENABLE_WRITES)
        self.assertTrue(config.QDRANT_RERANK_ENABLED)
        self.assertEqual(config.QDRANT_RERANK_MODEL, "qwen2.5:3b")
        self.assertEqual(config.QDRANT_CANDIDATE_LIMIT, 25)
        self.assertEqual(config.QDRANT_ADMIN_ACTION, "inspect")

        config.QDRANT_ENABLE_READS = original_reads
        config.QDRANT_ENABLE_WRITES = original_writes
        config.QDRANT_EMBEDDING_PROVIDER = original_provider
        config.QDRANT_EMBEDDING_MODEL = original_model
        config.QDRANT_ADMIN_ACTION = original_admin
        config.QDRANT_RERANK_ENABLED = original_rerank_enabled
        config.QDRANT_RERANK_MODEL = original_rerank_model
        config.QDRANT_CANDIDATE_LIMIT = original_candidate_limit

    def test_apply_runtime_overrides_retry_policy(self) -> None:
        original_nav_retries = config.WORKER_NAVIGATION_RETRIES
        original_qdrant_retries = config.WORKER_QDRANT_INIT_RETRIES
        original_boundary_retries = config.WORKER_BOUNDARY_RECOVERY_RETRIES
        original_base_delay = config.RETRY_BASE_DELAY_SECONDS

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

        apply_runtime_overrides(args)

        self.assertEqual(config.WORKER_NAVIGATION_RETRIES, 4)
        self.assertEqual(config.WORKER_QDRANT_INIT_RETRIES, 2)
        self.assertEqual(config.WORKER_BOUNDARY_RECOVERY_RETRIES, 3)
        self.assertEqual(config.RETRY_BASE_DELAY_SECONDS, 1.25)

        config.WORKER_NAVIGATION_RETRIES = original_nav_retries
        config.WORKER_QDRANT_INIT_RETRIES = original_qdrant_retries
        config.WORKER_BOUNDARY_RECOVERY_RETRIES = original_boundary_retries
        config.RETRY_BASE_DELAY_SECONDS = original_base_delay

    def test_stall_detector_marks_stuck_state_detected(self) -> None:
        defects = DefectTracker()
        detector = StallDetector(defects, threshold=3)

        for step, action in enumerate(["restart_target", "restart_target", "restart_target"], start=1):
            detector.record_state(step, "https://example.com/", "same-hash", action)

        finding = detector.check_for_stall(4, "restart_target")

        self.assertIsNotNone(finding)
        self.assertEqual(finding.get("type"), "stuck_state_detected")
        self.assertEqual(finding.get("reason"), "stuck_state_detected")
        self.assertEqual(len(defects.ux_flow_freezes), 1)

    def test_execute_step_with_timeout_raises_timeout_error(self) -> None:
        async def slow_coro() -> str:
            await asyncio.sleep(0.05)
            return "done"

        with self.assertRaises(TimeoutError):
            asyncio.run(_execute_step_with_timeout(slow_coro(), timeout_seconds=0.001))

    def test_normalize_action_plan_accepts_press_key(self) -> None:
        plan = normalize_action_plan({"action": "press_key", "value": "Escape"})
        self.assertEqual(plan["action"], "press_key")
        self.assertEqual(plan["value"], "Escape")

    def test_write_failure_debug_artifact_emits_recent_state_buffer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            settings = load_settings()
            settings._output_dir_override = tmp_dir
            history = [
                {"step": 1, "action": "scroll", "url": "https://example.com/", "dom_hash": "hash-a"},
                {"step": 2, "action": "click", "url": "https://example.com/", "dom_hash": "hash-b"},
            ]
            artifact_path = write_failure_debug_artifact(
                settings,
                step=3,
                failure_reason="step_timeout",
                failure_context={"step": 3, "action": "type", "target": "[id=2]", "url": "https://example.com/"},
                recent_history=history,
            )
            self.assertTrue(os.path.exists(artifact_path))
            with open(artifact_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            self.assertEqual(payload["failure_reason"], "step_timeout")
            self.assertEqual(payload["recent_history"][0]["action"], "scroll")
            self.assertEqual(payload["recent_history"][1]["dom_hash"], "hash-b")

    def test_detect_click_interception_detects_overlay_blocking(self) -> None:
        class DummyLocator:
            async def bounding_box(self) -> Dict[str, float]:
                return {"x": 10.0, "y": 10.0, "width": 20.0, "height": 20.0}

        class DummyPage:
            def __init__(self) -> None:
                self.url = "https://example.com/"

            async def evaluate(self, script: str, *args: Any) -> Dict[str, Any]:
                return {
                    "is_blocked": True,
                    "reason": "overlay_blocked",
                    "top_element": "div",
                    "target_element": "button",
                    "top_text": "dialog",
                }

            async def content(self) -> str:
                return "<html><body><div>overlay</div></body></html>"

        findings = asyncio.run(detect_click_interception(DummyPage(), DummyLocator(), "1"))
        self.assertTrue(findings["is_blocked"])
        self.assertEqual(findings["reason"], "overlay_blocked")

    def test_collect_failure_context_includes_last_action_url_and_dom_snippet(self) -> None:
        class DummyPage:
            def __init__(self) -> None:
                self.url = "https://example.com/"

            async def content(self) -> str:
                return "<html><body><button>Continue</button></body></html>"

        context = asyncio.run(collect_failure_context(DummyPage(), step=7, action="click", target="1", error="timed out"))
        self.assertEqual(context["step"], 7)
        self.assertEqual(context["last_action"], "click")
        self.assertEqual(context["url"], "https://example.com/")
        self.assertIn("Continue", context["compact_dom_snapshot"])

    def test_collect_failure_context_includes_runtime_errors(self) -> None:
        class DummyPage:
            def __init__(self) -> None:
                self.url = "https://example.com/"

            async def content(self) -> str:
                return "<html><body><button>Continue</button></body></html>"

        context = asyncio.run(
            collect_failure_context(
                DummyPage(),
                step=8,
                action="click",
                target="2",
                error="overlay blocked",
                runtime_errors=[{"type": "error", "message": "TypeError: failed"}],
            )
        )
        self.assertEqual(context["failure_category"], "page_blocked")
        self.assertEqual(context["runtime_errors"][0]["message"], "TypeError: failed")

    def test_recover_nonresponsive_state_reports_reload_details(self) -> None:
        class DummyPage:
            def __init__(self) -> None:
                self.url = "https://example.com/"
                self.reload_calls = 0

            async def reload(self, timeout: int = 0) -> None:
                self.reload_calls += 1
                raise RuntimeError("net::ERR_FAILED")

            async def goto(self, *args: Any, **kwargs: Any) -> None:
                return None

            async def wait_for_load_state(self, *args: Any, **kwargs: Any) -> None:
                return None

        settings = load_settings()
        settings._target_url_override = "https://example.com/"
        recovery = asyncio.run(recover_nonresponsive_state(DummyPage(), settings, step=4, action="click", target="3", error="timed out"))
        self.assertTrue(recovery["attempted"])
        self.assertFalse(recovery["success"])
        self.assertTrue(any("reload_attempt_1" in detail for detail in recovery["details"]))

    def test_classify_runtime_failure_detects_sandbox_errors(self) -> None:
        self.assertEqual(classify_runtime_failure(RuntimeError("sandbox launch failed")), "sandbox_failure")
        self.assertEqual(classify_runtime_failure(RuntimeError("timed out while waiting for element")), "step_timeout")
        self.assertEqual(classify_runtime_failure(RuntimeError("overlay blocked by modal")), "page_blocked")
        self.assertEqual(classify_runtime_failure(RuntimeError("page crashed")), "app_failure")

    def test_runtime_failures_are_emitted_in_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            settings = load_settings()
            settings._output_dir_override = tmp_dir
            defects = DefectTracker()
            browser_launch_info = {
                "worker_failures": [
                    {
                        "worker_id": 1,
                        "failure_reason": "stuck_state_detected",
                        "failure_artifact": "failure_debug_step_3.json",
                        "failure_context": {"step": 3, "url": "https://example.com/"},
                    }
                ]
            }

            generate_markdown_report(settings, defects, [], browser_launch_info, datetime.now(), datetime.now())
            with open(os.path.join(tmp_dir, "test_report.md"), "r", encoding="utf-8") as handle:
                markdown_output = handle.read()
            self.assertIn("Runtime Failures", markdown_output)
            self.assertIn("stuck_state_detected", markdown_output)
            self.assertIn("failure_debug_step_3.json", markdown_output)

            generate_json_summary(settings, defects, [], browser_launch_info, [], False, datetime.now(), datetime.now())
            with open(os.path.join(tmp_dir, "results.json"), "r", encoding="utf-8") as handle:
                json_output = json.load(handle)
            self.assertEqual(json_output["worker_failures"][0]["failure_reason"], "stuck_state_detected")

    def test_runtime_preflight_is_emitted_in_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            settings = load_settings()
            settings._output_dir_override = tmp_dir
            defects = DefectTracker()
            browser_launch_info = {
                "runtime_preflight": inspect_optional_runtime_dependencies(),
            }

            generate_markdown_report(settings, defects, [], browser_launch_info, datetime.now(), datetime.now())
            with open(os.path.join(tmp_dir, "test_report.md"), "r", encoding="utf-8") as handle:
                markdown_output = handle.read()
            self.assertIn("Runtime Preflight", markdown_output)
            self.assertIn("playwright", markdown_output)

            generate_json_summary(settings, defects, [], browser_launch_info, [], False, datetime.now(), datetime.now())
            with open(os.path.join(tmp_dir, "results.json"), "r", encoding="utf-8") as handle:
                json_output = json.load(handle)
            self.assertIn("runtime_preflight", json_output)
            self.assertIn("playwright", json_output["runtime_preflight"])

    def test_json_report_emits_failure_context_samples(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            settings = load_settings()
            settings._output_dir_override = tmp_dir
            defects = DefectTracker()
            test_logs = [{
                "step": 2,
                "status": "FAILED",
                "action": "click",
                "target": "1",
                "error": "timed out",
                "failure_context": {
                    "step": 2,
                    "last_action": "click",
                    "url": "https://example.com/",
                    "dom_context": "<button>Continue</button>",
                },
            }]

            generate_json_summary_direct(settings, defects, test_logs, {}, [], False, datetime.now(), datetime.now())
            with open(os.path.join(tmp_dir, "results.json"), "r", encoding="utf-8") as handle:
                json_output = json.load(handle)
            self.assertEqual(len(json_output["failure_context_samples"]), 1)
            self.assertEqual(json_output["failure_context_samples"][0]["action"], "click")

    def test_generate_json_summary_contains_seed_and_boundary_drift(self) -> None:
        original_output_dir = config.OUTPUT_DIR
        original_seed = config.ACTIVE_SEED
        original_logs = list(test_logs)
        original_defects = core_module.DEFECTS
        original_network_monitor = core_module.NETWORK_MONITOR

        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                config.OUTPUT_DIR = tmp_dir
                config.ACTIVE_SEED = "999"
                test_logs.clear()
                core_module.DEFECTS = DefectTracker()
                core_module.NETWORK_MONITOR = NetworkMonitor(core_module.DEFECTS)

                core_module.DEFECTS.add(
                    "boundary_drift",
                    {
                        "step": 1,
                        "type": "Boundary Drift",
                        "current_url": "https://example.com/",
                        "target_url": "https://noblequran-85hu2yge.manus.space/",
                    },
                )

                now = datetime.now()
                settings = load_settings()
                settings._output_dir_override = tmp_dir
                settings.active_seed = "999"
                generate_json_summary(settings, core_module.DEFECTS, test_logs, {}, [], False, now, now)

                with open(f"{tmp_dir}/results.json", "r", encoding="utf-8") as f:
                    data = json.load(f)

                self.assertEqual(data.get("active_seed"), "999")
                self.assertIn("boundary_drift", data.get("defects", {}))
                self.assertEqual(len(data["defects"]["boundary_drift"]), 1)
        finally:
            config.OUTPUT_DIR = original_output_dir
            config.ACTIVE_SEED = original_seed
            test_logs.clear()
            test_logs.extend(original_logs)
            core_module.DEFECTS = original_defects
            core_module.NETWORK_MONITOR = original_network_monitor

    def test_generate_json_summary_includes_semantic_memory_telemetry(self) -> None:
        original_output_dir = config.OUTPUT_DIR
        original_seed = config.ACTIVE_SEED
        original_logs = list(test_logs)
        original_defects = core_module.DEFECTS
        original_network_monitor = core_module.NETWORK_MONITOR

        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                config.OUTPUT_DIR = tmp_dir
                config.ACTIVE_SEED = "123"
                test_logs.clear()
                test_logs.append(
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
                )
                core_module.DEFECTS = DefectTracker()
                core_module.NETWORK_MONITOR = NetworkMonitor(core_module.DEFECTS)

                now = datetime.now()
                settings = load_settings()
                settings._output_dir_override = tmp_dir
                settings.active_seed = "123"
                generate_json_summary(settings, core_module.DEFECTS, test_logs, {}, [], False, now, now)

                with open(f"{tmp_dir}/results.json", "r", encoding="utf-8") as f:
                    data = json.load(f)

                telemetry = data.get("semantic_memory_telemetry", {})
                self.assertIn("retrieval", telemetry)
                self.assertIn("write", telemetry)
                self.assertEqual(telemetry["retrieval"].get("ok"), 1)
                self.assertEqual(telemetry["write"].get("ok"), 1)
                self.assertEqual(telemetry.get("providers", {}).get("ollama"), 2)
        finally:
            config.OUTPUT_DIR = original_output_dir
            config.ACTIVE_SEED = original_seed
            test_logs.clear()
            test_logs.extend(original_logs)
            core_module.DEFECTS = original_defects
            core_module.NETWORK_MONITOR = original_network_monitor

    def test_vibe_coding_accountability_sets_failed_on_drift(self) -> None:
        original_defects = core_module.DEFECTS
        try:
            core_module.DEFECTS = DefectTracker()
            core_module.DEFECTS.add(
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

            accountability = summarize_vibe_coding_accountability(core_module.DEFECTS)

            self.assertEqual(accountability.get("total_missing_historical_components"), 2)
            self.assertEqual(accountability.get("total_expected_baseline_components"), 8)
            self.assertEqual(accountability.get("regression_drift_index"), 25.0)
            self.assertEqual(accountability.get("run_summary_status"), "FAILED: Structural Drift Detected")
        finally:
            core_module.DEFECTS = original_defects

    def test_generate_markdown_report_includes_semantic_memory_telemetry(self) -> None:
        original_output_dir = config.OUTPUT_DIR
        original_logs = list(test_logs)
        original_defects = core_module.DEFECTS

        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                config.OUTPUT_DIR = tmp_dir
                test_logs.clear()
                test_logs.append(
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
                )
                core_module.DEFECTS = DefectTracker()
                core_module.DEFECTS.add(
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
                settings = load_settings()
                settings._output_dir_override = tmp_dir
                generate_markdown_report(settings, core_module.DEFECTS, test_logs, {}, now, now)

                with open(f"{tmp_dir}/test_report.md", "r", encoding="utf-8") as f:
                    report = f.read()

                self.assertIn("## Semantic Memory Telemetry", report)
                self.assertIn("Retrieval events", report)
                self.assertIn("Avg Qdrant upsert", report)
                self.assertIn("### \u26a0\ufe0f Vibe Coding Drift Summary", report)
                self.assertIn("FAILED: Structural Drift Detected", report)
                self.assertIn("#save-settings", report)
        finally:
            config.OUTPUT_DIR = original_output_dir
            test_logs.clear()
            test_logs.extend(original_logs)
            core_module.DEFECTS = original_defects

    def test_diff_component_manifests_detects_missing_components(self) -> None:
        golden = [
            {"kind": "button", "tag": "BUTTON", "text": "Save", "selector_hint": "#save"},
            {"kind": "form", "tag": "FORM", "text": "profile", "selector_hint": "form#profile"},
        ]
        current = [
            {"kind": "form", "tag": "FORM", "text": "profile", "selector_hint": "form#profile"},
        ]

        missing, broken = diff_component_manifests(golden, current)

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

        prompt = build_decision_prompt(page_state, memory_logs)

        self.assertIn("## Memory Logs of Previous Vibe Changes", prompt)
        self.assertIn("Vibe-Code-Regression-Missing-Component", prompt)

    def test_build_decision_prompt_json_example_survives_fstring_interpolation(self) -> None:
        page_state = "URL: https://example.com/\nTitle: Example\nElements:\n[id=0] <BUTTON text=\"Save\" />"
        prompt = build_decision_prompt(page_state, memory_logs=[])

        self.assertIn('"target": "[id=1]"', prompt)
        self.assertIn('"value": "valid@example.com"', prompt)
        self.assertIn('"reason": "happy_valid_email"', prompt)
        self.assertIn('"input_payloads": [', prompt)
        self.assertIn(']', prompt)

    def test_build_decision_prompt_includes_discovery_strategy_context(self) -> None:
        from monkeylm.types import TestingStrategy, PersonaGoal, CriticalFlow

        strategy = TestingStrategy(
            app_domain="Quran app",
            primary_personas=[PersonaGoal(name="Reader", description="Reads verses", behaviors=["navigates content"])] ,
            critical_flows=[CriticalFlow(name="browse_and_search", description="Find content", steps=["browse", "search"])],
            edge_cases_to_test=["empty search"],
            security_focus=["input validation"],
            strategy_summary="Prioritize browsing and searching",
        )
        prompt = build_decision_prompt(
            "Title: Noble Quran\nButtons: Browse, Search",
            memory_logs=[],
            testing_strategy=strategy,
        )

        self.assertIn("Quran app", prompt)
        self.assertIn("browse_and_search", prompt)
        self.assertIn("Prioritize browsing and searching", prompt)

    def test_qdrant_parse_rerank_response(self) -> None:
        store = QdrantMemoryStore(load_settings())
        parsed = store._parse_rerank_response('{"ranked_indices": [2, 0, 1]}')
        self.assertEqual(parsed, [2, 0, 1])

    def test_allocate_worker_steps_respects_total_and_cap(self) -> None:
        allocations = allocate_worker_steps(total_steps=10, worker_count=3, per_worker_cap=4)
        self.assertEqual(sum(allocations), 10)
        self.assertTrue(all(count <= 4 for count in allocations))

    def test_load_settings_clamps_worker_cap_to_total_steps(self) -> None:
        settings = load_settings(argparse.Namespace(max_steps=3, max_steps_per_worker=None))
        self.assertEqual(settings.max_steps, 3)
        self.assertEqual(settings.max_steps_per_worker, 3)

    def test_validate_runtime_configuration_rejects_invalid_per_worker_cap(self) -> None:
        settings = load_settings()
        settings.max_steps = 5
        settings.max_steps_per_worker = 6
        with self.assertRaises(ValueError):
            validate_runtime_configuration(settings)

    def test_validate_runtime_configuration_rejects_excessive_retry_count(self) -> None:
        settings = load_settings()
        settings.worker_navigation_retries = MAX_ALLOWED_RETRIES + 1
        with self.assertRaises(ValueError):
            validate_runtime_configuration(settings)

    def test_validate_runtime_configuration_rejects_excessive_retry_delay(self) -> None:
        settings = load_settings()
        settings.retry_base_delay_seconds = MAX_ALLOWED_RETRY_BASE_DELAY_SECONDS + 0.5
        with self.assertRaises(ValueError):
            validate_runtime_configuration(settings)

    def test_inspect_optional_runtime_dependencies_reports_missing_node_tooling(self) -> None:
        report = inspect_optional_runtime_dependencies()
        self.assertIsInstance(report, dict)
        self.assertIn("node", report)
        self.assertIn("python", report)
        self.assertIn("playwright", report)
        self.assertIn("dotenv", report)
        self.assertIn("status", report["node"])
        self.assertIn("status", report["python"])

    def test_parse_cli_args_accepts_inspect_runtime_flag(self) -> None:
        with patch.object(sys, "argv", ["monkeylm", "--inspect-runtime"]):
            args = parse_cli_args()
        self.assertTrue(args.inspect_runtime)

    def test_parse_cli_args_accepts_inspect_runtime_json_flag(self) -> None:
        with patch.object(sys, "argv", ["monkeylm", "--inspect-runtime-json"]):
            args = parse_cli_args()
        self.assertTrue(args.inspect_runtime_json)

    def test_build_worker_user_data_dir_returns_distinct_paths(self) -> None:
        settings = load_settings()
        dir_one = build_worker_user_data_dir(settings, 1)
        dir_two = build_worker_user_data_dir(settings, 2)

        self.assertNotEqual(dir_one, dir_two)
        self.assertTrue(os.path.isdir(dir_one))
        self.assertTrue(os.path.isdir(dir_two))

    def test_build_redis_key_applies_prefix(self) -> None:
        original_prefix = config.REDIS_PREFIX
        try:
            config.REDIS_PREFIX = "monkey:"
            self.assertEqual(build_redis_key("monkey:", "visited"), "monkey:visited")
            config.REDIS_PREFIX = ""
            self.assertEqual(build_redis_key("", "visited"), "visited")
        finally:
            config.REDIS_PREFIX = original_prefix

    def test_apply_runtime_overrides_redis_prefix(self) -> None:
        original_prefix = config.REDIS_PREFIX
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
            apply_runtime_overrides(args)
            self.assertEqual(config.REDIS_PREFIX, "test:")
        finally:
            config.REDIS_PREFIX = original_prefix

    def test_apply_runtime_overrides_ollama_timeout(self) -> None:
        original_timeout = config.OLLAMA_TIMEOUT_SECONDS
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
            apply_runtime_overrides(args)
            self.assertEqual(config.OLLAMA_TIMEOUT_SECONDS, 30.0)
        finally:
            config.OLLAMA_TIMEOUT_SECONDS = original_timeout

    def test_apply_runtime_overrides_redis_path_lock_ttl(self) -> None:
        original_ttl = config.REDIS_PATH_LOCK_TTL_SECONDS
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
            apply_runtime_overrides(args)
            self.assertEqual(config.REDIS_PATH_LOCK_TTL_SECONDS, 90)
        finally:
            config.REDIS_PATH_LOCK_TTL_SECONDS = original_ttl

    def test_validate_runtime_configuration_rejects_invalid_path_lock_ttl(self) -> None:
        settings = load_settings()
        settings.redis_path_lock_ttl_seconds = 0
        with self.assertRaises(ValueError) as ctx:
            validate_runtime_configuration(settings)
        self.assertIn("REDIS_PATH_LOCK_TTL_SECONDS", str(ctx.exception))

        settings = load_settings()
        settings.redis_path_lock_ttl_seconds = 301
        with self.assertRaises(ValueError) as ctx:
            validate_runtime_configuration(settings)
        self.assertIn("REDIS_PATH_LOCK_TTL_SECONDS", str(ctx.exception))

    def test_compute_action_path_hash_is_deterministic(self) -> None:
        h1 = _compute_action_path_hash("example.com", "/login", "click", "submit-btn")
        h2 = _compute_action_path_hash("example.com", "/login", "click", "submit-btn")
        self.assertEqual(h1, h2)
        h3 = _compute_action_path_hash("example.com", "/login", "click", "cancel-btn")
        self.assertNotEqual(h1, h3)

    def test_is_cloud_vision_model_preview_suffix(self) -> None:
        self.assertTrue(_is_cloud_vision_model("gemini-3-flash-preview"))

    def test_is_cloud_vision_model_cloud_suffix(self) -> None:
        self.assertTrue(_is_cloud_vision_model("gemma4:31b-cloud"))

    def test_is_cloud_vision_model_minimax_m3(self) -> None:
        self.assertTrue(_is_cloud_vision_model("minimax-m3"))
        self.assertTrue(_is_cloud_vision_model("minimax-m3:cloud"))

    def test_is_cloud_vision_model_local(self) -> None:
        self.assertFalse(_is_cloud_vision_model("llama3.2-vision"))
        self.assertFalse(_is_cloud_vision_model(""))

    def test_build_vision_annotation_prompt_contains_box_2d(self) -> None:
        prompt = _build_vision_annotation_prompt("button missing")
        self.assertIn("box_2d", prompt)
        self.assertIn("[ymin, xmin, ymax, xmax]", prompt)
        self.assertNotIn('"box":', prompt)
        self.assertIn("do not use `box`", prompt)

    def test_apply_runtime_overrides_vision_model(self) -> None:
        original_vision_model = config.VISION_MODEL
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
            apply_runtime_overrides(args)
            self.assertEqual(config.VISION_MODEL, "gemini-3-flash-preview")
        finally:
            config.VISION_MODEL = original_vision_model


    def test_break_action_loop_blacklist_expires_by_step(self) -> None:
        """Regression test for a bug where the blacklist's expiry check read a
        frozen module-level ``CURRENT_GLOBAL_STEP`` that was always 0 (a
        same-named local variable in the caller silently shadowed it instead
        of updating it), so blacklisted targets never actually expired. The
        function now takes ``current_step`` explicitly; this proves entries
        are pruned once the caller's real step passes their expiry step."""

        class FakeSnapshot:
            elements = [f"<BUTTON>X</BUTTON> [id={i}]" for i in range(1, 6)]

        loop_state: Dict[str, Any] = {"blacklist": {}, "loop_count": 0}

        _break_action_loop(
            {"action": "click", "target": "[id=1]"},
            FakeSnapshot(),
            "worker-00",
            current_step=0,
            loop_state=loop_state,
            blacklist_expiry_steps=2,
        )
        self.assertEqual(loop_state["blacklist"], {"click:[id=1]": 2})

        # Still before expiry (2 > 1): the earlier entry must survive pruning.
        _break_action_loop(
            {"action": "click", "target": "[id=2]"},
            FakeSnapshot(),
            "worker-00",
            current_step=1,
            loop_state=loop_state,
            blacklist_expiry_steps=2,
        )
        self.assertIn("click:[id=1]", loop_state["blacklist"])
        self.assertIn("click:[id=2]", loop_state["blacklist"])

        # Past expiry for both prior entries (2 <= 5 and 3 <= 5): they must be
        # pruned before the new one is inserted.
        _break_action_loop(
            {"action": "click", "target": "[id=3]"},
            FakeSnapshot(),
            "worker-00",
            current_step=5,
            loop_state=loop_state,
            blacklist_expiry_steps=2,
        )
        self.assertNotIn("click:[id=1]", loop_state["blacklist"])
        self.assertNotIn("click:[id=2]", loop_state["blacklist"])
        self.assertIn("click:[id=3]", loop_state["blacklist"])


class MonkeyLMAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_with_retry_backoff_retries_and_succeeds(self) -> None:
        attempts = {"count": 0}

        async def flaky() -> str:
            attempts["count"] += 1
            if attempts["count"] < 3:
                raise RuntimeError("transient")
            return "ok"

        result = await with_retry_backoff(
            "test-flaky",
            flaky,
            retries=3,
            initial_delay_seconds=0.01,
        )
        self.assertEqual(result, "ok")
        self.assertEqual(attempts["count"], 3)

    async def test_performance_snapshot_includes_navigation_telemetry_shape(self) -> None:
        defects = DefectTracker()
        perf_monitor = PerformanceMonitor(defects)

        from playwright.async_api import async_playwright
        from monkeylm.browser import wait_for_page_ready

        async with async_playwright() as p:
            user_data_dir = f"{config.RUN_USER_DATA_DIR}/test_perf_nav"
            context, _ = await launch_context_with_fallback(
                p,
                settings=load_settings(),
                user_data_dir=user_data_dir,
                worker_label="test-perf-nav",
            )
            page = context.pages[0]

            await page.goto(DEFAULT_TARGET_URL, wait_until="domcontentloaded", timeout=45000)
            await wait_for_page_ready(page, "test-nav-telemetry")

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
        local_defects = DefectTracker()

        from playwright.async_api import async_playwright
        from monkeylm.browser import wait_for_page_ready

        async with async_playwright() as p:
            user_data_dir = f"{config.RUN_USER_DATA_DIR}/test_boundary_recovery"
            context, _ = await launch_context_with_fallback(
                p,
                settings=load_settings(),
                user_data_dir=user_data_dir,
                worker_label="test-boundary",
            )
            page = context.pages[0]

            await page.goto(DEFAULT_TARGET_URL, wait_until="domcontentloaded", timeout=45000)
            await wait_for_page_ready(page, "test-initial")

            await page.goto("https://example.com/", wait_until="domcontentloaded", timeout=45000)
            await wait_for_page_ready(page, "test-out-of-scope")

            self.assertFalse(is_in_scope(page.url, DEFAULT_TARGET_URL))

            if not is_in_scope(page.url, DEFAULT_TARGET_URL):
                local_defects.add(
                    "boundary_drift",
                    {
                        "step": 1,
                        "type": "Boundary Drift",
                        "current_url": page.url,
                        "target_url": DEFAULT_TARGET_URL,
                    },
                )
                await page.goto(DEFAULT_TARGET_URL, wait_until="domcontentloaded", timeout=45000)
                await wait_for_page_ready(page, "test-boundary-recovery")

            self.assertTrue(is_in_scope(page.url, DEFAULT_TARGET_URL))
            self.assertEqual(len(local_defects.boundary_drift), 1)
            self.assertEqual(local_defects.boundary_drift[0]["type"], "Boundary Drift")

            await context.close()

    async def test_claim_action_path_lock_with_fake_redis(self) -> None:
        defects = DefectTracker()
        engine = PersistenceEngine(load_settings(), defects, max_workers=2)

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
            self.assertTrue(await engine.claim_action_path_lock("abc123", "test-worker"))
            self.assertFalse(await engine.claim_action_path_lock("abc123", "test-worker"))
            self.assertTrue(await engine.claim_action_path_lock("def456", "test-worker"))
        finally:
            engine.redis_client = None

    async def test_graceful_shutdown_event_exists_and_can_be_set(self) -> None:
        self.assertIsInstance(SHUTDOWN_EVENT, asyncio.Event)
        self.assertFalse(SHUTDOWN_EVENT.is_set())
        SHUTDOWN_EVENT.set()
        self.assertTrue(SHUTDOWN_EVENT.is_set())
        SHUTDOWN_EVENT.clear()
        self.assertFalse(SHUTDOWN_EVENT.is_set())

    async def test_request_graceful_shutdown_sets_event_and_flag(self) -> None:
        original_flag = config.GRACEFUL_SHUTDOWN_REQUESTED
        try:
            config.GRACEFUL_SHUTDOWN_REQUESTED = False
            SHUTDOWN_EVENT.clear()
            _request_graceful_shutdown(signal.SIGINT, None)
            self.assertTrue(config.GRACEFUL_SHUTDOWN_REQUESTED)
            await asyncio.sleep(0)
            self.assertTrue(SHUTDOWN_EVENT.is_set())
        finally:
            config.GRACEFUL_SHUTDOWN_REQUESTED = original_flag
            SHUTDOWN_EVENT.clear()


if __name__ == "__main__":
    unittest.main()
