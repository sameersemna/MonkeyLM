# TESTING STRATEGY

## Monkey vs Smart Monkey

### Classic Monkey Testing
- random clicks/inputs with no memory of state,
- broad but noisy coverage,
- low reproducibility and weak diagnostics.

### Smart Monkey (this project)
- LLM-guided action planning from current page state,
- state revisit awareness (`url::structure_hash`),
- targeted defect signals (a11y, performance, layout, race, security),
- structured artifacts for post-run triage.

In short: this is not pure random chaos; it is guided chaos with instrumentation.

## Strategy Layers in Current Implementation

1. **Exploration Layer**
- `decide_next_action` chooses a single high-value action.
- `apply_state_aware_policy` prevents getting stuck in state loops.

2. **Execution Layer**
- resilient action handlers with fallback locators and navigation waits.

3. **Observation Layer**
- snapshots before/after action,
- visual and layout comparisons,
- monitor-based findings (network/perf/a11y).

4. **Reporting Layer**
- markdown summary for humans,
- JSON summary for automation.

## Fuzzing Inputs and Security Checks

`Fuzzer` payload pool combines:
- OWASP-like attack strings:
  - SQLi style payloads,
  - XSS payloads,
  - path traversal payloads,
  - oversized strings.
- realistic synthetic data (if Faker is available):
  - emails, usernames, names, URIs, random strings.

Security-oriented checks currently include:
- `fuzz-payload-injected` marker when high-risk payloads are used.
- `possible-reflected-input` when suspicious payloads appear in resulting page markup.
- console CSP-related warnings captured via listener.

## Performance and Reliability Signals

- Long task detection (`>50ms`, severe at `>2000ms`).
- JS heap growth spike detection (`>30MB` per action).
- FPS drop detection (`<20 FPS`).
- Zombie UI checks when spinner/disabled state does not recover.
- Layout instability when anchor movement exceeds threshold.

## Run Profiles

### Quick Local Smoke

```bash
python monkey_agent_advanced.py
```

Use when:
- validating new action handlers,
- checking major regressions.

### Deep Inspection Run

- keep defaults or increase `MAX_STEPS` in code,
- run against staging-like environment,
- inspect both markdown and JSON outputs.

### Focused A11y Audit

- reduce random aggression,
- increase cadence of `A11Y_CHECKER.scan` if needed,
- prioritize modal/form states to maximize accessibility surface.

## Interpreting `test_report.md`

Primary sections:
- **Errors Detected**: direct action failures and screenshots.
- **Security Risks**: payload-related and reflection clues.
- **Accessibility Violations**: axe severe findings.
- **Performance Bottlenecks**: long tasks, heap spikes, FPS drops.
- **Visual Regressions**: screenshot diff and layout instability clues.
- **Action Log**: per-step intent and status.

Triage sequence:
1. Start with repeated failures in same target/action.
2. Correlate with screenshots and URL context.
3. Check if issue is deterministic by rerunning with shorter scope.
4. Validate root cause manually in browser.

## Interpreting Error Screenshots

Naming pattern:
- `error_step_<N>.png`

Best use:
- confirm UI state at the failure moment,
- compare with nearest `step_<N>_before/after/final` captures,
- verify whether failure is locator mismatch vs app instability.

## Suggested Test Discipline for Teams

- Keep a stable "smoke" target journey and run it every commit.
- Run deep monkey tests nightly.
- Treat `results.json` as the source for trend metrics.
- Baseline defect categories and watch for drift over time.

## Known Coverage Limits

- LLM can still choose suboptimal actions on ambiguous UIs.
- Complex anti-bot/captcha walls are observed, not bypassed.
- Auth/session-heavy apps may need curated starting context.

This is expected; the framework is intentionally extensible so teams can add domain-specific actions over time.
