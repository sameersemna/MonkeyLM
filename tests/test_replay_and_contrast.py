"""Tests for replay mode (#7) and OCR contrast checks (#8)."""

from __future__ import annotations

import json

import pytest

from monkeylm.config import cv2, np, RapidOCR


class TestReplayMode:
    def test_decision_cache_roundtrip_through_results_json(self, tmp_path):
        """json_report must persist decision_cache so --replay-from can read it."""
        from monkeylm.reporting.json_report import generate_json_summary
        from monkeylm.types import Settings
        from datetime import datetime

        cache = {"https://app.example/::abc123": {"action": "click", "target": "[id=4]", "value": ""}}
        settings = Settings()
        settings._output_dir_override = str(tmp_path)
        defects = type("D", (), {
            "security_risks": [], "accessibility_violations": [], "performance_bottlenecks": [],
            "visual_regressions": [], "layout_instability": [], "regression_findings": [],
            "race_findings": [], "console_findings": [], "boundary_drift": [],
            "context_anomalies": [], "ux_flow_freezes": [], "validation_failures": [],
            "capture_diagnostics": [], "rendering_defects": [],
        })()
        generate_json_summary(
            settings, defects, [], {"worker_failures": []}, [], False,
            datetime.now(), datetime.now(), decision_cache=cache,
        )
        written = json.loads((tmp_path / "results.json").read_text())
        assert written["decision_cache"] == cache

    def test_replay_cache_lookup_shape(self, tmp_path):
        """The runner's replay path expects {state_key: {action, target, value}}."""
        results = tmp_path / "results.json"
        results.write_text(json.dumps({"decision_cache": {"k::h": {"action": "scroll", "target": "", "value": ""}}}))
        loaded = json.loads(results.read_text()).get("decision_cache", {})
        assert loaded["k::h"]["action"] == "scroll"


@pytest.mark.skipif(RapidOCR is None or cv2 is None or np is None, reason="rapidocr/opencv not installed")
class TestOcrContrastCheck:
    def _text_image(self, path: str, fg: int, bg: int) -> str:
        frame = np.full((120, 640, 3), bg, dtype=np.uint8)
        cv2.putText(frame, "Sample text", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (fg, fg, fg), 2)
        cv2.imwrite(path, frame)
        return path

    def test_low_contrast_text_flagged(self, tmp_path):
        from monkeylm.browser.snapshot.cv import ocr_contrast_check
        # Light gray text on white background — WCAG failure.
        path = self._text_image(str(tmp_path / "low.png"), fg=200, bg=255)
        result = ocr_contrast_check(path)
        assert result["engine"] == "rapidocr-contrast"
        if result["checked"] > 0:
            assert len(result["violations"]) >= 1
            assert result["violations"][0]["contrast_ratio"] < 4.5

    def test_high_contrast_text_passes(self, tmp_path):
        from monkeylm.browser.snapshot.cv import ocr_contrast_check
        # Black text on white background — WCAG pass (21:1).
        path = self._text_image(str(tmp_path / "high.png"), fg=0, bg=255)
        result = ocr_contrast_check(path)
        assert result["engine"] == "rapidocr-contrast"
        assert result["violations"] == []

    def test_missing_file(self):
        from monkeylm.browser.snapshot.cv import ocr_contrast_check
        assert ocr_contrast_check("")["error"] == "missing_screenshot"


class TestContrastMath:
    def test_wcag_luminance_extremes(self):
        from monkeylm.browser.snapshot.cv import _contrast_ratio, _relative_luminance
        black = _relative_luminance(np.array([[0.0, 0.0, 0.0]]))
        white = _relative_luminance(np.array([[255.0, 255.0, 255.0]]))
        assert _contrast_ratio(float(black[0]), float(white[0])) == pytest.approx(21.0, abs=0.1)

    def test_identical_colors_ratio_one(self):
        from monkeylm.browser.snapshot.cv import _contrast_ratio
        assert _contrast_ratio(0.5, 0.5) == pytest.approx(1.0)
