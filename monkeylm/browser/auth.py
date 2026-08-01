"""Credential-aware login helpers for target systems requiring authentication."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from monkeylm.types import PageSnapshot, Settings


def _normalize_field(field: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(field, dict):
        return {}

    visible = field.get("visible", True)
    disabled = field.get("disabled", False)
    kind = str(field.get("kind") or field.get("semantic_kind") or field.get("input_type") or "text").strip().lower()
    input_type = str(field.get("input_type") or "").strip().lower()
    label = str(field.get("resolved_label") or field.get("aria_label") or field.get("placeholder") or "").strip()
    name = str(field.get("name") or field.get("id") or "").strip()
    return {
        "visible": bool(visible),
        "disabled": bool(disabled),
        "kind": kind,
        "input_type": input_type,
        "label": label,
        "name": name,
        "placeholder": str(field.get("placeholder") or "").strip(),
        "aria_label": str(field.get("aria_label") or "").strip(),
        "control_id": field.get("control_id"),
        "form_id": field.get("form_id"),
    }


def _field_matches(field: Dict[str, Any], *, keywords: List[str]) -> bool:
    haystack = " ".join(
        part for part in [
            field.get("label", ""),
            field.get("name", ""),
            field.get("placeholder", ""),
            field.get("aria_label", ""),
        ] if part
    ).lower()
    return any(keyword.lower() in haystack for keyword in keywords)


def _looks_like_login_page(snapshot: Optional[PageSnapshot], fields: List[Dict[str, Any]]) -> bool:
    if snapshot is None:
        return False

    url = (getattr(snapshot, "url", "") or "").lower()
    title = (getattr(snapshot, "title", "") or "").lower()
    body_text = " ".join(getattr(snapshot, "elements", [])).lower()
    hints = [url, title, body_text]
    if any(token in hint for hint in hints for token in ["login", "signin", "sign in", "auth", "authenticate", "account", "password"]):
        return True

    return any(
        _field_matches(field, keywords=["email", "username", "user", "login", "account", "password"])
        for field in fields
    )


def infer_login_field_targets(fields: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Infer which visible controls should receive the target username/password values."""

    normalized_fields: List[Dict[str, Any]] = []
    for field in fields:
        normalized = _normalize_field(field)
        if not normalized:
            continue
        if not normalized.get("visible", True) or normalized.get("disabled", False):
            continue
        normalized_fields.append(normalized)

    username_candidates: List[Tuple[Dict[str, Any], int]] = []
    password_candidates: List[Tuple[Dict[str, Any], int]] = []

    for field in normalized_fields:
        kind = field.get("kind", "")
        input_type = field.get("input_type", "")
        score = 0

        if kind == "password" or input_type == "password":
            score = 100
            if _field_matches(field, keywords=["password", "pass", "passwd", "secret"]):
                score += 20
            password_candidates.append((field, score))
            continue

        if kind == "email" or input_type == "email":
            score = 90
        elif kind in {"text", "search", "tel"} or input_type in {"text", "search", "tel", "username"}:
            score = 40

        if _field_matches(field, keywords=["email", "e-mail"]):
            score += 40
        elif _field_matches(field, keywords=["username", "user", "login", "account"]):
            score += 25
        elif re.search(r"(?:user|login|account|email)", field.get("name", ""), re.IGNORECASE):
            score += 25

        if kind in {"email", "text"}:
            score += 5

        username_candidates.append((field, score))

    def _pick_best(candidates: List[Tuple[Dict[str, Any], int]]) -> Optional[Dict[str, Any]]:
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[1], reverse=True)
        return candidates[0][0]

    result: Dict[str, Dict[str, Any]] = {}
    username_target = _pick_best(username_candidates)
    if username_target is not None:
        result["username"] = username_target
    password_target = _pick_best(password_candidates)
    if password_target is not None:
        result["password"] = password_target

    return result


async def attempt_login_with_target_credentials(page: Any, settings: Settings, *, snapshot: Optional[PageSnapshot] = None, worker_label: str = "worker") -> bool:
    """Attempt to log in to a detected login form using configured target credentials."""

    username = (getattr(settings, "target_username", "") or "").strip()
    password = (getattr(settings, "target_password", "") or "").strip()
    if not username or not password:
        return False

    from monkeylm.browser.actions.helpers import _click_element_resilient, _fill_input_resilient, _locator_for_target_id
    from monkeylm.browser.snapshot.state import get_page_state

    if snapshot is None:
        snapshot = await get_page_state(page, -1, phase="auth", output_dir=settings.output_dir)

    fields = [
        {
            "control_id": fc.control_id,
            "form_id": fc.form_id,
            "kind": fc.semantic_kind,
            "input_type": fc.input_type,
            "name": fc.name_attr,
            "id": fc.id_attr,
            "placeholder": fc.placeholder,
            "aria_label": fc.aria_label,
            "resolved_label": fc.resolved_label,
            "visible": fc.visible,
            "disabled": fc.disabled,
        }
        for fc in snapshot.form_controls
    ]

    if not _looks_like_login_page(snapshot, fields):
        return False

    targets = infer_login_field_targets(fields)
    username_target = targets.get("username")
    password_target = targets.get("password")
    if not username_target or not password_target:
        return False

    username_locator = await _locator_for_target_id(page, username_target.get("control_id"))
    password_locator = await _locator_for_target_id(page, password_target.get("control_id"))
    if username_locator is None or password_locator is None:
        return False

    filled_username = await _fill_input_resilient(page, username_locator, username, target="username")
    filled_password = await _fill_input_resilient(page, password_locator, password, target="password")
    if not filled_username or not filled_password:
        return False

    print(f"🔐 [{worker_label}] Detected a likely login form and filled target credentials.")

    form_id = username_target.get("form_id") or password_target.get("form_id")
    if form_id:
        matching_form = next((frm for frm in snapshot.forms if frm.form_id == form_id), None)
        if matching_form and matching_form.submit_candidate_id is not None:
            submit_locator = await _locator_for_target_id(page, matching_form.submit_candidate_id)
            if submit_locator is not None:
                await _click_element_resilient(page, submit_locator, timeout_ms=2500)
                await page.wait_for_timeout(1500)
                return True

    try:
        await password_locator.press("Enter")
        await page.wait_for_timeout(1500)
        return True
    except Exception:
        return True
