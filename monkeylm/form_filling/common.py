"""Shared form-role constants and label inference."""

from __future__ import annotations

import re
from typing import Any, Sequence, Tuple


HAPPY = "happy"
INVALID = "invalid"
BOUNDARY = "boundary"
UNEXPECTED = "unexpected"

CRUD_CREATE = "create"
CRUD_UPDATE = "update"
CRUD_DELETE = "delete"
CRUD_READ = "read"

_TEXT_INPUT_TYPES = frozenset(
    {"", "text", "search", "email", "url", "tel", "password", "number"}
)
_SKIPPED_INPUT_TYPES = frozenset({"hidden", "file", "submit", "button", "reset", "image"})
_REQUIRED_TEXT_ROLES = frozenset(
    {
        "text",
        "textarea",
        "description",
        "search",
        "password",
        "username",
        "first_name",
        "last_name",
        "full_name",
        "address",
        "city",
        "state",
        "postal_code",
        "country",
        "company",
        "title",
    }
)

_ROLE_PHRASES: Sequence[Tuple[str, Tuple[str, ...]]] = (
    ("email", ("email", "e mail", "mail address")),
    ("password", ("password", "passwd", "passcode")),
    ("username", ("username", "user name", "login", "handle", "account id")),
    ("search", ("search", "query", "filter", "find")),
    ("first_name", ("first name", "given name", "fname")),
    ("last_name", ("last name", "surname", "family name", "lname")),
    ("phone", ("phone", "telephone", "mobile", "cell")),
    ("url", ("website", "url", "link", "homepage")),
    ("address", ("street address", "address line", "address", "street")),
    ("city", ("city", "town")),
    ("state", ("state", "province", "region")),
    ("postal_code", ("zip", "postal", "postcode")),
    ("country", ("country",)),
    ("company", ("company", "organization", "organisation", "employer")),
    ("title", ("title", "subject", "headline")),
    ("description", ("description", "message", "comment", "notes", "bio", "summary")),
    ("age", ("age",)),
    ("quantity", ("quantity", "qty", "count")),
    ("price", ("price", "amount", "cost", "salary")),
    ("date", ("date", "birthday", "dob")),
    ("time", ("time",)),
    ("full_name", ("full name", "your name", "display name", "name")),
)


def _normalized_text(value: Any) -> str:
    return re.sub(r"[\s_\-]+", " ", str(value or "").strip()).lower()


def _control_text(control: Any) -> str:
    return " ".join(
        part
        for part in (
            _normalized_text(getattr(control, "resolved_label", "")),
            _normalized_text(getattr(control, "aria_label", "")),
            _normalized_text(getattr(control, "placeholder", "")),
            _normalized_text(getattr(control, "name_attr", "")),
            _normalized_text(getattr(control, "id_attr", "")),
        )
        if part
    )


def _contains_phrase(text: str, phrase: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", text) is not None


def infer_control_role(control: Any) -> str:
    """Infer a semantic data role from HTML type and human-facing labels."""
    tag_name = (getattr(control, "tag_name", "") or "").strip().lower()
    semantic_kind = (getattr(control, "semantic_kind", "") or "").strip().lower()
    input_type = (getattr(control, "input_type", "") or "").strip().lower()

    if tag_name == "select" or semantic_kind == "select":
        return "select"
    if semantic_kind in {"checkbox", "radio"}:
        return semantic_kind
    if semantic_kind in {"file", "hidden"}:
        return semantic_kind
    if semantic_kind in {"email", "password", "phone", "url"}:
        return semantic_kind

    text = _control_text(control)
    for role, phrases in _ROLE_PHRASES:
        if any(_contains_phrase(text, phrase) for phrase in phrases):
            return role

    if semantic_kind == "numeric" or input_type in {"number", "range"}:
        return "numeric"
    if semantic_kind == "datetime" or input_type in {"date", "datetime-local", "time"}:
        return "datetime"
    if semantic_kind == "search" or input_type == "search":
        return "search"
    if tag_name == "textarea" or semantic_kind == "textarea":
        return "textarea"
    if semantic_kind in {"text", ""} and input_type in _TEXT_INPUT_TYPES:
        return "text"
    return semantic_kind or input_type or "text"
