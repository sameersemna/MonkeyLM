"""Stage budgeting for the per-step execution window.

The worker enforces a wall-clock cap around the whole of ``execute_action``
(``_execute_step_with_timeout``). Several stages inside that coroutine are
*optional* or *advisory* — most importantly the vision-LLM screenshot
annotation, which can legitimately take tens of seconds on a reasoning model
or hang indefinitely behind an unresponsive gateway.

When such a stage is given a budget larger than the enclosing step budget the
outer cap always wins: the stage's own graceful-degradation handler is
structurally unreachable and the worker is torn down mid-step, even though the
action itself already succeeded. This module keeps the two in bounds:

* :func:`StepBudget` tracks the monotonic deadline for the current step and how
  much of it each stage consumed.
* :func:`resolve_stage_budget` clamps any stage budget to what is actually left
  in the step, minus a reserve that covers the stages which must still run
  (final snapshot, log flush).

Budgets are derived, never guessed: the annotation budget stays whatever the
operator configured, but is capped by the time the step actually has left.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, Optional

# Time kept aside for the mandatory work that follows an advisory stage:
# the ``final`` screenshot capture, perf/zombie checks, and log assembly.
DEFAULT_STAGE_RESERVE_SECONDS = 4.0

# Floor for any advisory stage. Below this a stage cannot do meaningful work,
# so it is skipped outright rather than burning the step window.
MIN_STAGE_BUDGET_SECONDS = 0.5


@dataclass
class StepBudget:
    """Monotonic deadline plus per-stage accounting for one step.

    ``deadline`` is an absolute ``time.monotonic()`` value or ``None`` when the
    caller did not impose a wall-clock cap (for example in tests that drive
    ``execute_action`` directly).
    """

    deadline: Optional[float] = None
    reserve_seconds: float = DEFAULT_STAGE_RESERVE_SECONDS
    stage_timings: Dict[str, float] = field(default_factory=dict)
    _stage_start: Optional[float] = None
    _stage_name: str = ""

    def remaining(self) -> Optional[float]:
        """Seconds left before the deadline, or ``None`` when uncapped."""
        if self.deadline is None:
            return None
        return max(0.0, self.deadline - time.monotonic())

    def start_stage(self, name: str) -> None:
        """Mark ``name`` as the in-flight stage."""
        self._stage_name = name
        self._stage_start = time.monotonic()

    def end_stage(self) -> Optional[float]:
        """Close the in-flight stage and return its elapsed seconds."""
        if not self._stage_name or self._stage_start is None:
            return None
        elapsed = max(0.0, time.monotonic() - self._stage_start)
        self.stage_timings[self._stage_name] = round(
            self.stage_timings.get(self._stage_name, 0.0) + elapsed, 3
        )
        self._stage_name = ""
        self._stage_start = None
        return elapsed

    def current_stage(self) -> str:
        """Name of the stage currently executing, or ``""`` when idle."""
        return self._stage_name

    def slowest_stage(self) -> Optional[str]:
        """Name of the stage that consumed the most time, if any."""
        if not self.stage_timings:
            return None
        return max(self.stage_timings, key=lambda key: self.stage_timings[key])

    def to_dict(self) -> Dict[str, object]:
        """Serialisable snapshot for ``log_entry`` and failure artifacts."""
        payload: Dict[str, object] = {
            "timings": dict(self.stage_timings),
            "slowest_stage": self.slowest_stage(),
            "reserve_seconds": self.reserve_seconds,
        }
        remaining = self.remaining()
        if remaining is not None:
            payload["remaining_seconds"] = round(remaining, 3)
        if self._stage_name:
            payload["in_flight_stage"] = self._stage_name
        return payload


def resolve_stage_budget(
    configured_seconds: float,
    budget: Optional[StepBudget],
) -> float:
    """Clamp ``configured_seconds`` to the time the step actually has left.

    Returns the budget an advisory stage may use. When the caller passed no
    ``budget`` (uncapped step) the configured value is returned unchanged, so
    tests and direct ``execute_action`` callers keep their previous behavior.
    """
    configured = max(0.0, float(configured_seconds))
    if budget is None:
        return configured

    remaining = budget.remaining()
    if remaining is None:
        return configured

    usable = remaining - max(0.0, float(budget.reserve_seconds))
    if usable <= MIN_STAGE_BUDGET_SECONDS:
        return 0.0
    return max(MIN_STAGE_BUDGET_SECONDS, min(configured, usable))