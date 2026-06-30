# EXTENSION GUIDE

This guide shows how to add new behavior to the smart monkey engine without destabilizing existing flows.

## Extension Workflow

1. Add a new action name to the planner prompt in `decide_next_action`.
2. Implement action branch in `execute_action`.
3. Add optional policy shaping in `apply_state_aware_policy`.
4. Add defect hooks and reporting fields if the action introduces new risk types.
5. Validate with a short run (`MAX_STEPS=10`) before full stress runs.

## Step 1: Add Action to Planner Prompt

Inside `decide_next_action`, extend the action list and rules.

Example add-on:

```python
# New planner action list entry
# 6. "download_file": Trigger download links and validate response behavior.
```

Keep the response contract unchanged:

```json
{"action": "...", "target": "...", "value": "..."}
```

## Step 2: Implement Branch in `execute_action`

Use this copy-paste template:

```python
elif action == "download_file":
    # Prefer explicit target if provided by model
    locator = page.get_by_text(target, exact=False).first if target else page.locator("a[download], a[href*='.pdf'], a[href*='.csv']").first
    if await locator.count() == 0:
        raise Exception("No downloadable element found")

    async with page.expect_download(timeout=7000) as download_info:
        await locator.click(timeout=3000)
    download = await download_info.value

    suggested_name = download.suggested_filename
    save_path = os.path.join(OUTPUT_DIR, f"download_step_{step_num}_{suggested_name}")
    await download.save_as(save_path)

    log_entry["download_file"] = os.path.basename(save_path)
```

### Example: `handle_captcha` Branch

```python
elif action == "handle_captcha":
    # Non-bypass strategy: detect and report challenge presence.
    challenge = page.locator("iframe[src*='captcha'], [data-testid*='captcha' i], .g-recaptcha").first
    if await challenge.count() > 0:
        defects.add(
            "security_risks",
            {
                "step": step_num,
                "type": "captcha-detected",
                "target": target,
                "url": page.url,
            },
        )
        log_entry["captcha_detected"] = True
    else:
        raise Exception("No captcha challenge detected")
```

## Step 3: Teach Policy About the New Action

If the action is expensive or stateful, gate it in `apply_state_aware_policy`:

```python
if action_plan.get("action") == "download_file" and revisit_count > 1:
    return {"action": "scroll", "target": "", "value": ""}
```

## Step 4: Add Prompt Hints for Better Recognition

Prompt additions should be explicit and low ambiguity.

Good examples:
- "If an element implies file export (`Download`, `Export`, `CSV`, `PDF`), prefer `download_file`."
- "If challenge widgets (`captcha`, `recaptcha`) are visible, choose `handle_captcha`."

Avoid:
- vague instructions like "do whatever seems best" for edge actions.

## Best Practices for Ollama Prompting

- Keep action names short and unique.
- Keep one JSON schema for all action types.
- Describe target matching rule precisely (already done in current prompt).
- Add hard fallback behavior in code (never rely solely on model correctness).
- Maintain one action per step to preserve debuggability.

## Extension Checklist

- [ ] Action added to planner prompt.
- [ ] Action branch added to `execute_action` with robust fallback.
- [ ] Logging fields added to `log_entry`.
- [ ] Defect taxonomy updated if needed.
- [ ] Report sections updated if introducing new finding types.
- [ ] Short smoke run completed.

## Common Pitfalls

- New action has no reliable locator fallback.
- Action mutates state but skips snapshot telemetry.
- Model emits new action name but executor lacks branch.
- Long-running action blocks loop due to missing timeout.

## Recommended Refactor for Many Actions

When actions exceed ~10 branches, switch from `if/elif` to registry:

```python
ACTION_HANDLERS = {
    "click": handle_click,
    "type": handle_type,
    "submit_form": handle_submit_form,
    "handle_modal": handle_modal_action,
    "scroll": handle_scroll,
    "download_file": handle_download_file,
}
```

This keeps extension work clean and testable.
