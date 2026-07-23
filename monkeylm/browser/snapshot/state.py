from __future__ import annotations

import hashlib
import os
import re
from typing import Any, Dict

from playwright.async_api import Page

from .dom import capture_dom_and_layout
from monkeylm.types import PageSnapshot, FormControlRecord, FormRecord


def _normalize_form_control_raw(raw_control: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(raw_control)
    for key in ("minlength", "maxlength"):
        val = normalized.get(key)
        if val is None or val == -1:
            normalized[key] = None
    if "options" not in normalized or not isinstance(normalized.get("options"), list):
        normalized["options"] = []
    return normalized


def _sanitize_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name)[:120]


async def get_page_state(page: Page, step_num: int, phase: str = "before", output_dir: str = "") -> PageSnapshot:
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=5000)
    except Exception:
        pass

    try:
        raw = await capture_dom_and_layout(page)
    except Exception as exc:
        if "Execution context was destroyed" in str(exc):
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
                raw = await capture_dom_and_layout(page)
            except Exception:
                raw = {
                    "url": page.url,
                    "title": "Loading",
                    "elements": [],
                    "structure": "",
                    "layoutAnchors": {},
                    "modalCount": 0,
                    "spinnerCount": 0,
                    "disabledControls": 0,
                }
        else:
            raise

    elements = raw.get("elements", [])
    structure = raw.get("structure", "")
    dom_fingerprint_source = "|".join(elements)
    dom_hash = hashlib.sha256(dom_fingerprint_source.encode("utf-8")).hexdigest()
    structure_hash = hashlib.sha256(structure.encode("utf-8")).hexdigest()

    screenshot_name = _sanitize_filename(f"step_{step_num:03d}_{phase}.png")
    screenshot_path = os.path.join(output_dir, screenshot_name)
    try:
        await page.screenshot(path=screenshot_path, full_page=True)
    except Exception:
        screenshot_path = ""

    return PageSnapshot(
        url=raw.get("url", page.url),
        title=raw.get("title", ""),
        dom_hash=dom_hash,
        structure_hash=structure_hash,
        elements=elements,
        layout_anchors=raw.get("layoutAnchors", {}),
        modal_count=raw.get("modalCount", 0),
        spinner_count=raw.get("spinnerCount", 0),
        disabled_controls=raw.get("disabledControls", 0),
        screenshot_path=screenshot_path,
        timestamp=__import__("time").time(),
        form_controls=[FormControlRecord(**_normalize_form_control_raw(fc)) for fc in raw.get("formControls", [])],
        forms=[FormRecord(**f) for f in raw.get("forms", [])],
    )


def state_to_prompt(snapshot: PageSnapshot) -> str:
    lines = [
        f"URL: {snapshot.url}",
        f"Title: {snapshot.title}",
        f"DOMHash: {snapshot.dom_hash}",
        f"Modals: {snapshot.modal_count}",
        f"Spinners: {snapshot.spinner_count}",
        f"DisabledControls: {snapshot.disabled_controls}",
        "Elements:",
    ]
    lines.extend(snapshot.elements)

    if snapshot.forms:
        lines.append("\nForms:")
        for form in snapshot.forms:
            control_summaries = []
            for fc in snapshot.form_controls:
                if fc.control_id in form.control_ids:
                    attrs = []
                    if fc.required:
                        attrs.append("required")
                    if fc.input_type:
                        attrs.append(f"type={fc.input_type}")
                    if fc.minlength is not None:
                        attrs.append(f"minlength={fc.minlength}")
                    if fc.maxlength is not None:
                        attrs.append(f"maxlength={fc.maxlength}")
                    if fc.pattern:
                        attrs.append(f"pattern={fc.pattern}")
                    if fc.min_value:
                        attrs.append(f"min={fc.min_value}")
                    if fc.max_value:
                        attrs.append(f"max={fc.max_value}")
                    if fc.tag_name == "select" and fc.options:
                        preview = ",".join(fc.options[:10])
                        attrs.append(f"options=[{preview}]")
                    attr_str = ",".join(attrs)
                    label_part = f' label="{fc.resolved_label}"' if fc.resolved_label else ""
                    control_summaries.append(f"  [id={fc.control_id}] {fc.tag_name}{attr_str}{label_part}")
            lines.append(f"- form_id={form.form_id} method={form.method} controls={len(form.control_ids)}")
            lines.extend(control_summaries)
            if form.submit_candidate_id is not None:
                lines.append(f"  submit_candidate=[id={form.submit_candidate_id}]")

    return "\n".join(lines)
