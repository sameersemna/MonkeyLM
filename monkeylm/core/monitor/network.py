"""Network interception and zombie UI detection."""

from __future__ import annotations

import asyncio
import json
import random
import time
from typing import Any, Dict, List, Optional

from playwright.async_api import Page, Route

from .defects import DefectTracker


class NetworkMonitor:
    """Intercepts API calls to inject realistic latency/failure and monitors stale loading UI."""

    def __init__(self, defects: DefectTracker) -> None:
        self.defects = defects
        self.injected_events: List[Dict[str, Any]] = []
        self.route_enabled = False

    async def install(self, page: Page) -> None:
        if self.route_enabled:
            return

        async def _route_handler(route: Route) -> None:
            request = route.request
            resource_type = request.resource_type
            url = request.url
            if resource_type in {"xhr", "fetch"} or "/api/" in url:
                roll = random.random()
                if roll < 0.15:
                    delay_seconds = random.randint(2, 5)
                    self.injected_events.append(
                        {
                            "type": "delay",
                            "url": url,
                            "delay_seconds": delay_seconds,
                            "timestamp": time.time(),
                        }
                    )
                    await asyncio.sleep(delay_seconds)
                    await route.continue_()
                    return
                if roll < 0.25:
                    status_code = random.choice([500, 503])
                    self.injected_events.append(
                        {
                            "type": "http_error",
                            "url": url,
                            "status": status_code,
                            "timestamp": time.time(),
                        }
                    )
                    await route.fulfill(
                        status=status_code,
                        content_type="application/json",
                        body=json.dumps({"error": "injected fault", "status": status_code}),
                    )
                    return
            await route.continue_()

        await page.route("**/*", _route_handler)
        self.route_enabled = True

    async def detect_zombie_ui(self, page: Page, step_num: int) -> Optional[Dict[str, Any]]:
        before_url = page.url
        try:
            before = await page.evaluate(
                """() => {
                    const spinnerSel = '[aria-busy="true"], .spinner, .loading, [data-testid*="spinner" i]';
                    const spinnerCount = document.querySelectorAll(spinnerSel).length;
                    const disabledCount = document.querySelectorAll('button:disabled, input:disabled, select:disabled, textarea:disabled').length;
                    return { spinnerCount, disabledCount };
                }"""
            )
            await asyncio.sleep(3.0)
            after_url = page.url
            if after_url != before_url:
                return None
            after = await page.evaluate(
                """() => {
                    const spinnerSel = '[aria-busy="true"], .spinner, .loading, [data-testid*="spinner" i]';
                    const spinnerCount = document.querySelectorAll(spinnerSel).length;
                    const disabledCount = document.querySelectorAll('button:disabled, input:disabled, select:disabled, textarea:disabled').length;
                    return { spinnerCount, disabledCount };
                }"""
            )
        except Exception:
            return None

        if before.get("spinnerCount", 0) > 0 and after.get("spinnerCount", 0) >= before.get("spinnerCount", 0):
            finding = {
                "step": step_num,
                "type": "zombie-ui",
                "description": "Potential zombie UI: spinners persisted for >3s after action.",
                "before": before,
                "after": after,
                "url": page.url,
            }
            self.defects.add("race_findings", finding)
            return finding

        if before.get("disabledCount", 0) > 0 and after.get("disabledCount", 0) >= before.get("disabledCount", 0):
            finding = {
                "step": step_num,
                "type": "disabled-stuck",
                "description": "Potential zombie UI: disabled controls persisted for >3s after action.",
                "before": before,
                "after": after,
                "url": page.url,
            }
            self.defects.add("race_findings", finding)
            return finding
        return None
