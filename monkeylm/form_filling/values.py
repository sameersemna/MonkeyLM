"""Constraint-aware form value generation."""

from __future__ import annotations

import re
from typing import Any, List, Optional, Sequence, Tuple

from monkeylm.types import FormControlRecord

from .common import _REQUIRED_TEXT_ROLES, infer_control_role


def _number(value: Any) -> Optional[float]:
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _numeric_step(control: Any) -> Optional[float]:
    step = _number(getattr(control, "step", ""))
    if step is None or step <= 0:
        return None
    return step


def _midpoint_number(control: Any) -> str:
    lower = _number(getattr(control, "min_value", ""))
    upper = _number(getattr(control, "max_value", ""))
    if lower is not None and upper is not None and upper < lower:
        lower, upper = upper, lower
    if lower is None:
        lower = 0.0
    if upper is None:
        upper = max(lower + 100.0, 100.0)
    midpoint = lower + (upper - lower) * 0.5
    step = _numeric_step(control)
    if step is not None:
        if step >= 1 and float(step).is_integer():
            return str(int(midpoint))
        midpoint = round(midpoint / step) * step
        return f"{midpoint:.2f}"
    return str(int(midpoint)) if float(midpoint).is_integer() else f"{midpoint:.2f}"


def _apply_text_constraints(value: str, control: Any, role: str) -> str:
    maxlength = getattr(control, "maxlength", None)
    minlength = getattr(control, "minlength", None)
    if maxlength is not None and maxlength > 0 and len(value) > maxlength:
        value = value[:maxlength]
    if minlength is not None and minlength > 0 and len(value) < minlength:
        if role == "email" and "@" in value:
            local, _, domain = value.partition("@")
            padding = "x" * (minlength - len(value))
            value = f"{local}{padding}@{domain}"
        elif role == "url" and "://" in value:
            scheme, _, rest = value.partition("://")
            padding = "x" * (minlength - len(value))
            value = f"{scheme}://{padding}{rest}"
        else:
            value = value + ("x" * (minlength - len(value)))
        if maxlength is not None and maxlength > 0:
            value = value[:maxlength]
    return value


def _value_for_pattern(pattern: str) -> Optional[str]:
    normalized = (pattern or "").strip()
    if not normalized:
        return None
    if re.fullmatch(r"\^?\\d\{\d+\}\$?", normalized):
        match = re.search(r"(\d+)", normalized)
        return "1" * int(match.group(1)) if match else None
    if re.fullmatch(r"\^?\[0-9\]\+\$?", normalized):
        return "12345"
    if re.fullmatch(r"\^?\[A-Za-z\]\+\$?", normalized):
        return "MonkeyLM"
    if re.fullmatch(r"\^?\[A-Za-z0-9\]\+\$?", normalized):
        return "MonkeyLM123"
    if re.fullmatch(r"\^?[A-Z]\{2,\}\$?", normalized):
        return "AB"
    return None


def _valid_value(control: Any, role: str, variant: int = 0) -> Tuple[str, str]:
    suffix = " updated" if variant else ""
    if role == "email":
        value = "monkey.test@example.com" if not variant else "monkey.test+updated@example.com"
        return value, "happy_valid_email"
    if role == "username":
        return f"monkey_user{variant or ''}", "happy_valid_username"
    if role == "first_name":
        return "Monkey" + (" Updated" if variant else ""), "happy_valid_first_name"
    if role == "last_name":
        return "Tester" + (" Updated" if variant else ""), "happy_valid_last_name"
    if role == "full_name":
        return "Monkey Tester" + (" Updated" if variant else ""), "happy_valid_full_name"
    if role == "phone":
        return "+15551234567", "happy_valid_phone"
    if role == "url":
        return "https://example.com/monkey-test", "happy_valid_url"
    if role == "address":
        return "123 Monkey Lane" + suffix, "happy_valid_address"
    if role == "city":
        return "Testville" + suffix, "happy_valid_city"
    if role == "state":
        return "CA", "happy_valid_state"
    if role == "postal_code":
        return "90210", "happy_valid_postal_code"
    if role == "country":
        return "US", "happy_valid_country"
    if role == "company":
        return "MonkeyLM QA" + suffix, "happy_valid_company"
    if role == "title":
        return "MonkeyLM validation test" + suffix, "happy_valid_title"
    if role in {"description", "textarea"}:
        return (
            "A concise happy-path description for MonkeyLM testing." + (" Updated." if variant else ""),
            "happy_textarea",
        )
    if role == "search":
        return "MonkeyLM", "happy_valid_search"
    if role in {"numeric", "age", "quantity", "price"}:
        value = _midpoint_number(control)
        if variant and value.lstrip("-").isdigit():
            step = _numeric_step(control) or 1.0
            candidate = float(value) + step
            upper = _number(getattr(control, "max_value", ""))
            if upper is None or candidate <= upper:
                value = str(int(candidate)) if step >= 1 else f"{candidate:.2f}"
        if role == "age":
            return value, "happy_mid_range_age"
        if role in {"quantity", "price"}:
            return value, f"happy_mid_range_{role}"
        reason = "happy_mid_range_integer" if value.isdigit() else "happy_mid_range_number"
        return value, reason
    if role in {"date", "datetime", "time"}:
        if role == "date":
            return "2026-07-01", "happy_valid_date"
        if role == "time":
            return "12:00", "happy_valid_time"
        return "2026-07-01T12:00", "happy_valid_datetime"
    if role == "select":
        options = list(getattr(control, "options", []) or [])
        if options:
            index = min(variant, len(options) - 1)
            return str(options[index]), "happy_select_first_option" if index == 0 else "happy_select_next_option"
        return "", "happy_select_no_options_skip"
    if role in {"checkbox", "radio"}:
        return "true", "happy_checked"
    if role == "password":
        return "MonkeyP@ssw0rd!2026", "happy_valid_password"

    pattern_value = _value_for_pattern(getattr(control, "pattern", ""))
    if pattern_value is not None:
        return pattern_value, "happy_pattern_value"
    value = "MonkeyLM happy path text" + suffix
    maxlength = getattr(control, "maxlength", None)
    if maxlength is not None and maxlength > 0 and len(value) > maxlength:
        return value[:maxlength], f"happy_text_within_maxlength_{maxlength}"
    return value, "happy_default_text"


def generate_valid_value(control: Any, variant: int = 0) -> Tuple[str, str]:
    role = infer_control_role(control)
    value, reason = _valid_value(control, role, variant)
    if role not in {"numeric", "age", "quantity", "price", "select"}:
        value = _apply_text_constraints(value, control, role)
    return value, reason


def _choice(options: Sequence[Tuple[str, str]], control: Any, salt: str = "") -> Tuple[str, str]:
    digest = sum(ord(char) for char in f"{getattr(control, 'control_id', 0)}:{salt}")
    return options[digest % len(options)]


def generate_invalid_value(control: Any) -> Tuple[str, str]:
    role = infer_control_role(control)
    if role in {"numeric", "age", "quantity", "price"}:
        lower = _number(getattr(control, "min_value", ""))
        upper = _number(getattr(control, "max_value", ""))
        choices: List[Tuple[str, str]] = []
        if lower is not None:
            choices.append((str(lower - 1), "fuzz_below_min"))
        if upper is not None:
            choices.append((str(upper + 1), "fuzz_above_max"))
        choices.extend(
            [
                ("not-a-number", "fuzz_string_in_number_field"),
                ("-999999999", "fuzz_large_negative"),
                ("1e309", "fuzz_numeric_overflow"),
            ]
        )
        return _choice(choices, control, "numeric")
    if role == "email":
        return _choice(
            [
                ("not-an-email", "fuzz_invalid_email_format"),
                ("a@b", "fuzz_too_short_email"),
                ("test@.com", "fuzz_malformed_domain"),
            ],
            control,
            "email",
        )
    if role == "url":
        return _choice([("not-a-url", "fuzz_invalid_url"), ("ftp://missing", "fuzz_partial_url")], control, "url")
    if role == "phone":
        return _choice([("abc-def-ghij", "fuzz_letters_in_phone"), ("", "fuzz_empty_phone")], control, "phone")
    if role in {"date", "datetime", "time"}:
        return _choice(
            [("not-a-date", "fuzz_invalid_datetime"), ("0000-00-00T00:00", "fuzz_zero_date")],
            control,
            "datetime",
        )
    if getattr(control, "required", False) and role in _REQUIRED_TEXT_ROLES:
        return _choice([("", "fuzz_empty_required_field"), ("   ", "fuzz_whitespace_only")], control, "required")
    if getattr(control, "pattern", ""):
        return _choice([("!!!INVALID!!!", "fuzz_pattern_mismatch"), ("", "fuzz_empty_against_pattern")], control, "pattern")
    maxlength = getattr(control, "maxlength", None)
    if maxlength is not None and maxlength > 0:
        return "A" * (maxlength + 10), f"fuzz_maxlength_overflow_{maxlength}"
    if role in {"textarea", "description"}:
        return "\n\n\n<script>alert(1)</script>", "fuzz_textarea_newline_and_xss"
    if role == "select":
        return "__monkeylm_invalid_option__", "fuzz_select_invalid_option"
    return _choice(
        [
            ("' OR 1=1 --", "fuzz_sql_fragment"),
            ("<script>alert('xss')</script>", "fuzz_xss_payload"),
            ("A" * 12000, "fuzz_large_string_blob"),
            ("\u0000\u0001\u0002", "fuzz_control_chars"),
            ("日本語テスト🐵", "fuzz_unicode"),
        ],
        control,
        "text",
    )


def generate_boundary_value(control: Any) -> Tuple[str, str]:
    role = infer_control_role(control)
    lower = _number(getattr(control, "min_value", ""))
    upper = _number(getattr(control, "max_value", ""))
    if role in {"numeric", "age", "quantity", "price"}:
        if lower is not None and (upper is None or getattr(control, "control_id", 0) % 2 == 0):
            return str(int(lower)) if lower.is_integer() else f"{lower:.2f}", "boundary_min_value"
        if upper is not None:
            return str(int(upper)) if upper.is_integer() else f"{upper:.2f}", "boundary_max_value"
        return generate_valid_value(control)
    maxlength = getattr(control, "maxlength", None)
    if maxlength is not None and maxlength > 0:
        return "A" * maxlength, "boundary_maxlength"
    minlength = getattr(control, "minlength", None)
    if minlength is not None and minlength > 0:
        return "A" * minlength, "boundary_minlength"
    if role == "select":
        options = list(getattr(control, "options", []) or [])
        if options:
            return str(options[-1]), "boundary_last_option"
    if role in {"date", "datetime", "time"}:
        date_min = str(getattr(control, "min_value", "") or "").strip()
        date_max = str(getattr(control, "max_value", "") or "").strip()
        if date_min:
            return date_min, "boundary_min_date"
        if date_max:
            return date_max, "boundary_max_date"
    return generate_valid_value(control)


def generate_unexpected_value(control: Any) -> Tuple[str, str]:
    role = infer_control_role(control)
    if role in {"numeric", "age", "quantity", "price"}:
        return "not-a-number", "unexpected_string_in_numeric"
    if role == "email":
        return "monkey.test.example.com", "unexpected_email_without_at"
    if role in {"date", "datetime", "time"}:
        return "MonkeyLM", "unexpected_text_in_date"
    if role == "select":
        return "__monkeylm_invalid_option__", "unexpected_invalid_option"
    if role in {"checkbox", "radio"}:
        return "unexpected", "unexpected_non_boolean_checkbox"
    return "<script>alert('unexpected-type')</script>", "unexpected_markup_in_text"


def generate_form_payload(control: FormControlRecord, strategy: str) -> Tuple[str, str]:
    """Backward-compatible single-field generator used by the decision layer."""
    normalized = (strategy or "").strip().upper()
    if normalized == "EDGE_CASE_FUZZ":
        return generate_invalid_value(control)
    if normalized == "BOUNDARY_VALUE":
        return generate_boundary_value(control)
    if normalized == "UNEXPECTED_TYPE":
        return generate_unexpected_value(control)
    return generate_valid_value(control)
