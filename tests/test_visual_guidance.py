"""Tests for visual-novelty policy guidance (V1) and confidence fusion (V2)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List

from monkeylm.config import STATE_LOOP_THRESHOLD
from monkeylm.models.prompts.antiloop import apply_state_aware_policy
from monkeylm.core.worker.runner import _visual_state_key, _VISUAL_STATE_HAMMING_TOLERANCE
from monkeylm.reporting.defects import _compile_defect_tickets


def _snapshot() -> Any:
    return SimpleNamespace(url="https://app.example/page", dom_hash="dom-1", elements=[])


def _settings() -> Any:
    return SimpleNamespace()


class TestVisualNoveltyPolicy:
    def test_visual_revisit_over_threshold_forces_escape(self):
        plan: Dict[str, Any] = {"action": "click", "target": "[id=3]", "value": ""}
        result = apply_state_aware_policy(
            _settings(), plan, _snapshot(), {}, set(),
            visual_revisit_count=STATE_LOOP_THRESHOLD + 1,
        )
        assert result["action"] in {"random_jump", "restart_target"}

    def test_fresh_visual_state_does_not_force_escape(self):
        plan: Dict[str, Any] = {"action": "click", "target": "[id=3]", "value": ""}
        result = apply_state_aware_policy(
            _settings(), plan, _snapshot(), {}, set(),
            visual_revisit_count=1,
        )
        assert result["action"] == "click"

    def test_loop_breaker_choice_not_overridden_by_visual_revisit(self):
        plan: Dict[str, Any] = {"action": "click", "target": "[id=3]", "value": ""}
        result = apply_state_aware_policy(
            _settings(), plan, _snapshot(), {}, set(),
            loop_break_applied=True,
            visual_revisit_count=STATE_LOOP_THRESHOLD + 5,
        )
        assert result["action"] == "click"

    def test_default_visual_count_is_noop(self):
        # Backward compatibility: callers that don't pass visual_revisit_count
        # get the pre-V1 DOM-only behavior.
        plan: Dict[str, Any] = {"action": "click", "target": "[id=3]", "value": ""}
        result = apply_state_aware_policy(_settings(), plan, _snapshot(), {}, set())
        assert result["action"] == "click"


class TestVisualStateKey:
    def test_identical_hash_buckets_together(self):
        seen: Dict[str, int] = {}
        key = _visual_state_key(seen, "ff" * 32)
        seen[key] = 1
        assert _visual_state_key(seen, "ff" * 32) == key

    def test_near_hash_within_tolerance_buckets_together(self):
        seen: Dict[str, int] = {}
        key = _visual_state_key(seen, "ff" * 32)
        seen[key] = 1
        # Flip 4 bits (one hex digit 0xf -> 0x0 in last position): distance 4.
        near = "ff" * 31 + "f0"
        assert _visual_state_key(seen, near) == key

    def test_distant_hash_gets_own_bucket(self):
        seen: Dict[str, int] = {}
        key = _visual_state_key(seen, "ff" * 32)
        seen[key] = 1
        distant = "00" * 32
        assert _visual_state_key(seen, distant) == distant

    def test_empty_hash_returns_empty(self):
        assert _visual_state_key({"ff" * 32: 1}, "") == ""

    def test_tolerance_constant_sane(self):
        assert 0 < _VISUAL_STATE_HAMMING_TOLERANCE <= 32


class TestConfidenceFusion:
    def _defects(self, **kwargs: List[Dict[str, Any]]) -> Any:
        return SimpleNamespace(
            **{cat: kwargs.get(cat, []) for cat in [
                "security_risks", "context_anomalies", "ux_flow_freezes",
                "validation_failures", "race_findings", "boundary_drift",
                "console_findings", "performance_bottlenecks", "accessibility_violations",
                "visual_regressions", "layout_instability", "regression_findings",
                "rendering_defects",
            ]}
        )

    def test_dom_collapse_plus_blank_screen_is_confirmed(self):
        defects = self._defects(
            layout_instability=[{"step": 5, "type": "dom-collapse", "url": "https://app.example/"}],
            rendering_defects=[{"step": 5, "type": "blank-screen", "url": "https://app.example/"}],
        )
        tickets = _compile_defect_tickets(defects, [])
        by_cat = {t.category: t for t in tickets}
        assert by_cat["layout_instability"].confidence == "confirmed"
        assert by_cat["rendering_defects"].confidence == "confirmed"

    def test_visual_region_plus_layout_instability_is_confirmed(self):
        defects = self._defects(
            visual_regressions=[{"step": 3, "type": "visual-diff-region", "url": "https://app.example/", "regions": []}],
            layout_instability=[{"step": 3, "type": "layout-instability", "url": "https://app.example/"}],
        )
        tickets = _compile_defect_tickets(defects, [])
        visual = next(t for t in tickets if t.category == "visual_regressions")
        assert visual.confidence == "confirmed"

    def test_single_signal_stays_probable(self):
        defects = self._defects(
            rendering_defects=[{"step": 2, "type": "blank-screen", "url": "https://app.example/"}],
        )
        tickets = _compile_defect_tickets(defects, [])
        assert tickets[0].confidence == "probable"

    def test_confidence_serialized_in_to_dict(self):
        defects = self._defects(
            layout_instability=[{"step": 5, "type": "dom-collapse", "url": "https://app.example/"}],
            rendering_defects=[{"step": 5, "type": "blank-screen", "url": "https://app.example/"}],
        )
        tickets = _compile_defect_tickets(defects, [])
        assert tickets[0].to_dict()["confidence"] in {"confirmed", "probable"}
