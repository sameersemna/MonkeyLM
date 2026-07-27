"""Shared interactive-element selector.

This CSS selector is used in two places that must never drift apart:

1. `capture_dom_and_layout` (dom.py) walks matching elements in the page and
   assigns each one a sequential `[id=N]` based on its position among visible
   matches — that numbering is what the decision model sees and reasons
   about ("click [id=0]").
2. `_locator_for_target_id` (actions/helpers.py) re-runs the *same* query to
   resolve a model-chosen `[id=N]` back to a real element to click/type into.

If the two selectors differ, the Nth visible match in one no longer
corresponds to the Nth visible match in the other, and every `[id=N]` the
model picks resolves to the wrong element (or nothing at all — "Element
'[id=N]' not found" on every single click/type). Import this constant in
both places instead of hand-copying the selector string.
"""

from __future__ import annotations

INTERACTIVE_ELEMENTS_SELECTOR = (
    "button, a, input, select, textarea, form, [onclick], "
    '[role="button"], [role="link"], [role="checkbox"], [role="radio"], '
    '[role="switch"], [role="tab"], [role="menuitem"], [role="option"], '
    '[tabindex], [contenteditable="true"]'
)
