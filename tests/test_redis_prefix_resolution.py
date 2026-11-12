"""``REDIS_PREFIX`` resolution, including the "no prefix" case.

Two behaviours are pinned here:

1. An **empty** ``REDIS_PREFIX`` means "no prefix". It used to be
   inexpressible: the empty string is falsy, so resolution fell back to the
   default and clearing the prefix silently kept ``monkey:``.
2. Every key the app writes goes through ``build_redis_key``, so scanning by
   prefix is a complete check — no write can escape the namespace.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

_BASE_ENV = "TARGET_URL=https://example.com/\n"


@pytest.fixture()
def resolve(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Resolve ``redis_prefix`` against a temp ``.env`` and a chosen shell layer."""
    import monkeylm.config.defaults as defaults
    import monkeylm.config.env as env_mod

    def _resolve(
        env_body: str = _BASE_ENV,
        *,
        shell_value: str | None = None,
        precedence: str | None = None,
    ) -> str:
        (tmp_path / ".env").write_text(env_body, encoding="utf-8")
        monkeypatch.setattr(env_mod, "PROJECT_ROOT", tmp_path)
        if shell_value is None:
            monkeypatch.setattr(defaults, "_pre_dotenv_env_keys", lambda: frozenset())
            monkeypatch.setattr(defaults, "_pre_dotenv_env_values", lambda: {})
            monkeypatch.delenv("REDIS_PREFIX", raising=False)
        else:
            monkeypatch.setattr(
                defaults, "_pre_dotenv_env_keys", lambda: frozenset({"REDIS_PREFIX"})
            )
            monkeypatch.setattr(
                defaults,
                "_pre_dotenv_env_values",
                lambda: {"REDIS_PREFIX": shell_value},
            )
            monkeypatch.setenv("REDIS_PREFIX", shell_value)
        if precedence is None:
            monkeypatch.delenv("MONKEYLM_ENV_PRECEDENCE", raising=False)
        else:
            monkeypatch.setenv("MONKEYLM_ENV_PRECEDENCE", precedence)
        return env_mod.load_settings(argparse.Namespace()).redis_prefix

    return _resolve


def test_explicit_prefix_is_honoured(resolve) -> None:
    assert resolve(_BASE_ENV + "REDIS_PREFIX=monkey:\n") == "monkey:"


def test_empty_prefix_is_honoured_not_defaulted(resolve) -> None:
    """Regression: ``REDIS_PREFIX=`` must mean "no prefix", not "use the default"."""
    assert resolve(_BASE_ENV + "REDIS_PREFIX=\n") == ""


def test_absent_key_falls_back_to_the_code_default(resolve) -> None:
    from monkeylm.types import Settings

    assert resolve(_BASE_ENV) == Settings().redis_prefix


def test_code_default_matches_documentation() -> None:
    """README and .env.example both document the default as empty."""
    from monkeylm.config.defaults import DEFAULT_REDIS_PREFIX
    from monkeylm.types import Settings

    assert Settings().redis_prefix == ""
    assert DEFAULT_REDIS_PREFIX == ""


# ── Precedence must still apply to this key ──────────────────────────────────


def test_shell_value_used_when_env_file_omits_the_key(resolve) -> None:
    assert resolve(_BASE_ENV, shell_value="shell:") == "shell:"


def test_env_file_beats_shell_by_default(resolve) -> None:
    assert resolve(_BASE_ENV + "REDIS_PREFIX=env:\n", shell_value="shell:") == "env:"


def test_shell_wins_in_rollback_mode(resolve) -> None:
    assert (
        resolve(_BASE_ENV + "REDIS_PREFIX=env:\n", shell_value="shell:", precedence="shell")
        == "shell:"
    )


def test_shell_export_can_also_be_empty(resolve) -> None:
    """An empty shell export clears the prefix in rollback mode."""
    assert resolve(_BASE_ENV + "REDIS_PREFIX=env:\n", shell_value="", precedence="shell") == ""


# ── Prefix scan completeness ────────────────────────────────────────────────


def test_build_redis_key_prefixes_the_base_key() -> None:
    from monkeylm.config.helpers import build_redis_key

    assert (
        build_redis_key("monkey:", "monkeylm:visited_states:20260101_000000")
        == "monkey:monkeylm:visited_states:20260101_000000"
    )
    assert (
        build_redis_key("", "monkeylm:visited_states:20260101_000000")
        == "monkeylm:visited_states:20260101_000000"
    )


def test_every_redis_write_applies_the_prefix() -> None:
    """Justifies checking by prefix: no write site can bypass the namespace.

    If a future write forgets ``build_redis_key``, a prefix scan would silently
    miss those keys — exactly the question this test answers.
    """
    import inspect

    from monkeylm.memory import postgres as pg

    writers = [
        pg.PersistenceEngine.increment_visited_state,
        pg.PersistenceEngine.claim_action_path_lock,
    ]
    for writer in writers:
        source = inspect.getsource(writer)
        assert "build_redis_key(self.settings.redis_prefix" in source, (
            f"{writer.__name__} writes a Redis key without the configured prefix"
        )


def test_no_redis_write_outside_the_prefix_helper() -> None:
    """Scan the package for raw client writes that skip ``build_redis_key``."""
    import re

    package = Path(__file__).resolve().parents[1] / "monkeylm"
    raw_write = re.compile(
        r"redis_client\.(hincrby|hset|set|setex|sadd|zadd|lpush|rpush|incr)\("
    )
    offenders = []
    for path in package.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for match in raw_write.finditer(text):
            line_start = text.rfind("\n", 0, match.start()) + 1
            line = text[line_start : text.find("\n", match.start())]
            # The prefixed key is produced on an earlier line in both call sites.
            context = text[max(0, match.start() - 400) : match.start()]
            if "build_redis_key(" not in context:
                offenders.append(f"{path.relative_to(package.parent)}: {line.strip()}")
    assert offenders == [], "unprefixed Redis writes:\n  " + "\n  ".join(offenders)
