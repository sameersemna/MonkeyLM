"""Classical computer-vision screenshot analyzers (OpenCV).

Complements the pixelmatch diff in ``visual.py`` with capabilities pixelmatch
lacks: *localization* of changed regions (SSIM map + contour extraction),
deterministic blank/crash-screen detection, perceptual hashing for visual
freeze detection, template-based expected-chrome verification, and optional
OCR error-text extraction. All functions are pure, sync, and fast, and degrade
gracefully to a no-op when their backing dependency is not installed.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

from monkeylm.config import cv2, np, RapidOCR
from .state import _sanitize_filename

# Minimum region area as a fraction of the total frame, below which a detected
# diff contour is treated as noise (anti-aliasing, cursor blink, etc.).
_MIN_REGION_AREA_RATIO = 0.0005
# Maximum number of diff regions returned, ordered by descending area.
_MAX_REGIONS = 10
# Unique-color count below which a frame is considered degenerate.
_BLANK_UNIQUE_COLOR_LIMIT = 24
# Average-hash grid size; the hash is _PHASH_SIZE² bits rendered as hex.
_PHASH_SIZE = 16
# Template-matching scales for DPI/zoom robustness (research-backed range).
_TEMPLATE_SCALES = (0.8, 1.0, 1.2)
# Chrome regions captured as baseline templates, as (name, x0%, y0%, x1%, y1%).
_CHROME_REGIONS = (
    ("logo", 0.0, 0.0, 0.15, 0.08),
    ("nav", 0.0, 0.0, 1.0, 0.06),
)
# Keywords that mark OCR-extracted text as error-relevant.
_OCR_ERROR_KEYWORDS = (
    "error", "failed", "failure", "exception", "denied", "forbidden",
    "not found", "404", "500", "502", "503", "unavailable", "invalid",
    "crash", "refused", "timeout", "unauthorized",
)


def _cv_available() -> bool:
    return cv2 is not None and np is not None


def _read_gray(path: str):
    """Read an image as a grayscale ndarray, or ``None`` on failure."""
    if not path or not os.path.exists(path):
        return None
    try:
        img = cv2.imread(os.path.abspath(path), cv2.IMREAD_GRAYSCALE)
        return img
    except Exception:
        return None


def analyze_screenshot_health(
    path: str,
    blank_stddev_threshold: float = 8.0,
) -> Dict[str, Any]:
    """Detect blank / crashed / degenerate frames via histogram statistics.

    A frame is flagged ``blank_screen`` when its grayscale standard deviation
    is below ``blank_stddev_threshold`` (near-uniform color, e.g. white screen
    of death or a solid error page) and it renders fewer than
    ``_BLANK_UNIQUE_COLOR_LIMIT`` distinct colors.
    """
    result: Dict[str, Any] = {
        "path": path,
        "blank_screen": False,
        "mean": 0.0,
        "stddev": 0.0,
        "unique_colors": 0,
        "engine": "none",
        "error": None,
    }
    if not _cv_available():
        result["error"] = "opencv_unavailable"
        return result

    gray = _read_gray(path)
    if gray is None:
        result["error"] = "missing_screenshot"
        return result

    try:
        mean, stddev = cv2.meanStdDev(gray)
        result["mean"] = float(mean[0][0])
        result["stddev"] = float(stddev[0][0])
        result["unique_colors"] = int(len(np.unique(gray)))
        result["blank_screen"] = bool(
            result["stddev"] < blank_stddev_threshold
            and result["unique_colors"] < _BLANK_UNIQUE_COLOR_LIMIT
        )
        result["engine"] = "opencv-histogram"
    except Exception as exc:
        result["error"] = f"health_analysis_failed: {exc}"
    return result


def _ssim_map(a: "np.ndarray", b: "np.ndarray") -> "np.ndarray":
    """Compute the per-pixel SSIM map between two grayscale float images.

    Windowed Wang et al. (2004) formulation using Gaussian-weighted local
    statistics, matching ``gaussian_weights=True, sigma=1.5`` behavior.
    Returns a float32 map in [0, 1] where 1 means identical structure.
    """
    a_f = a.astype(np.float64)
    b_f = b.astype(np.float64)

    kernel = (11, 11)
    sigma = 1.5
    mu_a = cv2.GaussianBlur(a_f, kernel, sigma)
    mu_b = cv2.GaussianBlur(b_f, kernel, sigma)

    mu_a_sq = mu_a * mu_a
    mu_b_sq = mu_b * mu_b
    mu_ab = mu_a * mu_b

    sigma_a_sq = cv2.GaussianBlur(a_f * a_f, kernel, sigma) - mu_a_sq
    sigma_b_sq = cv2.GaussianBlur(b_f * b_f, kernel, sigma) - mu_b_sq
    sigma_ab = cv2.GaussianBlur(a_f * b_f, kernel, sigma) - mu_ab

    c1 = (0.01 * 255.0) ** 2
    c2 = (0.03 * 255.0) ** 2

    numerator = (2.0 * mu_ab + c1) * (2.0 * sigma_ab + c2)
    denominator = (mu_a_sq + mu_b_sq + c1) * (sigma_a_sq + sigma_b_sq + c2)
    denominator = np.where(denominator == 0.0, 1e-10, denominator)
    ssim = numerator / denominator
    return np.clip(ssim, 0.0, 1.0).astype(np.float32)


def compare_screenshots_cv(
    before_path: str,
    after_path: str,
    step_num: int,
    output_dir: str = "",
    ssim_threshold: float = 0.95,
) -> Dict[str, Any]:
    """Compare two screenshots with SSIM and localize changed regions.

    Returns a superset of the ``compare_screenshots_pixelmatch`` contract,
    adding ``ssim_score`` and ``diff_regions`` (bounding boxes of changed
    areas, ordered by descending area). ``diff_ratio`` is the fraction of
    pixels whose local SSIM falls below ``ssim_threshold``.
    """
    result: Dict[str, Any] = {
        "step": step_num,
        "before": before_path,
        "after": after_path,
        "engine": "none",
        "ssim_score": 1.0,
        "diff_ratio": 0.0,
        "diff_regions": [],
        "diff_image": "",
        "error": None,
    }
    if not _cv_available():
        result["error"] = "opencv_unavailable"
        return result

    before = _read_gray(before_path)
    after = _read_gray(after_path)
    if before is None or after is None:
        result["error"] = "missing_screenshot"
        return result

    diff_image_path = os.path.join(output_dir, _sanitize_filename(f"cv_diff_step_{step_num:03d}.png"))
    result["diff_image"] = diff_image_path

    try:
        if before.shape != after.shape:
            after = cv2.resize(after, (before.shape[1], before.shape[0]), interpolation=cv2.INTER_AREA)

        ssim = _ssim_map(before, after)
        result["ssim_score"] = float(ssim.mean())

        mask = (ssim < ssim_threshold).astype(np.uint8) * 255
        total = int(mask.shape[0] * mask.shape[1])
        result["diff_ratio"] = float(cv2.countNonZero(mask)) / float(max(total, 1))

        # Merge nearby diff pixels into coherent regions.
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
        merged = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        contours, _ = cv2.findContours(merged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        min_area = _MIN_REGION_AREA_RATIO * float(total)
        frame_h, frame_w = mask.shape[:2]
        regions: List[Dict[str, Any]] = []
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            area = float(w * h)
            if area < min_area:
                continue
            regions.append({
                "x": int(x),
                "y": int(y),
                "w": int(w),
                "h": int(h),
                # Normalized [ymin, xmin, ymax, xmax] matching the vision
                # annotation contract, so downstream consumers (annotation
                # skip path, reports) can use regions without image dims.
                "box_2d": [
                    round(y / frame_h, 4),
                    round(x / frame_w, 4),
                    round((y + h) / frame_h, 4),
                    round((x + w) / frame_w, 4),
                ],
                "area_ratio": area / float(total),
            })
        regions.sort(key=lambda r: r["area_ratio"], reverse=True)
        result["diff_regions"] = regions[:_MAX_REGIONS]

        # Render an annotated diff image with region boxes.
        color = cv2.cvtColor(after, cv2.COLOR_GRAY2BGR)
        for region in result["diff_regions"]:
            cv2.rectangle(
                color,
                (region["x"], region["y"]),
                (region["x"] + region["w"], region["y"] + region["h"]),
                (0, 0, 255),
                2,
            )
        if output_dir:
            cv2.imwrite(diff_image_path, color)
        else:
            result["diff_image"] = ""

        result["engine"] = "opencv-ssim"
    except Exception as exc:
        result["error"] = f"cv_compare_failed: {exc}"
    return result


def visual_phash(path: str) -> str:
    """Compute a perceptual average-hash of a screenshot as a hex string.

    The frame is downscaled to a ``_PHASH_SIZE`` x ``_PHASH_SIZE`` grayscale
    grid and each bit records whether a cell is above the grid mean. Two
    visually identical screens hash identically even when their DOM hashes
    differ (canvas apps, randomized element IDs, timestamps in attributes).
    Returns an empty string when OpenCV/numpy is unavailable or the file
    cannot be read.
    """
    if not _cv_available():
        return ""
    gray = _read_gray(path)
    if gray is None:
        return ""
    try:
        small = cv2.resize(gray, (_PHASH_SIZE, _PHASH_SIZE), interpolation=cv2.INTER_AREA)
        bits = small > float(small.mean())
        return "".join(
            f"{int(''.join('1' if b else '0' for b in row[i:i + 4]), 2):x}"
            for row in bits
            for i in range(0, _PHASH_SIZE, 4)
        )
    except Exception:
        return ""


def phash_hamming(hash_a: str, hash_b: str) -> int:
    """Hamming distance between two ``visual_phash`` hex strings.

    Returns -1 when either hash is missing or malformed so callers can treat
    the comparison as inconclusive rather than as a match or mismatch.
    """
    if not hash_a or not hash_b or len(hash_a) != len(hash_b):
        return -1
    try:
        return sum(bin(int(a, 16) ^ int(b, 16)).count("1") for a, b in zip(hash_a, hash_b))
    except ValueError:
        return -1


def _downscale(gray: "np.ndarray", scale: float) -> "np.ndarray":
    """Downscale a grayscale frame for cheaper template matching.

    INTER_AREA is the correct interpolation for shrinking: it averages pixel
    blocks, preserving region structure better than nearest-neighbor at the
    cost of a slightly softer image — irrelevant for correlation matching.
    """
    if scale >= 1.0:
        return gray
    width = max(1, int(gray.shape[1] * scale))
    height = max(1, int(gray.shape[0] * scale))
    return cv2.resize(gray, (width, height), interpolation=cv2.INTER_AREA)


def capture_chrome_templates(screenshot_path: str, scale: float = 1.0) -> Dict[str, Any]:
    """Capture baseline chrome templates (logo quadrant, nav strip) from a frame.

    Called once per worker on its first same-domain screenshot; the returned
    dict is kept in memory by the caller and passed to
    ``verify_chrome_templates`` on subsequent steps. ``scale`` downscales the
    frame before cropping and MUST match the scale used at verification time.
    Returns ``{}`` when the frame cannot be read.
    """
    if not _cv_available():
        return {}
    gray = _read_gray(screenshot_path)
    if gray is None:
        return {}
    gray = _downscale(gray, scale)
    height, width = gray.shape[:2]
    templates: Dict[str, Any] = {}
    for name, x0r, y0r, x1r, y1r in _CHROME_REGIONS:
        crop = gray[int(y0r * height):int(y1r * height), int(x0r * width):int(x1r * width)]
        if crop.size > 0:
            templates[name] = crop
    return templates


def _match_score_multiscale(frame: "np.ndarray", template: "np.ndarray") -> float:
    """Best TM_CCOEFF_NORMED score of ``template`` in ``frame`` across scales."""
    best = -1.0
    for scale in _TEMPLATE_SCALES:
        scaled = template if scale == 1.0 else cv2.resize(template, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        if scaled.shape[0] > frame.shape[0] or scaled.shape[1] > frame.shape[1]:
            continue
        result = cv2.matchTemplate(frame, scaled, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, _ = cv2.minMaxLoc(result)
        best = max(best, float(max_val))
    return best


def verify_chrome_templates(
    screenshot_path: str,
    templates: Dict[str, Any],
    min_score: float = 0.75,
    scale: float = 1.0,
) -> Dict[str, Any]:
    """Verify that baseline chrome elements are still present in a frame.

    Returns per-region scores and a ``missing`` list of regions whose best
    multi-scale match score fell below ``min_score`` — a deterministic signal
    for broken navigation, vanished branding, or an unexpected full-page state.
    ``scale`` MUST match the scale used at capture time so probe frames and
    templates live at the same resolution.
    """
    result: Dict[str, Any] = {"scores": {}, "missing": [], "engine": "none", "error": None}
    if not _cv_available() or not templates:
        result["error"] = "opencv_unavailable" if not _cv_available() else "no_templates"
        return result
    gray = _read_gray(screenshot_path)
    if gray is None:
        result["error"] = "missing_screenshot"
        return result
    gray = _downscale(gray, scale)
    try:
        for name, template in templates.items():
            if template.shape[0] > gray.shape[0] or template.shape[1] > gray.shape[1]:
                # Downscaling shrank the frame below the template size — the
                # region cannot be verified at this scale, skip it.
                continue
            score = _match_score_multiscale(gray, template)
            result["scores"][name] = round(score, 4)
            if score < min_score:
                result["missing"].append(name)
        result["engine"] = "opencv-template"
    except Exception as exc:
        result["error"] = f"template_verify_failed: {exc}"
    return result


def ocr_error_text(screenshot_path: str, max_chars: int = 500) -> Dict[str, Any]:
    """Extract error-relevant visible text from a screenshot via OCR.

    Uses RapidOCR (ONNX runtime, no system tesseract dependency). Intended for
    content the DOM cannot reach: canvas-rendered apps, iframes, PDF viewers,
    native error pages. Only lines containing error keywords are returned, so
    the payload stays small enough for defect reports and LLM prompts.
    """
    result: Dict[str, Any] = {"text": "", "lines": [], "engine": "none", "error": None}
    if RapidOCR is None:
        result["error"] = "rapidocr_unavailable"
        return result
    if not screenshot_path or not os.path.exists(screenshot_path):
        result["error"] = "missing_screenshot"
        return result
    try:
        engine = RapidOCR()
        ocr_result, _ = engine(os.path.abspath(screenshot_path))
        if not ocr_result:
            result["engine"] = "rapidocr"
            return result
        matched: List[str] = []
        for entry in ocr_result:
            text = str(entry[1]) if isinstance(entry, (list, tuple)) and len(entry) > 1 else str(entry)
            if any(keyword in text.lower() for keyword in _OCR_ERROR_KEYWORDS):
                matched.append(text.strip())
        joined = "\n".join(matched)[:max_chars]
        result["lines"] = matched[:20]
        result["text"] = joined
        result["engine"] = "rapidocr"
    except Exception as exc:
        result["error"] = f"ocr_failed: {exc}"
    return result
