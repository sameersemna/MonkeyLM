"""Export integrity: every name in monkeylm.__all__ must actually resolve.

The package root maintains hand-written re-export lists across three
subpackage import blocks; this test turns "forgot to export" from a silent
runtime surprise into a CI failure. Names legitimately resolving to None
(optional dependency paths) are still considered exported — the contract is
that the *attribute exists*, not that the optional dep is installed.
"""

from __future__ import annotations

import monkeylm


def test_all_exports_resolve():
    missing = [name for name in monkeylm.__all__ if not hasattr(monkeylm, name)]
    assert not missing, f"monkeylm.__all__ names that do not resolve: {missing}"


def test_all_exports_unique():
    dupes = {n for n in monkeylm.__all__ if monkeylm.__all__.count(n) > 1}
    assert not dupes, f"duplicate names in monkeylm.__all__: {dupes}"


def test_cv_exports_present():
    for name in (
        "analyze_screenshot_health",
        "compare_screenshots_cv",
        "compare_screenshots_pixelmatch",
    ):
        assert hasattr(monkeylm, name), f"missing CV export: {name}"
