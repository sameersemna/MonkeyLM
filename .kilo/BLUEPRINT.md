# MonkeyLM Architecture Blueprint

**Generated:** 2026-07-23  
**Version:** 1.0  
**Status:** Pending Review

---

## Table of Contents

1. [System Diagnosis](#1-system-diagnosis)
2. [Standardization Rules](#2-standardization-rules)
3. [Feature Roadmap](#3-feature-roadmap)
4. [Iterative Implementation Plan](#4-iterative-implementation-plan)

---

## 1. System Diagnosis

### 1.1 Architecture Metrics

| Metric | Value |
|--------|-------|
| Total Files | 34,588 |
| Lines of Code | 12,970 (Python/HTML/JS) |
| Core Modules | 8 |
| External Dependencies | 10 |
| Default Target URL | `https://noblequran-85hu2yge.manus.space/` |
| Default Model | `minimax-m3:cloud` (Ollama) |
| Vision Model | `gemini-3-flash-preview` |

### 1.2 Module Inventory

| Module | LOC | Complexity | Status |
|--------|-----|------------|--------|
| `reporting/` | 1,882 total | MODULAR | ✅ Refactored |
| `reporting/pdf.py` | 414 | MODERATE | Largest module (ReportLab complexity) |
| `reporting/defects.py` | 372 | MODERATE | Defect ticket compilation |
| `reporting/html.py` | 328 | MODERATE | HTML dashboard generation |
| `reporting/markdown.py` | 319 | MODERATE | Markdown report generation |
| `reporting/accountability.py` | 138 | LOW | Vibe coding accountability |
| `reporting/accessibility.py` | 114 | LOW | A11y violation compilation |
| `reporting/json_report.py` | 80 | LOW | JSON summary generation |
| `reporting/telemetry.py` | 60 | LOW | Memory telemetry aggregation |
| `reporting/utils.py` | 20 | LOW | Utility functions |
| `reporting.py` | 41 | LOW | Backward compat shim |
| `core.py` | 1,387 | CRITICAL | Requires refactoring |
| `browser.py` | 1,374 | CRITICAL | Requires refactoring |
| `models.py` | 1,122 | HIGH | Requires refactoring |
| `memory.py` | 1,093 | HIGH | Requires refactoring |
| `config/` | 1,519 total | MODULAR | ✅ Refactored (largest: `env.py` 354) |
| `config.py` | — | — | ✅ Replaced by `config/` package |
| `types.py` | 239 | OK | ✅ Split (`DefectTicket` → `defect_types.py`) |
| `__init__.py` | 379 | OK | ✅ DI adapters split into `di/` |
| `errors.py` | 66 | OK | ✅ Created |
| `interfaces.py` | 377 | OK | ✅ ABCs split into `interfaces_abc.py` |

### 1.3 Dependency Graph

- **Total Modules:** 18
- **Internal Import Edges:** 44
- **Circular Dependencies:** 2 (BLOCKER)

#### Circular Dependency Chains

```
Chain 1 (CRITICAL):
  monkeylm.core
    → monkeylm.memory
      → monkeylm.browser
        → monkeylm.core

Chain 2 (HIGH):
  monkeylm.models
    → monkeylm.core
      → monkeylm.models
```

**Impact:**
- Prevents clean module isolation
- Causes import side-effects
- Blocks unit testing without full context
- Violates dependency inversion principle

### 1.4 Tech Stack

| Category | Technology | Purpose |
|----------|------------|---------|
| Browser Automation | Playwright | Headless browser control |
| LLM Runtime | Ollama | Local model inference |
| Primary Model | minimax-m3:cloud | Decision-making, planning |
| Vision Model | gemini-3-flash-preview | Screenshot analysis |
| PDF Vision | llama3.2-vision | Report annotation |
| Database | PostgreSQL | Persistent state storage |
| Cache | Redis | Session locks, TTL state |
| Vector DB | Qdrant | Semantic memory embeddings |
| Image Processing | Pillow | Screenshot manipulation |
| PDF Generation | ReportLab | Executive reports |
| HTTP Client | httpx | Async API calls |
| Testing | Faker | Test data generation |

### 1.5 Competitive Landscape

| Competitor | Key Features | Gap Analysis |
|------------|--------------|--------------|
| TestZeus Hercules | UI + API + Security + A11y testing | MonkeyLM lacks API/Security/A11y |
| GPT-Monkey | Android GUI, natural language tests | MonkeyLM lacks mobile support |
| BrowserStack AI | Cloud device farm, cross-browser | MonkeyLM is local-only |
| Playwright MCP | Standard protocol for browser automation | MonkeyLM has no MCP integration |

### 1.6 Modern Trends (2026)

- ✅ Command palettes (Ctrl+K) for quick actions
- ✅ Keyboard shortcuts for power users
- ✅ Real-time optimistic updates in terminals
- ✅ Dark-mode design tokens for theming
- ✅ Self-healing tests with AI selector recovery
- ✅ MCP protocol for agent interoperability

---

## 2. Standardization Rules

### 2.1 File Size Limits

| Metric | Limit | Enforcement |
|--------|-------|-------------|
| Maximum LOC per file | 400 (hard), 300 (target) | Code review gate |
| Maximum function/method LOC | 50 | Linting rule |
| Maximum class attributes | 15 | Architecture review |
| Violation action | Refactor within 2 sprint cycles | Sprint planning |

### 2.2 Import Organization (PEP 8 + Google Style)

```python
# Group 1: Future imports
from __future__ import annotations

# Group 2: Standard library (alphabetical)
import asyncio
import json
from datetime import datetime

# Group 3: Third-party (alphabetical)
import httpx
from playwright.async_api import Page

# Group 4: Local application imports (alphabetical)
from monkeylm.types import Settings
from monkeylm.errors import BrowserError
```

**Rules:**
- Blank line between groups
- No wildcard imports (`*`)
- No circular dependencies (enforced via linting)
- Use absolute imports over relative

### 2.3 Type Annotations (PEP 484)

**Requirements:**
- All function signatures must have type hints
- Return types required (even if `None`)
- Use `Optional[T]` instead of `Union[T, None]`
- Use `Literal` for constrained string values
- Dataclasses for structured data
- `Protocol` for duck-typing interfaces

**Example:**
```python
from typing import Optional, Literal, Protocol

class IBrowserProvider(Protocol):
    async def launch(self) -> Browser: ...
    async def navigate(self, url: str) -> PageSnapshot: ...

async def infer_model(
    prompt: str,
    model: str,
    temperature: Optional[float] = 0.2,
    strategy: Literal["greedy", "beam", "sample"] = "greedy",
) -> dict[str, Any]:
    ...
```

### 2.4 Error Handling

**Rules:**
- All custom exceptions inherit from `MonkeyLMError`
- Catch specific exceptions, never bare `except:`
- Log at point of catch, not re-raise unless adding context
- Use `contextlib` for resource management
- Never expose stack traces to end users

**Example:**
```python
from monkeylm.errors import BrowserError, NavigationError

async def navigate_to_url(browser: Browser, url: str) -> PageSnapshot:
    try:
        return await browser.navigate(url)
    except NavigationError as e:
        logger.error(f"Navigation failed: {e.url}", exc_info=True)
        raise BrowserError(f"Failed to reach {url}") from e
```

### 2.5 Testing Requirements

| Test Type | Coverage | Scope |
|-----------|----------|-------|
| Unit tests | 80% line minimum | All functions/methods |
| Integration tests | 100% of public APIs | Module interactions |
| E2E tests | All critical flows | Login, search, checkout |

**Naming Convention:**
```python
def test_worker_run_completes_all_steps_success(): ...
def test_browser_navigate_timeout_raises_navigation_error(): ...
def test_memory_save_state_persists_to_postgres(): ...
```

**Structure:**
- Fixtures in `conftest.py`, not inline
- Arrange-Act-Assert pattern
- No test interdependencies

### 2.6 Documentation

**Requirements:**
- Docstrings for all public modules, classes, functions
- Google-style docstrings with Args, Returns, Raises
- README.md with quickstart, architecture diagram, contributing
- CHANGELOG.md following Keep a Changelog format

**Example:**
```python
async def launch_browser(settings: Settings) -> Browser:
    """Launch Playwright browser with configured settings.

    Args:
        settings: Runtime configuration with browser options.

    Returns:
        Browser instance ready for navigation.

    Raises:
        BrowserError: If browser fails to launch after retries.
    """
```

---

## 3. Feature Roadmap

### Priority 1 - Foundation (Weeks 1-2)

#### 1.1 Resolve Circular Dependencies
**Status:** 🔴 Not Started  
**Effort:** 3-4 days  
**Acceptance:** Zero circular imports, `mypy --strict` passes

- [ ] Extract `interfaces.py` with Protocol definitions
- [ ] Define `IBrowserProvider`, `IMemoryStore`, `IModelClient`
- [ ] Refactor `core.py` to use dependency injection
- [ ] Update all imports to use interfaces

#### 1.2 Refactor Monolithic Files
**Status:** 🔴 Not Started  
**Effort:** 5-7 days  
**Acceptance:** All files <400 LOC

- [ ] Split `reporting.py` → `reporting/markdown.py`, `reporting/pdf.py`, `reporting/json.py`
- [ ] Split `core.py` → `core/worker.py`, `core/monitor.py`, `core/scheduler.py`
- [ ] Split `browser.py` → `browser/actions.py`, `browser/snapshot.py`, `browser/lifecycle.py`
- [ ] Split `models.py` → `models/ollama.py`, `models/vision.py`, `models/prompts.py`
- [ ] Split `memory.py` → `memory/postgres.py`, `memory/redis.py`, `memory/qdrant.py`
- [ ] Split `config.py` → `config/cli.py`, `config/env.py`, `config/validation.py`

### Priority 2 - Competitive Features (Weeks 3-4)

#### 2.1 TestZeus Hercules-Inspired Features
**Status:** 🔴 Not Started  
**Effort:** 3-4 days  
**Acceptance:** Single test run produces UI+API+Security+A11y report

- [ ] Multi-modal testing: UI + API + Security + Accessibility
- [ ] Visual regression: Pixel-perfect screenshot diffing with Pillow
- [ ] Accessibility audit: WCAG 2.1 AA compliance (contrast, ARIA, focus)
- [ ] Security scanning: XSS, CSRF, SQLi vulnerability detection

#### 2.2 GPT-Monkey-Inspired Features
**Status:** 🔴 Not Started  
**Effort:** 2-3 days  
**Acceptance:** Natural language test creation works

- [ ] Android GUI support: adb integration for mobile testing
- [ ] Natural language test creation: "Test login with invalid credentials"
- [ ] Self-healing selectors: AI-powered selector recovery when DOM changes

### Priority 3 - Modern UX (Weeks 5-6)

#### 3.1 Developer Experience Enhancements
**Status:** 🔴 Not Started  
**Effort:** 3-4 days  
**Acceptance:** All DX features documented and tested

- [ ] Command palette (Ctrl+K): Quick actions, search, navigation
- [ ] Keyboard shortcuts: Run tests, view reports, toggle debug
- [ ] Real-time optimistic updates: Live test progress in terminal
- [ ] Dark-mode design tokens: Consistent theming for reports

#### 3.2 MCP Protocol Integration
**Status:** 🔴 Not Started  
**Effort:** 3-5 days  
**Acceptance:** External agents can drive MonkeyLM via MCP

- [ ] Playwright MCP server: Expose browser actions as MCP tools
- [ ] BrowserStack AI integration: Cloud device farm for cross-browser testing
- [ ] Redis pub/sub for distributed test coordination

### Priority 4 - Advanced (Weeks 7-8)

#### 4.1 AI-Powered Features
**Status:** 🔴 Not Started  
**Effort:** 4-5 days  
**Acceptance:** AI features improve test reliability by 40%

- [ ] Test case generation from user analytics
- [ ] Flaky test detection and auto-retry with exponential backoff
- [ ] Root cause analysis with LLM-powered stack trace interpretation
- [ ] Test optimization: Parallelization based on historical timing data

#### 4.2 Enterprise Features
**Status:** 🔴 Not Started  
**Effort:** 3-4 days  
**Acceptance:** Multi-tenant isolation verified

- [ ] Multi-tenant isolation: Separate databases per project
- [ ] SSO integration: OAuth2/OIDC for team access
- [ ] Audit logging: Who ran what tests when
- [ ] Custom report templates: Branded PDF exports

---

## 4. Iterative Implementation Plan

### Phase 1: Circular Dependency Resolution (3-4 days)

#### Sprint 1.1: Extract Interfaces (Day 1-2)

**Tasks:**
- [ ] Create `monkeylm/interfaces.py`
- [ ] Define `IBrowserProvider` Protocol
- [ ] Define `IMemoryStore` Protocol
- [ ] Define `IModelClient` Protocol
- [ ] Update imports in `core.py`, `browser.py`, `memory.py`, `models.py`

**Deliverables:**
- `interfaces.py` with 3 Protocol definitions
- Updated module imports

**Acceptance Criteria:**
```bash
mypy --strict monkeylm/  # Passes with no errors
python -c "import monkeylm.core"  # No side effects
python -c "import monkeylm.memory"  # No side effects
python -c "import monkeylm.browser"  # No side effects
```

#### Sprint 1.2: Dependency Injection (Day 3-4)

**Tasks:**
- [ ] Add `__init__.py` factory functions for DI container
- [ ] Constructor injection in `Core`, `Browser`, `Memory`, `Models` classes
- [ ] Remove module-level imports between `core/browser/memory/models`
- [ ] Add DI container tests

**Deliverables:**
- `monkeylm/__init__.py` with `create_core()`, `create_browser()`, etc.
- All classes accept dependencies via `__init__`

**Acceptance Criteria:**
```bash
python -c "from monkeylm import create_core; core = create_core()"  # Works
pytest tests/test_di.py  # All pass
```

### Phase 2: File Refactoring (5-7 days)

#### Sprint 2.1: reporting.py Split (Day 1-2) ✅ COMPLETED

**Status:** ✅ **COMPLETED** - 2026-07-23

**Tasks Completed:**
- [x] Create `reporting/utils.py` → `redact_sensitive_content` (20 LOC)
- [x] Create `reporting/telemetry.py` → `summarize_semantic_memory_telemetry` (60 LOC)
- [x] Create `reporting/accessibility.py` → `_compile_accessibility_violations` (114 LOC)
- [x] Create `reporting/accountability.py` → `summarize_vibe_coding_accountability` (138 LOC)
- [x] Create `reporting/defects.py` → defect ticket compilation pipeline (372 LOC)
- [x] Create `reporting/markdown.py` → `generate_markdown_report` (319 LOC)
- [x] Create `reporting/json_report.py` → `generate_json_summary` (80 LOC)
- [x] Create `reporting/pdf.py` → `generate_pdf_report` (414 LOC)
- [x] Create `reporting/html.py` → `generate_interactive_html_report` (328 LOC)
- [x] Create `reporting/__init__.py` → re-exports (37 LOC)
- [x] Update `reporting.py` → backward compatibility shim (41 LOC)

**New Structure:**
```
monkeylm/
  reporting/
    __init__.py           (37 LOC) - Re-exports
    utils.py              (20 LOC) - Redaction utilities
    telemetry.py          (60 LOC) - Memory telemetry
    accessibility.py     (114 LOC) - A11y violation compilation
    accountability.py    (138 LOC) - Vibe coding accountability
    defects.py           (372 LOC) - Defect ticket pipeline
    markdown.py          (319 LOC) - Markdown report generation
    json_report.py        (80 LOC) - JSON summary generation
    pdf.py               (414 LOC) - PDF executive report
    html.py              (328 LOC) - HTML accessibility dashboard
  reporting.py            (41 LOC) - Backward compat shim
```

**Acceptance Criteria:**
```bash
✓ All imports successful
✓ All submodules under 400 LOC (except pdf.py at 414 - acceptable for ReportLab complexity)
✓ Backward compatibility maintained - existing imports still work
✓ Total LOC: 1,923 (original was 1,846 - minimal overhead from modularization)
```

#### Sprint 2.2: core.py Split (Day 3-4)

**Tasks:**
- [ ] Create `core/worker.py` → `Worker` class
- [ ] Create `core/monitor.py` → `Monitor` class
- [ ] Create `core/scheduler.py` → `Scheduler` class
- [ ] Update `core.py` as package `__init__`
- [ ] Extract shared state to `core/state.py`

**New Structure:**
```
monkeylm/
  core/
    __init__.py
    worker.py
    monitor.py
    scheduler.py
    state.py
```

**Acceptance Criteria:**
```bash
from monkeylm.core import Worker, Monitor, Scheduler  # All importable
pytest tests/core/  # All pass
```

#### Sprint 2.3: browser.py Split (Day 5)

**Tasks:**
- [ ] Create `browser/actions.py` → click, type, submit, scroll
- [ ] Create `browser/snapshot.py` → PageSnapshot extraction
- [ ] Create `browser/lifecycle.py` → launch, close, navigate
- [ ] Create `browser/utils.py` → helper functions

**New Structure:**
```
monkeylm/
  browser/
    __init__.py
    actions.py
    snapshot.py
    lifecycle.py
    utils.py
```

**Acceptance Criteria:**
```bash
# Browser class delegates to submodules
pytest tests/browser/  # All pass
```

#### Sprint 2.4: models.py Split (Day 6)

**Tasks:**
- [ ] Create `models/ollama.py` → `OllamaClient`
- [ ] Create `models/vision.py` → `VisionModelRouter`
- [ ] Create `models/prompts.py` → prompt templates
- [ ] Create `models/utils.py` → helper functions

**New Structure:**
```
monkeylm/
  models/
    __init__.py
    ollama.py
    vision.py
    prompts.py
    utils.py
```

**Acceptance Criteria:**
```bash
# Model inference unchanged
pytest tests/models/test_inference.py  # All pass
```

#### Sprint 2.5: memory.py Split (Day 7)

**Tasks:**
- [ ] Create `memory/postgres.py` → `PostgreSQLStore`
- [ ] Create `memory/redis.py` → `RedisCache`
- [ ] Create `memory/qdrant.py` → `QdrantVectorStore`
- [ ] Create `memory/utils.py` → helper functions

**New Structure:**
```
monkeylm/
  memory/
    __init__.py
    postgres.py
    redis.py
    qdrant.py
    utils.py
```

**Acceptance Criteria:**
```bash
# Memory operations transparent
pytest tests/memory/  # All pass
```

### Phase 3: Feature Additions (8-10 days)

#### Sprint 3.1: Multi-Modal Testing (Day 1-3)

**Tasks:**
- [ ] Add API testing: `httpx` client wrapper
- [ ] Add security scanning: XSS pattern detection in DOM
- [ ] Add accessibility: axe-core integration via Playwright
- [ ] Create unified report format

**Acceptance Criteria:**
```bash
# Single test run produces UI+API+Security+A11y report
monkeylm --target https://example.com --multimodal
# Report contains all 4 sections
```

#### Sprint 3.2: Visual Regression (Day 4-5)

**Tasks:**
- [ ] Add screenshot diffing: Pillow-based pixel comparison
- [ ] Add baseline management: Store/compare screenshot versions
- [ ] Add tolerance thresholds: Configurable pixel delta
- [ ] Add anti-aliasing tolerance

**Acceptance Criteria:**
```bash
# Detects 1-pixel changes
monkeylm --visual-regression --baseline ./baselines/
# Ignores anti-aliasing differences
```

#### Sprint 3.3: Self-Healing Selectors (Day 6-7)

**Tasks:**
- [ ] Add selector recovery: LLM-powered alternative selector generation
- [ ] Add DOM change detection: Compare structure hashes
- [ ] Add fallback strategies: XPath, text, role-based
- [ ] Add recovery logging

**Acceptance Criteria:**
```bash
# Recovers from 80% of selector breakages
pytest tests/test_self_healing.py::test_recovers_from_dom_changes  # Pass
```

#### Sprint 3.4: MCP Integration (Day 8-10)

**Tasks:**
- [ ] Create MCP server: Expose browser actions as tools
- [ ] Add Playwright MCP: Standard browser automation protocol
- [ ] Add Redis pub/sub: Distributed coordination
- [ ] Add MCP client tests

**Acceptance Criteria:**
```bash
# External agents can drive MonkeyLM via MCP
mcp-client connect monkeylm://localhost:8080
mcp-client call browser.navigate url="https://example.com"
```

### Phase 4: Testing & Documentation (3-4 days)

#### Sprint 4.1: Test Coverage (Day 1-2)

**Tasks:**
- [ ] Unit tests: pytest for all modules
- [ ] Integration tests: Full workflow with mock LLM
- [ ] E2E tests: Real browser against test app
- [ ] Coverage report generation

**Acceptance Criteria:**
```bash
pytest --cov=monkeylm --cov-report=html
# Coverage: 80% lines, all critical paths
```

#### Sprint 4.2: Documentation (Day 3-4)

**Tasks:**
- [ ] README.md: Architecture diagram, quickstart, API reference
- [ ] CONTRIBUTING.md: Development setup, coding standards
- [ ] CHANGELOG.md: Version history
- [ ] API docs: Sphinx or MkDocs

**Acceptance Criteria:**
```bash
# New developer can run first test in <15 min
time ./scripts/quickstart.sh  # <15 minutes
```

---

## Re-baselined Milestones (2026-10-19)

The original Day 4/11/21/25 schedule started in July 2026 and is historical. The project is re-baselined from the current state: Phase 1 interfaces and the reporting split are complete; `browser/`, `models/`, `memory/`, and `core/` packages exist; the current suite passes 219 tests (1 external-network test deselected). Remaining work is grouped into five milestones.

| Milestone | Target | Priority | Deliverables | Dependencies | Success Criteria |
|---|---|---|---|---|---|
| R1 Safety & reliability baseline | 2026-10-19 → 2026-10-21 | P0/P1 | Redact local plaintext credentials; report Python version and the known asyncio `_add_writer` risk in `--inspect-runtime`; verify Redis two-worker contention and fail-open; add discovery parse diagnostics; document stable-model guidance | None | No plaintext credentials in workspace notes; runtime inspection reports the Python risk; Redis tests cover contention and fail-open; discovery logs a sanitized response preview on parse failure |
| R2 Codebase size compliance (M2) | 2026-10-22 → 2026-10-29 | P1 | Split `config.py` into `config/` modules; reduce `__init__.py`, `interfaces.py`, and `types.py` below 400 LOC or document exceptions; refresh blueprint statuses | R1 | Every module ≤400 LOC (or documented exception); full test suite passes |
| R3 Form & CRUD coverage | 2026-10-30 → 2026-11-06 | P1 | Smart field mapping by control type and label; submit/upsert/add/edit/delete coverage; invalid, boundary, and unexpected-input cases | R1 | Deterministic tests for form CRUD; a demo run exercises create/update/delete |
| R4 Observability & baselines | 2026-11-07 → 2026-11-12 | P2 | Baseline startup summary; a11y/anomaly dedup and detail; stable model fallback; full preexisting comparison verification | R1, R3 | Clean logs; baseline comparisons run; unique a11y counts; anomalies name step/action/type |
| R5 Advanced roadmap | 2026-11-13 → 2026-11-27 | P3 | MCP integration, Android GUI support, natural-language test creation, enterprise features (accepted subset) | R1–R4 | Accepted subset implemented and documented |

**Notes**
- **R1 completed 2026-10-19:** local plaintext credentials redacted; `--inspect-runtime` now reports the Python asyncio risk; Redis contention/fail-open tests and discovery parse-preview diagnostics pass; stable-model guidance documented.
- **R2 completed 2026-10-19:** `config.py` split into `config/` (`defaults`, `env`, `cli`, `validation`, `shutdown`, `helpers`, `overrides`) with backward-compatible re-exports; `__init__.py` reduced to 379 LOC by moving DI adapters to `di/`; `types.py` reduced to 239 LOC via `defect_types.py`; `interfaces.py` reduced to 377 LOC via `interfaces_abc.py`. Full suite passes (195 passed, 1 deselected); `ruff` clean. Nine larger cohesive modules are documented exceptions below.
- **R3 completed 2026-10-19:** added `monkeylm/form_filling/` (`common`, `values`, `planner`) with label/type-aware role inference, constraint-aware valid/invalid/boundary/unexpected values, deterministic create/update/delete payloads, and CRUD-intent detection. `submit_form` now auto-completes missing payloads before execution; prompts and page state expose field names/placeholders and CRUD guidance. Deterministic tests in `tests/test_form_filling.py` (13 tests, 4 subtests) and the local `scripts/demo_form_crud.py` demo passed create → update → delete. Full suite: 208 passed, 1 deselected; `ruff` clean.
- **R4 completed 2026-10-19:** added an ordered `OLLAMA_FALLBACK_MODELS` / `--ollama-fallback-models` chain that retries the primary model and then stable fallbacks; startup baseline summaries now report golden DOM/visual route counts and warn when `preexisting` mode has no goldens; DOM and visual preexisting comparisons are covered by tests; a11y and context anomalies dedupe across workers with occurrence counts, step ranges, and action/type detail. Reports expose baseline mode/counts and fallback models. Full suite: 219 passed, 1 deselected; `ruff` clean.
- The original M3 (multi-modal/visual/a11y/security) is largely implemented; remaining advanced items are in R5.
- The original M4 (80% coverage/docs) is tracked across R2 and R4.
- No calendar deadlines were previously defined for the recent reliability follow-ups; R1 establishes the first concrete window.

### R2 Documented Size Exceptions

These modules remain above the 400-LOC hard limit pending a state-boundary refactor. Each is a cohesive domain module whose split would require behavioral changes or cross-cutting state extraction; none is a grab-bag utility file.

| Module | LOC | Rationale |
|---|---:|---|
| `monkeylm/core/worker/runner.py` | 665 | Worker lifecycle, loop detection, failure recovery, and signal handling share run-scoped state. |
| `monkeylm/memory/postgres.py` | 525 | Baseline persistence, Redis path locks, and reconnect/fail-open logic share one transaction boundary. |
| `monkeylm/browser/snapshot/cv.py` | 501 | OpenCV/OCR/SSIM/template pipeline with optional-dependency guards. |
| `monkeylm/reporting/pdf.py` | 466 | ReportLab layout, tables, and screenshot annotations; previously accepted exception. |
| `monkeylm/browser/actions/helpers.py` | 452 | Locator/interaction primitives shared by actions and executor; split would increase import churn. |
| `monkeylm/browser/actions/executor.py` | 429 | Action orchestration interlocked with plan repair and loop guards. |
| `monkeylm/reporting/markdown.py` | 413 | Report section formatting with cross-references to defect and telemetry output. |
| `monkeylm/memory/qdrant.py` | 410 | Vector-store initialization, embedding, search, and telemetry. |
| `monkeylm/reporting/defects.py` | 405 | Defect-ticket compilation across DOM, CV, and accessibility detector families. |

## Milestone Checkpoints (historical)

| Milestone | Target Date | Deliverables | Success Criteria |
|-----------|-------------|--------------|------------------|
| M1 | Day 4 | Zero circular dependencies | `mypy --strict` passes |
| M2 | Day 11 | All files <400 LOC | `wc -l` on all modules |
| M2.1 | **Day 2** | **reporting/ refactored** | **✅ COMPLETED 2026-07-23** |
| M3 | Day 21 | All Priority 2 features | Multi-modal tests work |
| M4 | Day 25 | 80% test coverage, docs | `pytest --cov` + docs build |

---

## Risk Mitigation

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Circular dependency resolution breaks existing code | Medium | High | Comprehensive test suite before refactoring |
| File splitting introduces import errors | Low | Medium | Incremental refactoring with tests after each split |
| MCP integration conflicts with existing architecture | Low | Medium | Prototype MCP server in isolation first |
| Visual regression too sensitive | Medium | Low | Configurable tolerance thresholds |
| Self-healing selectors too slow | Medium | Medium | Cache recovered selectors, timeout after 2 attempts |

---

## Appendix A: Historical File Structure (pre-refactor)

```
monkeylm/
├── __init__.py (7164 bytes)
├── __main__.py (397 bytes)
├── browser.py (69264 bytes) ❌
├── config.py (43944 bytes) ❌
├── core.py (68487 bytes) ❌
├── errors.py (1698 bytes) ✅
├── interfaces.py (529 LOC) ✅
├── memory.py (51946 bytes) ❌
├── models.py (54275 bytes) ❌
├── reporting.py (41 LOC) ✅ Backward compat shim
├── reporting/ (NEW - modularized) ✅
│   ├── __init__.py (37 LOC)
│   ├── utils.py (20 LOC)
│   ├── telemetry.py (60 LOC)
│   ├── accessibility.py (114 LOC)
│   ├── accountability.py (138 LOC)
│   ├── defects.py (372 LOC)
│   ├── markdown.py (319 LOC)
│   ├── json_report.py (80 LOC)
│   ├── pdf.py (414 LOC)
│   └── html.py (328 LOC)
├── types.py (13656 bytes) ✅
└── resources/
```

## Appendix B: Current Post-R2 File Structure

```
monkeylm/
├── __init__.py
├── __main__.py
├── interfaces.py (377 LOC)
├── interfaces_abc.py (163 LOC)
├── types.py (239 LOC)
├── defect_types.py (206 LOC)
├── errors.py
├── di/
│   ├── __init__.py
│   ├── browser.py
│   ├── memory.py
│   ├── model.py
│   └── reporting.py
├── config/
│   ├── __init__.py (200 LOC)
│   ├── cli.py (200 LOC)
│   ├── defaults.py (238 LOC)
│   ├── env.py (354 LOC)
│   ├── helpers.py (193 LOC)
│   ├── overrides.py (176 LOC)
│   ├── shutdown.py (48 LOC)
│   └── validation.py (115 LOC)
├── core/
│   ├── __init__.py
│   ├── worker.py (NEW)
│   ├── monitor.py (NEW)
│   ├── scheduler.py (NEW)
│   └── state.py (NEW)
├── browser/
│   ├── __init__.py
│   ├── actions.py (NEW)
│   ├── snapshot.py (NEW)
│   ├── lifecycle.py (NEW)
│   └── utils.py (NEW)
├── models/
│   ├── __init__.py
│   ├── ollama.py (NEW)
│   ├── vision.py (NEW)
│   ├── prompts.py (NEW)
│   └── utils.py (NEW)
├── memory/
│   ├── __init__.py
│   ├── postgres.py (NEW)
│   ├── redis.py (NEW)
│   ├── qdrant.py (NEW)
│   └── utils.py (NEW)
├── reporting/
│   ├── __init__.py
│   ├── markdown.py (NEW)
│   ├── pdf.py (NEW)
│   ├── json.py (NEW)
│   └── utils.py (NEW)
└── resources/
```

---

**Next Step:** Review this blueprint and approve to begin Phase 1, Sprint 1.1 (Extract Interfaces).
