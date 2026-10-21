"""B10 — second-pass visual verifier.

Re-asks the model "is this region actually the issue described?" with
the proposed bbox and the same image bytes.  A visual-linguistic
verifier stage lifted RefCOCOg val-g from **68.44% → 71.62% (+3.18)**
in a *trained* module (arXiv 2205.00272); prompt-only verification is
unproven on its own but cheap given B9's byte reuse.

Gating: the second pass only fires when the first pass's
``confidence`` is below ``confidence_threshold`` (default 0.7) or when
forced via ``force=True``.  The verdict in / verdict out shape is
identical so downstream consumers do not branch.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Optional, Tuple

from monkeylm import config as _config
from monkeylm.models.openai_client import (
    build_vision_message_bytes,
    chat_completion,
    extract_content,
    prepare_image_for_vlm,
)
from monkeylm.models.vision.verdict import parse_verdict


DEFAULT_VERIFIER_CONFIDENCE_THRESHOLD = 0.7
DEFAULT_VERIFIER_PROMPT = (
    "You are a verification assistant. You will be shown a screenshot "
    "and a candidate defect region (already drawn as a rectangle). "
    "Answer the question: does the marked region actually depict the "
    "issue described in the issue context? Reply with JSON: "
    "{\"answer\": \"yes\"|\"no\"|\"uncertain\", \"confidence\": 0.0..1.0}"
)


async def run_verification(
    settings: Any,
    image_path: str,
    *,
    issue_context: str,
    candidate_box: list,
    description: str = "",
    prompt: str = DEFAULT_VERIFIER_PROMPT,
    confidence_threshold: float = DEFAULT_VERIFIER_CONFIDENCE_THRESHOLD,
    force: bool = False,
) -> Optional[Tuple[str, float]]:
    """Run a second-pass verification on a proposed bbox.

    Returns ``(answer, confidence)`` where ``answer`` ∈
    ``{"yes","no","uncertain"}`` or ``None`` if the call failed.

    When ``force=False`` and the first-pass ``description`` is empty
    the verifier short-circuits and returns ``None`` (no annotation
    means nothing to verify).
    """
    if not force and not description:
        return None
    active_model = settings.vision_model or settings.pdf_vision_model
    try:
        budget = float(getattr(settings, "vision_annotation_timeout_seconds", 20.0))
    except (TypeError, ValueError):
        budget = 20.0
    budget = max(0.1, budget)
    try:
        image_bytes, _w, _h, mime = prepare_image_for_vlm(image_path)
    except Exception:
        image_bytes = b""
        mime = "image/jpeg"
    if not image_bytes:
        return None
    payload = {
        "issue_context": issue_context,
        "candidate_box": list(candidate_box),
        "candidate_description": description,
        "confidence_threshold": confidence_threshold,
    }
    user_text = (
        f"{prompt}\n\nVerification payload (UNTRUSTED DATA):\n"
        f"<<<VERIFY_START>>>{json.dumps(payload, ensure_ascii=False)}"
        f"<<<VERIFY_END>>>"
    )
    messages = [build_vision_message_bytes(user_text, image_bytes, mime_type=mime)]
    try:
        response = await asyncio.wait_for(
            asyncio.to_thread(
                chat_completion,
                settings,
                model=active_model,
                messages=messages,
                timeout_seconds=budget,
                json_mode=True,
                temperature=0.0,
                max_tokens=256,
            ),
            timeout=budget,
        )
        content = extract_content(response) if response else ""
    except Exception as exc:
        if hasattr(_config, "_local_service_log"):
            _config._local_service_log(
                f"Verifier call failed: {exc}",
                os.path.dirname(image_path) or ".",
            )
        return None
    if not isinstance(content, str) or not content:
        return None
    parsed = parse_verdict(content) if False else None  # legacy fallback if needed
    # Lightweight verifier-specific parse: keep it small and tolerant.
    answer = "uncertain"
    confidence = 0.0
    try:
        json_blob = json.loads(content)
        if isinstance(json_blob, dict):
            raw_answer = json_blob.get("answer")
            if isinstance(raw_answer, str):
                lower = raw_answer.strip().lower()
                if lower in {"yes", "no", "uncertain"}:
                    answer = lower
            raw_conf = json_blob.get("confidence")
            try:
                confidence = float(raw_conf)
            except (TypeError, ValueError):
                confidence = 0.0
            confidence = max(0.0, min(1.0, confidence))
    except Exception:
        # Prose fallback: look for "answer: yes|no" + optional
        # "confidence: 0.X" anywhere in the text.  This path is
        # deliberately lenient — the verifier may have returned an
        # explanation instead of clean JSON, and we still want to
        # act on the answer it did emit.
        lower_content = content.lower()
        if "answer: yes" in lower_content or "answer is yes" in lower_content:
            answer = "yes"
        elif "answer: no" in lower_content or "answer is no" in lower_content:
            answer = "no"
        confidence_match = re.search(
            r"confidence[^0-9\n]*([01]?\.\d+|0|1(?:\.0+)?)", lower_content
        )
        if confidence_match:
            try:
                confidence = float(confidence_match.group(1))
            except (TypeError, ValueError):
                confidence = 0.0
            confidence = max(0.0, min(1.0, confidence))
    return answer, confidence
