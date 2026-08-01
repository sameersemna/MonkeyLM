from __future__ import annotations

import hashlib
import os
import re
import time
from typing import Any, Dict

try:
    from playwright.async_api import Page
except Exception:  # pragma: no cover - optional dependency path
    Page = Any  # type: ignore[misc]

from .dom import capture_dom_and_layout
from monkeylm.types import PageSnapshot, FormControlRecord, FormRecord

EMPTY_CONTENT_HASH = hashlib.sha256(b"").hexdigest()


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


async def _wait_for_hydration(page: Page, timeout_ms: int = 3000, poll_ms: int = 200) -> None:
    """Wait for client-rendered content to actually appear in the DOM.

    `domcontentloaded` fires as soon as the initial HTML document (and any
    deferred scripts) finish parsing — for a client-side-rendered app (React,
    Vue, Expo/React Native Web, etc.) that's often before the JS bundle has
    fetched, executed, and mounted anything. Capturing page state at that
    point sees an essentially empty `<div id="root">` and produces an empty
    (hash-of-empty-string) snapshot even though the app is working fine; it
    just hasn't rendered yet. Poll briefly for real body content before
    giving the capture a chance to run.
    """
    # A couple of stray characters of boilerplate/placeholder text (e.g. a
    # "Loading..." splash) can satisfy a naive "innerText is non-empty" check
    # before the app has actually mounted anything interactive. Poll on DOM
    # size stabilizing instead — client-rendered apps keep mutating the DOM
    # while they mount; once two consecutive samples agree, rendering has
    # settled (or there was never anything to render).
    deadline = time.monotonic() + (timeout_ms / 1000)
    last_child_count = -1
    while time.monotonic() < deadline:
        try:
            child_count = await page.evaluate(
                "() => document.body ? document.body.querySelectorAll('*').length : 0"
            )
        except Exception:
            return
        if child_count > 0 and child_count == last_child_count:
            return
        last_child_count = child_count
        await page.wait_for_timeout(poll_ms)


async def get_page_state(page: Page, step_num: int, phase: str = "before", output_dir: str = "") -> PageSnapshot:
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=5000)
    except Exception:
        pass

    await _wait_for_hydration(page)

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

    # A hash of the empty string means no elements were captured at all. That's
    # either a genuinely blank page or a capture bug (stale/detached frame,
    # navigation mid-capture, a selector that doesn't match this app's markup),
    # and must not be treated as legitimate "unchanged" page state by the
    # freeze/collapse detectors downstream.
    is_empty_capture = dom_hash == EMPTY_CONTENT_HASH and structure_hash == EMPTY_CONTENT_HASH

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
        timestamp=time.time(),
        form_controls=[FormControlRecord(**_normalize_form_control_raw(fc)) for fc in raw.get("formControls", [])],
        forms=[FormRecord(**f) for f in raw.get("forms", [])],
        is_empty_capture=is_empty_capture,
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
