from __future__ import annotations

import contextlib
import io
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from monkeylm.config import Settings
from monkeylm.config.env import _parse_model_list
from monkeylm.core.monitor import DefectTracker
from monkeylm.core.monitor.anomaly import format_anomaly_summary
from monkeylm.memory.postgres import PersistenceEngine
from monkeylm.models.ollama import _ollama_chat_with_retry
from monkeylm.reporting.accessibility import _compile_accessibility_violations


class _FakeConnection:
    def __init__(self, dom_count: int, visual_count: int, domains: list[dict]) -> None:
        self.dom_count = dom_count
        self.visual_count = visual_count
        self.domains = domains

    async def fetchval(self, query: str) -> int:
        return self.visual_count if "visual_baselines" in query else self.dom_count

    async def fetch(self, query: str) -> list[dict]:
        return self.domains


class _FakeAcquire:
    def __init__(self, connection: _FakeConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> _FakeConnection:
        return self.connection

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class _FakePool:
    def __init__(self, connection: _FakeConnection) -> None:
        self.connection = connection

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self.connection)


class ModelFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_fallback_model_list_parsing_deduplicates(self) -> None:
        self.assertEqual(_parse_model_list("model-a, model-b,model-a,, "), ["model-a", "model-b"])

    async def test_falls_back_after_non_retryable_primary_failure(self) -> None:
        settings = Settings(
            ollama_model="primary-model",
            ollama_fallback_models=["stable-fallback"],
            ollama_timeout_seconds=1.0,
        )
        calls: list[str] = []

        def _chat(_settings, **kwargs):
            calls.append(kwargs["model"])
            if kwargs["model"] == "primary-model":
                raise RuntimeError("primary unavailable")
            return {"choices": [{"message": {"content": "{}"}}]}

        with patch("monkeylm.models.ollama.chat_completion", side_effect=_chat):
            with patch("monkeylm.models.ollama._local_service_log") as log:
                response = await _ollama_chat_with_retry(
                    settings=settings,
                    model="primary-model",
                    messages=[],
                    timeout_seconds=1.0,
                    max_retries=1,
                )

        self.assertIsNotNone(response)
        self.assertEqual(calls, ["primary-model", "stable-fallback"])
        self.assertTrue(
            any("Falling back to model stable-fallback" in str(call) for call in log.call_args_list)
        )

    async def test_primary_success_does_not_call_fallback(self) -> None:
        settings = Settings(
            ollama_model="primary-model",
            ollama_fallback_models=["stable-fallback"],
            ollama_timeout_seconds=1.0,
        )
        calls: list[str] = []

        def _chat(_settings, **kwargs):
            calls.append(kwargs["model"])
            return {"choices": [{"message": {"content": "{}"}}]}

        with patch("monkeylm.models.ollama.chat_completion", side_effect=_chat):
            response = await _ollama_chat_with_retry(
                settings=settings,
                model="primary-model",
                messages=[],
                timeout_seconds=1.0,
                max_retries=1,
            )

        self.assertIsNotNone(response)
        self.assertEqual(calls, ["primary-model"])


class BaselineStartupSummaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_summary_reports_golden_routes(self) -> None:
        engine = PersistenceEngine(
            Settings(golden_baseline_mode="preexisting"),
            DefectTracker(),
        )
        engine.pg_pool = _FakePool(
            _FakeConnection(
                dom_count=3,
                visual_count=2,
                domains=[{"domain": "example.test", "count": 3}],
            )
        )

        with contextlib.redirect_stdout(io.StringIO()) as output:
            summary = await engine._load_baseline_summary()

        self.assertEqual(summary["dom_golden_routes"], 3)
        self.assertEqual(summary["visual_golden_routes"], 2)
        self.assertEqual(summary["domains"], [{"domain": "example.test", "count": 3}])
        self.assertTrue(summary["preexisting_ready"])
        self.assertIn("golden DOM routes=3", output.getvalue())

    async def test_preexisting_mode_warns_when_store_is_empty(self) -> None:
        engine = PersistenceEngine(
            Settings(golden_baseline_mode="preexisting"),
            DefectTracker(),
        )
        engine.pg_pool = _FakePool(_FakeConnection(0, 0, []))

        with patch("monkeylm.memory.postgres._local_service_log") as log:
            summary = await engine._load_baseline_summary()

        self.assertFalse(summary["preexisting_ready"])
        self.assertTrue(any("no golden baselines" in str(call) for call in log.call_args_list))

    async def test_summary_reports_unavailable_store(self) -> None:
        engine = PersistenceEngine(Settings(), DefectTracker())
        with contextlib.redirect_stdout(io.StringIO()) as output:
            summary = await engine._load_baseline_summary()

        self.assertFalse(summary["available"])
        self.assertIn("unavailable", output.getvalue())


class PreexistingComparisonTests(unittest.IsolatedAsyncioTestCase):
    async def test_dom_comparison_reports_missing_component(self) -> None:
        tracker = DefectTracker()
        engine = PersistenceEngine(Settings(golden_baseline_mode="preexisting"), tracker)
        engine.pg_pool = object()
        engine._upsert_baseline = AsyncMock()
        engine._insert_regression_drift_log = AsyncMock()
        engine._fetch_golden_baseline = AsyncMock(
            return_value={
                "dom_structure_hash": "golden-hash",
                "component_manifest": [
                    {
                        "kind": "button",
                        "tag": "BUTTON",
                        "text": "Save",
                        "selector_hint": "#save",
                    }
                ],
            }
        )
        page = SimpleNamespace(url="https://example.test/records")
        snapshot = SimpleNamespace(url=page.url, structure_hash="current-hash")

        with patch("monkeylm.browser.extract_component_manifest", AsyncMock(return_value=[])):
            await engine.analyze_route_regression(page, snapshot, 7)

        self.assertEqual(len(tracker.regression_findings), 1)
        finding = tracker.regression_findings[0]
        self.assertEqual(finding["type"], "Vibe-Code-Regression-Missing-Component")
        self.assertEqual(finding["step"], 7)
        self.assertEqual(finding["expected_baseline_components"], 1)
        self.assertEqual(finding["page_route"], "/records")
        engine._insert_regression_drift_log.assert_awaited_once()

    async def test_visual_comparison_reports_phash_drift(self) -> None:
        tracker = DefectTracker()
        engine = PersistenceEngine(
            Settings(golden_baseline_mode="preexisting", cv_analysis_enabled=True),
            tracker,
        )
        engine.pg_pool = object()
        engine._upsert_visual_baseline = AsyncMock()
        engine._insert_regression_drift_log = AsyncMock()
        engine._fetch_visual_baseline = AsyncMock(
            return_value={"visual_phash": "golden-phash", "screenshot_path": "golden.png"}
        )
        snapshot = SimpleNamespace(
            url="https://example.test/records",
            screenshot_path="current.png",
        )

        with patch("monkeylm.browser.snapshot.cv.visual_phash", return_value="current-phash"):
            with patch("monkeylm.browser.snapshot.cv.phash_hamming", return_value=99):
                await engine.analyze_route_visual_drift(snapshot, 4)

        self.assertEqual(len(tracker.regression_findings), 1)
        finding = tracker.regression_findings[0]
        self.assertEqual(finding["type"], "Visual-Baseline-Drift")
        self.assertEqual(finding["step"], 4)
        self.assertEqual(finding["phash_hamming_distance"], 99)
        engine._insert_regression_drift_log.assert_awaited_once()


class DedupDetailTests(unittest.TestCase):
    def test_context_anomalies_dedupe_with_step_and_action_detail(self) -> None:
        tracker = DefectTracker()
        payload = {
            "step": 5,
            "action": "click:[id=1]",
            "type": "console-error",
            "message": "Error toggling bookmark",
            "url": "https://example.test/",
        }
        tracker.add("context_anomalies", payload)
        tracker.add("context_anomalies", {**payload, "step": 9})

        self.assertEqual(len(tracker.context_anomalies), 1)
        anomaly = tracker.context_anomalies[0]
        self.assertEqual(anomaly["occurrences"], 2)
        self.assertEqual(anomaly["occurrence_count"], 2)
        self.assertEqual(anomaly["last_seen_step"], 9)
        self.assertEqual(anomaly["step_range"], [5, 9])
        summary = format_anomaly_summary(tracker.context_anomalies)
        self.assertIn("occurrences=2", summary)
        self.assertIn("action=click:[id=1]", summary)
        self.assertIn("type=console-error", summary)

        tracker.add(
            "context_anomalies",
            {**payload, "step": 11, "action": "type:[id=2]"},
        )
        self.assertEqual(len(tracker.context_anomalies), 2)

    def test_merge_from_dedupes_accessibility_findings(self) -> None:
        first = DefectTracker()
        second = DefectTracker()
        payload = {
            "step": 3,
            "id": "color-contrast",
            "selector": ".tracking-wider",
            "url": "https://example.test/",
            "severity": "serious",
        }
        first.add("accessibility_violations", payload)
        second.add("accessibility_violations", {**payload, "step": 8})

        first.merge_from(second)

        self.assertEqual(len(first.accessibility_violations), 1)
        finding = first.accessibility_violations[0]
        self.assertEqual(finding["occurrences"], 2)
        self.assertEqual(finding["occurrence_count"], 2)
        self.assertEqual(finding["step_range"], [3, 8])

    def test_compiled_accessibility_counts_observations_and_uniques(self) -> None:
        tracker = DefectTracker()
        payload = {
            "step": 3,
            "id": "color-contrast",
            "selector": ".tracking-wider",
            "url": "https://example.test/",
            "severity": "serious",
        }
        tracker.add("accessibility_violations", payload)
        tracker.add("accessibility_violations", {**payload, "step": 8})
        compiled = _compile_accessibility_violations(tracker.accessibility_violations)

        self.assertEqual(compiled["total_raw_violations"], 2)
        self.assertEqual(compiled["unique_violations"], 1)
        self.assertEqual(compiled["unique_rules_found"], 1)
        self.assertEqual(compiled["unique_selectors_found"], 1)
        self.assertEqual(compiled["rules"][0]["occurrence_steps"], [3, 8])


if __name__ == "__main__":
    unittest.main()
