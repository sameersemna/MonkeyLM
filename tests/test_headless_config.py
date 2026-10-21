"""Regression tests for HEADLESS resolution and browser launch mode.

Two defects let the runner launch a headful browser while ``HEADLESS`` was
``true``:

1. ``monkeylm.config.defaults`` calls ``load_dotenv()`` at import time, which
   copies every ``.env`` entry into :data:`os.environ`. ``_env_bool`` then
   could not tell an ``.env`` value from a genuine shell export, so ``.env``
   shadowed an explicit CLI flag.
2. A large block of CLI overrides in ``load_settings`` — ``headless``,
   ``window_size``, ``no_viewport``, the retry policy, and every Qdrant knob —
   was nested inside ``if s.max_steps_per_worker > s.max_steps:``. Those flags
   were applied only when that unrelated clamp fired.

The documented precedence is ``CLI > shell > .env > default``.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

_HEADLESS_MATRIX_SOURCE = """
import argparse
import monkeylm.config.defaults  # noqa: F401  (triggers import-time load_dotenv)
from monkeylm.config import load_settings

settings = load_settings(argparse.Namespace(headless={cli}))
print(settings.headless)
"""


def _resolve_headless_in_subprocess(
    *,
    dotenv_value: str,
    shell_value: str | None,
    cli_value: bool | None,
) -> bool:
    """Resolve ``Settings.headless`` in a clean interpreter.

    A subprocess is required because ``.env`` is loaded into ``os.environ``
    once at import time; mutating it inside the test session would leak
    between tests.
    """
    env_path = PROJECT_ROOT / ".env"
    original = env_path.read_text(encoding="utf-8")
    rewritten = "\n".join(
        f"HEADLESS={dotenv_value}" if line.startswith("HEADLESS=") else line
        for line in original.splitlines()
    )
    env_path.write_text(rewritten + ("\n" if original.endswith("\n") else ""), encoding="utf-8")

    child_env = {k: v for k, v in os.environ.items() if k != "HEADLESS"}
    if shell_value is not None:
        child_env["HEADLESS"] = shell_value

    try:
        proc = subprocess.run(
            [sys.executable, "-c", _HEADLESS_MATRIX_SOURCE.format(cli=repr(cli_value))],
            cwd=str(PROJECT_ROOT),
            env=child_env,
            capture_output=True,
            text=True,
            timeout=120,
        )
    finally:
        env_path.write_text(original, encoding="utf-8")

    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip().splitlines()[-1] == "True"


# ── Precedence contract: CLI > shell > .env > default ─────────────────────────


def test_env_true_and_no_flags_resolves_headless() -> None:
    assert _resolve_headless_in_subprocess(
        dotenv_value="true", shell_value=None, cli_value=None
    ) is True


def test_cli_headless_overrides_env_false() -> None:
    """Regression guard: ``--headless`` used to lose to ``.env``."""
    assert _resolve_headless_in_subprocess(
        dotenv_value="false", shell_value=None, cli_value=True
    ) is True


def test_cli_headed_overrides_env_true() -> None:
    """Regression guard: the reported symptom — ``.env true`` beat ``--headed``."""
    assert _resolve_headless_in_subprocess(
        dotenv_value="true", shell_value=None, cli_value=False
    ) is False


def test_shell_overrides_env() -> None:
    assert _resolve_headless_in_subprocess(
        dotenv_value="false", shell_value="true", cli_value=None
    ) is True
    assert _resolve_headless_in_subprocess(
        dotenv_value="true", shell_value="false", cli_value=None
    ) is False


def test_env_false_without_flags_resolves_headful() -> None:
    assert _resolve_headless_in_subprocess(
        dotenv_value="false", shell_value=None, cli_value=None
    ) is False


# ── CLI overrides must not depend on the max_steps clamp ──────────────────────


def test_cli_overrides_apply_when_clamp_does_not_fire() -> None:
    """``headless`` must resolve even when ``max_steps_per_worker <= max_steps``."""
    from monkeylm.config import load_settings

    settings = load_settings(argparse.Namespace(headless=True, max_steps=10, max_steps_per_worker=5))
    assert settings.headless is True
    assert settings.max_steps_per_worker == 5  # clamp did not fire


def test_cli_overrides_apply_when_clamp_fires() -> None:
    from monkeylm.config import load_settings

    settings = load_settings(argparse.Namespace(headless=False, max_steps=3, max_steps_per_worker=10))
    assert settings.headless is False
    assert settings.max_steps_per_worker == 3  # clamp fired


@pytest.mark.parametrize(
    ("attr", "cli_value", "setting_attr", "expected"),
    [
        ("no_viewport", True, "no_viewport", True),
        ("step_timeout_seconds", 42.0, "step_timeout_seconds", 42.0),
        ("stuck_state_threshold", 9, "stuck_state_threshold", 9),
        ("retry_base_delay_seconds", 2.5, "retry_base_delay_seconds", 2.5),
        ("worker_navigation_retries", 7, "worker_navigation_retries", 7),
    ],
)
def test_sibling_cli_overrides_unblocked(
    attr: str, cli_value: object, setting_attr: str, expected: object
) -> None:
    """Every flag trapped in the clamp must be honoured on its own."""
    from monkeylm.config import load_settings

    settings = load_settings(argparse.Namespace(**{attr: cli_value}))
    assert getattr(settings, setting_attr) == expected


def test_max_steps_clamp_still_applies() -> None:
    from monkeylm.config import load_settings

    settings = load_settings(argparse.Namespace(max_steps=2, max_steps_per_worker=8))
    assert settings.max_steps_per_worker == 2


# ── _env_bool must ignore dotenv-injected values ──────────────────────────────


def test_env_bool_ignores_dotenv_injected_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """A value injected by ``load_dotenv`` must not shadow ``default``.

    ``tests/conftest.py`` exports ``HEADLESS`` in the shell, so a key absent
    from ``.env`` is used here to get the pure dotenv-injection case.
    """
    from monkeylm.config.env import _env_bool

    monkeypatch.setenv("MONKEYLM_DOTENV_ONLY_PROBE", "false")
    assert _env_bool("MONKEYLM_DOTENV_ONLY_PROBE", default=True) is True
    assert _env_bool("MONKEYLM_DOTENV_ONLY_PROBE", default=False) is False


def test_env_bool_honours_real_shell_export(monkeypatch: pytest.MonkeyPatch) -> None:
    import monkeylm.config.defaults as defaults
    from monkeylm.config.env import _env_bool

    monkeypatch.setattr(defaults, "_pre_dotenv_env_keys", lambda: frozenset({"HEADLESS"}))
    monkeypatch.setenv("HEADLESS", "false")
    assert _env_bool("HEADLESS", default=True) is False


# ── The resolved flag must reach Playwright ───────────────────────────────────


def _capture_launch_kwargs(settings) -> dict:
    from monkeylm.browser.lifecycle import launch_context_with_fallback

    captured: dict = {}

    class _Chromium:
        async def launch_persistent_context(self, **kwargs):
            captured.update(kwargs)
            raise SystemExit

    class _Playwright:
        chromium = _Chromium()

    with pytest.raises(SystemExit):
        asyncio.run(
            launch_context_with_fallback(
                _Playwright(),
                settings=settings,
                user_data_dir="/tmp/monkeylm-headless-test",
                worker_label="test",
            )
        )
    return captured


def test_headless_flag_reaches_playwright_launch() -> None:
    from monkeylm.config import load_settings

    settings = load_settings(argparse.Namespace(headless=True))
    assert _capture_launch_kwargs(settings)["headless"] is True


def test_headed_flag_reaches_playwright_launch() -> None:
    from monkeylm.config import load_settings

    settings = load_settings(argparse.Namespace(headless=False))
    assert _capture_launch_kwargs(settings)["headless"] is False