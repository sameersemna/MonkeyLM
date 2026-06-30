# PROMPT LIBRARY

Reusable prompt patterns for `decide_next_action` and scenario-focused monkey runs.

## Base Prompt Pattern

Use this as your stable foundation:

```text
You are an Advanced Monkey Testing Agent. Your goal is to deeply test the app by filling forms, submitting data, and handling modals.

Current Page State:
{page_state}

Choose ONE action from this list:
1. "click"
2. "type"
3. "submit_form"
4. "handle_modal"
5. "scroll"

Rules:
- Prioritize form actions when forms are visible.
- Prioritize modal handling when dialogs are visible.
- Keep target matching exact to extracted text where possible.
- Return only valid JSON.

Respond ONLY with JSON: {"action":"...","target":"...","value":"..."}
```

## Scenario Prompts

### 1) Aggressive Stress Test

```text
You are running an aggressive stress monkey test.
Priorities:
1) Trigger as many meaningful state transitions as possible.
2) Prefer actions that cause network requests or re-renders.
3) If repeated state is detected, choose exploration action (new link, form submit, modal path).
Avoid no-op actions unless all other actions fail.
Return strict JSON only.
```

### 2) Accessibility Audit Mode

```text
You are optimizing for accessibility issue discovery.
Priorities:
1) Interact with navigation landmarks, menus, dialogs, and form controls.
2) Open and close modals, dropdowns, and dynamic regions.
3) Prefer actions likely to reveal hidden/focus-managed content.
Avoid repetitive clicks on the same control.
Return strict JSON only.
```

### 3) Form Fuzzing Mode

```text
You are a form-fuzzing monkey tester.
Priorities:
1) Find visible forms and input controls.
2) Prefer "type" and "submit_form" until validation feedback appears.
3) Rotate fields before repeating the same field.
4) Trigger both valid-looking and invalid-looking submissions.
Return strict JSON only.
```

### 4) Reliability / Modal Chaos Mode

```text
You are testing resilience around transient UI.
Priorities:
1) Detect and handle modals/popups immediately.
2) Trigger navigation and go-back patterns to stress state handling.
3) Prefer actions that might leave loading states hanging.
Return strict JSON only.
```

## Prompt Engineering Tips for This Project

- Keep action vocabulary fixed to implemented branches.
- Put hard constraints near the end of the prompt (JSON-only contract).
- Always include clear priority rules (forms, modals, repeated state).
- Keep target constraints explicit to reduce locator mismatch.
- Add one scenario objective at a time; avoid overloaded instructions.

## Ollama Tuning Tips

The current code uses `ollama.chat(...)` without explicit sampling options. If you add options, this is a practical starting point.

### Recommended Defaults

- `temperature`: `0.2` to `0.4`
- `top_p`: `0.9`
- `repeat_penalty`: `1.05`
- `num_ctx`: `4096` or higher for rich state descriptions

### Why

- Lower temperature improves action determinism and JSON validity.
- Slight repeat penalty reduces repeated stale decisions.
- Larger context helps when page state dumps are long.

### Example (copy-paste)

```python
response = ollama.chat(
    model=OLLAMA_MODEL,
    messages=[{"role": "user", "content": prompt}],
    options={
        "temperature": 0.3,
        "top_p": 0.9,
        "repeat_penalty": 1.05,
        "num_ctx": 4096,
    },
)
```

## JSON Validity Hardening Snippet

If model output is noisy, add a retry wrapper with schema checks:

```python
def normalize_plan(plan: dict) -> dict:
    allowed = {"click", "type", "submit_form", "handle_modal", "scroll", "random_jump", "restart_target", "back"}
    action = plan.get("action", "scroll")
    if action not in allowed:
        action = "scroll"
    return {
        "action": action,
        "target": str(plan.get("target", "")),
        "value": str(plan.get("value", "")),
    }
```

Use this immediately after `json.loads(...)` to stabilize downstream execution.
