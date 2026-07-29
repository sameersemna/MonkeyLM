import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from monkeylm.models.prompts.discovery import _parse_testing_strategy, refresh_testing_strategy, run_application_discovery
from monkeylm.types import Settings


class DiscoveryParserTests(unittest.TestCase):
    def test_rejects_incomplete_payload(self) -> None:
        payload = """
        {
          "app_domain": "e-commerce checkout",
          "strategy_summary": "Focus on payment and account flows."
        }
        """
        self.assertIsNone(_parse_testing_strategy(payload))

    def test_parses_complete_payload(self) -> None:
        payload = """
        {
          "app_domain": "e-commerce checkout",
          "strategy_summary": "Focus on payment and account flows.",
          "primary_personas": [
            {
              "name": "Shopper",
              "description": "Buys products quickly",
              "behaviors": ["adds items", "checks out"]
            }
          ],
          "critical_flows": [
            {
              "name": "checkout",
              "description": "Completes a purchase",
              "steps": ["add item", "enter shipping", "pay"]
            }
          ],
          "edge_cases_to_test": ["declined card", "empty cart"],
          "security_focus": ["input validation", "session handling"]
        }
        """
        strategy = _parse_testing_strategy(payload)
        self.assertIsNotNone(strategy)
        self.assertEqual(strategy.app_domain, "e-commerce checkout")
        self.assertEqual(len(strategy.primary_personas), 1)
        self.assertEqual(strategy.primary_personas[0].name, "Shopper")
        self.assertEqual(strategy.critical_flows[0].name, "checkout")

    def test_falls_back_to_heuristic_strategy_when_model_call_fails(self) -> None:
        async def _run() -> object:
            with patch("monkeylm.models.prompts.discovery._ollama_chat_with_retry", new=AsyncMock(return_value=None)):
                return await run_application_discovery(
                    Settings(ollama_model="dummy-model", ollama_timeout_seconds=10.0),
                    "Title: Noble Quran\nButtons: Browse, Search",
                )

        strategy = asyncio.run(_run())
        self.assertIsNotNone(strategy)
        self.assertIn("Quran", strategy.app_domain)
        self.assertGreaterEqual(len(strategy.primary_personas), 1)
        self.assertGreaterEqual(len(strategy.critical_flows), 1)

    def test_heuristic_fallback_uses_page_labels_for_personas_and_flows(self) -> None:
        from monkeylm.models.prompts.discovery import _build_heuristic_strategy

        strategy = _build_heuristic_strategy(
            "Title: Noble Quran\nHeader: Home\nButtons: Browse, Search, Bookmarks\nText: Welcome to the Quran app"
        )

        self.assertIn("Quran", strategy.app_domain)
        self.assertGreaterEqual(len(strategy.primary_personas), 2)
        self.assertGreaterEqual(len(strategy.critical_flows), 2)
        self.assertTrue(any("search" in flow.name.lower() or "browse" in flow.name.lower() for flow in strategy.critical_flows))

    def test_tries_secondary_model_before_falling_back(self) -> None:
        async def _run() -> object:
            responses = [None, {"message": {"content": '{"app_domain": "Quran app", "strategy_summary": "Browse and search", "primary_personas": [{"name": "Reader", "description": "Reads content", "behaviors": ["browses"]}], "critical_flows": [{"name": "browse", "description": "Open content", "steps": ["browse"]}], "edge_cases_to_test": ["empty state"], "security_focus": ["validation"]}'}}]
            with patch("monkeylm.models.prompts.discovery._ollama_chat_with_retry", new=AsyncMock(side_effect=responses)):
                return await run_application_discovery(
                    Settings(ollama_model="dummy-model", ollama_timeout_seconds=10.0),
                    "Title: Noble Quran\nButtons: Browse, Search",
                )

        strategy = asyncio.run(_run())
        self.assertIsNotNone(strategy)
        self.assertEqual(strategy.app_domain, "Quran app")
        self.assertEqual(strategy.primary_personas[0].name, "Reader")

    def test_refresh_testing_strategy_adds_search_and_bookmark_flows(self) -> None:
        from monkeylm.models.prompts.discovery import _build_heuristic_strategy

        strategy = _build_heuristic_strategy("Title: Noble Quran\nButtons: Browse, Search, Bookmarks")
        refreshed = refresh_testing_strategy(strategy, "Title: Noble Quran\nSearch box available\nBookmark button visible")

        self.assertIsNotNone(refreshed)
        self.assertTrue(any("search" in flow.name.lower() for flow in refreshed.critical_flows))
        self.assertTrue(any("bookmark" in flow.name.lower() for flow in refreshed.critical_flows))


if __name__ == "__main__":
    unittest.main()
