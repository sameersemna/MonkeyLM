"""Modern regression tests for the browser interaction guard modules."""

from __future__ import annotations

import unittest

from monkeylm.browser.actions.interaction import detect_click_interception


class InteractionGuardModernTests(unittest.IsolatedAsyncioTestCase):
    async def test_detect_click_interception_flags_overlay_blocking(self) -> None:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.set_content(
                """
                <html>
                  <body>
                    <div style="position:fixed; inset:0; background:rgba(0,0,0,0.25); z-index:9999; pointer-events:auto;">
                      <button id=\"overlay\" style=\"margin:120px auto; display:block;\">Overlay</button>
                    </div>
                    <button id=\"target\" style=\"position:relative; z-index:1; margin-top:40px; display:block;\">Target</button>
                  </body>
                </html>
                """
            )

            target = page.locator("#target")
            await target.wait_for()
            interception = await detect_click_interception(page, target, "target")

            self.assertTrue(interception["is_blocked"])
            self.assertEqual(interception["reason"], "overlay_blocked")

            await browser.close()


if __name__ == "__main__":
    unittest.main()
