from __future__ import annotations

import unittest

from monkeylm.form_filling import (
    CRUD_CREATE,
    CRUD_DELETE,
    CRUD_UPDATE,
    build_form_payloads,
    generate_boundary_value,
    generate_form_payload,
    generate_invalid_value,
    generate_unexpected_value,
    infer_control_role,
    infer_crud_operation,
    merge_form_payloads,
    prepare_submit_form_payloads,
)
from monkeylm.types import FormControlRecord, FormRecord, PageSnapshot


def _control(
    control_id: int = 0,
    *,
    tag_name: str = "input",
    input_type: str = "text",
    label: str = "",
    name: str = "",
    placeholder: str = "",
    semantic_kind: str = "text",
    required: bool = False,
    disabled: bool = False,
    readonly: bool = False,
    minlength: int | None = None,
    maxlength: int | None = None,
    pattern: str = "",
    min_value: str = "",
    max_value: str = "",
    step: str = "",
    visible: bool = True,
    options: list[str] | None = None,
    form_id: str | None = "records",
) -> FormControlRecord:
    return FormControlRecord(
        control_id=control_id,
        form_id=form_id,
        tag_name=tag_name,
        input_type=input_type,
        name_attr=name,
        id_attr=name,
        placeholder=placeholder,
        aria_label="",
        aria_labelledby="",
        required=required,
        disabled=disabled,
        readonly=readonly,
        minlength=minlength,
        maxlength=maxlength,
        pattern=pattern,
        min_value=min_value,
        max_value=max_value,
        step=step,
        resolved_label=label,
        label_confidence=1.0,
        semantic_kind=semantic_kind,
        visible=visible,
        options=options or [],
    )


def _snapshot(
    controls: list[FormControlRecord],
    *,
    submit_id: int = 9,
    submit_text: str = "Create",
    elements: list[str] | None = None,
) -> PageSnapshot:
    return PageSnapshot(
        url="https://example.test/records",
        title="Records",
        dom_hash="dom",
        structure_hash="structure",
        elements=(
            elements
            if elements is not None
            else [f'[id={submit_id}] <BUTTON text="{submit_text}" />']
        ),
        form_controls=controls,
        forms=[
            FormRecord(
                form_id="records",
                action="/records",
                method="post",
                control_ids=[control.control_id for control in controls],
                submit_candidate_id=submit_id,
            )
        ],
    )


class FormRoleInferenceTests(unittest.TestCase):
    def test_uses_input_type_and_label(self) -> None:
        self.assertEqual(
            infer_control_role(_control(input_type="email", semantic_kind="email", label="Email Address")),
            "email",
        )
        self.assertEqual(
            infer_control_role(_control(input_type="number", semantic_kind="numeric", label="Age")),
            "age",
        )
        self.assertEqual(
            infer_control_role(_control(input_type="password", semantic_kind="password", label="Password")),
            "password",
        )
        self.assertEqual(
            infer_control_role(_control(semantic_kind="text", label="Full Name")),
            "full_name",
        )
        self.assertEqual(
            infer_control_role(_control(semantic_kind="search", input_type="search", label="Search records")),
            "search",
        )
        self.assertEqual(
            infer_control_role(_control(tag_name="select", input_type="select-one", semantic_kind="select")),
            "select",
        )

    def test_label_wins_over_generic_text_type(self) -> None:
        self.assertEqual(infer_control_role(_control(label="Postal Code")), "postal_code")
        self.assertEqual(infer_control_role(_control(label="Company")), "company")
        self.assertEqual(infer_control_role(_control(label="Message")), "description")


class FormValueGenerationTests(unittest.TestCase):
    def test_legacy_happy_payloads_are_stable(self) -> None:
        email = _control(input_type="email", semantic_kind="email", label="Email Address", required=True)
        age = _control(
            control_id=1,
            input_type="number",
            semantic_kind="numeric",
            label="Age",
            min_value="18",
            max_value="65",
            step="1",
        )
        self.assertEqual(generate_form_payload(email, "HAPPY_UPSERT"), ("monkey.test@example.com", "happy_valid_email"))
        self.assertEqual(generate_form_payload(age, "HAPPY_UPSERT"), ("41", "happy_mid_range_age"))

    def test_text_constraints_are_respected(self) -> None:
        short = _control(label="Name", minlength=5, maxlength=8)
        value, _ = generate_form_payload(short, "HAPPY_UPSERT")
        self.assertGreaterEqual(len(value), 5)
        self.assertLessEqual(len(value), 8)

        patterned = _control(label="Code", pattern=r"^\d{5}$")
        value, reason = generate_form_payload(patterned, "HAPPY_UPSERT")
        self.assertEqual(value, "11111")
        self.assertEqual(reason, "happy_pattern_value")

    def test_invalid_required_text_is_rejected_shape(self) -> None:
        control = _control(label="Name", required=True)
        value, reason = generate_invalid_value(control)
        self.assertIn(value, {"", "   "})
        self.assertTrue(reason.startswith("fuzz_"))

    def test_boundary_values_use_numeric_and_length_edges(self) -> None:
        numeric = _control(
            control_id=2,
            input_type="number",
            semantic_kind="numeric",
            label="Quantity",
            min_value="10",
            max_value="20",
            step="1",
        )
        value, reason = generate_boundary_value(numeric)
        self.assertIn(value, {"10", "20"})
        self.assertTrue(reason.startswith("boundary_"))

        text = _control(label="Name", maxlength=12)
        value, reason = generate_boundary_value(text)
        self.assertEqual(len(value), 12)
        self.assertEqual(reason, "boundary_maxlength")

    def test_unexpected_type_values(self) -> None:
        numeric = _control(input_type="number", semantic_kind="numeric", label="Quantity")
        self.assertEqual(generate_unexpected_value(numeric), ("not-a-number", "unexpected_string_in_numeric"))
        email = _control(input_type="email", semantic_kind="email", label="Email")
        self.assertEqual(generate_unexpected_value(email), ("monkey.test.example.com", "unexpected_email_without_at"))


class FormPayloadAssemblyTests(unittest.TestCase):
    def _controls(self) -> list[FormControlRecord]:
        return [
            _control(0, input_type="email", semantic_kind="email", label="Email", required=True),
            _control(1, label="Full Name", required=True),
            _control(
                2,
                input_type="number",
                semantic_kind="numeric",
                label="Age",
                min_value="18",
                max_value="120",
                step="1",
            ),
            _control(
                3,
                tag_name="select",
                input_type="select-one",
                semantic_kind="select",
                label="Role",
                options=["Admin", "Editor", "Viewer"],
            ),
            _control(4, input_type="checkbox", semantic_kind="checkbox", label="Subscribe"),
            _control(5, label="Disabled", disabled=True),
            _control(6, input_type="hidden", semantic_kind="hidden", label="Token"),
        ]

    def test_build_covers_fillable_controls(self) -> None:
        snapshot = _snapshot(self._controls())
        payloads = build_form_payloads(snapshot, "[id=9]", operation=CRUD_CREATE)
        self.assertEqual([item["target"] for item in payloads], ["[id=0]", "[id=1]", "[id=2]", "[id=3]"])
        self.assertEqual(payloads[3]["value"], "Admin")

    def test_update_payloads_change_values(self) -> None:
        snapshot = _snapshot(self._controls())
        created = build_form_payloads(snapshot, "[id=9]", operation=CRUD_CREATE)
        updated = build_form_payloads(snapshot, "[id=9]", operation=CRUD_UPDATE)
        self.assertNotEqual(created[0]["value"], updated[0]["value"])
        self.assertNotEqual(created[1]["value"], updated[1]["value"])
        self.assertTrue(all(item["reason"].startswith("crud_update_") for item in updated))

    def test_delete_has_no_payloads(self) -> None:
        snapshot = _snapshot(self._controls())
        self.assertEqual(build_form_payloads(snapshot, "[id=9]", operation=CRUD_DELETE), [])

    def test_merge_preserves_model_values_and_fills_gaps(self) -> None:
        snapshot = _snapshot(self._controls())
        merged = merge_form_payloads(
            snapshot,
            "[id=9]",
            [{"target": "[id=0]", "value": "provided@example.com", "reason": "model"}],
            operation=CRUD_CREATE,
        )
        self.assertEqual(merged[0]["value"], "provided@example.com")
        self.assertEqual(merged[1]["target"], "[id=1]")
        self.assertEqual(merged[2]["target"], "[id=2]")

    def test_prepare_uses_edge_case_for_fuzzy_plans(self) -> None:
        snapshot = _snapshot(self._controls())
        operation, payloads = prepare_submit_form_payloads(
            {"action": "submit_form", "target": "[id=9]", "action_strategy": "EDGE_CASE_FUZZ"},
            snapshot,
        )
        self.assertEqual(operation, CRUD_CREATE)
        self.assertTrue(any("fuzz_" in item["reason"] for item in payloads))


class CrudIntentTests(unittest.TestCase):
    def test_detects_create_update_delete_and_read(self) -> None:
        controls = [_control(0, label="Full Name")]
        cases = [
            ("Create", CRUD_CREATE),
            ("Save Changes", CRUD_UPDATE),
            ("Delete", CRUD_DELETE),
            ("Search", "read"),
        ]
        for submit_text, expected in cases:
            with self.subTest(submit_text=submit_text):
                snapshot = _snapshot(controls, submit_text=submit_text)
                self.assertEqual(infer_crud_operation(snapshot, "[id=9]"), expected)


if __name__ == "__main__":
    unittest.main()
