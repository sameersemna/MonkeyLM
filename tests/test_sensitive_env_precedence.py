"""Regression tests for ``.env``-authoritative precedence and its diagnostics.

``.env`` is the configuration source of truth: a key it defines wins over a
shell export of the same name, and only CLI flags outrank it. A terminal that
injected ``.env`` before an edit keeps the old value exported, so the
diagnostic must name the winning layer — with credentials masked — instead of
leaving the operator to infer it from a failed connection.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Placeholder credentials only. Live values belong in the untracked ``.env``;
# committing a real service password in a test fixture would both rot (the
# service rotates it) and leak it into version control.
_ENV_URL = "redis://app-user:dotenv-placeholder-pw@redis.example.internal:6379/0"
_SHELL_URL = "redis://:shell-placeholder-pw@retired-redis.example.invalid:6379/0"
_ENV_PW = "dotenv-placeholder-pw"
_SHELL_PW = "shell-placeholder-pw"
_ENV_HOST = "redis.example.internal:6379"
_SHELL_HOST = "retired-redis.example.invalid:6379"

_ACCEPTANCE_SOURCE = """
import argparse
from monkeylm.config import load_settings

settings = load_settings(argparse.Namespace())
print("RESOLVED_REDIS_URL:" + settings.redis_url)
"""


def _notices(env_vars, monkeypatch, shell_values, precedence=None):
    import monkeylm.config.defaults as defaults
    from monkeylm.config.env import _precedence_notices

    monkeypatch.setattr(
        defaults, "_pre_dotenv_env_keys", lambda: frozenset(shell_values)
    )
    monkeypatch.setattr(
        defaults, "_pre_dotenv_env_values", lambda: dict(shell_values)
    )
    if precedence is None:
        monkeypatch.delenv("MONKEYLM_ENV_PRECEDENCE", raising=False)
    else:
        monkeypatch.setenv("MONKEYLM_ENV_PRECEDENCE", precedence)
    return _precedence_notices(env_vars, Path(".env"))


def test_dotenv_wins_and_the_shadowed_shell_value_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notices = _notices({"REDIS_URL": _ENV_URL}, monkeypatch, {"REDIS_URL": _SHELL_URL})

    assert len(notices) == 1
    notice = notices[0]
    assert "REDIS_URL" in notice
    assert "takes precedence" in notice
    # Both layers are identifiable by host, so the operator sees which one won.
    assert _ENV_HOST in notice
    assert _SHELL_HOST in notice
    # Credentials must never be printed verbatim.
    assert _ENV_PW not in notice
    assert _SHELL_PW not in notice


def test_rollback_mode_reports_the_shell_layer_as_winner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notices = _notices(
        {"REDIS_URL": _ENV_URL},
        monkeypatch,
        {"REDIS_URL": _SHELL_URL},
        precedence="shell",
    )

    assert len(notices) == 1
    notice = notices[0]
    assert "MONKEYLM_ENV_PRECEDENCE=shell" in notice
    assert _SHELL_HOST in notice
    assert _ENV_HOST in notice
    assert _ENV_PW not in notice
    assert _SHELL_PW not in notice


def test_identical_layers_are_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _notices({"REDIS_URL": _ENV_URL}, monkeypatch, {"REDIS_URL": _ENV_URL}) == []


def test_key_absent_from_dotenv_is_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing is shadowed when ``.env`` does not define the key."""
    notices = _notices(
        {"REDIS_URL": _ENV_URL},
        monkeypatch,
        {"POSTGRES_DSN": "postgresql://postgres:placeholder-pw@db.example.internal:5432/monkeylm"},
    )
    assert notices == []


def test_dotenv_injected_value_is_not_a_shell_export(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _notices({"REDIS_URL": _ENV_URL}, monkeypatch, {}) == []


def test_cli_override_suppresses_the_notice(monkeypatch: pytest.MonkeyPatch) -> None:
    """A CLI flag already decided the value, so no env layer deserves a notice."""
    import monkeylm.config.defaults as defaults
    from monkeylm.config.env import _precedence_notices

    monkeypatch.setattr(
        defaults, "_pre_dotenv_env_keys", lambda: frozenset({"REDIS_URL"})
    )
    monkeypatch.setattr(
        defaults, "_pre_dotenv_env_values", lambda: {"REDIS_URL": _SHELL_URL}
    )
    monkeypatch.delenv("MONKEYLM_ENV_PRECEDENCE", raising=False)

    assert _precedence_notices({"REDIS_URL": _ENV_URL}, Path(".env"), {"REDIS_URL"}) == []


def test_stale_shell_export_cannot_override_env_file(tmp_path: Path) -> None:
    """End-to-end: the reported bug — an edited ``.env`` must take effect.

    The expected value and the expected masking both come from ``.env`` at run
    time, so this test follows whatever the operator configured instead of
    pinning a credential that will later be stale.
    """
    from urllib.parse import urlparse

    from dotenv import dotenv_values

    dotenv_value = str(dotenv_values(PROJECT_ROOT / ".env").get("REDIS_URL") or "").strip()
    if not dotenv_value:
        pytest.skip("no REDIS_URL in .env to resolve")
    dotenv_password = urlparse(dotenv_value).password or ""

    child_env = dict(os.environ)
    child_env.pop("MONKEYLM_ENV_PRECEDENCE", None)
    child_env["REDIS_URL"] = _SHELL_URL  # stale export from a terminal opened earlier
    child_env["PYTHONPATH"] = str(PROJECT_ROOT)

    proc = subprocess.run(
        [sys.executable, "-c", _ACCEPTANCE_SOURCE],
        cwd=str(tmp_path),
        env=child_env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stderr
    resolved = [
        line.split(":", 1)[1]
        for line in proc.stdout.splitlines()
        if line.startswith("RESOLVED_REDIS_URL:")
    ]
    assert resolved and resolved[-1] == dotenv_value
    # The diagnostic (also appended to service_connectivity.log) names both
    # layers by host but must never print either password. Only the notice line
    # is inspected here: the harness's own RESOLVED echo intentionally carries
    # the full URL.
    notice_lines = [line for line in proc.stdout.splitlines() if "takes precedence" in line]
    assert notice_lines, proc.stdout
    notice = notice_lines[0]
    assert _SHELL_HOST in notice
    assert _SHELL_PW not in notice
    if dotenv_password:
        assert dotenv_password not in notice
