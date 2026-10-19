"""Test-suite defaults that must not depend on a developer's local .env."""

from __future__ import annotations

import os

os.environ.setdefault("HEADLESS", "true")
