"""Worker module - helpers and runner for per-worker execution.

``runner`` imports :mod:`monkeylm.browser.actions`, which in turn imports
``budget`` from this package. Eagerly importing ``runner`` here would therefore
close an import cycle and leave the re-exports below set to ``None`` for
whichever side happened to be imported first. The budget helpers are safe to
expose directly; ``run_worker`` is resolved lazily through ``__getattr__``.
"""

from monkeylm.core.worker.budget import StepBudget, resolve_stage_budget
from monkeylm.core.worker.helpers import (
    build_worker_user_data_dir,
    with_retry_backoff,
    allocate_worker_steps,
)

__all__ = [
    "StepBudget",
    "resolve_stage_budget",
    "build_worker_user_data_dir",
    "with_retry_backoff",
    "allocate_worker_steps",
    "run_worker",
    "_run_worker_with_limit",
]


def __getattr__(name: str):
    if name in {"run_worker", "_run_worker_with_limit"}:
        from monkeylm.core.worker import runner

        return getattr(runner, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")