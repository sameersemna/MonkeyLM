"""Guard the single-source rule for configuration defaults.

Defaults used to be written three times — ``Settings`` field literals,
``DEFAULT_*`` constants in ``config/defaults.py``, and a fallback block in
``monkeylm/__init__.py``. All three agreed, so no test could fail, but the
``config/defaults.py`` copy was not even consulted during resolution: editing
it changed nothing at runtime.

``config/defaults.py`` now derives every constant from ``Settings``. These
tests fail the build if that is undone, if a new constant or field arrives
without a decision, or if the derivation is replaced by a coincidentally equal
literal.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULTS_PATH = PROJECT_ROOT / "monkeylm" / "config" / "defaults.py"

#: ``Settings`` field name used by each ``DEFAULT_*`` constant, where the
#: mechanical ``DEFAULT_X -> x`` rule does not apply.
_NAME_EXCEPTIONS = {"DEFAULT_WINDOW_SIZE": "browser_window_size"}

#: Constants that intentionally have no ``Settings`` field.
#:
#: ``DEFAULT_OLLAMA_FALLBACK_MODELS`` seeds comma-separated ``.env`` parsing, so
#: it is a string while the dataclass field is a list.
_NON_FIELD_CONSTANTS = {"DEFAULT_OLLAMA_FALLBACK_MODELS"}

#: ``Settings`` fields with no ``DEFAULT_*`` counterpart. They are either
#: derived at runtime (``timestamp``), parsed from ``.env`` directly, or tuning
#: knobs that were never exposed as module constants. Listing them here means a
#: newly added field must be classified instead of silently escaping the check.
_FIELDS_WITHOUT_CONSTANT = {
    "active_seed",
    "allow_no_sandbox_fallback",
    "cv_skip_max_ssim",
    "cv_skip_min_area_ratio",
    "cv_skip_min_dominance",
    "max_consecutive_step_timeouts",
    "qdrant_admin_action",
    "scenario_file",
    "scenario_name",
    "scenario_text",
    "strict_sandbox",
    "timestamp",
    "vision_annotation_max_attempts",
    "vision_annotation_max_tokens",
    "vision_annotation_timeout_seconds",
    "vision_verifier_max_tokens",
}


def _declared_constants() -> dict:
    """Map each ``DEFAULT_*`` constant in ``defaults.py`` to its assigned value."""
    source = DEFAULTS_PATH.read_text(encoding="utf-8")
    module = ast.parse(source)
    declared = {}
    for node in module.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id.startswith("DEFAULT_"):
            declared[target.id] = node.value
    return declared


def _settings_fields() -> set:
    from monkeylm.types import Settings

    # ``_headless_source`` is diagnostic bookkeeping, not configuration.
    return set(Settings.__dataclass_fields__) - {"_headless_source"}


def _field_for(constant: str) -> str:
    return _NAME_EXCEPTIONS.get(constant, constant[len("DEFAULT_") :].lower())


def test_every_default_constant_maps_to_a_settings_field() -> None:
    """A new ``DEFAULT_*`` must be either mapped or explicitly exempted."""
    unmapped = (
        set(_declared_constants()) - _NON_FIELD_CONSTANTS
    ) - {c for c in _declared_constants() if _field_for(c) in _settings_fields()}
    assert unmapped == set(), (
        f"constants with no Settings field: {sorted(unmapped)}. "
        "Derive them from Settings or add them to _NON_FIELD_CONSTANTS."
    )


def test_every_setting_field_is_accounted_for() -> None:
    """A new ``Settings`` field must get a constant or an exemption."""
    covered = {_field_for(c) for c in _declared_constants()} | set(_NAME_EXCEPTIONS.values())
    unaccounted = _settings_fields() - covered - _FIELDS_WITHOUT_CONSTANT
    assert unaccounted == set(), (
        f"Settings fields with no DEFAULT_* constant and no documented exemption: "
        f"{sorted(unaccounted)}"
    )
    # The exemption list must not rot: every entry still has to be a real field.
    stale = _FIELDS_WITHOUT_CONSTANT - _settings_fields()
    assert stale == set(), f"exemptions naming non-existent fields: {sorted(stale)}"


@pytest.mark.parametrize("constant", sorted(_declared_constants()))
def test_constant_matches_its_settings_field(constant: str) -> None:
    """Values agree, so editing ``Settings`` propagates to every consumer."""
    from monkeylm.config import defaults
    from monkeylm.types import Settings

    if constant in _NON_FIELD_CONSTANTS:
        pytest.skip("documented non-field constant")
    field = _field_for(constant)
    assert hasattr(Settings, field), f"{constant} maps to missing field {field}"
    assert getattr(defaults, constant) == getattr(Settings(), field)


def test_window_size_mapping_is_explicit() -> None:
    """The only non-mechanical name mapping must stay covered."""
    assert _field_for("DEFAULT_WINDOW_SIZE") == "browser_window_size"


@pytest.mark.parametrize("constant", sorted(_declared_constants()))
def test_configurable_constants_are_derived_not_literal(constant: str) -> None:
    """Catch a "fix" that restores a literal with a coincidentally equal value.

    Equality alone cannot distinguish ``DEFAULT_REDIS_URL = "redis://..."``
    from ``DEFAULT_REDIS_URL = _DEFAULTS.redis_url``; the source shape can.
    """
    if constant in _NON_FIELD_CONSTANTS:
        pytest.skip("documented non-field constant")

    assigned = _declared_constants()[constant]
    assert isinstance(assigned, ast.Attribute), (
        f"{constant} is assigned {ast.dump(assigned)[:60]} instead of "
        "`_DEFAULTS.<field>`; duplicated literals drift apart."
    )
    assert isinstance(assigned.value, ast.Name) and assigned.value.id == "_DEFAULTS"


def test_changing_a_settings_default_changes_the_constant() -> None:
    """End-to-end proof of derivation, not merely equal values.

    Patching ``__dataclass_fields__[...].default`` has no effect on
    instantiation because ``@dataclass`` bakes defaults into the generated
    ``__init__``, so a dataclass subclass is used instead. ``import monkeylm``
    already computes ``defaults``, hence the reload: a literal assignment would
    survive the reload unchanged, a derived one would not.
    """
    program = (
        "import importlib\n"
        "from dataclasses import dataclass\n"
        "import monkeylm.config.defaults as d\n"
        "import monkeylm.types as t\n"
        "@dataclass\n"
        "class Patched(t.Settings):\n"
        "    redis_url: str = 'redis://probe:6379/9'\n"
        "t.Settings = Patched\n"
        "importlib.reload(d)\n"
        "print(d.DEFAULT_REDIS_URL)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", program],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().splitlines()[-1] == "redis://probe:6379/9", proc.stdout


def test_credentials_are_absent_from_defaults() -> None:
    """A committed default must never carry an inline password."""
    source = DEFAULTS_PATH.read_text(encoding="utf-8")
    leaks = [
        match.group(0)
        for match in re.finditer(r"://[^/\s\"'@]*:[^@\s\"']+@", source)
        if "placeholder" not in match.group(0).lower()
    ]
    assert leaks == [], f"credential-shaped defaults found: {leaks}"
