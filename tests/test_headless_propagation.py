"""Regression tests for headless-mode propagation.

``HEADLESS=true`` was resolving correctly inside ``load_settings`` and being
forwarded to Playwright, but two launch paths bypassed that chain entirely:

* ``scripts/destructive_test.py`` hardcoded ``headless=False``, so the script
  always opened a visible window regardless of ``.env`` or any CLI flag.
* ``monkeylm/di/browser.py::create_browser_provider`` called
  ``load_settings()`` with no ``cli_args``, silently discarding
  ``--headless``/``--headed``.

These tests pin that every launch path honors the resolved setting, and that
the resolution source is reported for diagnostics.
"""

from __future__ import annotations

import argparse
import ast
import pathlib
from typing import Any, Dict

import pytest

from monkeylm.config.env import load_settings
from monkeylm.types import Settings

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _settings(**overrides: Any) -> Settings:
    base: Dict[str, Any] = {"vision_model": "x", "pdf_vision_model": "x"}
    base.update(overrides)
    return Settings(**base)


# ---------------------------------------------------------------------------
# destructive_test.py must not hardcode a headful launch
# ---------------------------------------------------------------------------


def _destructive_test_source() -> str:
    return (REPO_ROOT / "scripts" / "destructive_test.py").read_text(encoding="utf-8")


def test_destructive_test_has_no_hardcoded_headless_literal() -> None:
    """The regression itself: a literal ``headless=False`` wins over config."""
    source = _destructive_test_source()
    assert "headless=False" not in source, (
        "destructive_test.py must not hardcode headless=False; it overrides "
        "HEADLESS in .env and any --headless/--headed flag"
    )
    assert "headless: bool = False" not in source


def test_destructive_test_resolves_headless_from_settings() -> None:
    """The script must defer to the shared configuration chain."""
    source = _destructive_test_source()
    assert "load_settings().headless" in source


def test_destructive_test_exposes_headless_cli_flags() -> None:
    """Tri-state flags mirroring the main CLI."""
    source = _destructive_test_source()
    assert '"--headless"' in source
    assert '"--headed"' in source
    assert "_parser.set_defaults(headless=None)" in source


def test_destructive_test_launch_is_ast_parseable_and_uses_launch_headless() -> None:
    """Statically assert the launch call reads a variable, not a literal."""
    tree = ast.parse(_destructive_test_source())
    launch_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "launch"
    ]
    assert launch_calls, "expected a chromium launch call"
    for call in launch_calls:
        headless_kw = [kw for kw in call.keywords if kw.arg == "headless"]
        assert headless_kw, "launch() must pass an explicit headless= keyword"
        assert not isinstance(headless_kw[0].value, ast.Constant), (
            "headless= must be a resolved value, not a constant"
        )


# ---------------------------------------------------------------------------
# DI provider must not drop CLI flags
# ---------------------------------------------------------------------------


def test_di_browser_provider_forwards_cli_args_to_load_settings() -> None:
    """``create_browser_provider(None)`` must recover the parsed namespace."""
    source = (REPO_ROOT / "monkeylm" / "di" / "browser.py").read_text(encoding="utf-8")
    assert "last_parsed_cli_args" in source, (
        "DI browser provider must forward the parsed CLI namespace so "
        "--headless/--headed are honored"
    )
    assert "load_settings(last_parsed_cli_args())" in source


def test_last_parsed_cli_args_captures_parsed_namespace(monkeypatch) -> None:
    from monkeylm.config import cli as cli_mod

    monkeypatch.setattr(
        "sys.argv", ["prog", "--headed", "--target-url", "https://example.com"]
    )
    cli_mod._LAST_CLI_ARGS = None
    parsed = cli_mod.parse_cli_args()
    assert parsed.headless is False
    assert cli_mod.last_parsed_cli_args() is parsed


# ---------------------------------------------------------------------------
# Resolution source diagnostics
# ---------------------------------------------------------------------------


def test_headless_source_reports_dotenv_layer(monkeypatch) -> None:
    from monkeylm.config import defaults as defaults_mod

    monkeypatch.setattr(defaults_mod, "_pre_dotenv_env_keys", lambda: frozenset())
    settings = load_settings()
    assert settings._headless_source == "dotenv"


def test_headless_source_reports_shell_layer(monkeypatch) -> None:
    from monkeylm.config import defaults as defaults_mod

    monkeypatch.setattr(
        defaults_mod, "_pre_dotenv_env_keys", lambda: frozenset({"HEADLESS"})
    )
    monkeypatch.setenv("HEADLESS", "true")
    settings = load_settings()
    assert settings._headless_source == "shell"


def test_cli_headless_overrides_every_layer_and_is_reported() -> None:
    """CLI wins and is labelled so launch logs can explain the mode."""
    settings = load_settings(argparse.Namespace(headless=False))
    assert settings.headless is False
    assert settings._headless_source == "cli"


def test_headless_source_is_not_part_of_settings_repr() -> None:
    """Diagnostic metadata must not pollute repr-based comparisons/logging."""
    assert "_headless_source" not in repr(Settings())


@pytest.mark.parametrize(
    "requested,expected_source",
    [(True, "cli"), (False, "cli")],
)
def test_launch_kwargs_follow_resolved_headless(requested, expected_source) -> None:
    """The value handed to Playwright is the resolved one."""
    settings = load_settings(argparse.Namespace(headless=requested))
    assert settings.headless is requested
    assert settings._headless_source == expected_source


if __name__ == "__main__":
    pytest.main([__file__])