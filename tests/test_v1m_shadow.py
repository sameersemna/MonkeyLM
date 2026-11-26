from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from monkeylm.config import load_settings
from monkeylm.models.v1m import (
    V1MDecisionError,
    build_v1m_candidates,
    decide_v1m,
    evaluate_v1m_shadow_decision,
    get_v1m_action_risk,
    get_v1m_probability_threshold,
    normalize_v1m_base_url,
    parse_v1m_choice,
    v1m_candidate_to_action_plan,
    reset_v1m_circuit_breaker,
    write_v1m_call_log,
)
from monkeylm.reporting.telemetry import summarize_v1m_shadow_telemetry
from monkeylm.types import Settings


class _FakeResponse:
    def __init__(self, payload=None, status_code=200, headers=None, text=None) -> None:
        self._payload = payload
        self.status_code = status_code
        self.headers = dict(headers or {})
        self.text = str(payload) if text is None else text

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


def test_loop_context_filters_seen_blacklisted_failed_and_write_candidates() -> None:
    snapshot = SimpleNamespace(
        elements=[
            "[id=6] <BUTTON text=\"Repeated\" />",
            "[id=7] <BUTTON text=\"Alternative\" />",
        ],
        form_controls=[
            SimpleNamespace(
                control_id=8,
                visible=True,
                disabled=False,
                readonly=False,
                tag_name="input",
            ),
        ],
        forms=[
            SimpleNamespace(form_id="form_0", submit_candidate_id=9),
        ],
        modal_count=0,
    )

    candidates = build_v1m_candidates(
        {"action": "click", "target": "[id=6]", "value": ""},
        snapshot,
        max_candidates=6,
        loop_context=True,
        seen_click_targets={"[id=6]", "[id=7]"},
        blacklisted_targets={"[id=6]"},
        failed_targets={"[id=7]"},
        repeat_count=3,
    )

    candidate_ids = {candidate["id"] for candidate in candidates}
    assert "click:[id=6]" in candidate_ids
    assert "click:[id=7]" not in candidate_ids
    assert "type:[id=8]" not in candidate_ids
    assert "submit_form:[id=9]" not in candidate_ids


def test_probability_margin_blocks_weak_rerank() -> None:
    async def _run():
        snapshot = SimpleNamespace(
            elements=[
                "[id=6] <BUTTON text=\"Repeated\" />",
                "[id=7] <BUTTON text=\"Alternative\" />",
            ],
            form_controls=[],
            forms=[],
            modal_count=0,
        )
        payload = {
            "model": "v1m-decision-engine",
            "answers": {
                "next_action": {
                    "choice": "click:[id=7]",
                    "confidence": 0.82,
                    "probabilities": {
                        "click:[id=6]": 0.48,
                        "click:[id=7]": 0.52,
                    },
                }
            }
        }
        with patch(
            "monkeylm.models.v1m.decide_v1m",
            return_value=payload,
        ):
            result = await evaluate_v1m_shadow_decision(
                Settings(
                    v1m_rerank_enabled=True,
                    v1m_api_key="test-key",
                    v1m_probability_margin=0.15,
                ),
                page_state="page",
                snapshot=snapshot,
                primary_plan={"action": "click", "target": "[id=6]", "value": ""},
                loop_context=True,
                seen_click_targets={"[id=6]"},
                repeat_count=3,
            )

        assert result["outcome"] == "weak_rerank"
        assert result["rerank_blocked_reason"] == "probability_margin"

    asyncio.run(_run())


def test_probability_margin_allows_clear_rerank() -> None:
    async def _run():
        snapshot = SimpleNamespace(
            elements=[
                "[id=6] <BUTTON text=\"Repeated\" />",
                "[id=7] <BUTTON text=\"Alternative\" />",
            ],
            form_controls=[],
            forms=[],
            modal_count=0,
        )
        payload = {
            "model": "v1m-decision-engine",
            "answers": {
                "next_action": {
                    "choice": "click:[id=7]",
                    "confidence": 0.90,
                    "probabilities": {
                        "click:[id=6]": 0.20,
                        "click:[id=7]": 0.80,
                    },
                }
            }
        }
        with patch(
            "monkeylm.models.v1m.decide_v1m",
            return_value=payload,
        ):
            result = await evaluate_v1m_shadow_decision(
                Settings(
                    v1m_rerank_enabled=True,
                    v1m_api_key="test-key",
                    v1m_probability_margin=0.15,
                ),
                page_state="page",
                snapshot=snapshot,
                primary_plan={"action": "click", "target": "[id=6]", "value": ""},
                loop_context=True,
                seen_click_targets={"[id=6]"},
                repeat_count=3,
            )

        assert result["outcome"] == "would_rerank"

    asyncio.run(_run())


def test_v1m_candidate_mapping_rejects_seen_failed_and_form_actions() -> None:
    snapshot = SimpleNamespace(
        elements=[
            "[id=7] <BUTTON text=\"Alternative\" />",
            "[id=16] <BUTTON type=\"submit\" text=\"Submit\" />",
        ],
        form_controls=[],
        forms=[],
        modal_count=0,
    )
    candidates = [
        {"id": "click:[id=7]", "action": "click", "target": "[id=7]"},
        {"id": "submit_form:[id=16]", "action": "submit_form", "target": "[id=16]"},
    ]

    assert v1m_candidate_to_action_plan(
        "click:[id=7]",
        candidates,
        snapshot,
        seen_click_targets={"[id=7]"},
    ) is None
    assert v1m_candidate_to_action_plan(
        "click:[id=7]",
        candidates,
        snapshot,
        failed_targets={"[id=7]"},
    ) is None
    assert v1m_candidate_to_action_plan(
        "submit_form:[id=16]",
        candidates,
        snapshot,
    ) is None


def test_v1m_candidate_mapping_returns_guarded_click() -> None:
    snapshot = SimpleNamespace(
        elements=["[id=7] <BUTTON text=\"Alternative\" />"],
        form_controls=[],
        forms=[],
        modal_count=0,
    )
    candidates = [
        {"id": "click:[id=7]", "action": "click", "target": "[id=7]"},
    ]

    plan = v1m_candidate_to_action_plan("click:[id=7]", candidates, snapshot)

    assert plan is not None
    assert plan["action"] == "click"
    assert plan["target"] == "[id=7]"


def test_v1m_telemetry_counts_rerank_applied_and_blocked() -> None:
    telemetry = summarize_v1m_shadow_telemetry([
        {"v1m_shadow": {"status": "evaluated", "outcome": "would_rerank", "rerank_applied": True, "rerank_blocked_reason": ""}},
        {"v1m_shadow": {"status": "evaluated", "outcome": "weak_rerank", "rerank_applied": False, "rerank_blocked_reason": "probability_margin"}},
        {"v1m_shadow": {"status": "fallback", "outcome": "timeout", "rerank_applied": False, "rerank_blocked_reason": ""}},
    ])

    assert telemetry["rerank_applied"] == 1
    assert telemetry["rerank_blocked"] == 1
    assert telemetry["outcomes"]["would_rerank"] == 1
    assert telemetry["outcomes"]["weak_rerank"] == 1
    assert telemetry["outcomes"]["timeout"] == 1


def test_v1m_timeout_default_is_three_seconds() -> None:
    assert Settings().v1m_timeout_seconds == 3.0


def test_parse_v1m_choice_tracks_model_pin_and_alias() -> None:
    payload = {
        "model": "v1m-decision-engine",
        "answers": {
            "next_action": {
                "choice": "scroll",
                "confidence": 0.9,
                "probabilities": {"scroll": 0.9, "back": 0.1},
            }
        },
    }

    parsed = parse_v1m_choice(
        payload,
        requested_model="v1m-decision-engine",
        pin_required=True,
    )
    assert parsed["model_gate"] == "ok"
    assert parsed["model_changed"] is False

    alias_payload = dict(payload)
    alias_payload["model"] = "rev-latest"
    alias = parse_v1m_choice(
        alias_payload,
        requested_model="rev-latest",
        pin_required=True,
    )
    assert alias["model_alias"] is True
    assert alias["model_gate"] == "requested_alias"


def test_action_risk_and_probability_thresholds() -> None:
    settings = Settings()
    candidates = [
        {"id": "scroll", "action": "scroll", "target": "", "description": "Scroll"},
        {"id": "submit_form:[id=9]", "action": "submit_form", "target": "[id=9]", "description": "Submit form"},
        {"id": "type:[id=3]", "action": "type", "target": "[id=3]", "description": "Enter value"},
    ]

    assert get_v1m_action_risk("scroll", candidates) == "read"
    assert get_v1m_action_risk("type:[id=3]", candidates) == "write"
    assert get_v1m_action_risk("submit_form:[id=9]", candidates) == "destructive"
    assert get_v1m_probability_threshold(settings, "read") == 0.50
    assert get_v1m_probability_threshold(settings, "write") == 0.60
    assert get_v1m_probability_threshold(settings, "destructive") == 0.80


def test_decide_v1m_retries_503_and_records_transport_metadata() -> None:
    import monkeylm.models.v1m as v1m_module

    reset_v1m_circuit_breaker()
    calls = []

    class _Client:
        def post(self, url, **kwargs):
            calls.append({"url": url, "kwargs": kwargs})
            if len(calls) == 1:
                return _FakeResponse(
                    payload={"detail": "temporarily unavailable"},
                    status_code=503,
                    headers={"retry-after": "0"},
                    text="temporarily unavailable",
                )
            return _FakeResponse(
                payload={
                    "model": "v1m-decision-engine",
                    "answers": {
                        "next_action": {
                            "choice": "scroll",
                            "confidence": 0.9,
                            "probabilities": {"scroll": 0.9, "back": 0.1},
                        }
                    },
                },
                status_code=200,
            )

    settings = Settings(
        v1m_api_key="test-key",
        v1m_model="v1m-decision-engine",
        v1m_max_attempts=2,
        v1m_retry_base_seconds=0.0,
        v1m_retry_max_seconds=0.1,
    )
    with patch.object(v1m_module, "httpx", SimpleNamespace(post=_Client().post)):
        with patch.object(v1m_module.time, "sleep", return_value=None):
            payload = decide_v1m(
                settings,
                state={"url": "https://example.test"},
                questions={"next_action": {"type": "choice", "instructions": "Choose"}},
            )

    assert len(calls) == 2
    transport = payload["_v1m_transport"]
    assert transport["attempt_count"] == 2
    assert transport["retry_count"] == 1
    assert transport["http_status_history"] == [503, 200]
    assert transport["model_gate"] == "ok"


def test_decide_v1m_does_not_retry_quota_429() -> None:
    import monkeylm.models.v1m as v1m_module

    reset_v1m_circuit_breaker()

    class _Client:
        def post(self, url, **kwargs):
            return _FakeResponse(
                payload={"detail": "quota exceeded"},
                status_code=429,
                text="quota exceeded",
            )

    settings = Settings(
        v1m_api_key="test-key",
        v1m_model="v1m-decision-engine",
        v1m_max_attempts=3,
    )
    with patch.object(v1m_module, "httpx", SimpleNamespace(post=_Client().post)):
        with pytest.raises(V1MDecisionError) as exc:
            decide_v1m(
                settings,
                state={"url": "https://example.test"},
                questions={"next_action": {"type": "choice", "instructions": "Choose"}},
            )

    assert exc.value.status_code == 429
    assert exc.value.retry_count == 0
    assert exc.value.attempt_count == 1


def test_decide_v1m_circuit_breaker_opens_after_threshold() -> None:
    import monkeylm.models.v1m as v1m_module

    reset_v1m_circuit_breaker()
    calls = []

    class _Client:
        def post(self, url, **kwargs):
            calls.append(url)
            return _FakeResponse(
                payload={"detail": "unavailable"},
                status_code=503,
                headers={"x-should-retry": "false"},
                text="unavailable",
            )

    settings = Settings(
        v1m_api_key="test-key",
        v1m_model="v1m-decision-engine",
        v1m_max_attempts=1,
        v1m_circuit_breaker_threshold=2,
        v1m_circuit_breaker_cooldown_seconds=60.0,
    )
    with patch.object(v1m_module, "httpx", SimpleNamespace(post=_Client().post)):
        with pytest.raises(V1MDecisionError):
            decide_v1m(
                settings,
                state={"url": "https://example.test"},
                questions={"next_action": {"type": "choice", "instructions": "Choose"}},
            )
        with pytest.raises(V1MDecisionError):
            decide_v1m(
                settings,
                state={"url": "https://example.test"},
                questions={"next_action": {"type": "choice", "instructions": "Choose"}},
            )
        with pytest.raises(V1MDecisionError) as opened:
            decide_v1m(
                settings,
                state={"url": "https://example.test"},
                questions={"next_action": {"type": "choice", "instructions": "Choose"}},
            )

    assert opened.value.kind == "circuit_open"
    assert len(calls) == 2


def test_v1m_model_drift_blocks_rerank() -> None:
    async def _run():
        snapshot = SimpleNamespace(
            elements=["[id=7] <BUTTON text=\"Alternative\" />"],
            form_controls=[],
            forms=[],
            modal_count=0,
        )
        payload = {
            "model": "rev-latest",
            "answers": {
                "next_action": {
                    "choice": "click:[id=7]",
                    "confidence": 0.90,
                    "probabilities": {"scroll": 0.10, "click:[id=7]": 0.90},
                }
            },
        }
        with patch(
            "monkeylm.models.v1m.decide_v1m",
            return_value=payload,
        ):
            result = await evaluate_v1m_shadow_decision(
                Settings(
                    v1m_rerank_enabled=True,
                    v1m_api_key="test-key",
                    v1m_model="v1m-decision-engine",
                    v1m_model_pin_required=True,
                ),
                page_state="page",
                snapshot=snapshot,
                primary_plan={"action": "scroll", "target": "", "value": ""},
                loop_context=True,
                repeat_count=3,
            )

        assert result["outcome"] == "model_drift"
        assert result["rerank_blocked_reason"] == "model_drift"
        assert result["model_changed"] is True

    asyncio.run(_run())


def test_v1m_permutation_check_rejects_candidate_order_flip() -> None:
    async def _run():
        snapshot = SimpleNamespace(
            elements=[
                "[id=6] <BUTTON text=\"Repeated\" />",
                "[id=7] <BUTTON text=\"Alternative\" />",
            ],
            form_controls=[],
            forms=[],
            modal_count=0,
        )
        original = {
            "model": "v1m-decision-engine",
            "answers": {
                "next_action": {
                    "choice": "click:[id=7]",
                    "confidence": 0.90,
                    "probabilities": {"click:[id=6]": 0.20, "click:[id=7]": 0.80},
                }
            },
        }
        reversed_payload = {
            "model": "v1m-decision-engine",
            "answers": {
                "next_action": {
                    "choice": "click:[id=6]",
                    "confidence": 0.90,
                    "probabilities": {"click:[id=6]": 0.80, "click:[id=7]": 0.20},
                }
            },
        }
        with patch(
            "monkeylm.models.v1m.decide_v1m",
            side_effect=[original, reversed_payload],
        ):
            result = await evaluate_v1m_shadow_decision(
                Settings(
                    v1m_rerank_enabled=True,
                    v1m_api_key="test-key",
                    v1m_model="v1m-decision-engine",
                    v1m_model_pin_required=True,
                    v1m_permutation_check_enabled=True,
                ),
                page_state="page",
                snapshot=snapshot,
                primary_plan={"action": "click", "target": "[id=6]", "value": ""},
                loop_context=True,
                seen_click_targets={"[id=6]"},
                repeat_count=3,
            )

        assert result["outcome"] == "weak_rerank"
        assert result["rerank_blocked_reason"] == "candidate_order_flip"
        assert result["permutation_check"]["flip"] is True

    asyncio.run(_run())


def test_write_v1m_call_log_shape_filename_and_redaction(tmp_path) -> None:
    settings = Settings(v1m_logs=True)
    fixed = datetime(2026, 11, 26, 7, 13, 5, tzinfo=timezone.utc)

    path = write_v1m_call_log(
        settings,
        url="https://v1m.ir/v1/systemone",
        request_payload={
            "model": "v1m-decision-engine",
            "authorization": "Bearer v1m_secret",
            "questions": {"next_action": {"type": "choice"}},
        },
        response_payload={
            "status_code": 200,
            "body": {"answers": {"next_action": {"choice": "scroll"}}},
            "api_key": "v1m_secret",
        },
        now=fixed,
        log_dir=tmp_path,
    )

    assert path is not None
    assert path.name == "20261126_071305.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    assert set(record) == {"url", "timestamp", "request", "response"}
    assert record["url"] == "https://v1m.ir/v1/systemone"
    assert record["request"]["method"] == "POST"
    assert record["request"]["payload"]["authorization"] == "[REDACTED]"
    assert record["response"]["body"]["api_key"] == "[REDACTED]"
    assert "v1m_secret" not in path.read_text(encoding="utf-8")


def test_write_v1m_call_log_disabled_creates_no_file(tmp_path) -> None:
    settings = Settings(v1m_logs=False)
    path = write_v1m_call_log(
        settings,
        url="https://v1m.ir/v1/systemone",
        request_payload={"model": "v1m-decision-engine"},
        response_payload={"status_code": 200, "body": {}},
        now=datetime(2026, 11, 26, 7, 13, 5, tzinfo=timezone.utc),
        log_dir=tmp_path,
    )

    assert path is None
    assert list(tmp_path.iterdir()) == []


def test_write_v1m_call_log_same_second_collides_safely(tmp_path) -> None:
    settings = Settings(v1m_logs=True)
    fixed = datetime(2026, 11, 26, 7, 13, 5, tzinfo=timezone.utc)

    first = write_v1m_call_log(
        settings,
        url="https://v1m.ir/v1/systemone",
        request_payload={"model": "v1m-decision-engine"},
        response_payload={"status_code": 200},
        now=fixed,
        log_dir=tmp_path,
    )
    second = write_v1m_call_log(
        settings,
        url="https://v1m.ir/v1/systemone",
        request_payload={"model": "v1m-decision-engine"},
        response_payload={"status_code": 200},
        now=fixed,
        log_dir=tmp_path,
    )

    assert first is not None
    assert second is not None
    assert first.name == "20261126_071305.json"
    assert second.name == "20261126_071305_001.json"
    assert re.fullmatch(r"\d{8}_\d{6}(?:_\d{3})?\.json", first.name)
    assert re.fullmatch(r"\d{8}_\d{6}(?:_\d{3})?\.json", second.name)


def test_v1m_logs_environment_toggle(monkeypatch) -> None:
    monkeypatch.setenv("V1M_LOGS", "true")
    with patch("dotenv.dotenv_values", return_value={}):
        enabled = load_settings()
    assert enabled.v1m_logs is True

    monkeypatch.setenv("V1M_LOGS", "false")
    with patch("dotenv.dotenv_values", return_value={}):
        disabled = load_settings()
    assert disabled.v1m_logs is False


def test_decide_v1m_logs_success_when_enabled(tmp_path) -> None:
    import monkeylm.models.v1m as v1m_module

    reset_v1m_circuit_breaker()

    class _Client:
        def post(self, url, **kwargs):
            return _FakeResponse(
                payload={
                    "model": "v1m-decision-engine",
                    "answers": {
                        "next_action": {
                            "choice": "scroll",
                            "confidence": 0.9,
                            "probabilities": {"scroll": 0.9, "back": 0.1},
                        }
                    },
                },
                status_code=200,
            )

    settings = Settings(
        v1m_logs=True,
        v1m_api_key="test-key",
        v1m_model="v1m-decision-engine",
    )
    with patch.object(v1m_module, "_V1M_LOG_DIR", tmp_path):
        with patch.object(v1m_module, "httpx", SimpleNamespace(post=_Client().post)):
            payload = decide_v1m(
                settings,
                state={"url": "https://example.test"},
                questions={"next_action": {"type": "choice", "instructions": "Choose"}},
            )

    assert payload["answers"]["next_action"]["choice"] == "scroll"
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    record = json.loads(files[0].read_text(encoding="utf-8"))
    assert set(record) == {"url", "timestamp", "request", "response"}
    assert record["response"]["body"]["body"]["model"] == "v1m-decision-engine"
