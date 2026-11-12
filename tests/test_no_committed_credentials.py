"""Fail the build if a live-looking credential is committed in Python source.

The trigger: a real Redis password was committed as a *code default* in three
modules (and later as a test fixture). Credentials belong only in the
gitignored ``.env``, so this scans every tracked ``.py`` file for inline
connection-string credentials.

Scope is deliberately narrow and measured: connection-string userinfo
(``scheme://user:password@host``). API-key shapes and other secret formats are
NOT covered — this is a guard for the defect class that actually occurred, not
a general secret scanner.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: ``scheme://userinfo@host`` — capture the userinfo for inspection.
_URL_USERINFO_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://([^/\s\"'@]+)@")

#: Passwords that are obviously not secrets. Deliberately broad: a false
#: negative here (a real password containing "password") is less harmful than
#: flagging every documentation example.
_PLACEHOLDER_MARKERS = (
    "placeholder",
    "your_password",
    "your-password",
    "yourpassword",
    "password",
    "changeme",
    "example",
    "redacted",
    "***",
    "$",
    "{",
    "<",
)


def _inline_credentials(text: str) -> list[str]:
    """Return ``scheme://user:password@host`` occurrences whose password looks real."""
    found = []
    for match in _URL_USERINFO_RE.finditer(text):
        userinfo = match.group(1)
        if ":" not in userinfo:
            continue  # user only, no password component
        _user, _, password = userinfo.partition(":")
        if not password or not password.strip():
            # An empty password component is not a secret (e.g. "user:@host").
            continue
        lowered = password.lower()
        if any(marker in lowered for marker in _PLACEHOLDER_MARKERS):
            continue
        found.append(match.group(0))
    return found


def _tracked_python_files() -> list[Path]:
    proc = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.py"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        pytest.skip("git is unavailable; cannot enumerate tracked files")
    return [PROJECT_ROOT / rel for rel in proc.stdout.split("\0") if rel]


# ── The guard itself ─────────────────────────────────────────────────────────


def test_no_tracked_python_file_contains_inline_credentials() -> None:
    files = _tracked_python_files()
    assert len(files) > 20, f"expected a real source tree, tracked {len(files)} files"

    findings = []
    for path in files:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for match in _inline_credentials(text):
            line = text[: text.index(match)].count("\n") + 1
            findings.append(f"{path.relative_to(PROJECT_ROOT)}:{line}: {match}")

    assert findings == [], (
        "credentials must live only in the gitignored .env; found:\n  "
        + "\n  ".join(findings)
    )


# ── Non-vacuity: prove the detector actually detects ────────────────────────


@pytest.mark.parametrize(
    "sample",
    [
        # Built by concatenation so this test file does not itself trip the guard.
        "redis://default:" + "supersecret123" + "@db.internal:6379/0",
        "postgresql://postgres:" + "hunter2" + "@db:5432/monkeylm",
        "redis://:" + "LatitudeStylePw" + "@latitude:6379/0",
    ],
)
def test_detector_flags_real_looking_credentials(sample: str) -> None:
    found = _inline_credentials(sample)
    assert len(found) == 1, sample
    assert found[0].startswith(sample.split("@")[0]), found


@pytest.mark.parametrize(
    "sample",
    [
        "redis://localhost:6379/0",  # no userinfo at all
        "redis://latitude:6379/0",
        "redis://app-user:" + "dotenv-placeholder-pw" + "@redis.example.internal:6379/0",
        "postgresql://user:" + "${DB_PASS}" + "@host:5432/db",
        "redis://:" + "<password>" + "@host:6379/0",
        "https://api.example.com/v1",  # no credentials
    ],
)
def test_detector_ignores_placeholders_and_plain_urls(sample: str) -> None:
    assert _inline_credentials(sample) == []
