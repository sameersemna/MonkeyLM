"""Playwright browser lifecycle, DOM snapshots, visual diffing, and action execution."""

from __future__ import annotations

import asyncio
import hashlib
import os
import random
import re
import subprocess
from typing import Any, Dict, List, Optional, Tuple

from playwright.async_api import Dialog, Page

from monkeylm.config import (
    ACTION_COOLDOWN_SECONDS,
    AXE_CDN_URL,
    LAYOUT_SHIFT_THRESHOLD_PX,
    PageSnapshot,
    FormControlRecord,
    FormRecord,
    VISUAL_DIFF_THRESHOLD_RATIO,
    Image,
    ImageDraw,
    pil_pixelmatch,
    _local_service_log,
)


# ── DOM extraction & layout anchors ───────────────────────────────────────────


async def capture_dom_and_layout(page: Page) -> Dict[str, Any]:
    """Inject JS to extract interactive elements, layout anchors, form controls, and forms."""
    return await page.evaluate(
        """() => {
            const collectText = (el) => {
                let txt = el.innerText?.trim()
                    || el.getAttribute('aria-label')
                    || el.getAttribute('name')
                    || el.placeholder
                    || el.getAttribute('title')
                    || el.value
                    || '';
                if (txt.length > 80) txt = txt.slice(0, 80) + '...';
                return txt;
            };

            const normalizeAttr = (el, name) => {
                const v = el.getAttribute(name);
                return (v === null || v === undefined) ? '' : String(v).trim();
            };

            const isVisible = (el) => {
                const rect = el.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
            };

            const resolveLabel = (el) => {
                let labelText = '';
                let confidence = 0.0;

                if (el.id) {
                    const explicitLabel = document.querySelector(`label[for="${el.id}"]`);
                    if (explicitLabel) {
                        labelText = explicitLabel.innerText?.trim() || '';
                        confidence = 1.0;
                    }
                }

                if (!labelText) {
                    const labelledBy = el.getAttribute('aria-labelledby');
                    if (labelledBy) {
                        const refs = labelledBy.split(/\\s+/).map(id => document.getElementById(id)).filter(Boolean);
                        if (refs.length > 0) {
                            labelText = refs.map(ref => ref.innerText?.trim() || ref.getAttribute('aria-label') || '').join(' ').trim();
                            confidence = 0.95;
                        }
                    }
                }

                if (!labelText) {
                    let ancestor = el.parentElement;
                    while (ancestor && ancestor.tagName !== 'LABEL' && ancestor.tagName !== 'FORM') {
                        ancestor = ancestor.parentElement;
                    }
                    if (ancestor && ancestor.tagName === 'LABEL') {
                        labelText = ancestor.innerText?.trim() || '';
                        confidence = 0.9;
                    }
                }

                if (!labelText) {
                    const prev = el.previousElementSibling;
                    if (prev && /^(label|span|div|p)$/i.test(prev.tagName)) {
                        const txt = prev.innerText?.trim() || '';
                        if (txt.length > 0 && txt.length < 120) {
                            labelText = txt;
                            confidence = 0.7;
                        }
                    }
                }

                if (!labelText && el.placeholder) {
                    labelText = el.placeholder.trim();
                    confidence = 0.5;
                }

                if (!labelText) {
                    const token = el.getAttribute('name') || el.id || '';
                    if (token) {
                        labelText = token.replace(/[_-]+/g, ' ').replace(/([a-z])([A-Z])/g, '$1 $2').trim();
                        confidence = 0.4;
                    }
                }

                if (labelText.length > 80) labelText = labelText.slice(0, 80) + '...';
                return { text: labelText, confidence };
            };

            const computeSemanticKind = (el) => {
                const tag = el.tagName.toLowerCase();
                if (tag === 'select') return 'select';
                if (tag === 'textarea') return 'textarea';
                if (tag === 'input') {
                    const type = (el.type || 'text').toLowerCase();
                    if (type === 'email') return 'email';
                    if (type === 'password') return 'password';
                    if (type === 'tel') return 'phone';
                    if (type === 'number' || type === 'range') return 'numeric';
                    if (type === 'search') return 'search';
                    if (type === 'url') return 'url';
                    if (type === 'date' || type === 'datetime-local' || type === 'time') return 'datetime';
                    if (type === 'checkbox') return 'checkbox';
                    if (type === 'radio') return 'radio';
                    if (type === 'file') return 'file';
                    if (type === 'hidden') return 'hidden';
                    return 'text';
                }
                return 'generic';
            };

            const interactives = Array.from(document.querySelectorAll(
                'button, a, input, select, textarea, [role="button"], [onclick], form'
            ));
            const tags = [];
            const anchors = {};
            let visibleIndex = 0;

            const elementIdMap = new Map();
            interactives.forEach((el) => {
                if (!isVisible(el)) return;
                elementIdMap.set(el, visibleIndex);
                const itemId = visibleIndex;
                visibleIndex += 1;
                const text = collectText(el);
                let typeInfo = el.tagName;
                if (el.tagName === 'INPUT') typeInfo = `INPUT[type=${el.type}]`;
                tags.push(`[id=${itemId}] <${typeInfo} text="${text}" />`);

                const idPart = el.id ? `#${el.id}` : '';
                const clsPart = (el.className && typeof el.className === 'string')
                    ? '.' + el.className.split(/\\s+/).slice(0, 2).join('.')
                    : '';
                const key = `${itemId}::${el.tagName}${idPart}${clsPart}::${text.slice(0, 20)}`;
                const rect = el.getBoundingClientRect();
                anchors[key] = { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
            });

            const formControls = [];
            const formInputs = Array.from(document.querySelectorAll('input, select, textarea'));
            formInputs.forEach((el) => {
                if (!isVisible(el)) return;
                const controlId = elementIdMap.get(el);
                if (controlId === undefined) return;

                const labelInfo = resolveLabel(el);
                const semanticKind = computeSemanticKind(el);
                const formEl = el.closest('form');
                const formId = formEl ? (formEl.id || `form_${Array.from(document.querySelectorAll('form')).indexOf(formEl)}`) : null;

                const optionValues = (el.tagName.toLowerCase() === 'select')
                    ? Array.from(el.querySelectorAll('option')).map(o => o.value || o.textContent.trim()).filter(v => v)
                    : [];

                formControls.push({
                    control_id: controlId,
                    form_id: formId,
                    tag_name: el.tagName.toLowerCase(),
                    input_type: el.type ? String(el.type).toLowerCase() : '',
                    name_attr: normalizeAttr(el, 'name'),
                    id_attr: normalizeAttr(el, 'id'),
                    placeholder: normalizeAttr(el, 'placeholder'),
                    aria_label: normalizeAttr(el, 'aria-label'),
                    aria_labelledby: normalizeAttr(el, 'aria-labelledby'),
                    required: el.required === true,
                    disabled: el.disabled === true,
                    readonly: el.readOnly === true,
                    minlength: el.minLength ? parseInt(el.minLength, 10) : null,
                    maxlength: el.maxLength ? parseInt(el.maxLength, 10) : null,
                    pattern: normalizeAttr(el, 'pattern'),
                    min_value: normalizeAttr(el, 'min'),
                    max_value: normalizeAttr(el, 'max'),
                    step: normalizeAttr(el, 'step'),
                    resolved_label: labelInfo.text,
                    label_confidence: labelInfo.confidence,
                    semantic_kind: semanticKind,
                    visible: true,
                    options: optionValues,
                });
            });

            const forms = [];
            const allForms = Array.from(document.querySelectorAll('form'));
            allForms.forEach((formEl, idx) => {
                if (!isVisible(formEl)) return;
                const fid = formEl.id || `form_${idx}`;
                const controlIds = formControls
                    .filter(fc => fc.form_id === fid)
                    .map(fc => fc.control_id);

                let submitCandidateId = null;
                const submitBtn = formEl.querySelector('button[type="submit"], input[type="submit"]');
                if (submitBtn && isVisible(submitBtn)) {
                    submitCandidateId = elementIdMap.get(submitBtn);
                }

                forms.push({
                    form_id: fid,
                    action: normalizeAttr(formEl, 'action'),
                    method: normalizeAttr(formEl, 'method') || 'get',
                    control_ids: controlIds,
                    submit_candidate_id: submitCandidateId,
                });
            });

            const looseControls = formControls.filter(fc => fc.form_id === null);
            if (looseControls.length > 0) {
                forms.push({
                    form_id: 'loose_controls',
                    action: '',
                    method: '',
                    control_ids: looseControls.map(fc => fc.control_id),
                    submit_candidate_id: null,
                });
            }

            const modals = Array.from(document.querySelectorAll('[role="dialog"], .modal, .popup, .alert'))
                .filter(el => isVisible(el));

            const spinnerSel = '[aria-busy="true"], .spinner, .loading, [data-testid*="spinner" i]';
            const spinnerCount = document.querySelectorAll(spinnerSel).length;
            const disabledControls = document.querySelectorAll(
                'button:disabled, input:disabled, select:disabled, textarea:disabled'
            ).length;

            const structure = tags.map(t => t.replace(/text=".*?"/, 'text=""')).join('|');

            return {
                url: window.location.href,
                title: document.title,
                elements: tags,
                structure,
                layoutAnchors: anchors,
                modalCount: modals.length,
                spinnerCount,
                disabledControls,
                formControls,
                forms,
            };
        }"""
    )


# ── Component manifest extraction (for regression comparison) ─────────────────


def _normalize_manifest_text(value: Any, max_len: int = 120) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).strip())[:max_len]


def _manifest_component_key(component: Dict[str, Any]) -> str:
    return "::".join(
        [
            _normalize_manifest_text(component.get("kind", "")).lower(),
            _normalize_manifest_text(component.get("tag", "")).lower(),
            _normalize_manifest_text(component.get("text", "")).lower(),
            _normalize_manifest_text(component.get("selector_hint", "")).lower(),
        ]
    )


def diff_component_manifests(
    golden_manifest: List[Dict[str, Any]], current_manifest: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], List[str]]:
    current_keys = {_manifest_component_key(component) for component in current_manifest}
    missing_components: List[Dict[str, Any]] = []
    for component in golden_manifest:
        if _manifest_component_key(component) not in current_keys:
            missing_components.append(component)

    broken_selectors = sorted(
        {
            _normalize_manifest_text(component.get("selector_hint", ""))
            for component in missing_components
            if _normalize_manifest_text(component.get("selector_hint", ""))
        }
    )
    return missing_components, broken_selectors


async def extract_component_manifest(page: Page) -> List[Dict[str, Any]]:
    try:
        manifest = await page.evaluate(
            """() => {
                const normalizeText = (value) => {
                    const text = String(value || '').replace(/\\s+/g, ' ').trim();
                    return text.slice(0, 120);
                };

                const selectorHint = (el) => {
                    if (!el) return '';
                    if (el.id) return `#${el.id}`;
                    const dataTestId = el.getAttribute('data-testid') || el.getAttribute('data-test-id');
                    if (dataTestId) return `[data-testid="${dataTestId}"]`;
                    const name = el.getAttribute('name');
                    if (name) return `${el.tagName.toLowerCase()}[name="${name}"]`;
                    const classes = (el.className && typeof el.className === 'string')
                        ? el.className.trim().split(/\\s+/).slice(0, 2).join('.')
                        : '';
                    return classes ? `${el.tagName.toLowerCase()}.${classes}` : el.tagName.toLowerCase();
                };

                const isVisible = (el) => {
                    if (!el) return false;
                    const rect = el.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                };

                const result = [];
                const pushComponent = (kind, el, textValue) => {
                    if (!isVisible(el)) return;
                    result.push({
                        kind,
                        tag: el.tagName,
                        text: normalizeText(textValue),
                        selector_hint: normalizeText(selectorHint(el)),
                    });
                };

                const buttonLike = document.querySelectorAll('button, [role="button"], input[type="button"], input[type="submit"], a');
                buttonLike.forEach((el) => {
                    const text = el.innerText || el.getAttribute('aria-label') || el.getAttribute('value') || '';
                    pushComponent('button', el, text);
                });

                const forms = document.querySelectorAll('form');
                forms.forEach((el) => {
                    const text = el.getAttribute('name') || el.getAttribute('id') || '';
                    pushComponent('form', el, text);
                });

                const textNodes = document.querySelectorAll('h1, h2, h3, h4, h5, h6, p, label, li, span');
                textNodes.forEach((el) => {
                    const text = normalizeText(el.innerText || el.textContent || '');
                    if (text.length < 2) return;
                    pushComponent('text', el, text);
                });

                return result.slice(0, 1500);
            }"""
        )
    except Exception as exc:
        _local_service_log(f"Failed to extract component manifest: {exc}")
        return []

    if isinstance(manifest, list):
        sanitized: List[Dict[str, Any]] = []
        for item in manifest:
            if not isinstance(item, dict):
                continue
            sanitized.append(
                {
                    "kind": _normalize_manifest_text(item.get("kind", ""), max_len=30),
                    "tag": _normalize_manifest_text(item.get("tag", ""), max_len=30),
                    "text": _normalize_manifest_text(item.get("text", ""), max_len=120),
                    "selector_hint": _normalize_manifest_text(item.get("selector_hint", ""), max_len=160),
                }
            )
        return sanitized
    return []


# ── Page state capture ────────────────────────────────────────────────────────


def _normalize_form_control_raw(raw_control: Dict[str, Any]) -> Dict[str, Any]:
    """Convert browser default sentinel values into clean Python semantics."""
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
    """Collects DOM state plus screenshot; resilient to transient navigation context resets."""
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


def compute_max_layout_shift(before: PageSnapshot, after: PageSnapshot) -> float:
    max_shift = 0.0
    common_keys = set(before.layout_anchors.keys()) & set(after.layout_anchors.keys())
    for key in common_keys:
        b = before.layout_anchors[key]
        a = after.layout_anchors[key]
        shift = max(abs(a["x"] - b["x"]), abs(a["y"] - b["y"]))
        max_shift = max(max_shift, shift)
    return max_shift


# ── Visual diffing ────────────────────────────────────────────────────────────


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

    if pil_pixelmatch and Image:
        try:
            before_img = Image.open(before_path).convert("RGBA")
            after_img = Image.open(after_path).convert("RGBA")
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
            ["node", "-e", node_script, before_path, after_path, diff_image_path],
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


# ── Browser launch & page readiness ───────────────────────────────────────────


async def wait_for_page_ready(page: Page, phase: str, strict: bool = False) -> None:
    """Robust page readiness wait with networkidle → domcontentloaded → load fallback."""
    try:
        await page.wait_for_load_state("networkidle", timeout=8000)
        return
    except Exception:
        pass

    try:
        await page.wait_for_load_state("domcontentloaded", timeout=10000)
        return
    except Exception:
        pass

    try:
        await page.wait_for_load_state("load", timeout=12000)
        return
    except Exception as exc:
        msg = f"⚠️ Readiness fallback failed during {phase}: {exc}"
        if strict:
            raise RuntimeError(msg) from exc
        print(msg)


async def launch_context_with_fallback(
    playwright_instance: Any,
    *,
    settings: Any,
    user_data_dir: str,
    worker_label: str,
) -> Tuple[Any, Dict[str, Any]]:
    """Launch Chromium with sandbox first; no-sandbox fallback if explicitly allowed."""
    base_args = [f"--window-size={settings.browser_window_size}", "--disable-blink-features=AutomationControlled"]
    sandbox_args = list(base_args)
    no_sandbox_args = base_args + ["--no-sandbox", "--disable-setuid-sandbox"]

    try:
        context = await playwright_instance.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=settings.headless,
            args=sandbox_args,
            no_viewport=settings.no_viewport,
        )
        launch_info = {
            "worker": worker_label,
            "mode": "sandbox",
            "args": sandbox_args,
            "error": None,
            "window_size": settings.browser_window_size,
            "no_viewport": settings.no_viewport,
            "headless": settings.headless,
            "user_data_dir": user_data_dir,
        }
        print(f"🛡️ Browser launch mode [{worker_label}]: sandbox")
        return context, launch_info
    except Exception as sandbox_exc:
        if settings.strict_sandbox or not settings.allow_no_sandbox_fallback:
            mode = "sandbox-required-failed" if settings.strict_sandbox else "sandbox-failed-no-fallback"
            launch_info = {
                "worker": worker_label,
                "mode": mode,
                "args": sandbox_args,
                "error": str(sandbox_exc),
                "window_size": settings.browser_window_size,
                "no_viewport": settings.no_viewport,
                "headless": settings.headless,
                "strict_sandbox": settings.strict_sandbox,
                "allow_no_sandbox_fallback": settings.allow_no_sandbox_fallback,
                "user_data_dir": user_data_dir,
            }
            _local_service_log(
                f"Browser launch failed [{worker_label}] with mode={mode}: {sandbox_exc}",
                settings.output_dir,
            )
            policy_hint = (
                "STRICT_SANDBOX is enabled" if settings.strict_sandbox else "ALLOW_NO_SANDBOX_FALLBACK is disabled"
            )
            raise RuntimeError(
                "Sandbox launch failed and no-sandbox fallback is blocked "
                f"({policy_hint}). Set ALLOW_NO_SANDBOX_FALLBACK=true if you want to permit fallback."
            ) from sandbox_exc

        print(f"⚠️ Sandbox launch failed, retrying with no-sandbox: {sandbox_exc}")
        context = await playwright_instance.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=settings.headless,
            args=no_sandbox_args,
            no_viewport=settings.no_viewport,
        )
        launch_info = {
            "worker": worker_label,
            "mode": "no-sandbox-fallback",
            "args": no_sandbox_args,
            "error": str(sandbox_exc),
            "window_size": settings.browser_window_size,
            "no_viewport": settings.no_viewport,
            "headless": settings.headless,
            "strict_sandbox": settings.strict_sandbox,
            "allow_no_sandbox_fallback": settings.allow_no_sandbox_fallback,
            "user_data_dir": user_data_dir,
        }
        print(f"🔓 Browser launch mode [{worker_label}]: no-sandbox-fallback")
        return context, launch_info


# ── Element interaction helpers ───────────────────────────────────────────────


def _extract_target_id(target: Any) -> Optional[int]:
    if isinstance(target, int):
        return target if target >= 0 else None
    target_str = str(target or "").strip()
    if not target_str:
        return None

    if target_str.isdigit():
        return int(target_str)

    match = re.search(r"\[id\s*=\s*(\d+)\]", target_str, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


async def _locator_for_target_id(page: Page, target_id: Any) -> Optional[Any]:
    parsed_id = _extract_target_id(target_id)
    if parsed_id is None:
        return None

    selector = "button, a, input, select, textarea, [role='button'], [onclick], form"
    candidates = page.locator(selector)
    count = await candidates.count()
    visible_index = 0
    for idx in range(count):
        candidate = candidates.nth(idx)
        bbox = await candidate.bounding_box()
        if not bbox:
            continue
        if bbox.get("width", 0) <= 0 or bbox.get("height", 0) <= 0:
            continue
        if visible_index == parsed_id:
            return candidate
        visible_index += 1
    return None


async def _resolve_interaction_mode(locator: Any) -> str:
    """Inspect the resolved element and return the safe interaction mode."""
    if locator is None:
        return "unsupported"
    try:
        tag_name = await locator.evaluate("el => el.tagName.toLowerCase()")
        input_type = await locator.evaluate("el => (el.type || '').toLowerCase()")
    except Exception:
        return "unsupported"

    if tag_name == "select":
        return "select"
    if tag_name == "textarea":
        return "text_input"
    if tag_name == "input":
        if input_type in {"checkbox", "radio"}:
            return "checkbox_radio"
        if input_type in {"file", "hidden", "submit", "button", "image", "reset"}:
            return "unsupported"
        return "text_input"
    return "unsupported"


async def _fill_select_option(
    page: Page, locator: Any, payload_value: str, control_options: List[str], strategy: str
) -> Tuple[str, str]:
    """Safely mutate a <select> element using Playwright's select_option."""
    if payload_value and payload_value in control_options:
        await locator.select_option(value=payload_value)
        return payload_value, "select_model_provided_option"

    if control_options:
        if strategy == "EDGE_CASE_FUZZ":
            invalid_value = "__monkeylm_invalid_option__"
            try:
                await locator.select_option(value=invalid_value)
            except Exception as exc:
                return invalid_value, f"fuzz_select_invalid_option_rejected:{type(exc).__name__}"
            return invalid_value, "fuzz_select_invalid_option_accepted"

        chosen = control_options[0]
        await locator.select_option(value=chosen)
        return chosen, "happy_select_first_option"

    return "", "select_no_options_available"


# ── Dialog handler ────────────────────────────────────────────────────────────


async def handle_dialog(dialog: Dialog) -> None:
    """Global dialog handler: randomly accept or dismiss native alerts."""
    print(f"   -> 🚨 Native Dialog Detected: {dialog.message}")
    if random.random() > 0.5:
        await dialog.accept()
        print("   -> Accepted dialog")
    else:
        await dialog.dismiss()
        print("   -> Dismissed dialog")


# ── Action execution (the big dispatcher) ─────────────────────────────────────


async def execute_action(
    page: Page,
    settings: Any,
    action_plan: Dict[str, Any],
    step_num: int,
    fuzzer: Any,  # Fuzzer instance
    defects: Any,  # DefectTracker instance
    network_monitor: Any,  # NetworkMonitor instance
    perf_monitor: Any,  # PerformanceMonitor instance
    log_sink: Optional[List[Dict[str, Any]]] = None,
    persistence_engine: Any = None,
    worker_id: int = 0,
) -> Tuple[Optional[PageSnapshot], Dict[str, Any]]:
    """Execute a single action plan on the page and return (after_snapshot, log_entry).

    All monitor classes are passed explicitly — no global reads.
    """
    from monkeylm.models import annotate_relevant_screenshot, generate_form_payload, _step_defects_summary

    action = action_plan.get("action", "scroll")
    target = action_plan.get("target", "")
    value = action_plan.get("value", "")
    action_strategy = action_plan.get("action_strategy", "")
    input_payloads = action_plan.get("input_payloads", [])
    if not isinstance(input_payloads, list):
        input_payloads = []

    before_snapshot = await get_page_state(page, step_num, phase="before", output_dir=settings.output_dir)
    perf_before = await perf_monitor.snapshot(page)

    log_entry = {
        "step": step_num,
        "action": action,
        "target": target,
        "value": value if action == "type" else None,
        "action_strategy": action_strategy,
        "input_payloads": input_payloads,
        "status": "SUCCESS",
        "error": None,
        "screenshot": None,
        "url": page.url,
    }

    # Cross-worker action-path deduplication via Redis lock
    worker_label = f"worker-{worker_id:02d}"
    if persistence_engine is not None and target.strip() and action in {"click", "type", "submit_form", "handle_modal"}:
        from monkeylm.config import split_domain_and_route

        domain, route = split_domain_and_route(page.url)
        path_hash = _compute_action_path_hash(domain, route, action, target)
        if not await persistence_engine.claim_action_path_lock(path_hash, worker_label):
            print(f"🤖 Step {step_num}: action path '{action}/{target}' already claimed by another worker; skipping.")
            action = "scroll"
            target = ""
            log_entry["action"] = action
            log_entry["target"] = target
            log_entry["status"] = "SKIPPED_PATH"

    print(f"🤖 Step {step_num}: Executing {action} on '{target}'")

    try:
        if action == "scroll":
            await page.evaluate(f"window.scrollBy(0, {random.choice([-500, 500])})")

        elif action == "back":
            history_length = await page.evaluate("() => window.history.length")
            if page.url == "about:blank" or history_length <= 2:
                await page.goto(settings.target_url, wait_until="domcontentloaded", timeout=45000)
                await wait_for_page_ready(page, "back-recovery")
            else:
                previous_page = await page.go_back(timeout=5000)
                if page.url == "about:blank" or previous_page is None:
                    await page.goto(settings.target_url, wait_until="domcontentloaded", timeout=45000)
                    await wait_for_page_ready(page, "back-recovery")

        elif action == "restart_target":
            await page.goto(settings.target_url, wait_until="domcontentloaded", timeout=45000)
            await wait_for_page_ready(page, "restart-target")

        elif action == "random_jump":
            links = page.locator("a[href]:visible")
            if await links.count() > 0:
                idx = random.randint(0, min(await links.count() - 1, 10))
                await links.nth(idx).click(timeout=3000)
            else:
                await page.evaluate("window.scrollTo(0, 0)")

        elif action == "handle_modal":
            close_btn = page.locator("button[aria-label='Close'], .close, [title='Close']").first
            if await close_btn.count() > 0:
                await close_btn.click(timeout=2000)
            else:
                cancel_btn = page.get_by_role("button", name=re.compile("cancel|close|no|dismiss", re.I)).first
                if await cancel_btn.count() > 0:
                    await cancel_btn.click(timeout=2000)
                else:
                    await page.keyboard.press("Escape")
                    print("   -> Sent Escape key to close modal")

        elif action == "submit_form":
            filled_payloads = []
            for payload in input_payloads:
                payload_target = payload.get("target", "")
                payload_value = payload.get("value", "")
                payload_reason = payload.get("reason", "")
                locator = await _locator_for_target_id(page, payload_target)
                if locator:
                    mode = await _resolve_interaction_mode(locator)
                    control_options: List[str] = []
                    parsed_id = _extract_target_id(payload_target)
                    if parsed_id is not None:
                        control = next(
                            (fc for fc in before_snapshot.form_controls if fc.control_id == parsed_id), None
                        )
                        if control is not None:
                            control_options = control.options

                    try:
                        if mode == "text_input":
                            await locator.fill(payload_value)
                            filled_payloads.append(
                                {"target": payload_target, "value": payload_value[:120], "reason": payload_reason}
                            )
                        elif mode == "select":
                            chosen, reason = await _fill_select_option(
                                page, locator, payload_value, control_options, action_strategy
                            )
                            filled_payloads.append(
                                {"target": payload_target, "value": chosen[:120], "reason": reason}
                            )
                        elif mode == "checkbox_radio":
                            await locator.check()
                            filled_payloads.append(
                                {
                                    "target": payload_target,
                                    "value": "checked",
                                    "reason": payload_reason or "happy_checkbox_radio_check",
                                }
                            )
                    except Exception as fill_exc:
                        _local_service_log(f"Step {step_num}: failed to mutate {payload_target}: {fill_exc}")

            log_entry["input_payloads"] = filled_payloads

            form = page.locator("form:visible").first
            if await form.count() > 0:
                submit_btn = form.locator("button[type='submit'], input[type='submit']").first
                if await submit_btn.count() > 0:
                    await submit_btn.click(timeout=3000)
                else:
                    inputs = form.locator("input:visible, textarea:visible")
                    if await inputs.count() > 0:
                        await inputs.last.press("Enter")
                    else:
                        raise Exception("Form found but no inputs or submit button")
            else:
                raise Exception("No visible form found to submit")

        elif action == "click":
            locator = await _locator_for_target_id(page, target)
            if locator:
                await locator.click(timeout=3000)
            else:
                raise Exception(f"Element '{target}' not found")

        elif action == "type":
            payload_value = value
            payload_reason = action_strategy
            for payload in input_payloads:
                if payload.get("target") == target:
                    payload_value = payload.get("value", value)
                    payload_reason = payload.get("reason", action_strategy)
                    break

            locator = await _locator_for_target_id(page, target)
            mode = "unsupported"
            control_options: List[str] = []
            if locator:
                mode = await _resolve_interaction_mode(locator)
                parsed_id = _extract_target_id(target)
                if parsed_id is not None:
                    control = next(
                        (fc for fc in before_snapshot.form_controls if fc.control_id == parsed_id), None
                    )
                    if control is not None:
                        control_options = control.options
            if mode == "unsupported":
                locator = page.locator("input:visible, textarea:visible, select:visible").first
                if await locator.count() > 0:
                    mode = await _resolve_interaction_mode(locator)
                    try:
                        fallback_options = await locator.evaluate(
                            "el => Array.from(el.options).map(o => o.value || o.textContent.trim()).filter(v => v)"
                        )
                        if isinstance(fallback_options, list):
                            control_options = [str(o) for o in fallback_options]
                    except Exception:
                        pass

            if await locator.count() > 0 and mode != "unsupported":
                payload = payload_value or fuzzer.next_payload()
                if mode == "select":
                    chosen, reason = await _fill_select_option(page, locator, payload, control_options, action_strategy)
                    log_entry["value"] = chosen[:120]
                    log_entry["input_payloads"] = [{"target": target, "value": chosen[:120], "reason": reason}]
                elif mode == "checkbox_radio":
                    await locator.check()
                    log_entry["value"] = "checked"
                    log_entry["input_payloads"] = [
                        {"target": target, "value": "checked", "reason": payload_reason or "happy_checkbox_radio_check"}
                    ]
                else:
                    await locator.fill(payload)
                    log_entry["value"] = payload[:120]
                    log_entry["input_payloads"] = [
                        {"target": target, "value": payload[:120], "reason": payload_reason or "fallback_fuzzer"}
                    ]

                    if any(marker in payload.lower() for marker in ["<script", "onerror", " or 1=1", "drop table"]):
                        defects.add(
                            "security_risks",
                            {
                                "step": step_num,
                                "type": "fuzz-payload-injected",
                                "target": target,
                                "payload_preview": payload[:200],
                                "url": page.url,
                            },
                        )
            else:
                raise Exception(f"Input or selectable target '{target}' not found")

        try:
            await page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
        log_entry["url"] = page.url

        after_snapshot = await get_page_state(page, step_num, phase="after", output_dir=settings.output_dir)
        perf_after = await perf_monitor.snapshot(page)

        # Layout instability detection
        max_shift = compute_max_layout_shift(before_snapshot, after_snapshot)
        if before_snapshot.url == after_snapshot.url and max_shift > LAYOUT_SHIFT_THRESHOLD_PX:
            defects.add(
                "layout_instability",
                {
                    "step": step_num,
                    "type": "layout-instability",
                    "max_shift_px": max_shift,
                    "url": after_snapshot.url,
                    "before_hash": before_snapshot.structure_hash,
                    "after_hash": after_snapshot.structure_hash,
                },
            )

        # DOM collapse detection
        if (
            before_snapshot.url == after_snapshot.url
            and len(after_snapshot.elements) < max(1, int(len(before_snapshot.elements) * 0.5))
        ):
            defects.add(
                "layout_instability",
                {
                    "step": step_num,
                    "type": "dom-collapse",
                    "before_elements": len(before_snapshot.elements),
                    "after_elements": len(after_snapshot.elements),
                    "url": after_snapshot.url,
                },
            )

        # Visual diffing
        visual_diff = compare_screenshots_pixelmatch(
            before_snapshot.screenshot_path, after_snapshot.screenshot_path, step_num, output_dir=settings.output_dir
        )
        if visual_diff.get("diff_ratio", 0.0) > VISUAL_DIFF_THRESHOLD_RATIO and before_snapshot.url == after_snapshot.url:
            defects.add(
                "visual_regressions",
                {
                    "step": step_num,
                    "type": "visual-diff",
                    "diff_ratio": visual_diff.get("diff_ratio"),
                    "diff_pixels": visual_diff.get("diff_pixels"),
                    "engine": visual_diff.get("engine"),
                    "diff_image": os.path.basename(visual_diff.get("diff_image", "")),
                    "url": after_snapshot.url,
                },
            )

        perf_findings = await perf_monitor.detect_bottlenecks(
            perf_before, perf_after, step_num, action, after_snapshot.url
        )
        log_entry["performance_findings"] = len(perf_findings)

        zombie = await network_monitor.detect_zombie_ui(page, step_num)
        if zombie:
            log_entry["zombie_ui"] = zombie["type"]

        log_entry["before_dom_hash"] = before_snapshot.dom_hash
        log_entry["after_dom_hash"] = after_snapshot.dom_hash
        log_entry["visual_diff_ratio"] = visual_diff.get("diff_ratio", 0.0)
        log_entry["screenshot"] = os.path.basename(after_snapshot.screenshot_path)

    except Exception as e:
        error_msg = str(e)
        log_entry["status"] = "FAILED"
        log_entry["error"] = error_msg
        print(f"💥 Error: {error_msg}")

        screenshot_name = f"error_step_{step_num}.png"
        try:
            await page.screenshot(path=os.path.join(settings.output_dir, screenshot_name))
            log_entry["screenshot"] = screenshot_name
        except Exception:
            pass

        action_remediation: Dict[str, str] = {
            "click": "Ensure the target element is visible, not obscured by overlays, and has a stable selector.",
            "type": "Verify the input field is enabled and accepts keyboard events.",
            "submit_form": "Ensure form wrappers properly enclose all input targets and submit buttons are visible.",
            "handle_modal": "Confirm the modal/dialog is present in the DOM and accessible.",
            "scroll": "Check that the page body has sufficient height to scroll.",
            "back": "Verify browser history has a previous entry.",
            "random_jump": "Ensure the target URL is reachable and returns a valid HTTP response.",
            "restart_target": "Confirm the target URL is valid and the server is responding.",
        }
        remediation_text = action_remediation.get(
            action, "Investigate the step failure context. Review the error message and annotated screenshot."
        )

        html_context = ""
        try:
            if target.strip():
                locator = await _locator_for_target_id(page, target)
                if locator:
                    html_context = await locator.evaluate("el => el.outerHTML", timeout=2000) or ""
        except Exception:
            pass

        defects.add(
            "console_findings",
            {
                "step": step_num,
                "type": f"functional-failure:{action}",
                "severity": "error",
                "selector": target if target.strip() else "(none)",
                "html_snippet": html_context[:500] if html_context else "",
                "failure_reason": error_msg[:300],
                "remediation_advice": remediation_text,
                "screenshot_path": screenshot_name if log_entry.get("screenshot") == screenshot_name else "",
                "url": page.url,
            },
        )

    # Selective vision annotation for FAILED/CRASH steps or defect-bearing steps
    if log_entry.get("screenshot") and settings.pdf_generate:
        status = log_entry.get("status", "")
        defect_reasons = _step_defects_summary(step_num, defects)
        if status in {"FAILED", "CRASH"} or defect_reasons:
            context_issue = f"status={status}"
            if defect_reasons:
                context_issue += "; " + "; ".join(defect_reasons)
            if log_entry.get("error"):
                context_issue += f"; error={log_entry['error'][:120]}"
            try:
                # Log which vision model is being used for annotation
                active_vision_model = settings.vision_model or settings.pdf_vision_model
                print(f"   └─ 📸 Annotating screenshot with {active_vision_model}...")
                original_path = os.path.join(settings.output_dir, log_entry["screenshot"])
                annotated_path = await annotate_relevant_screenshot(settings, original_path, context_issue)
                if annotated_path != original_path:
                    log_entry["screenshot"] = os.path.basename(annotated_path)
                    log_entry["screenshot_annotated"] = True
            except Exception as exc:
                _local_service_log(f"Annotation hook failed at step {step_num}: {exc}", settings.output_dir)

    if log_sink is None:
        # Fallback to global logs (shouldn't happen in normal worker flow)
        from monkeylm.core import test_logs

        test_logs.append(log_entry)
    else:
        log_sink.append(log_entry)

    try:
        return await get_page_state(page, step_num, phase="final", output_dir=settings.output_dir), log_entry
    except Exception:
        return None, log_entry


def _compute_action_path_hash(domain: str, route: str, action: str, target: str) -> str:
    """Compute a deterministic signature for an action path."""
    import hashlib

    raw = f"{domain}|{route}|{action}|{target}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()
