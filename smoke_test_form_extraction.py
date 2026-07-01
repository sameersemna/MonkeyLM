"""Browser smoke test for capture_dom_and_layout form extraction."""

import asyncio
import sys
sys.path.insert(0, "/home/sameer/Public/Shared/Work/Projects/MonkeyLM")

from playwright.async_api import async_playwright
from monkey_agent_advanced import capture_dom_and_layout, get_page_state, _normalize_form_control_raw


HTML = """
<!DOCTYPE html>
<html>
<head><title>Form Test</title></head>
<body>
  <form id="signup" action="/api/signup" method="post">
    <label for="email">Email Address</label>
    <input id="email" name="email" type="email" required placeholder="you@example.com" />
    <label for="age">Age</label>
    <input id="age" name="age" type="number" min="18" max="120" />
    <textarea id="bio" name="bio" maxlength="200"></textarea>
    <button type="submit">Join</button>
  </form>
</body>
</html>
"""


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(HTML)
        raw = await capture_dom_and_layout(page)

        print("URL:", raw["url"])
        print("Title:", raw["title"])
        print("Elements:")
        for el in raw["elements"]:
            print(" ", el)
        print("\nForm controls:")
        for fc in raw["formControls"]:
            print(" ", fc)
        print("\nForms:")
        for f in raw["forms"]:
            print(" ", f)

        assert len(raw["formControls"]) == 3, f"Expected 3 controls, got {len(raw['formControls'])}"
        assert len(raw["forms"]) == 1, f"Expected 1 form, got {len(raw['forms'])}"
        form = raw["forms"][0]
        assert form["form_id"] == "signup"
        assert form["method"] == "post"
        assert len(form["control_ids"]) == 3
        assert form["submit_candidate_id"] is not None

        email_ctrl = next(c for c in raw["formControls"] if c["id_attr"] == "email")
        assert email_ctrl["semantic_kind"] == "email"
        assert email_ctrl["resolved_label"] == "Email Address"
        assert email_ctrl["required"] is True

        age_ctrl = next(c for c in raw["formControls"] if c["id_attr"] == "age")
        assert age_ctrl["semantic_kind"] == "numeric"
        assert age_ctrl["min_value"] == "18"
        assert age_ctrl["max_value"] == "120"

        bio_ctrl = next(c for c in raw["formControls"] if c["id_attr"] == "bio")
        assert bio_ctrl["semantic_kind"] == "textarea"
        assert bio_ctrl["maxlength"] == 200

        # Verify Python-side normalization of absent minlength/maxlength.
        norm_email = _normalize_form_control_raw(email_ctrl)
        assert norm_email["minlength"] is None
        assert norm_email["maxlength"] is None
        norm_bio = _normalize_form_control_raw(bio_ctrl)
        assert norm_bio["maxlength"] == 200

        # Verify PageSnapshot construction applies normalization.
        snapshot = await get_page_state(page, step_num=1, phase="smoke")
        snap_email = next(fc for fc in snapshot.form_controls if fc.id_attr == "email")
        assert snap_email.minlength is None
        assert snap_email.maxlength is None
        snap_bio = next(fc for fc in snapshot.form_controls if fc.id_attr == "bio")
        assert snap_bio.maxlength == 200

        await browser.close()
        print("\n✅ Browser form extraction smoke test passed.")


if __name__ == "__main__":
    asyncio.run(main())
