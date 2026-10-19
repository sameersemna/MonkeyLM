"""Deterministic end-to-end demo of label-aware create/update/delete payloads."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.async_api import async_playwright

from monkeylm.browser.actions.helpers import (
    _click_element_resilient,
    _fill_input_resilient,
    _fill_select_option,
    _locator_for_target_id,
    _resolve_interaction_mode,
)
from monkeylm.browser.snapshot.state import get_page_state
from monkeylm.form_filling import build_form_payloads, infer_crud_operation

HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>MonkeyLM CRUD demo</title>
</head>
<body>
  <h1>Records</h1>
  <form id="record-form">
    <label for="full_name">Full Name</label>
    <input id="full_name" name="full_name" type="text" required />
    <label for="email">Email Address</label>
    <input id="email" name="email" type="email" required />
    <label for="age">Age</label>
    <input id="age" name="age" type="number" min="18" max="120" step="1" />
    <label for="role">Role</label>
    <select id="role" name="role">
      <option value="Admin">Admin</option>
      <option value="Editor">Editor</option>
      <option value="Viewer">Viewer</option>
    </select>
    <button id="save" type="submit">Create</button>
  </form>
  <table id="records">
    <tbody></tbody>
  </table>
  <script>
    const records = [];
    let editIndex = null;
    const form = document.getElementById('record-form');
    const save = document.getElementById('save');
    const tbody = document.querySelector('#records tbody');

    function render() {
      tbody.innerHTML = '';
      records.forEach((record, index) => {
        const row = document.createElement('tr');
        row.dataset.index = String(index);
        row.innerHTML = `
          <td>${record.full_name}</td>
          <td>${record.email}</td>
          <td>${record.age}</td>
          <td>${record.role}</td>
          <td>
            <button type="button" data-action="edit" data-index="${index}">Edit</button>
            <button type="button" data-action="delete" data-index="${index}">Delete</button>
          </td>`;
        tbody.appendChild(row);
      });
    }

    form.addEventListener('submit', (event) => {
      event.preventDefault();
      const data = {
        full_name: form.full_name.value,
        email: form.email.value,
        age: form.age.value,
        role: form.role.value,
      };
      if (editIndex === null) {
        records.push(data);
      } else {
        records[editIndex] = data;
      }
      editIndex = null;
      save.textContent = 'Create';
      form.reset();
      render();
    });

    document.addEventListener('click', (event) => {
      const button = event.target.closest('button[data-action]');
      if (!button) return;
      const index = Number(button.dataset.index);
      if (button.dataset.action === 'delete') {
        records.splice(index, 1);
        editIndex = null;
        save.textContent = 'Create';
        form.reset();
        render();
        return;
      }
      const record = records[index];
      editIndex = index;
      form.full_name.value = record.full_name;
      form.email.value = record.email;
      form.age.value = record.age;
      form.role.value = record.role;
      save.textContent = 'Save Changes';
    });

    render();
  </script>
</body>
</html>
"""


async def _fill_form(page, snapshot, payloads) -> None:
    controls = {control.control_id: control for control in snapshot.form_controls}
    for payload in payloads:
        locator = await _locator_for_target_id(page, payload["target"])
        if locator is None:
            raise AssertionError(f"Could not resolve {payload['target']}")
        control_id = int(payload["target"].split("=")[1].rstrip("]"))
        control = controls.get(control_id)
        mode = await _resolve_interaction_mode(locator)
        if mode == "select":
            options = control.options if control is not None else []
            chosen, _ = await _fill_select_option(page, locator, payload["value"], options, "HAPPY_UPSERT")
            if chosen != payload["value"]:
                raise AssertionError(f"Expected select {payload['value']}, got {chosen}")
        elif mode == "checkbox_radio":
            await locator.check(timeout=2200)
        else:
            filled = await _fill_input_resilient(page, locator, payload["value"])
            if not filled:
                raise AssertionError(f"Could not fill {payload['target']}")


async def _submit(page, snapshot) -> None:
    form = snapshot.forms[0]
    submit = await _locator_for_target_id(page, f"[id={form.submit_candidate_id}]")
    if submit is None:
        raise AssertionError("Submit button not found")
    if not await _click_element_resilient(page, submit, timeout_ms=2500):
        raise AssertionError("Submit click failed")
    await page.wait_for_timeout(150)


async def main() -> None:
    output_dir = Path("/tmp/kilo/r3_crud_demo")
    output_dir.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(HTML)

        create_snapshot = await get_page_state(page, 1, phase="create", output_dir=str(output_dir))
        operation = infer_crud_operation(create_snapshot, f"[id={create_snapshot.forms[0].submit_candidate_id}]")
        assert operation == "create", operation
        create_payloads = build_form_payloads(
            create_snapshot,
            f"[id={create_snapshot.forms[0].submit_candidate_id}]",
            operation="create",
        )
        await _fill_form(page, create_snapshot, create_payloads)
        await _submit(page, create_snapshot)
        assert await page.locator("#records tbody tr").count() == 1

        await page.locator('button[data-action="edit"]').first.click()
        update_snapshot = await get_page_state(page, 2, phase="update", output_dir=str(output_dir))
        operation = infer_crud_operation(update_snapshot, f"[id={update_snapshot.forms[0].submit_candidate_id}]")
        assert operation == "update", operation
        update_payloads = build_form_payloads(
            update_snapshot,
            f"[id={update_snapshot.forms[0].submit_candidate_id}]",
            operation="update",
        )
        await _fill_form(page, update_snapshot, update_payloads)
        await _submit(page, update_snapshot)
        row_text = await page.locator("#records tbody tr").first.inner_text()
        assert "Updated" in row_text, row_text
        assert "monkey.test+updated@example.com" in row_text, row_text

        await page.locator('button[data-action="delete"]').first.click()
        await page.wait_for_timeout(100)
        assert await page.locator("#records tbody tr").count() == 0

        await browser.close()

    print("R3 CRUD demo passed: create -> update -> delete")
    print(f"Artifacts: {output_dir}")


if __name__ == "__main__":
    asyncio.run(main())
