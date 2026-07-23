"""UX flow freeze detection."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .defects import DefectTracker


class StallDetector:
    """Detects UX flow freezes when DOM structure or URL stays identical across steps."""

    def __init__(self, defects: DefectTracker, *, threshold: int = 3) -> None:
        self.defects = defects
        self.threshold = max(2, threshold)
        self._history: List[Dict[str, Any]] = []

    def record_state(self, step: int, url: str, structure_hash: str, action: str = "") -> None:
        self._history.append({
            "step": step,
            "url": url,
            "structure_hash": structure_hash,
            "action": action,
        })
        if len(self._history) > self.threshold + 2:
            excess = len(self._history) - (self.threshold + 1)
            self._history = self._history[excess:]

    def check_for_stall(self, step: int, current_action: str) -> Optional[Dict[str, Any]]:
        if len(self._history) < self.threshold:
            return None
        window = self._history[-self.threshold:]
        urls = set(e["url"] for e in window)
        hashes = set(e["structure_hash"] for e in window)
        actions = [e["action"] for e in window]
        all_actions = actions + [current_action]
        passive_actions = {"scroll", "back"}
        meaningful_count = sum(1 for a in all_actions if a not in passive_actions)
        if len(urls) <= 1 and len(hashes) <= 1 and meaningful_count >= self.threshold:
            sentinel = (window or [{}])[0] if window else {}
            finding = {
                "step": step,
                "type": "ux-flow-freeze",
                "description": (
                    f"Page state unchanged across {self.threshold} consecutive steps "
                    f"(URL={sentinel.get('url', 'unknown')!r}, "
                    f"hash={sentinel.get('structure_hash', 'unknown')!r}). "
                    f"Actions attempted: {actions}"
                ),
                "stall_window_steps": window,
                "meaningful_actions_in_window": meaningful_count,
                "url": sentinel.get("url", "unknown"),
                "structure_hash": sentinel.get("structure_hash", "unknown"),
            }
            self.defects.add("ux_flow_freezes", finding)
            return finding
        return None
