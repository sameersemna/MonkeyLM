from __future__ import annotations

import os
import subprocess
from typing import Any, Dict

from monkeylm.config import (
    LAYOUT_SHIFT_THRESHOLD_PX,
    VISUAL_DIFF_THRESHOLD_RATIO,
    Image,
    PIL_Image,
    pil_pixelmatch,
    _local_service_log,
)
from .state import _sanitize_filename
from monkeylm.types import PageSnapshot


def compute_max_layout_shift(before: PageSnapshot, after: PageSnapshot) -> float:
    max_shift = 0.0
    common_keys = set(before.layout_anchors.keys()) & set(after.layout_anchors.keys())
    for key in common_keys:
        b = before.layout_anchors[key]
        a = after.layout_anchors[key]
        shift = max(abs(a["x"] - b["x"]), abs(a["y"] - b["y"]))
        max_shift = max(max_shift, shift)
    return max_shift


def compare_screenshots_pixelmatch(
    before_path: str, after_path: str, step_num: int, output_dir: str = ""
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "step": step_num,
        "before": before_path,
        "after": after_path,
        "diff_pixels": 0,
        "diff_ratio": 0.0,
        "engine": "none",
        "diff_image": "",
        "error": None,
    }
    if not before_path or not after_path or not os.path.exists(before_path) or not os.path.exists(after_path):
        result["error"] = "missing_screenshot"
        return result

    diff_image_path = os.path.join(output_dir, _sanitize_filename(f"visual_diff_step_{step_num:03d}.png"))
    result["diff_image"] = diff_image_path

    if pil_pixelmatch and Image and PIL_Image:
        try:
            before_img = PIL_Image.open(before_path).convert("RGBA")
            after_img = PIL_Image.open(after_path).convert("RGBA")
            if before_img.size != after_img.size:
                after_img = after_img.resize(before_img.size)
            diff_img = Image.new("RGBA", before_img.size)
            mismatch = pil_pixelmatch(before_img, after_img, diff_img, threshold=0.1)
            total = before_img.size[0] * before_img.size[1]
            result["diff_pixels"] = int(mismatch)
            result["diff_ratio"] = float(mismatch) / float(total)
            result["engine"] = "python-pixelmatch"
            diff_img.save(diff_image_path)
            return result
        except Exception as exc:
            result["error"] = f"python_pixelmatch_failed: {exc}"

    safe_before = os.path.abspath(before_path)
    safe_after = os.path.abspath(after_path)
    safe_diff = os.path.abspath(diff_image_path)

    try:
        node_script = (
            "const fs=require('fs');"
            "const {PNG}=require('pngjs');"
            "const pixelmatch=require('pixelmatch');"
            "const a=PNG.sync.read(fs.readFileSync(process.argv[1]));"
            "const b=PNG.sync.read(fs.readFileSync(process.argv[2]));"
            "const w=Math.min(a.width,b.width),h=Math.min(a.height,b.height);"
            "const out=new PNG({width:w,height:h});"
            "const m=pixelmatch(a.data,b.data,out.data,w,h,{threshold:0.1});"
            "fs.writeFileSync(process.argv[3],PNG.sync.write(out));"
            "console.log(JSON.stringify({mismatch:m,total:w*h}));"
        )
        completed = subprocess.run(
            ["node", "-e", node_script, safe_before, safe_after, safe_diff],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
        data = __import__("json").loads(completed.stdout.strip())
        result["diff_pixels"] = int(data.get("mismatch", 0))
        total = int(data.get("total", 1))
        result["diff_ratio"] = float(result["diff_pixels"]) / float(max(total, 1))
        result["engine"] = "node-pixelmatch"
    except Exception as exc:
        result["error"] = f"node_pixelmatch_failed: {exc}"
    return result
