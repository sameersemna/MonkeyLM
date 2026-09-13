"""Unit tests for the OpenCV screenshot analyzers in snapshot/cv.py.

Uses synthetic images generated with numpy so no browser or fixture files are
needed. Skips gracefully when OpenCV/numpy are unavailable.
"""

from __future__ import annotations

import os

import pytest

from monkeylm.config import cv2, np, RapidOCR
from monkeylm.browser.snapshot.cv import (
    analyze_screenshot_health,
    capture_chrome_templates,
    compare_screenshots_cv,
    ocr_error_text,
    phash_hamming,
    verify_chrome_templates,
    visual_phash,
)

pytestmark = pytest.mark.skipif(cv2 is None or np is None, reason="opencv/numpy not installed")


def _write_png(path: str, array: "np.ndarray") -> str:
    cv2.imwrite(path, array)
    return path


def _textured_frame(width: int = 320, height: int = 240) -> "np.ndarray":
    """A non-blank grayscale frame with deterministic texture."""
    rng = np.random.default_rng(42)
    return rng.integers(0, 255, size=(height, width), dtype=np.uint8)


class TestAnalyzeScreenshotHealth:
    def test_blank_white_frame_flagged(self, tmp_path):
        frame = np.full((240, 320), 255, dtype=np.uint8)
        path = _write_png(str(tmp_path / "blank.png"), frame)
        result = analyze_screenshot_health(path)
        assert result["blank_screen"] is True
        assert result["stddev"] < 8.0
        assert result["unique_colors"] < 24
        assert result["engine"] == "opencv-histogram"

    def test_textured_frame_not_flagged(self, tmp_path):
        path = _write_png(str(tmp_path / "textured.png"), _textured_frame())
        result = analyze_screenshot_health(path)
        assert result["blank_screen"] is False
        assert result["stddev"] > 8.0

    def test_missing_file(self):
        result = analyze_screenshot_health("")
        assert result["error"] == "missing_screenshot"
        assert result["blank_screen"] is False


class TestCompareScreenshotsCv:
    def test_identical_frames_no_regions(self, tmp_path):
        frame = _textured_frame()
        before = _write_png(str(tmp_path / "a.png"), frame)
        after = _write_png(str(tmp_path / "b.png"), frame.copy())
        result = compare_screenshots_cv(before, after, 1, output_dir=str(tmp_path))
        assert result["engine"] == "opencv-ssim"
        assert result["ssim_score"] == pytest.approx(1.0, abs=1e-3)
        assert result["diff_ratio"] == pytest.approx(0.0, abs=1e-3)
        assert result["diff_regions"] == []

    def test_localized_change_produces_region(self, tmp_path):
        before_frame = _textured_frame()
        after_frame = before_frame.copy()
        # Paint a solid 60x60 block — well above the min-area noise floor.
        after_frame[40:100, 120:180] = 255
        before = _write_png(str(tmp_path / "a.png"), before_frame)
        after = _write_png(str(tmp_path / "b.png"), after_frame)
        result = compare_screenshots_cv(before, after, 2, output_dir=str(tmp_path))
        assert result["engine"] == "opencv-ssim"
        assert result["ssim_score"] < 1.0
        assert result["diff_ratio"] > 0.0
        assert len(result["diff_regions"]) >= 1
        region = result["diff_regions"][0]
        # Region must overlap the painted block.
        assert region["x"] <= 120 and region["y"] <= 40
        assert region["x"] + region["w"] >= 180
        assert region["y"] + region["h"] >= 100
        # Normalized box_2d [ymin, xmin, ymax, xmax] matches pixel coords.
        ymin, xmin, ymax, xmax = region["box_2d"]
        assert ymin == pytest.approx(region["y"] / 240, abs=0.01)
        assert xmin == pytest.approx(region["x"] / 320, abs=0.01)
        assert ymax == pytest.approx((region["y"] + region["h"]) / 240, abs=0.01)
        assert xmax == pytest.approx((region["x"] + region["w"]) / 320, abs=0.01)
        assert os.path.exists(result["diff_image"])

    def test_cv_confident_box_draws_annotation(self, tmp_path):
        """The vision-skip path: _draw_red_box_arrow accepts CV-derived boxes."""
        from monkeylm.models.vision import _draw_red_box_arrow
        frame = _textured_frame()
        path = _write_png(str(tmp_path / "frame.png"), cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR))
        out = str(tmp_path / "annotated.png")
        ok = _draw_red_box_arrow(path, [0.15, 0.35, 0.45, 0.60], "status=FAILED", out, description="CV-localized", step_num=7)
        assert ok is True
        assert os.path.exists(out)


class TestScreenshotDedup:
    def test_identical_frames_share_one_file(self, tmp_path):
        from monkeylm.browser.snapshot.state import _dedup_screenshot, _screenshot_registry
        _screenshot_registry.clear()
        frame = _textured_frame()
        first = _write_png(str(tmp_path / "a.png"), frame)
        second = _write_png(str(tmp_path / "b.png"), frame.copy())
        out_dir = str(tmp_path)
        kept = _dedup_screenshot(first, out_dir)
        assert kept == first
        deduped = _dedup_screenshot(second, out_dir)
        assert deduped == first
        assert not os.path.exists(second)  # duplicate file removed

    def test_different_frames_keep_separate_files(self, tmp_path):
        from monkeylm.browser.snapshot.state import _dedup_screenshot, _screenshot_registry
        _screenshot_registry.clear()
        first = _write_png(str(tmp_path / "a.png"), _textured_frame())
        other = np.full((240, 320), 255, dtype=np.uint8)
        second = _write_png(str(tmp_path / "b.png"), other)
        out_dir = str(tmp_path)
        assert _dedup_screenshot(first, out_dir) == first
        assert _dedup_screenshot(second, out_dir) == second
        assert os.path.exists(second)

    def test_empty_path_passthrough(self):
        from monkeylm.browser.snapshot.state import _dedup_screenshot
        assert _dedup_screenshot("", "/tmp") == ""

    def test_size_mismatch_handled(self, tmp_path):
        before = _write_png(str(tmp_path / "a.png"), _textured_frame(320, 240))
        after = _write_png(str(tmp_path / "b.png"), _textured_frame(160, 120))
        result = compare_screenshots_cv(before, after, 3, output_dir=str(tmp_path))
        assert result["error"] is None
        assert result["engine"] == "opencv-ssim"

    def test_missing_screenshot(self, tmp_path):
        result = compare_screenshots_cv("", str(tmp_path / "nope.png"), 4, output_dir=str(tmp_path))
        assert result["error"] == "missing_screenshot"

    def test_no_output_dir_skips_diff_image(self, tmp_path):
        frame = _textured_frame()
        before = _write_png(str(tmp_path / "a.png"), frame)
        after = _write_png(str(tmp_path / "b.png"), frame.copy())
        result = compare_screenshots_cv(before, after, 5, output_dir="")
        assert result["diff_image"] == ""


class TestVisualPhash:
    def test_identical_frames_same_hash(self, tmp_path):
        frame = _textured_frame()
        a = _write_png(str(tmp_path / "a.png"), frame)
        b = _write_png(str(tmp_path / "b.png"), frame.copy())
        assert visual_phash(a) == visual_phash(b)
        assert phash_hamming(visual_phash(a), visual_phash(b)) == 0

    def test_different_frames_diverge(self, tmp_path):
        a = _write_png(str(tmp_path / "a.png"), _textured_frame())
        other = np.full((240, 320), 255, dtype=np.uint8)
        b = _write_png(str(tmp_path / "b.png"), other)
        distance = phash_hamming(visual_phash(a), visual_phash(b))
        assert distance > 32  # well-separated hashes

    def test_small_visual_change_low_distance(self, tmp_path):
        frame = _textured_frame()
        a = _write_png(str(tmp_path / "a.png"), frame)
        tweaked = frame.copy()
        tweaked[0:8, 0:8] = 255  # tiny corner change
        b = _write_png(str(tmp_path / "b.png"), tweaked)
        distance = phash_hamming(visual_phash(a), visual_phash(b))
        assert 0 <= distance <= 8

    def test_missing_file_returns_empty(self):
        assert visual_phash("") == ""
        assert phash_hamming("", "abcd") == -1
        assert phash_hamming("zz", "zz") == -1


class TestStallDetectorVisualFreeze:
    """Stall detector must catch freezes the DOM hash misses (canvas apps)."""

    def _make_detector(self):
        from monkeylm.core.monitor.defects import DefectTracker
        from monkeylm.core.monitor.stall import StallDetector
        return StallDetector(DefectTracker(), threshold=3)

    def test_visual_freeze_detected_despite_dom_drift(self):
        detector = self._make_detector()
        for step in range(1, 4):
            detector.record_state(
                step,
                "https://app.example/canvas",
                f"dom-hash-{step}",  # DOM hash changes every step
                "click",
                visual_hash="deadbeef" * 16,
            )
        finding = detector.check_for_stall(4, "click")
        assert finding is not None
        assert finding["detection_signal"] == "visual_phash"

    def test_no_visual_hash_falls_back_to_dom_only(self):
        detector = self._make_detector()
        for step in range(1, 4):
            detector.record_state(step, "https://app.example/page", f"dom-hash-{step}", "click")
        assert detector.check_for_stall(4, "click") is None

    def test_dom_freeze_still_detected_without_visual_hash(self):
        detector = self._make_detector()
        for step in range(1, 4):
            detector.record_state(step, "https://app.example/page", "same-hash", "click")
        finding = detector.check_for_stall(4, "click")
        assert finding is not None
        assert finding["detection_signal"] == "dom_hash"


class TestChromeTemplates:
    def _chrome_frame(self, logo: bool = True) -> "np.ndarray":
        """Frame with a textured logo quadrant and a nav strip.

        The logo uses a 4px-block checkerboard: real logos have multi-pixel
        features, and 4px blocks survive the 0.5 INTER_AREA downscale with
        texture intact (a 1px checkerboard averages into uniform gray, which
        would make the template degenerate for normalized correlation).
        """
        frame = np.full((240, 320), 128, dtype=np.uint8)
        if logo:
            blocks = (np.indices((19, 48)) // 4).sum(axis=0) % 2 * 255
            frame[0:19, 0:48] = blocks.astype(np.uint8)
        frame[0:14, 60:300] = 90  # nav strip content
        return frame

    def test_capture_and_verify_intact_chrome(self, tmp_path):
        baseline = _write_png(str(tmp_path / "base.png"), self._chrome_frame())
        templates = capture_chrome_templates(baseline)
        assert set(templates.keys()) == {"logo", "nav"}
        current = _write_png(str(tmp_path / "current.png"), self._chrome_frame())
        result = verify_chrome_templates(current, templates)
        assert result["engine"] == "opencv-template"
        assert result["missing"] == []
        assert all(score > 0.75 for score in result["scores"].values())

    def test_missing_logo_detected(self, tmp_path):
        baseline = _write_png(str(tmp_path / "base.png"), self._chrome_frame())
        templates = capture_chrome_templates(baseline)
        broken = self._chrome_frame(logo=False)
        current = _write_png(str(tmp_path / "current.png"), broken)
        result = verify_chrome_templates(current, templates)
        assert "logo" in result["missing"]

    def test_empty_templates_noop(self, tmp_path):
        frame = _write_png(str(tmp_path / "f.png"), self._chrome_frame())
        result = verify_chrome_templates(frame, {})
        assert result["error"] == "no_templates"
        assert result["missing"] == []

    def test_unreadable_baseline_returns_empty(self):
        assert capture_chrome_templates("") == {}

    def test_downscaled_capture_and_verify_roundtrip(self, tmp_path):
        baseline = _write_png(str(tmp_path / "base.png"), self._chrome_frame())
        templates = capture_chrome_templates(baseline, scale=0.5)
        assert set(templates.keys()) == {"logo", "nav"}
        # Templates must be half the size of the full-res crops.
        assert templates["logo"].shape == (9, 24)
        current = _write_png(str(tmp_path / "current.png"), self._chrome_frame())
        result = verify_chrome_templates(current, templates, scale=0.5)
        assert result["engine"] == "opencv-template"
        assert result["missing"] == []

    def test_downscaled_missing_logo_detected(self, tmp_path):
        baseline = _write_png(str(tmp_path / "base.png"), self._chrome_frame())
        templates = capture_chrome_templates(baseline, scale=0.5)
        broken = _write_png(str(tmp_path / "broken.png"), self._chrome_frame(logo=False))
        result = verify_chrome_templates(broken, templates, scale=0.5)
        assert "logo" in result["missing"]

    def test_scale_mismatch_still_functions(self, tmp_path):
        # Capture full-res, verify downscaled: templates larger than the
        # downscaled frame regions are skipped rather than crashing.
        baseline = _write_png(str(tmp_path / "base.png"), self._chrome_frame())
        templates = capture_chrome_templates(baseline, scale=1.0)
        current = _write_png(str(tmp_path / "current.png"), self._chrome_frame())
        result = verify_chrome_templates(current, templates, scale=0.5)
        assert result["error"] is None


@pytest.mark.skipif(RapidOCR is None, reason="rapidocr not installed")
class TestOcrErrorText:
    def test_error_keyword_extracted(self, tmp_path):
        frame = np.full((120, 640), 255, dtype=np.uint8)
        cv2.putText(frame, "Error 500: request failed", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0,), 2)
        path = _write_png(str(tmp_path / "err.png"), frame)
        result = ocr_error_text(path)
        assert result["engine"] == "rapidocr"
        assert "500" in result["text"] or "failed" in result["text"].lower()

    def test_benign_text_filtered_out(self, tmp_path):
        frame = np.full((120, 640), 255, dtype=np.uint8)
        cv2.putText(frame, "Welcome back", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0,), 2)
        path = _write_png(str(tmp_path / "ok.png"), frame)
        result = ocr_error_text(path)
        assert result["text"] == ""

    def test_missing_file(self):
        assert ocr_error_text("")["error"] == "missing_screenshot"
