"""Deterministic, label-aware form data and CRUD payload generation."""

from .common import (
    BOUNDARY,
    CRUD_CREATE,
    CRUD_DELETE,
    CRUD_READ,
    CRUD_UPDATE,
    HAPPY,
    INVALID,
    UNEXPECTED,
    infer_control_role,
)
from .planner import (
    build_form_payloads,
    infer_crud_operation,
    merge_form_payloads,
    prepare_submit_form_payloads,
    resolve_form_for_target,
)
from .values import (
    generate_boundary_value,
    generate_form_payload,
    generate_invalid_value,
    generate_unexpected_value,
    generate_valid_value,
)

__all__ = [
    "HAPPY",
    "INVALID",
    "BOUNDARY",
    "UNEXPECTED",
    "CRUD_CREATE",
    "CRUD_UPDATE",
    "CRUD_DELETE",
    "CRUD_READ",
    "infer_control_role",
    "generate_form_payload",
    "generate_valid_value",
    "generate_invalid_value",
    "generate_boundary_value",
    "generate_unexpected_value",
    "resolve_form_for_target",
    "build_form_payloads",
    "merge_form_payloads",
    "infer_crud_operation",
    "prepare_submit_form_payloads",
]
