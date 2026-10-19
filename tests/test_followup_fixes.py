import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from monkeylm.config import Settings, inspect_optional_runtime_dependencies
from monkeylm.core.monitor import DefectTracker
from monkeylm.core.monitor.anomaly import format_anomaly_summary
from monkeylm.memory.postgres import PersistenceEngine
from monkeylm.models.prompts.decision import build_decision_prompt
from monkeylm.models.prompts.discovery import run_application_discovery


class _FakeRedis:
    def __init__(self, fail_calls: int = 0) -> None:
        self.fail_calls = fail_calls
        self.calls = 0
        self.values = {}

    def _maybe_fail(self) -> None:
        self.calls += 1
        if self.calls <= self.fail_calls:
            raise AttributeError("'NoneType' object has no attribute '_add_writer'")

    async def set(self, key, value, nx=True, ex=None):
        self._maybe_fail()
        self.values[key] = value
        return True

    async def get(self, key):
        return self.values.get(key)

    async def hincrby(self, key, field, amount):
        self._maybe_fail()
        self.values[(key, field)] = int(self.values.get((key, field), 0)) + amount
        return self.values[(key, field)]

    async def expire(self, key, ttl):
        return True

    async def aclose(self):
        return None


class RedisRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def _engine(self) -> PersistenceEngine:
        engine = PersistenceEngine(Settings(), DefectTracker())
        engine.settings.timestamp = "test"
        return engine

    async def test_action_path_lock_reconnects_after_add_writer_error(self) -> None:
        engine = self._engine()
        engine.redis_client = _FakeRedis(fail_calls=1)

        async def _reinitialize() -> None:
            engine.redis_client = _FakeRedis()

        engine._initialize_redis = AsyncMock(side_effect=_reinitialize)
        result = await engine.claim_action_path_lock("hash", "worker-01")

        self.assertTrue(result)
        self.assertEqual(engine.redis_client.calls, 1)

    async def test_action_path_lock_fails_open_after_repeated_errors(self) -> None:
        engine = self._engine()
        engine.redis_client = _FakeRedis(fail_calls=10)

        async def _reinitialize() -> None:
            engine.redis_client = _FakeRedis(fail_calls=10)

        engine._initialize_redis = AsyncMock(side_effect=_reinitialize)
        with patch("monkeylm.memory.postgres._local_service_log") as log:
            result = await engine.claim_action_path_lock("hash", "worker-01")

        self.assertTrue(result)
        self.assertTrue(any("proceeding without lock" in str(call) for call in log.call_args_list))

    async def test_increment_visited_state_recovers_after_add_writer_error(self) -> None:
        engine = self._engine()
        engine.redis_client = _FakeRedis(fail_calls=1)

        async def _reinitialize() -> None:
            engine.redis_client = _FakeRedis()

        engine._initialize_redis = AsyncMock(side_effect=_reinitialize)
        count = await engine.increment_visited_state("state")

        self.assertEqual(count, 1)


class BaselineAndObservabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovery_logs_response_preview_on_parse_failure(self) -> None:
        settings = Settings(
            ollama_model="test-model",
            openai_base_url="https://api.example.com/v1",
        )
        response = {"choices": [{"message": {"content": "not valid json"}}]}
        with patch(
            "monkeylm.models.prompts.discovery._ollama_chat_with_retry",
            AsyncMock(return_value=response),
        ):
            with patch("monkeylm.models.prompts.discovery._local_service_log") as log:
                strategy = await run_application_discovery(settings, "state")

        self.assertIsNotNone(strategy)
        self.assertTrue(
            any("response preview" in str(call) for call in log.call_args_list)
        )

    async def test_missing_baseline_logged_once_per_route(self) -> None:
        engine = PersistenceEngine(Settings(golden_baseline_mode="preexisting"), DefectTracker())
        engine.pg_pool = object()
        engine._fetch_golden_baseline = AsyncMock(return_value=None)
        engine._upsert_baseline = AsyncMock()
        page = SimpleNamespace(url="https://quranml.samehash.com/")
        snapshot = SimpleNamespace(url="https://quranml.samehash.com/", structure_hash="hash")

        with patch("monkeylm.memory.postgres._local_service_log") as log:
            with patch("monkeylm.browser.extract_component_manifest", AsyncMock(return_value=[])):
                await engine.analyze_route_regression(page, snapshot, 1)
                await engine.analyze_route_regression(page, snapshot, 2)

        missing_calls = [call for call in log.call_args_list if "Golden baseline missing" in str(call)]
        self.assertEqual(len(missing_calls), 1)


class DefectAndAnomalyFormattingTests(unittest.TestCase):
    def test_runtime_inspection_reports_python_version(self) -> None:
        report = inspect_optional_runtime_dependencies()

        self.assertIn("python", report)
        self.assertIn("version", report["python"])
        self.assertIn(report["python"]["status"], {"ok", "warning", "missing"})

    def test_accessibility_findings_are_deduplicated(self) -> None:
        tracker = DefectTracker()
        payload = {
            "step": 5,
            "id": "color-contrast",
            "selector": ".tracking-wider",
            "url": "https://quranml.samehash.com/",
        }
        tracker.add("accessibility_violations", payload)
        tracker.add("accessibility_violations", {**payload, "step": 10})

        self.assertEqual(len(tracker.accessibility_violations), 1)
        finding = tracker.accessibility_violations[0]
        self.assertEqual(finding["last_seen_step"], 10)
        self.assertEqual(finding["occurrences"], 2)

    def test_anomaly_summary_includes_step_action_and_type(self) -> None:
        anomalies = [
            {
                "step": 14,
                "action": "click:[id=129]",
                "type": "console-error",
                "message": "Error toggling offline bookmark",
            },
            {
                "step": 15,
                "action": "type:[id=0]",
                "type": "network-5xx-fetch",
                "message": "Server error",
            },
        ]
        summary = format_anomaly_summary(anomalies)

        self.assertIn("step=14", summary)
        self.assertIn("click:[id=129]", summary)
        self.assertIn("console-error", summary)
        self.assertIn("step=15", summary)

    def test_decision_prompt_forbids_type_on_buttons_and_forms(self) -> None:
        prompt = build_decision_prompt("state")

        self.assertIn('Only choose "type" when the target is an <INPUT>', prompt)
        self.assertIn('Never choose "type" for a <FORM>', prompt)
        self.assertNotIn('"target": "[id=0]"', prompt)


if __name__ == "__main__":
    unittest.main()
