"""MonkeyLM embedded resources package.

All bundled third-party assets live here so that no runtime network
fetches are required (e.g., axe-core injection bypasses CSP).
"""

from pathlib import Path

RESOURCES_DIR = Path(__file__).parent.resolve()
AXE_CORE_PATH = RESOURCES_DIR / "axe-core.min.js"

__all__ = ["RESOURCES_DIR", "AXE_CORE_PATH"]
