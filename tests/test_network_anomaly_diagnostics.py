"""Regression tests for network anomaly capture and reporting.

The field run surfaced anomalies like::

    type=network-5xx-fetch message=

with no URL, no status, and no method — unactionable for an operator, and the
duplicated entries made it worse because the dedupe key ignored status/method/
resource type. These tests lock down:

* the summary renders URL, status, and method when there is no ``message``;
* transport-level failures (``requestfailed``) are captured instead of dropped;
* framework route prefetch 5xx is separated from genuine API 5xx;
* the dedupe key distinguishes entries that differ by status or method.
"""

from __future__ import annotations

import unittest

from monkeylm.core.monitor.anomaly import (
    BrowserAnomalySensor,
    _dedupe_batch,
    format_anomaly_summary,
)


class _FakeRequest:
    def __init__(self, url: str, method: str = "GET", resource_type: str = "fetch", failure: str = "") -> None:
        self.url = url
        self.method = method
        self.resource_type = resource_type
        self.failure = failure


class _FakeResponse:
    def __init__(self, status: int, request: _FakeRequest) -> None:
        self.status = status
        self.request = request


class _FakePage:
    def __init__(self) -> None:
        self.handlers: dict = {}

    def on(self, event: str, handler) -> None:
        self.handlers.setdefault(event, []).append(handler)

    def emit(self, event: str, payload) -> None:
        for handler in self.handlers.get(event, []):
            handler(payload)


class _RecordingDefects:
    def __init__(self) -> None:
        self.added: list = []

    def add(self, bucket: str, entry: dict) -> None:
        self.added.append((bucket, entry))


class FormatAnomalySummaryTests(unittest.TestCase):
    def test_network_anomaly_surfaces_url_and_status(self) -> None:
        summary = format_anomaly_summary([
            {
                "step": 2,
                "action": "click:[id=37]",
                "type": "network-5xx-fetch",
                "url": "https://example.com/api/leads",
                "method": "POST",
                "status": 500,
                "resource_type": "xhr",
            }
        ])
        self.assertIn("status=500", summary)
        self.assertIn("method=POST", summary)
        self.assertIn("https://example.com/api/leads", summary)
        self.assertNotIn("message= ", summary)
        self.assertNotIn("message=;", summary)

    def test_explicit_message_wins_over_network_detail(self) -> None:
        summary = format_anomaly_summary([
            {
                "step": 1,
                "action": "click",
                "type": "console-error",
                "message": "boom",
                "url": "https://example.com",
                "status": 500,
            }
        ])
        self.assertIn("message=boom", summary)
        self.assertNotIn("status=500", summary)


class DedupeTests(unittest.TestCase):
    def test_status_is_part_of_the_dedupe_key(self) -> None:
        batch = _dedupe_batch([
            {"type": "network-5xx-fetch", "action": "a", "url": "u", "status": 500, "method": "GET"},
            {"type": "network-5xx-fetch", "action": "a", "url": "u", "status": 503, "method": "GET"},
        ])
        self.assertEqual(len(batch), 2)

    def test_method_is_part_of_the_dedupe_key(self) -> None:
        batch = _dedupe_batch([
            {"type": "network-5xx-fetch", "action": "a", "url": "u", "status": 500, "method": "GET"},
            {"type": "network-5xx-fetch", "action": "a", "url": "u", "status": 500, "method": "POST"},
        ])
        self.assertEqual(len(batch), 2)

    def test_identical_entries_still_collapse(self) -> None:
        batch = _dedupe_batch([
            {"type": "network-5xx-fetch", "action": "a", "url": "u", "status": 500, "method": "GET", "step": 1},
            {"type": "network-5xx-fetch", "action": "a", "url": "u", "status": 500, "method": "GET", "step": 1},
        ])
        self.assertEqual(len(batch), 1)
        self.assertEqual(batch[0]["occurrences"], 2)


class NetworkCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def _install(self) -> tuple:

        defects = _RecordingDefects()
        sensor = BrowserAnomalySensor(defects)
        page = _FakePage()
        await sensor.check_network_failures(page)
        sensor.set_action_context(3, "click:[id=37]")
        return sensor, page, defects

    async def test_api_5xx_is_error_severity_with_diagnostics(self) -> None:
        sensor, page, _ = await self._install()
        page.emit("response", _FakeResponse(500, _FakeRequest("https://x.com/api/leads", "POST", "xhr")))
        batch = await sensor.flush_anomalies()
        self.assertEqual(len(batch), 1)
        entry = batch[0]
        self.assertEqual(entry["type"], "network-5xx-fetch")
        self.assertEqual(entry["severity"], "error")
        self.assertEqual(entry["status"], 500)
        self.assertEqual(entry["method"], "POST")
        self.assertIn("https://x.com/api/leads", entry["failure_reason"])

    async def test_rsc_prefetch_is_downgraded(self) -> None:
        sensor, page, _ = await self._install()
        page.emit(
            "response",
            _FakeResponse(500, _FakeRequest("https://x.com/assessment?service=ut-audit&_rsc=ABC", "GET", "fetch")),
        )
        batch = await sensor.flush_anomalies()
        self.assertEqual(len(batch), 1)
        self.assertEqual(batch[0]["type"], "network-5xx-prefetch")
        self.assertEqual(batch[0]["severity"], "warning")
        self.assertTrue(batch[0]["prefetch"])

    async def test_successful_responses_are_ignored(self) -> None:
        sensor, page, _ = await self._install()
        page.emit("response", _FakeResponse(200, _FakeRequest("https://x.com/api/ok", "GET", "xhr")))
        self.assertEqual(await sensor.flush_anomalies(), [])

    async def test_request_failed_is_captured(self) -> None:
        sensor, page, _ = await self._install()
        page.emit(
            "requestfailed",
            _FakeRequest("https://x.com/api/slow", "GET", "xhr", failure="net::ERR_CONNECTION_RESET"),
        )
        batch = await sensor.flush_anomalies()
        self.assertEqual(len(batch), 1)
        entry = batch[0]
        self.assertEqual(entry["type"], "network-request-failed")
        self.assertIsNone(entry["status"])
        self.assertIn("ERR_CONNECTION_RESET", entry["failure_reason"])

    async def test_request_failed_is_registered_on_page(self) -> None:
        _, page, _ = await self._install()
        self.assertIn("requestfailed", page.handlers)
        self.assertIn("response", page.handlers)

    async def test_flushed_anomalies_reach_the_defect_tracker(self) -> None:
        sensor, page, defects = await self._install()
        page.emit("response", _FakeResponse(503, _FakeRequest("https://x.com/api/a", "GET", "fetch")))
        await sensor.flush_anomalies()
        self.assertEqual(len(defects.added), 1)
        bucket, entry = defects.added[0]
        self.assertEqual(bucket, "context_anomalies")
        self.assertEqual(entry["status"], 503)

    async def test_summary_of_real_capture_is_actionable(self) -> None:
        sensor, page, _ = await self._install()
        page.emit("response", _FakeResponse(500, _FakeRequest("https://x.com/api/a", "GET", "fetch")))
        summary = format_anomaly_summary(await sensor.flush_anomalies())
        self.assertIn("status=500", summary)
        self.assertIn("https://x.com/api/a", summary)


if __name__ == "__main__":
    unittest.main()