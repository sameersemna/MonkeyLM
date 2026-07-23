"""Form payload generation and defect summary helpers."""

from __future__ import annotations

import random
from typing import Any, List, Optional, Tuple

from monkeylm.types import FormControlRecord


def generate_form_payload(control: FormControlRecord, strategy: str) -> Tuple[str, str]:
    kind = control.semantic_kind
    input_type = control.input_type

    if strategy == "HAPPY_UPSERT":
        if kind == "email":
            return ("monkey.test@example.com", "happy_valid_email")
        if kind == "phone":
            return ("+15551234567", "happy_valid_phone")
        if kind == "url":
            return ("https://example.com/monkey-test", "happy_valid_url")
        if kind == "numeric":
            try:
                lo = float(control.min_value) if control.min_value else 0.0
                hi = float(control.max_value) if control.max_value else max(lo + 100.0, 100.0)
                val = lo + (hi - lo) * 0.5
                if input_type == "number" and (not control.step or float(control.step) == 1.0):
                    return (str(int(val)), "happy_mid_range_integer")
                return (f"{val:.2f}", "happy_mid_range_number")
            except Exception:
                return ("42", "happy_default_number")
        if kind == "datetime":
            return ("2026-07-01T12:00", "happy_valid_datetime")
        if kind in ("checkbox", "radio"):
            return ("true", "happy_checked")
        if kind == "password":
            return ("MonkeyP@ssw0rd!2026", "happy_valid_password")
        if kind == "textarea":
            return ("A concise happy-path description for MonkeyLM testing.", "happy_textarea")
        if kind == "select":
            if control.options:
                return (control.options[0], "happy_select_first_option")
            return ("", "happy_select_no_options_skip")
        if control.maxlength is not None and control.maxlength > 0:
            base = "MonkeyLM"
            return (base[: control.maxlength], f"happy_text_within_maxlength_{control.maxlength}")
        return ("MonkeyLM happy path text", "happy_default_text")

    if kind == "numeric":
        try:
            f_lo: Optional[float] = float(control.min_value) if control.min_value else None
            f_hi: Optional[float] = float(control.max_value) if control.max_value else None
            choices = []
            if f_lo is not None:
                choices.append((str(f_lo - 1), "fuzz_below_min"))
            if f_hi is not None:
                choices.append((str(f_hi + 1), "fuzz_above_max"))
            choices.extend([
                ("not-a-number", "fuzz_string_in_number_field"),
                ("-999999999", "fuzz_large_negative"),
                ("1e309", "fuzz_numeric_overflow"),
            ])
            return random.choice(choices)
        except Exception:
            return ("not-a-number", "fuzz_string_in_number_field")

    if kind == "email":
        return random.choice([
            ("not-an-email", "fuzz_invalid_email_format"),
            ("a@b", "fuzz_too_short_email"),
            ("test@.com", "fuzz_malformed_domain"),
        ])

    if kind == "url":
        return random.choice([("not-a-url", "fuzz_invalid_url"), ("ftp://missing", "fuzz_partial_url")])

    if kind == "phone":
        return random.choice([("abc-def-ghij", "fuzz_letters_in_phone"), ("", "fuzz_empty_phone")])

    if kind == "datetime":
        return random.choice([("not-a-date", "fuzz_invalid_datetime"), ("0000-00-00T00:00", "fuzz_zero_date")])

    if control.required and kind in {"text", "textarea", "search", "password"}:
        return random.choice([("", "fuzz_empty_required_field"), ("   ", "fuzz_whitespace_only")])

    if control.pattern:
        return random.choice([("!!!INVALID!!!", "fuzz_pattern_mismatch"), ("", "fuzz_empty_against_pattern")])

    if control.maxlength is not None and control.maxlength > 0:
        overflow = "A" * (control.maxlength + 10)
        return (overflow, f"fuzz_maxlength_overflow_{control.maxlength}")

    if kind == "textarea":
        return ("\n\n\n" + "\u003cscript\u003ealert(1)\u003c/script\u003e", "fuzz_textarea_newline_and_xss")

    if kind == "select":
        return ("__monkeylm_invalid_option__", "fuzz_select_invalid_option")

    return random.choice([
        ("' OR 1=1 --", "fuzz_sql_fragment"),
        ("\u003cscript\u003ealert('xss')\u003c/script\u003e", "fuzz_xss_payload"),
        ("A" * 12000, "fuzz_large_string_blob"),
        ("\u0000\u0001\u0002", "fuzz_control_chars"),
        ("日本語テスト🐵", "fuzz_unicode"),
    ])


def _step_defects_summary(step_num: int, defects: Any) -> list[str]:
    reasons: list[str] = []
    category_names = [
        ("security_risks", "security_risk"),
        ("visual_regressions", "visual_regression"),
        ("layout_instability", "layout_instability"),
        ("accessibility_violations", "a11y_violation"),
        ("performance_bottlenecks", "perf_bottleneck"),
        ("regression_findings", "regression"),
        ("console_findings", "console_finding"),
        ("race_findings", "race_condition"),
        ("boundary_drift", "boundary_drift"),
    ]
    for attr_name, label in category_names:
        for item in getattr(defects, attr_name, []):
            if int(item.get("step", -1)) == step_num:
                dtype = item.get("type", item.get("severity", "unknown"))
                reasons.append(f"{label}:{dtype}")
    return reasons
