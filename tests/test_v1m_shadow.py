from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from monkeylm.models.v1m import (
    V1MDecisionError,
    build_v1m_candidates,
    evaluate_v1m_shadow_decision,
    normalize_v1m_base_url,
    parse_v1m_choice,
)
from monkeylm.types import Settings


class _FakeResponse:
    def __init__(self, payload=None, status_code=200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def test_normalize_v1m_base_url() -> None:
    assert normalize_v1m_base_url("https://v1m.ir") == "https://v1m.ir/v1/systemone"
    assert normalize_v1m_base_url("https://v1m.ir/v1") == "https://v1m.ir/v1/systemone"
    assert normalize_v1m_base_url("https://v1m.ir/v1/systemone") == "https://v1m.ir/v1/systemone"


def test_parse_v1m_choice_accepts_live_shape() -> None:
    parsed = parse_v1m_choice(
        {
            "model": "rev-latest",
            "answers": {
                "next_action": {
                    "type": "choice",
                    "choice": "click:[id=3]",
                    "confidence": 0.9,
                    "probabilities": {"click:[id=3]": 0.9, "scroll": 0.1},
                }
            },
            "usage": {"input_tokens": 128, "output_tokens": 32},
        }
    )

    assert parsed["choice"] == "click:[id=3]"
    assert parsed["confidence"] == 0.9
    assert parsed["probabilities"]["click:[id=3]"] == 0.9
    assert parsed["usage"]["input_tokens"] == 128


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"answers": {}},
        {"answers": {"next_action": {"choice": "", "confidence": 0.9}}},
        {"answers": {"next_action": {"choice": "scroll", "confidence": "unknown"}}},
        {"answers": {"next_action": {"choice": "scroll", "confidence": 1.2}}},
    ],
)
def test_parse_v1m_choice_rejects_malformed_payload(payload) -> None:
    with pytest.raises(V1MDecisionError):
        parse_v1m_choice(payload)


def test_build_candidates_is_bounded_and_context_valid() -> None:
    controls = [
        SimpleNamespace(
            control_id=0,
            visible=True,
            disabled=False,
            readonly=False,
            tag_name="input",
        ),
        SimpleNamespace(
            control_id=1,
            visible=True,
            disabled=True,
            readonly=False,
            tag_name="textarea",
        ),
    ]
    snapshot = SimpleNamespace(
        url="https://example.test/",
        elements=[
            "[id=2] <BUTTON text=\"Save\" />",
            "[id=3] <A text=\"Browse\" />",
        ],
        form_controls=controls,
        forms=[
            SimpleNamespace(form_id="form_0", submit_candidate_id=2),
        ],
        modal_count=0,
    )

    candidates = build_v1m_candidates(
        {"action": "scroll", "target": "", "value": ""},
        snapshot,
        max_candidates=3,
    )

    assert len(candidates) <= 3
    assert len({candidate["id"] for candidate in candidates}) == len(candidates)
    assert any(candidate["id"] == "scroll" for candidate in candidates)
    assert all(candidate["action"] in {"scroll", "back", "type", "submit_form", "click"} for candidate in candidates)


def test_shadow_disabled_never_calls_v1m() -> None:
    async def _run():
        with patch("monkeylm.models.v1m.decide_v1m") as decide:
            result = await evaluate_v1m_shadow_decision(
                Settings(v1m_shadow_enabled=False),
                page_state="page",
                snapshot=SimpleNamespace(
                    elements=[],
                    form_controls=[],
                    forms=[],
                    modal_count=0,
                ),
                primary_plan={"action": "scroll", "target": "", "value": ""},
            )
        decide.assert_not_called()
        assert result["outcome"] == "disabled"

    asyncio.run(_run())


def test_shadow_missing_key_does_not_call_v1m() -> None:
    async def _run():
        with patch("monkeylm.models.v1m.decide_v1m") as decide:
            result = await evaluate_v1m_shadow_decision(
                Settings(v1m_shadow_enabled=True, v1m_api_key=""),
                page_state="page",
                snapshot=SimpleNamespace(
                    elements=[],
                    form_controls=[],
                    forms=[],
                    modal_count=0,
                ),
                primary_plan={"action": "scroll", "target": "", "value": ""},
            )
        decide.assert_not_called()
        assert result["outcome"] == "missing_api_key"

    asyncio.run(_run())


def test_shadow_low_confidence_keeps_primary_plan() -> None:
    async def _run():
        payload = {
            "answers": {
                "next_action": {
                    "choice": "scroll",
                    "confidence": 0.4,
                    "probabilities": {"scroll": 0.4, "back": 0.6},
                }
            }
        }
        snapshot = SimpleNamespace(
            elements=["[id=2] <BUTTON text=\"Save\" />"],
            form_controls=[],
            forms=[],
            modal_count=0,
        )
        with patch(
            "monkeylm.models.v1m.decide_v1m",
            return_value=payload,
        ):
            result = await evaluate_v1m_shadow_decision(
                Settings(
                    v1m_shadow_enabled=True,
                    v1m_api_key="test-key",
                    v1m_confidence_threshold=0.75,
                ),
                page_state="page",
                snapshot=snapshot,
                primary_plan={"action": "scroll", "target": "", "value": ""},
            )

        assert result["outcome"] == "low_confidence"
        assert result["confidence"] == 0.4

    asyncio.run(_run())


def test_shadow_invalid_choice_is_fallback() -> None:
    async def _run():
        payload = {
            "answers": {
                "next_action": {
                    "choice": "invented_action",
                    "confidence": 0.95,
                    "probabilities": {"invented_action": 0.95},
                }
            }
        }
        snapshot = SimpleNamespace(
            elements=["[id=2] <BUTTON text=\"Save\" />"],
            form_controls=[],
            forms=[],
            modal_count=0,
        )
        with patch(
            "monkeylm.models.v1m.decide_v1m",
            return_value=payload,
        ):
            result = await evaluate_v1m_shadow_decision(
                Settings(
                    v1m_shadow_enabled=True,
                    v1m_api_key="test-key",
                    v1m_confidence_threshold=0.75,
                ),
                page_state="page",
                snapshot=snapshot,
                primary_plan={"action": "scroll", "target": "", "value": ""},
            )

        assert result["outcome"] == "invalid_choice"
        assert result["status"] == "fallback"

    asyncio.run(_run())


def test_shadow_api_error_is_safe_fallback() -> None:
    async def _run():
        snapshot = SimpleNamespace(
            elements=["[id=2] <BUTTON text=\"Save\" />"],
            form_controls=[],
            forms=[],
            modal_count=0,
        )
        with patch(
            "monkeylm.models.v1m.decide_v1m",
            side_effect=V1MDecisionError(
                "upstream unavailable",
                status_code=503,
                kind="http_error",
            ),
        ):
            result = await evaluate_v1m_shadow_decision(
                Settings(
                    v1m_shadow_enabled=True,
                    v1m_api_key="test-key",
                ),
                page_state="page",
                snapshot=snapshot,
                primary_plan={"action": "scroll", "target": "", "value": ""},
            )

        assert result["outcome"] == "http_error"
        assert result["status"] == "fallback"

    asyncio.run(_run())
