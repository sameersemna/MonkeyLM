from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from monkeylm.config import Settings, load_settings, parse_cli_args
from monkeylm.core.monitor import DefectTracker
from monkeylm.core.scheduler import main
from monkeylm.scenario import (
    load_scenario_file,
    load_scenario_from_settings,
    parse_scenario_text,
    scenario_from_mapping,
    scenario_to_dict,
    write_scenario_artifact,
)

SCENARIO = (
    "As a new user, create an account with a valid email and submit the registration form. "
    "Then edit the profile and delete the account. "
    "Try invalid emails, empty fields, and a duplicate account. "
    "Check for XSS and SQL injection in the name field."
)


class ScenarioParsingTests(unittest.TestCase):
    def test_parses_persona_flows_edge_cases_and_security(self) -> None:
        strategy = parse_scenario_text(SCENARIO, name="account lifecycle")

        self.assertEqual(strategy.app_domain, "account lifecycle")
        self.assertEqual(strategy.primary_personas[0].name, "new user")
        self.assertGreaterEqual(len(strategy.critical_flows), 2)
        self.assertTrue(any("create an account" in flow.description for flow in strategy.critical_flows))
        self.assertTrue(any("edit the profile" in flow.description for flow in strategy.critical_flows))
        self.assertTrue(any("invalid emails" in item for item in strategy.edge_cases_to_test))
        self.assertTrue(any("XSS" in item for item in strategy.security_focus))

    def test_falls_back_to_a_single_flow(self) -> None:
        strategy = parse_scenario_text("The dashboard should be healthy.", name="smoke")

        self.assertEqual(strategy.app_domain, "smoke")
        self.assertEqual(len(strategy.critical_flows), 1)
        self.assertEqual(strategy.critical_flows[0].name, "scenario_objective")
        self.assertEqual(strategy.strategy_summary, "The dashboard should be healthy.")

    def test_empty_text_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_scenario_text("   ")

    def test_mapping_supports_structured_and_discovery_fields(self) -> None:
        structured = scenario_from_mapping(
            {
                "name": "checkout",
                "summary": "Validate checkout",
                "personas": [{"name": "buyer", "description": "Buys items", "behaviors": ["pay"]}],
                "flows": [
                    {
                        "name": "pay",
                        "description": "Pay with a card",
                        "steps": ["add item", "pay"],
                    }
                ],
                "edge_cases": ["empty cart"],
                "security_focus": ["xss"],
            }
        )
        self.assertEqual(structured.app_domain, "checkout")
        self.assertEqual(structured.primary_personas[0].name, "buyer")
        self.assertEqual(structured.critical_flows[0].steps, ["add item", "pay"])
        self.assertEqual(structured.edge_cases_to_test, ["empty cart"])

        discovery = scenario_from_mapping(
            {
                "app_domain": "legacy",
                "strategy_summary": "Legacy summary",
                "primary_personas": [{"name": "user", "description": "Legacy user", "behaviors": []}],
                "critical_flows": [{"name": "flow", "description": "Legacy flow", "steps": []}],
                "edge_cases_to_test": ["boundary"],
                "security_focus": ["auth"],
            }
        )
        self.assertEqual(discovery.app_domain, "legacy")
        self.assertEqual(discovery.strategy_summary, "Legacy summary")

    def test_mapping_requires_a_flow_or_summary(self) -> None:
        with self.assertRaises(ValueError):
            scenario_from_mapping({"name": "empty"})


class ScenarioFileTests(unittest.TestCase):
    def test_loads_plain_text_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scenario.txt"
            path.write_text(SCENARIO, encoding="utf-8")
            strategy = load_scenario_file(str(path))
            self.assertEqual(strategy.app_domain, "scenario")
            self.assertGreaterEqual(len(strategy.critical_flows), 2)

    def test_loads_json_file_and_name_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "flow.json"
            path.write_text(
                json.dumps(
                    {
                        "name": "json-name",
                        "summary": "Checkout flow",
                        "flows": [{"name": "pay", "description": "Pay", "steps": ["pay"]}],
                    }
                ),
                encoding="utf-8",
            )
            strategy = load_scenario_file(str(path), name="override")
            self.assertEqual(strategy.app_domain, "override")
            self.assertEqual(strategy.critical_flows[0].name, "pay")

    def test_invalid_json_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text("{not valid", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_scenario_file(str(path))

    def test_missing_file_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            load_scenario_file("/tmp/does-not-exist-monkeylm-scenario.json")

    def test_settings_reject_text_and_file_together(self) -> None:
        settings = Settings(scenario_text="create a record", scenario_file="scenario.json")
        with self.assertRaises(ValueError):
            load_scenario_from_settings(settings)

    def test_settings_loads_inline_text(self) -> None:
        settings = Settings(scenario_text=SCENARIO, scenario_name="inline")
        strategy = load_scenario_from_settings(settings)
        self.assertIsNotNone(strategy)
        assert strategy is not None
        self.assertEqual(strategy.app_domain, "inline")

    def test_cli_scenario_flags_populate_settings(self) -> None:
        with patch(
            "sys.argv",
            ["monkeylm", "--scenario", SCENARIO, "--scenario-name", "cli"],
        ):
            settings = load_settings(parse_cli_args())

        self.assertEqual(settings.scenario_text, SCENARIO)
        self.assertEqual(settings.scenario_name, "cli")


class _FakePersistence:
    def __init__(self, *args, **kwargs) -> None:
        self.baseline_summary = {}

    async def initialize(self) -> None:
        return None

    async def close(self) -> None:
        return None


class _FakePlaywright:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class ScenarioExecutionIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_main_passes_scenario_to_worker_and_writes_artifact(self) -> None:
        captured = {}

        async def _fake_worker(settings, semaphore, **kwargs):
            captured["scenario"] = kwargs.get("scenario_strategy")
            return SimpleNamespace(
                defects=DefectTracker(),
                logs=[],
                network_injections=[],
                decision_cache={},
                launch_info={},
                discovery_strategy=kwargs.get("scenario_strategy"),
                worker_id=1,
                allocated_steps=1,
                completed_steps=0,
                failure_reason=None,
                failure_artifact=None,
                failure_context=None,
            )

        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(
                scenario_text=SCENARIO,
                scenario_name="integration",
                max_steps=1,
                workers=1,
                max_steps_per_worker=1,
            )
            settings._output_dir_override = tmp
            with patch("monkeylm.core.scheduler.async_playwright", lambda: _FakePlaywright()):
                with patch("monkeylm.memory.PersistenceEngine", _FakePersistence):
                    with patch("monkeylm.core.scheduler._run_worker_with_limit", _fake_worker):
                        with patch("monkeylm.reporting.generate_markdown_report"):
                            with patch("monkeylm.reporting.generate_json_summary"):
                                with patch("monkeylm.reporting.generate_pdf_report"):
                                    await main(settings)

            self.assertIsNotNone(captured["scenario"])
            self.assertEqual(captured["scenario"].app_domain, "integration")
            self.assertTrue((Path(tmp) / "scenario.json").is_file())


class ScenarioArtifactTests(unittest.TestCase):
    def test_writes_stable_json_artifact(self) -> None:
        strategy = parse_scenario_text(SCENARIO, name="artifact")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(write_scenario_artifact(strategy, tmp))
            self.assertTrue(path.is_file())
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data, scenario_to_dict(strategy))
            self.assertEqual(data["name"], "artifact")
            self.assertTrue(data["flows"])


if __name__ == "__main__":
    unittest.main()
