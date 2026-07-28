# MonkeyLM Remediation Playbook

## Objective
Turn the latest MonkeyLM report into a bulletproof set of implementation tasks focused on the real failure pattern: click interception, overlay/state instability, and poor recovery from repeated navigation/reset flows.

---

## 1. Immediate Root Cause Hypothesis
The current failures are not random. They cluster around repeated clicking attempts that are failing because the target element appears interactive but is being blocked by another UI layer or by a stale interaction state.

This points to one or more of the following:
- overlay or modal intercepting pointer events
- invisible or non-interactive wrapper capturing the click
- stale floating layer remaining after state reset
- navigation/restart flow leaving the UI in a half-initialized state
- async state transitions causing the UI to become temporarily non-interactive

---

## 2. Fix Priorities

### Priority 1 — Fix click interception and interaction layering
#### Goal
Ensure that clicks land on the intended element and are not blocked by another element.

#### Checklist
- Audit all modals, drawers, overlays, toasts, and floating containers.
- Ensure these layers do not intercept pointer events when not active.
- Verify that only the active overlay receives pointer events.
- Ensure pressable elements are above other content in the stacking context.
- Remove invisible wrappers that capture clicks.
- Confirm that interactive surfaces are not hidden under stale or inactive layers.

#### Verification
- Manually click the affected UI elements.
- Confirm that the intended element receives the event.
- Test repeated clicks after state changes.
- Verify that the app remains interactive after navigation/reset cycles.

---

### Priority 2 — Make reset/navigation recovery robust
#### Goal
When the app is reset or restarted, it should return to a known-good state without leaving stale overlays or inactive layers behind.

#### Checklist
- Add a centralized reset path for navigation and app state.
- Clear pending async work and temporary UI state during reset.
- Dismiss open overlays/modals before reset completes.
- Reset any in-progress loading/interaction flags.
- Ensure the home/default route is fully rehydrated and interactive.

#### Verification
- Trigger reset/restart repeatedly.
- Confirm the UI returns to a clean state each time.
- Confirm no stale overlays remain after recovery.

---

### Priority 3 — Add global async error handling
#### Goal
Prevent silent failures and console anomalies from leaving the app in a broken interaction state.

#### Checklist
- Add global unhandled promise rejection handling.
- Wrap async flows in guarded error handling.
- Surface meaningful fallback UI when requests fail.
- Ensure failed async work does not leave the app in a stuck loading state.

#### Verification
- Simulate failed requests or rejected promises.
- Confirm the app surfaces an error state instead of becoming unresponsive.

---

### Priority 4 — Fix accessibility structure and semantic landmarks
#### Goal
Remove structural issues that make the page harder to use and harder for the test agent to reason about.

#### Checklist
- Ensure a single main landmark exists.
- Add a visible h1 for the primary page title.
- Use semantic landmarks for navigation, header, and content regions.
- Confirm focus order is sensible and not trapped.

#### Verification
- Run accessibility checks.
- Confirm landmark and heading rules pass.

---

### Priority 5 — Reduce layout instability and content shift
#### Goal
Prevent UI elements from jumping or collapsing during interaction.

#### Checklist
- Reserve space for images, iframes, and dynamic content.
- Avoid late-injected content that shifts layout after first paint.
- Ensure loaders and overlays do not cause major reflow.

#### Verification
- Perform repeated navigation and interaction sequences.
- Confirm the layout remains stable.

---

## 3. Recommended Engineering Changes

### A. Introduce a UI interaction guard layer
Add a shared interaction guard that:
- detects whether an element is currently blocked by another element
- avoids clicks on overlays that are not intended to be active
- logs diagnostic context when a click is likely intercepted

### B. Build a centralized app reset routine
Create a single reset/recovery function used by:
- restart flows
- navigation recovery
- onboarding reset
- error recovery

### C. Add a diagnostic event stream
Record:
- last action
- current URL
- current view state
- active overlay state
- known interaction blockers

This will make debugging far more reliable.

---

## 4. Acceptance Criteria
The remediation is complete when:
- repeated clicks on the intended targets succeed reliably
- reset/restart flows return the app to a clean interactive state
- no stale overlays persist after navigation changes
- async failures produce recoverable UI states
- accessibility and layout checks no longer show obvious structural issues

---

## 5. Suggested PR Breakdown

### PR 1 — Interaction-layer hardening
- overlay/interception fixes
- clickability validation
- interaction guard improvements

### PR 2 — Reset and recovery flow
- centralized reset logic
- stale-state clearing
- navigation recovery improvements

### PR 3 — Error handling and observability
- unhandled rejection handling
- diagnostic logging
- recovery UI

### PR 4 — Accessibility and layout stability
- landmarks and heading fixes
- layout shift reduction

---
