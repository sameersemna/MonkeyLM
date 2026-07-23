from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from playwright.async_api import Page

from monkeylm.config import _local_service_log


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
