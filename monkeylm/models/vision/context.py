"""Re-export of the CV-OCR context normalizer for the vision pipeline.

The :func:`monkeylm.browser.snapshot.cv.defect_context` implementation
was added to ``cv.py`` in Phase 2.4 so it sits next to its data source
(``compare_screenshots_cv``).  This shim re-exports it under the
vision subpackage for callers that already import ``from
monkeylm.models.vision import defect_context``.
"""

from monkeylm.browser.snapshot.cv import defect_context

__all__ = ["defect_context"]
