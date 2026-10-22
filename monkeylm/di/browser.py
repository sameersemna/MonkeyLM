"""Browser provider DI adapter."""

from __future__ import annotations

from typing import Any, Optional

from monkeylm.config import Settings, load_settings
from monkeylm.interfaces import IBrowserProvider
from monkeylm.types import PageSnapshot


def create_browser_provider(settings: Optional[Settings] = None) -> IBrowserProvider:
    """Factory function to create browser provider instance.

    Args:
        settings: Runtime configuration. Defaults to load_settings() if None.

    Returns:
        IBrowserProvider implementation (currently BrowserProviderAdapter).

    Example:
        >>> from monkeylm import create_browser_provider
        >>> browser = create_browser_provider()
        >>> await browser.launch()

    Note:
        Currently returns BrowserProviderAdapter which wraps browser.py functions.
        Will be replaced with proper Browser class in Phase 2 refactoring.
        launch(), navigate(), snapshot(), close(), and current_page work.
        click(), type_text(), and submit_form() raise NotImplementedError -
        the real execute_action() needs live fuzzer/defect-tracker/monitor
        session objects this thin adapter has no way to construct honestly.
        See IMPROVEMENT_LOG.md Cycle 5.
    """
    from monkeylm.browser import (
        get_page_state,
        launch_context_with_fallback,
    )
    from playwright.async_api import async_playwright, BrowserContext, Page, Playwright

    if settings is None:
        # Reuse the namespace the entry point already parsed so
        # --headless/--headed are not silently dropped here; fall back to
        # the .env / shell / default chain when this runs standalone.
        from monkeylm.config.cli import last_parsed_cli_args

        settings = load_settings(last_parsed_cli_args())

    class BrowserProviderAdapter:
        """Adapter that wraps browser.py functions to implement IBrowserProvider."""

        def __init__(self, settings: Settings):
            self._settings = settings
            self._playwright: Optional[Playwright] = None
            self._context: Optional[BrowserContext] = None
            self._page: Optional[Page] = None
            self._step = 0

        async def launch(self) -> None:
            from monkeylm import build_worker_user_data_dir
            self._playwright = await async_playwright().start()
            user_data_dir = build_worker_user_data_dir(self._settings, 0)
            self._context, _ = await launch_context_with_fallback(
                self._playwright,
                settings=self._settings,
                user_data_dir=user_data_dir,
                worker_label="DI",
            )
            self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()

        async def navigate(self, url: str) -> PageSnapshot:
            if self._page is None:
                raise RuntimeError("Browser not launched. Call launch() first.")
            await self._page.goto(url)
            self._step += 1
            return await get_page_state(self._page, self._step, phase="di-navigate", output_dir=self._settings.output_dir)

        async def snapshot(self) -> PageSnapshot:
            if self._page is None:
                raise RuntimeError("Browser not launched. Call launch() first.")
            self._step += 1
            return await get_page_state(self._page, self._step, phase="di-snapshot", output_dir=self._settings.output_dir)

        # click/type_text/submit_form are intentionally not implemented: the real
        # execute_action(page, settings, action_plan, step_num, fuzzer, defects,
        # network_monitor, perf_monitor, ...) takes a structured action_plan dict
        # plus live fuzzer/defect-tracker/network-monitor/perf-monitor session
        # objects that carry accumulated state across a whole run. A thin
        # (selector) / (selector, text) adapter method can't honestly construct
        # those - see IMPROVEMENT_LOG.md Cycle 5.

        async def click(self, selector: str) -> None:
            raise NotImplementedError(
                "BrowserProviderAdapter.click: the real execute_action() needs a "
                "structured action_plan dict plus live fuzzer/defects/network_monitor/"
                "perf_monitor session objects that this thin adapter has no way to "
                "honestly construct. Use monkeylm.browser.execute_action directly "
                "within a real worker run instead."
            )

        async def type_text(self, selector: str, text: str) -> None:
            raise NotImplementedError(
                "BrowserProviderAdapter.type_text: same limitation as click() - see its error."
            )

        async def submit_form(self, selector: str) -> None:
            raise NotImplementedError(
                "BrowserProviderAdapter.submit_form: same limitation as click() - see its error."
            )

        async def close(self) -> None:
            if self._context:
                await self._context.close()
            if self._playwright:
                await self._playwright.stop()
            self._page = None
            self._context = None
            self._playwright = None

        @property
        def current_page(self) -> Optional[Any]:
            return self._page

    return BrowserProviderAdapter(settings)
