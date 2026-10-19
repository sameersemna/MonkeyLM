import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from monkeylm.browser.actions.actions import (
    _action_type,
    _resolve_type_locator,
    _safe_count,
)
from monkeylm.browser.actions.executor import _repair_type_plan
from monkeylm.browser.actions.helpers import _is_typeable_control
from monkeylm.core.worker.guard import FailedPlanGuard, build_plan_signature


class _FakeLocator:
    def __init__(
        self,
        tag_name: str = "input",
        input_type: str = "text",
        visible: bool = True,
        enabled: bool = True,
        count: int = 1,
    ) -> None:
        self.tag_name = tag_name
        self.input_type = input_type
        self.visible = visible
        self.enabled = enabled
        self._count = count

    async def wait_for(self, state=None, timeout=None) -> None:
        if not self.visible:
            raise RuntimeError("not visible")

    async def is_enabled(self) -> bool:
        return self.enabled

    async def evaluate(self, expression, timeout=None):
        if "tagName" in expression:
            return self.tag_name
        if "type" in expression:
            return self.input_type
        return ""

    async def count(self) -> int:
        return self._count

    @property
    def first(self):
        return self


class _FakePage:
    url = "https://quranml.samehash.com/"

    def __init__(self, placeholder_locator=None, label_locator=None) -> None:
        self._placeholder = placeholder_locator or _FakeLocator(visible=False, count=0)
        self._label = label_locator or _FakeLocator(visible=False, count=0)

    def locator(self, selector):
        return _FakeLocator(visible=False, count=0)

    def get_by_placeholder(self, text):
        return self._placeholder

    def get_by_label(self, text):
        return self._label


class _FakeDefects:
    def __init__(self) -> None:
        self.findings = []

    def add(self, category, payload) -> None:
        self.findings.append({"category": category, **payload})


def _control(
    control_id: int = 0,
    tag_name: str = "input",
    input_type: str = "text",
    placeholder: str = "Search surahs...",
    aria_label: str = "",
    disabled: bool = False,
    readonly: bool = False,
    visible: bool = True,
):
    return SimpleNamespace(
        control_id=control_id,
        tag_name=tag_name,
        input_type=input_type,
        id_attr="",
        name_attr="",
        placeholder=placeholder,
        aria_label=aria_label,
        disabled=disabled,
        readonly=readonly,
        visible=visible,
        options=[],
    )


class ActionResolutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_safe_count_uses_valid_playwright_api(self) -> None:
        class _StrictCountLocator:
            async def count(self):
                return 3

        self.assertEqual(await _safe_count(_StrictCountLocator()), 3)

    async def test_resolve_type_locator_uses_positional_target(self) -> None:
        good = _FakeLocator()
        snapshot = SimpleNamespace(form_controls=[_control()])
        with patch(
            "monkeylm.browser.actions.actions._locator_for_target_id",
            AsyncMock(return_value=good),
        ):
            locator, source, control = await _resolve_type_locator(
                _FakePage(), "[id=0]", snapshot
            )

        self.assertIs(locator, good)
        self.assertEqual(source, "positional")
        self.assertEqual(control.control_id, 0)

    async def test_resolve_type_locator_falls_back_to_placeholder(self) -> None:
        placeholder_locator = _FakeLocator()
        snapshot = SimpleNamespace(form_controls=[_control()])
        with patch(
            "monkeylm.browser.actions.actions._locator_for_target_id",
            AsyncMock(return_value=None),
        ):
            locator, source, control = await _resolve_type_locator(
                _FakePage(placeholder_locator=placeholder_locator), "[id=0]", snapshot
            )

        self.assertIs(locator, placeholder_locator)
        self.assertEqual(source, "semantic-fallback")
        self.assertEqual(control.control_id, 0)

    async def test_resolve_type_locator_returns_none_when_no_typeable_target(self) -> None:
        snapshot = SimpleNamespace(form_controls=[_control(tag_name="button", input_type="")])
        with patch(
            "monkeylm.browser.actions.actions._locator_for_target_id",
            AsyncMock(return_value=None),
        ):
            locator, source, _control_record = await _resolve_type_locator(
                _FakePage(), "[id=0]", snapshot
            )

        self.assertIsNone(locator)
        self.assertEqual(source, "none")

    async def test_action_type_falls_back_to_scroll_instead_of_raising(self) -> None:
        defects = _FakeDefects()
        log_entry = {"status": "SUCCESS"}
        with patch(
            "monkeylm.browser.actions.actions._resolve_type_locator",
            AsyncMock(return_value=(None, "none", None)),
        ), patch(
            "monkeylm.browser.actions.actions._action_scroll",
            AsyncMock(),
        ) as scroll:
            await _action_type(
                _FakePage(),
                None,
                "[id=0]",
                "",
                "",
                [],
                1,
                SimpleNamespace(form_controls=[]),
                None,
                defects,
                None,
                log_entry,
            )

        self.assertEqual(log_entry["status"], "RECOVERED_MISSING_TARGET")
        scroll.assert_awaited_once()
        self.assertEqual(defects.findings[0]["type"], "recovered-missing-target:type")


class TypeabilityAndGuardTests(unittest.TestCase):
    def test_typeable_control_filter(self) -> None:
        self.assertTrue(_is_typeable_control(_control()))
        self.assertTrue(_is_typeable_control(_control(tag_name="textarea", input_type="")))
        self.assertTrue(_is_typeable_control(_control(tag_name="select", input_type="select-one")))
        self.assertFalse(_is_typeable_control(_control(input_type="checkbox")))
        self.assertFalse(_is_typeable_control(_control(tag_name="button", input_type="")))
        self.assertFalse(_is_typeable_control(_control(disabled=True)))
        self.assertFalse(_is_typeable_control(_control(readonly=True)))
        self.assertFalse(_is_typeable_control(_control(visible=False)))

    def test_repair_type_plan_keeps_valid_target(self) -> None:
        action, target, repair = _repair_type_plan(
            "type", "[id=0]", SimpleNamespace(form_controls=[_control(control_id=0)])
        )
        self.assertEqual((action, target, repair), ("type", "[id=0]", None))

    def test_repair_type_plan_repairs_to_first_typeable_control(self) -> None:
        action, target, repair = _repair_type_plan(
            "type", "[id=0]", SimpleNamespace(form_controls=[_control(control_id=2)])
        )
        self.assertEqual(action, "type")
        self.assertEqual(target, "[id=2]")
        self.assertEqual(repair["repaired_from"], "[id=0]")
        self.assertEqual(repair["repaired_to"], "[id=2]")

    def test_repair_type_plan_rejects_when_no_typeable_control(self) -> None:
        action, target, repair = _repair_type_plan(
            "type",
            "[id=0]",
            SimpleNamespace(form_controls=[_control(tag_name="button", input_type="")]),
        )
        self.assertEqual(action, "scroll")
        self.assertEqual(target, "")
        self.assertEqual(repair["rejected_from"], "[id=0]")

    def test_failed_plan_guard_scopes_by_dom_hash(self) -> None:
        guard = FailedPlanGuard()
        signature = build_plan_signature(
            "https://quranml.samehash.com/",
            "hash-1",
            {"action": "type", "target": "[id=0]"},
        )
        guard.record_failure(signature)

        self.assertTrue(guard.should_skip(signature))
        self.assertFalse(
            guard.should_skip(
                build_plan_signature(
                    "https://quranml.samehash.com/",
                    "hash-2",
                    {"action": "type", "target": "[id=0]"},
                )
            )
        )
        guard.clear()
        self.assertFalse(guard.should_skip(signature))


if __name__ == "__main__":
    unittest.main()
