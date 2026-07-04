"""Smoke test for the new form intelligence layer in monkeylm/ package."""

import sys
sys.path.insert(0, "/home/sameer/Public/Shared/Work/Projects/MonkeyLM")

from monkeylm.config import FormControlRecord, FormRecord, normalize_action_plan
from monkeylm.models import generate_form_payload, parse_action_plan_response


def test_payload_generator():
    cases = [
        (FormControlRecord(0, None, "input", "email", "email", "email", "", "", "", True, False, False, None, None, "", "", "", "", "Email", 1.0, "email"), "HAPPY_UPSERT", "monkey.test@example.com"),
        (FormControlRecord(1, None, "input", "number", "age", "age", "", "", "", True, False, False, None, None, "", "18", "65", "1", "Age", 1.0, "numeric"), "HAPPY_UPSERT", "41"),
        (FormControlRecord(2, None, "input", "number", "age", "age", "", "", "", True, False, False, None, None, "", "18", "65", "1", "Age", 1.0, "numeric"), "EDGE_CASE_FUZZ", None),
        (FormControlRecord(3, None, "input", "text", "name", "name", "", "", "", True, False, False, 2, 50, "", "", "", "", "Name", 1.0, "text"), "EDGE_CASE_FUZZ", None),
        (FormControlRecord(4, None, "textarea", "", "bio", "bio", "", "", "", False, False, False, None, 200, "", "", "", "", "Bio", 1.0, "textarea"), "EDGE_CASE_FUZZ", None),
    ]

    for control, strategy, expected_value in cases:
        value, reason = generate_form_payload(control, strategy)
        print(f"[{strategy}] {control.semantic_kind} (required={control.required}, max={control.maxlength}, min={control.min_value}, max={control.max_value}) -> value={value!r} reason={reason}")
        if expected_value is not None:
            assert value == expected_value, f"Expected {expected_value!r}, got {value!r}"


def test_action_plan_normalization():
    raw = {
        "action": "submit_form",
        "target": "[id=0]",
        "value": "",
        "action_strategy": "EDGE_CASE_FUZZ",
        "input_payloads": [
            {"target": "[id=1]", "value": "not-an-email", "reason": "fuzz_invalid_email_format"},
        ],
    }
    normalized = normalize_action_plan(raw)
    assert normalized["action_strategy"] == "EDGE_CASE_FUZZ"
    assert len(normalized["input_payloads"]) == 1
    assert normalized["input_payloads"][0]["reason"] == "fuzz_invalid_email_format"

    parsed = parse_action_plan_response('''{"action":"type","target":"[id=5]","value":"x","action_strategy":"HAPPY_UPSERT","input_payloads":[{"target":"[id=5]","value":"x","reason":"happy_default_text"}]}''')
    assert parsed is not None
    assert parsed["action_strategy"] == "HAPPY_UPSERT"
    print("Action plan normalization and parsing OK")


def test_form_record():
    form = FormRecord("form_0", "/api/users", "post", [0, 1, 2], 3)
    assert form.submit_candidate_id == 3
    print("FormRecord construction OK")


if __name__ == "__main__":
    test_payload_generator()
    test_action_plan_normalization()
    test_form_record()
    print("\n✅ All smoke tests passed.")
