"""Regression tests for credential-aware login heuristics."""

from __future__ import annotations

import unittest

from monkeylm.browser.auth import infer_login_field_targets


class LoginHeuristicsTests(unittest.TestCase):
    def test_prefers_email_and_password_fields_for_login(self) -> None:
        fields = [
            {"kind": "text", "input_type": "email", "name": "email", "id": "email", "placeholder": "Email", "aria_label": "Email", "visible": True},
            {"kind": "password", "input_type": "password", "name": "password", "id": "password", "placeholder": "Password", "aria_label": "Password", "visible": True},
        ]

        targets = infer_login_field_targets(fields)

        self.assertEqual(targets["username"]["name"], "email")
        self.assertEqual(targets["password"]["name"], "password")

    def test_falls_back_to_first_visible_text_input_when_no_email_hint_exists(self) -> None:
        fields = [
            {"kind": "text", "input_type": "text", "name": "username", "id": "username", "placeholder": "Username", "aria_label": "Username", "visible": True},
            {"kind": "password", "input_type": "password", "name": "password", "id": "password", "placeholder": "Password", "aria_label": "Password", "visible": True},
        ]

        targets = infer_login_field_targets(fields)

        self.assertEqual(targets["username"]["name"], "username")
        self.assertEqual(targets["password"]["name"], "password")


if __name__ == "__main__":
    unittest.main()
