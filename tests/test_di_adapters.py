"""Regression tests for the DI factory adapters in monkeylm/__init__.py.

These adapters (create_browser_provider, create_memory_store,
create_model_client, create_report_generator) wrap the real function-based
modules to satisfy the Protocols in monkeylm/interfaces.py. A mypy-adoption
pass (see IMPROVEMENT_LOG.md Cycle 4/5) found that several of the Protocol
contracts don't correspond to any real capability in the underlying
subsystems (wrong argument shapes, missing required session objects, sync
vs async mismatches). Those methods now fail loudly with NotImplementedError
instead of silently raising a confusing AttributeError/TypeError deep in an
await chain. These tests pin that contract down.
"""

from __future__ import annotations

import unittest

from monkeylm import (
    create_browser_provider,
    create_memory_store,
    create_model_client,
    create_report_generator,
)


class DIAdapterNotImplementedTests(unittest.IsolatedAsyncioTestCase):
    async def test_browser_provider_click_not_implemented(self) -> None:
        provider = create_browser_provider()
        with self.assertRaises(NotImplementedError):
            await provider.click("[id=1]")

    async def test_browser_provider_type_text_not_implemented(self) -> None:
        provider = create_browser_provider()
        with self.assertRaises(NotImplementedError):
            await provider.type_text("[id=1]", "hello")

    async def test_browser_provider_submit_form_not_implemented(self) -> None:
        provider = create_browser_provider()
        with self.assertRaises(NotImplementedError):
            await provider.submit_form("[id=1]")

    async def test_memory_store_save_state_not_implemented(self) -> None:
        memory = create_memory_store()
        with self.assertRaises(NotImplementedError):
            await memory.save_state("example.com", "/", snapshot=None, metadata={})

    async def test_memory_store_load_state_not_implemented(self) -> None:
        memory = create_memory_store()
        with self.assertRaises(NotImplementedError):
            await memory.load_state("example.com", "/")

    async def test_memory_store_search_memory_not_implemented(self) -> None:
        memory = create_memory_store()
        with self.assertRaises(NotImplementedError):
            await memory.search_memory("query")

    async def test_memory_store_acquire_lock_not_implemented(self) -> None:
        memory = create_memory_store()
        with self.assertRaises(NotImplementedError):
            await memory.acquire_lock("key", 30)

    async def test_memory_store_release_lock_not_implemented(self) -> None:
        memory = create_memory_store()
        with self.assertRaises(NotImplementedError):
            await memory.release_lock("key")

    async def test_model_client_analyze_testing_strategy_not_implemented(self) -> None:
        model = create_model_client()
        with self.assertRaises(NotImplementedError):
            model.analyze_testing_strategy(page_snapshot=None)

    async def test_model_client_decide_next_action_not_implemented(self) -> None:
        model = create_model_client()
        with self.assertRaises(NotImplementedError):
            model.decide_next_action(page_snapshot=None, goal="explore", history=[])

    async def test_report_generator_generate_not_implemented(self) -> None:
        generator = create_report_generator("markdown")
        with self.assertRaises(NotImplementedError):
            await generator.generate(results=[], output_dir=".", settings=None)

    async def test_report_generator_generate_rejects_unsupported_format(self) -> None:
        generator = create_report_generator("xml")
        with self.assertRaises(ValueError):
            await generator.generate(results=[], output_dir=".", settings=None)


if __name__ == "__main__":
    unittest.main()
