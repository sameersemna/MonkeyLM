"""Form resolution, payload assembly, and CRUD intent detection."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from monkeylm.types import FormControlRecord, FormRecord, PageSnapshot

from .common import (
    BOUNDARY,
    CRUD_CREATE,
    CRUD_DELETE,
    CRUD_READ,
    CRUD_UPDATE,
    HAPPY,
    INVALID,
    UNEXPECTED,
    _SKIPPED_INPUT_TYPES,
    _contains_phrase,
    _control_text,
    _normalized_text,
)
from .values import (
    generate_boundary_value,
    generate_invalid_value,
    generate_unexpected_value,
    generate_valid_value,
)


def _extract_target_id(target: Any) -> Optional[int]:
    if isinstance(target, int):
        return target if target >= 0 else None
    match = re.search(r"\[id\s*=\s*(\d+)\]", str(target or ""), re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def resolve_form_for_target(snapshot: PageSnapshot, target: Any) -> Optional[FormRecord]:
    target_id = _extract_target_id(target)
    if target_id is None:
        return snapshot.forms[0] if len(snapshot.forms) == 1 else None
    for form in snapshot.forms:
        if form.submit_candidate_id == target_id:
            return form
    for form in snapshot.forms:
        if target_id in form.control_ids:
            return form
    return snapshot.forms[0] if len(snapshot.forms) == 1 else None


def _controls_for_form(snapshot: PageSnapshot, form: FormRecord) -> List[FormControlRecord]:
    controls = {control.control_id: control for control in snapshot.form_controls}
    return [controls[control_id] for control_id in form.control_ids if control_id in controls]


def _should_fill(control: FormControlRecord) -> bool:
    if not control.visible or control.disabled or control.readonly:
        return False
    if control.input_type in _SKIPPED_INPUT_TYPES:
        return False
    if control.semantic_kind in {"hidden", "file"}:
        return False
    if control.semantic_kind in {"checkbox", "radio"}:
        return bool(control.required)
    return True


def _scenario_value(control: FormControlRecord, scenario: str, variant: int) -> Tuple[str, str]:
    if scenario == INVALID:
        return generate_invalid_value(control)
    if scenario == BOUNDARY:
        return generate_boundary_value(control)
    if scenario == UNEXPECTED:
        return generate_unexpected_value(control)
    return generate_valid_value(control, variant=variant)


def build_form_payloads(
    snapshot: PageSnapshot,
    target: Any,
    *,
    scenario: str = HAPPY,
    operation: str = CRUD_CREATE,
) -> List[Dict[str, str]]:
    """Build deterministic payloads for every fillable control in the target form."""
    form = resolve_form_for_target(snapshot, target)
    if form is None or operation == CRUD_DELETE:
        return []

    variant = 1 if operation == CRUD_UPDATE else 0
    payloads: List[Dict[str, str]] = []
    for control in _controls_for_form(snapshot, form):
        if not _should_fill(control):
            continue
        value, reason = _scenario_value(control, scenario, variant)
        if operation == CRUD_UPDATE:
            reason = f"crud_update_{reason}"
        payloads.append(
            {
                "target": f"[id={control.control_id}]",
                "value": value,
                "reason": reason,
            }
        )
    return payloads


def merge_form_payloads(
    snapshot: PageSnapshot,
    target: Any,
    provided: Optional[List[Dict[str, Any]]],
    *,
    scenario: str = HAPPY,
    operation: str = CRUD_CREATE,
) -> List[Dict[str, str]]:
    """Preserve model-supplied values and fill any missing form controls."""
    provided_payloads = [
        {
            "target": str(item.get("target", "")),
            "value": str(item.get("value", "")),
            "reason": str(item.get("reason", "")),
        }
        for item in (provided or [])
        if isinstance(item, dict)
    ]
    generated = build_form_payloads(snapshot, target, scenario=scenario, operation=operation)
    if not generated:
        return provided_payloads

    form = resolve_form_for_target(snapshot, target)
    controls = {control.control_id: control for control in snapshot.form_controls}
    provided_by_target = {item["target"]: item for item in provided_payloads if item.get("target")}
    generated_targets = {item["target"] for item in generated}
    merged: List[Dict[str, str]] = []

    for generated_item in generated:
        existing = provided_by_target.get(generated_item["target"])
        if existing is not None and existing.get("value", "").strip():
            merged.append(existing)
            continue
        control_id = _extract_target_id(generated_item["target"])
        control = controls.get(control_id) if control_id is not None else None
        if existing is not None and control is not None and not control.required:
            merged.append(existing)
            continue
        merged.append(generated_item)

    merged.extend(
        item
        for item in provided_payloads
        if item.get("target") and item["target"] not in generated_targets
    )
    if form is None:
        return provided_payloads
    return merged


def _element_text(snapshot: PageSnapshot, element_id: Optional[int]) -> str:
    if element_id is None:
        return ""
    for element in snapshot.elements:
        match = re.match(r"\[id=(\d+)\].*?text=\"(.*?)\"", element)
        if match and int(match.group(1)) == element_id:
            return match.group(2).strip().lower()
    return ""


def infer_crud_operation(snapshot: PageSnapshot, target: Any) -> str:
    """Infer the likely CRUD intent from the form, submit control, and labels."""
    form = resolve_form_for_target(snapshot, target)
    if form is None:
        return CRUD_CREATE

    texts = [
        _element_text(snapshot, form.submit_candidate_id),
        _normalized_text(form.form_id),
        _normalized_text(form.action),
    ]
    texts.extend(
        _control_text(control) for control in _controls_for_form(snapshot, form)
    )
    haystack = " ".join(text for text in texts if text)

    scores = {CRUD_CREATE: 0, CRUD_UPDATE: 0, CRUD_DELETE: 0, CRUD_READ: 0}
    keywords = {
        CRUD_CREATE: ("add", "create", "new", "register", "sign up", "insert", "post"),
        CRUD_UPDATE: ("edit", "update", "save", "modify", "change", "apply"),
        CRUD_DELETE: ("delete", "remove", "destroy", "archive"),
        CRUD_READ: ("search", "filter", "query", "find", "view"),
    }
    for operation, terms in keywords.items():
        for term in terms:
            if _contains_phrase(haystack, term):
                scores[operation] += 1

    best = max(scores, key=lambda operation: scores[operation])
    if scores[best] == 0:
        return CRUD_CREATE
    if best == CRUD_DELETE and not _element_text(snapshot, form.submit_candidate_id):
        return CRUD_CREATE
    return best


def prepare_submit_form_payloads(
    action_plan: Dict[str, Any],
    snapshot: PageSnapshot,
) -> Tuple[str, List[Dict[str, str]]]:
    """Resolve CRUD intent and complete a submit_form plan's payloads."""
    target = action_plan.get("target", "")
    strategy = str(action_plan.get("action_strategy", "") or "").strip().upper()
    scenario = INVALID if strategy == "EDGE_CASE_FUZZ" else HAPPY
    operation = infer_crud_operation(snapshot, target)
    provided = action_plan.get("input_payloads", [])
    if not isinstance(provided, list):
        provided = []
    payloads = merge_form_payloads(
        snapshot,
        target,
        provided,
        scenario=scenario,
        operation=operation,
    )
    return operation, payloads
