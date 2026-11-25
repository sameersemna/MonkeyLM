# MonkeyLM: Advanced Smart Monkey Testing Agent

[![MonkeyLM Deep Test](https://github.com/sameersemna/MonkeyLM/actions/workflows/monkeylm-deep-test.yml/badge.svg)](https://github.com/sameersemna/MonkeyLM/actions/workflows/monkeylm-deep-test.yml)

An LLM-guided, Playwright-powered monkey testing framework for aggressively exploring web apps and surfacing defects across UX, reliability, accessibility, security, and performance.

Run it as a module: `python -m monkeylm` (the full-featured smart monkey engine with defect tracking and report artifacts).

## 🚀 Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
ollama pull llama3.2

# Optional: pull a vision model if you want smart screenshot annotations.
ollama pull llama3.2-vision

# Optional: copy the example environment file and edit values for your setup.
cp .env.example .env

python -m monkeylm
```

Environment variables are resolved in this precedence order:
1. CLI flags (highest)
2. Values in a local `.env` file
3. Shell environment variables
4. Built-in defaults (lowest)

Notes:
- `.env` is the single source of truth. A variable it defines wins over the
  same variable exported in the shell, so a terminal that captured old values
  (for example VS Code's `python.terminal.useEnvFile`) cannot silently win.
  Startup lists **every** key where that happens, with credentials masked.
- **This includes inline prefixes.** `WORKERS=2 python -m monkeylm` is a shell
  export, so a `WORKERS=1` in `.env` still wins and the run uses one worker.
  Startup says so explicitly and names the escape hatch. To override the file,
  use a CLI flag: `python -m monkeylm --workers 2`. Not every setting has a
  flag; where none exists, edit `.env` or use `MONKEYLM_ENV_PRECEDENCE=shell`.
- Shell variables still supply keys `.env` does not define, which keeps CI
  usable. Set `MONKEYLM_ENV_PRECEDENCE=shell` in the process environment to
  restore the older shell-first order.
- The current default model in code is `minimax-m3:cloud`. If you want local Ollama inference, switch to a local model (for example `llama3.2`) in configuration.
- `requirements.txt` installs `Pillow` and `reportlab` for image annotation and PDF generation.
- Test artifacts are written into a timestamped folder like `testrun_YYYYMMDD_HHMMSS/`.

## 🧠 Architecture Diagram

```mermaid
flowchart LR
	 A[Target App] <--> B[Playwright Browser Context]
	 B --> C[get_page_state]
	 C --> D[PageSnapshot + DOM/Structure Hash + Screenshot]
	 D --> E[state_to_prompt]
	 E --> F[Ollama decide_next_action]
	 F --> G[apply_state_aware_policy]
	 G --> H[execute_action]
	 H --> B

	 H --> I[DefectTracker]
	 B --> J[NetworkMonitor]
	 B --> K[PerformanceMonitor]
	 B --> L[A11yChecker]

	 I --> M[test_report.md]
	 I --> N[results.json]
	 H --> O[step screenshots + visual diffs]
```

## ⚙️ Configuration

The advanced runner now supports both environment-variable configuration and CLI overrides.

### Environment Variables (implemented)

| Variable | Default | Purpose |
|---|---|---|
| `TARGET_URL` | `https://noblequran-85hu2yge.manus.space/dashboard` | Start URL for the monkey run. |
| `SCENARIO_TEXT` | unset | Inline natural-language scenario; bypasses LLM application discovery. |
| `SCENARIO_FILE` | unset | Path to a `.json` or plain-text scenario file; bypasses LLM application discovery. |
| `SCENARIO_NAME` | unset | Display name stored in the scenario artifact. |
| `OLLAMA_MODEL` | `minimax-m3:cloud` | Primary LLM model used for action planning. Must exist on the configured endpoint. |
| `OLLAMA_FALLBACK_MODELS` | unset | Comma-separated fallback planning models tried in order when the primary model fails. |
| `OPENAI_BASE_URL` | `http://localhost:11434` | Base URL of an OpenAI-compatible API. Ollama's port 11434 gets `/v1` appended automatically; include the full path for other providers. |
| `OPENAI_API_KEY` | unset | Optional bearer token sent to the configured endpoint. |
| `OLLAMA_TIMEOUT_SECONDS` | `15` | Hard timeout in seconds for each LLM inference call. |
| `V1M_SHADOW_ENABLED` | `false` | Enable the V1M native System One shadow reranker. It records comparisons but never changes the executed plan. |
| `V1M_API_KEY` | unset | Bearer token for the native V1M `/v1/systemone` endpoint. Keep it only in `.env`. |
| `V1M_BASE_URL` | `https://v1m.ir` | V1M API origin. The native `/v1/systemone` endpoint is appended automatically. |
| `V1M_MODEL` | `rev-latest` | V1M System One model name for shadow decisions. |
| `V1M_TIMEOUT_SECONDS` | `2.0` | Hard timeout for each V1M shadow decision. |
| `V1M_CONFIDENCE_THRESHOLD` | `0.75` | Confidence at or above which the shadow result is classified as eligible to adopt in a future non-shadow mode. |
| `V1M_MAX_CANDIDATES` | `6` | Maximum finite action candidates sent to V1M; bounded from 2 to 8. |
| `MAX_STEPS` | `100` | Number of monkey actions to execute. |
| `WORKERS` | `1` | Maximum number of concurrent worker browser contexts. |
| `MAX_STEPS_PER_WORKER` | `100` | Per-worker step cap. Must be less than or equal to `MAX_STEPS`. |
| `WORKER_NAVIGATION_RETRIES` | `2` | Retry count for initial worker navigation to target URL. |
| `WORKER_QDRANT_INIT_RETRIES` | `1` | Retry count for worker-level Qdrant initialization. |
| `WORKER_BOUNDARY_RECOVERY_RETRIES` | `1` | Retry count for out-of-scope boundary recovery navigation. |
| `RETRY_BASE_DELAY_SECONDS` | `0.75` | Base backoff delay in seconds (exponential with jitter). |
| `STEP_TIMEOUT_SECONDS` | `30` | Per-step timeout before the worker aborts the run with a structured runtime failure. |
| `STUCK_STATE_THRESHOLD` | `6` | Consecutive repeated-state steps required before a stuck-state failure is declared. |
| `HEADLESS` | `true` | Run browser headless when enabled. |
| `BROWSER_WINDOW_SIZE` | `1920,1080` | Browser launch size (`width,height` or `widthxheight`). |
| `NO_VIEWPORT` | `true` | Use window size directly instead of Playwright viewport emulation. |
| `POSTGRES_DSN` | `postgresql://localhost:5432/monkeylm` | PostgreSQL connection string. |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL. Only used when `WORKERS >= 2` (cross-worker coordination); single-worker runs skip the connection. |
| `REDIS_PREFIX` | `""` | Prefix for all Redis keys. Empty means no prefix. Keys become `{prefix}monkeylm:visited_states:<run>` and `{prefix}monkeylm:active_path:<hash>`; written only when `WORKERS > 1`. |
| `REDIS_PATH_LOCK_TTL_SECONDS` | `45` | TTL for cross-worker action-path Redis locks. Must be 1–300 seconds. |
| `STRICT_SANDBOX` | `false` | If `true`, Chromium must launch in sandbox mode or the run fails. |
| `ALLOW_NO_SANDBOX_FALLBACK` | `false` | If `true`, fallback to `--no-sandbox` is allowed when sandbox launch fails. |
| `PDF_GENERATE` | `false` | Set to `true` to generate an executive PDF audit report (`test_execution_audit.pdf`). |
| `PDF_VISION_MODEL` | `llama3.2-vision` | Local Ollama vision model used to locate anomalies on annotated screenshots. |
| `PDF_VISION_TIMEOUT_SECONDS` | `30` | Hard timeout for each vision-model annotation call. |
| `CV_ANALYSIS_ENABLED` | `true` | Master switch for OpenCV screenshot analysis (SSIM region localization, blank-screen detection). |
| `CV_SSIM_THRESHOLD` | `0.95` | Per-pixel SSIM cutoff (0.5–1.0) below which pixels count as changed. |
| `CV_BLANK_SCREEN_STDDEV` | `8.0` | Grayscale stddev below which a frame is flagged as a blank/crashed screen. |
| `CV_PHASH_DEDUP` | `true` | Perceptual-hash visual freeze detection and visual-novelty exploration guidance. |
| `CV_TEMPLATE_VERIFY` | `true` | Template-matching verification that expected page chrome (logo/nav) persists. |
| `CV_TEMPLATE_MIN_SCORE` | `0.75` | Minimum multi-scale match score (0.3–1.0) before chrome is reported missing. |
| `CV_TEMPLATE_SCALE` | `0.5` | Downscale factor (0.25–1.0) for template matching; 0.5 roughly quarters matching cost. |
| `CV_TEMPLATE_EVERY_N_STEPS` | `2` | Verify chrome every N steps instead of every step. |
| `OCR_ENABLED` | `false` | Extract error text from failure screenshots via RapidOCR (canvas/iframe/PDF content the DOM cannot reach). |

### Using OpenAI-Compatible LLM Endpoints

All chat, vision, embedding, and reranking calls go through a single
OpenAI-compatible client. Configuration is read from the project-root `.env`
file (see `.env.example`); `.env` is authoritative, so an `OPENAI_*` value
defined there wins over the same variable exported in the shell. CLI flags
still override it.

```bash
# Local Ollama (default; Ollama serves the OpenAI-compatible API on /v1)
OPENAI_BASE_URL=http://localhost:11434

# Hosted OpenAI-compatible providers (include the full API base path)
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=sk-...

# Self-hosted vLLM / LM Studio / LiteLLM
OPENAI_BASE_URL=http://localhost:8000/v1
```

`OPENAI_API_KEY` is optional and is only sent when non-empty. Remote endpoints
should use HTTPS; the plaintext default is intended for loopback Ollama.
The model names in `OLLAMA_MODEL`, `VISION_MODEL`, `PDF_VISION_MODEL`, and
`QDRANT_RERANK_MODEL` must be valid for whichever endpoint you configure.

For backward compatibility, `OLLAMA_HOST` and `OLLAMA_API_KEY` are used as
fallbacks when the corresponding `OPENAI_*` variables are unset.

For application discovery and action planning, prefer a stable text model.
Free-model pools can rotate to slow or unavailable upstreams; the harness logs
model failures and falls back to the heuristic discovery strategy. Use
`--ollama-model <name>` to override the planning model for a single run, or
`OLLAMA_FALLBACK_MODELS=model-a,model-b` / `--ollama-fallback-models model-a,model-b`
to try stable alternatives after the primary model fails.

### V1M System One Shadow Decisions

Set `V1M_SHADOW_ENABLED=true` and `V1M_API_KEY` in `.env` to run V1M beside the
existing action planner. Shadow mode sends only a bounded finite action set
and records the V1M choice, probabilities, confidence, latency, and fallback
outcome. It never replaces or executes the existing primary plan.

The native V1M endpoint is `POST https://v1m.ir/v1/systemone`. V1M is optional;
missing credentials, malformed responses, low confidence, invalid choices,
timeouts, and API errors preserve the current planner and heuristics.

For a visible 20-step CLI run:

```bash
source .venv/bin/activate
V1M_SHADOW_ENABLED=true python -m monkeylm --max-steps 20
```

The CLI prints the V1M endpoint and model at worker startup, then each step
prints the primary choice, finite candidates, V1M choice/probability,
confidence, outcome, and latency. Shadow mode never executes V1M's alternative
plan.

### Selective Vision Audit & Executive PDF

When `PDF_GENERATE=true`, the runner selectively sends only the most relevant
screenshots to a local vision model and compiles a rich executive PDF report.

A step is annotated only when it is genuinely interesting:
- Status is `FAILED` or `CRASH`.
- A security risk, visual regression, or layout instability defect was recorded for that step.

Benign/successful steps are skipped to protect local computing throughput.

**Prerequisite:** you must have a vision model available locally, for example:

```bash
ollama pull llama3.2-vision
```

Equivalent models such as `qwen2.5-vl` can be used by setting `PDF_VISION_MODEL`.

**Example invocation:**

```bash
export PDF_GENERATE=true
python -m monkeylm
```

You can combine it with the standard configuration variables:

```bash
export PDF_GENERATE=true
export PDF_VISION_MODEL=llama3.2-vision
export PDF_VISION_TIMEOUT_SECONDS=30
export TARGET_URL="https://example.com/dashboard"
export MAX_STEPS=50
python -m monkeylm
```

### CLI Overrides

| Flag | Purpose |
|---|---|
| `--target-url` | Override target URL for the run. |
| `--scenario` | Inline natural-language scenario; bypasses LLM application discovery. |
| `--scenario-file` | Load a `.json` or plain-text scenario file. |
| `--scenario-name` | Set the scenario display name used in reports and `scenario.json`. |
| `--ollama-model` | Override model name for the run. |
| `--ollama-timeout-seconds` | Override Ollama inference timeout. |
| `--max-steps` | Override step budget. |
| `--workers` | Override concurrent worker count. |
| `--max-steps-per-worker` | Override per-worker step cap (must be <= `--max-steps`). |
| `--worker-navigation-retries` | Override retries for initial worker navigation. |
| `--worker-qdrant-init-retries` | Override retries for worker Qdrant initialization. |
| `--worker-boundary-recovery-retries` | Override retries for boundary recovery navigation. |
| `--retry-base-delay-seconds` | Override base retry delay for exponential backoff. |
| `--step-timeout-seconds` | Override the per-step timeout before the worker aborts the run. |
| `--stuck-state-threshold` | Override the repeated-state window that triggers stuck-state failure detection. |
| `--redis-prefix` | Override Redis key prefix. |
| `--redis-path-lock-ttl-seconds` | Override cross-worker action-path lock TTL. |
| `--window-size` | Override browser window size. |
| `--headless` / `--headed` | Force headless or headed mode. |
| `--no-viewport` / `--use-viewport` | Force viewport behavior. |
| `--cv-analysis` / `--no-cv-analysis` | Enable/disable OpenCV screenshot analysis. |
| `--cv-ssim-threshold` | Override the SSIM change cutoff. |
| `--cv-blank-screen-stddev` | Override the blank-screen stddev threshold. |
| `--cv-phash-dedup` / `--no-cv-phash-dedup` | Toggle perceptual-hash freeze detection and visual-novelty guidance. |
| `--cv-template-verify` / `--no-cv-template-verify` | Toggle chrome template verification. |
| `--cv-template-min-score` | Override the chrome match-score threshold. |
| `--cv-template-scale` | Override the template-matching downscale factor. |
| `--cv-template-every-n-steps` | Override the chrome verification cadence. |
| `--ocr` / `--no-ocr` | Toggle OCR error-text extraction on failed steps. |

Precedence rule:
- A CLI flag value wins over every other layer.
- A value defined in `.env` wins over the same variable exported in the shell.
- `MONKEYLM_ENV_PRECEDENCE=shell` restores the older shell-first order.

### Example Runs

Environment-driven:

```bash
TARGET_URL="https://example.com/dashboard" \
OLLAMA_MODEL="llama3.2" \
MAX_STEPS=75 \
WORKERS=3 \
MAX_STEPS_PER_WORKER=30 \
WORKER_NAVIGATION_RETRIES=3 \
WORKER_QDRANT_INIT_RETRIES=2 \
WORKER_BOUNDARY_RECOVERY_RETRIES=2 \
RETRY_BASE_DELAY_SECONDS=1.0 \
HEADLESS=true \
BROWSER_WINDOW_SIZE="1600x900" \
NO_VIEWPORT=true \
python -m monkeylm
```

CLI-driven:

```bash
python -m monkeylm \
	--target-url "https://example.com/dashboard" \
	--ollama-model "llama3.2" \
	--max-steps 75 \
	--workers 3 \
	--max-steps-per-worker 30 \
	--worker-navigation-retries 3 \
	--worker-qdrant-init-retries 2 \
	--worker-boundary-recovery-retries 2 \
	--retry-base-delay-seconds 1.0 \
	--step-timeout-seconds 15 \
	--stuck-state-threshold 4 \
	--window-size "1600,900" \
	--headless \
	--no-viewport
```

### Natural-Language Scenarios

Provide a scenario when you want deterministic, user-directed coverage instead of
LLM application discovery:

```bash
python -m monkeylm \
  --target-url "https://example.com/dashboard" \
  --scenario "As a new user, create an account, then edit and delete it. Try invalid emails, empty fields, duplicate accounts, and XSS." \
  --scenario-name "account lifecycle"
```

Scenario files may be plain text or JSON:

```json
{
  "name": "account lifecycle",
  "summary": "Create, update, and delete an account",
  "personas": [
    {"name": "new user", "description": "First-time visitor", "behaviors": ["register", "edit profile"]}
  ],
  "flows": [
    {"name": "create_account", "description": "Register with valid data", "steps": ["open registration", "fill fields", "submit"]},
    {"name": "delete_account", "description": "Remove the account", "steps": ["open settings", "delete", "confirm"]}
  ],
  "edge_cases": ["empty fields", "duplicate email", "invalid email"],
  "security_focus": ["XSS in display name"]
}
```

The runner writes the resolved scenario to `<output_dir>/scenario.json` and uses
it for action planning and report provenance. If both `SCENARIO_TEXT` and
`SCENARIO_FILE` are set, startup fails fast.

### MCP Scenario Tool

A minimal stdio MCP server exposes `create_test_scenario`, which converts
natural-language scenarios into the same structured JSON format:

```json
{
  "mcpServers": {
    "monkeylm-scenario": {
      "command": "/path/to/MonkeyLM/.venv/bin/python",
      "args": ["-m", "monkeylm.mcp_scenario"]
    }
  }
}
```

The MCP tool authors scenarios; execution still runs through `python -m monkeylm`
so the browser, persistence, and reporting lifecycle stays explicit.

Step-budget semantics:
- `MAX_STEPS` is the global total budget for the run.
- `MAX_STEPS_PER_WORKER` caps how many steps one worker can consume.
- If `WORKERS * MAX_STEPS_PER_WORKER < MAX_STEPS`, the run will stop after exhausting capped worker allocations.

## 📐 Concurrency Tuning

Use these baseline settings to avoid overloading local CPU, browser processes, and persistence services.

| Host Size | Suggested `WORKERS` | Suggested `MAX_STEPS_PER_WORKER` | Suggested `MAX_STEPS` |
|---|---:|---:|---:|
| 2 vCPU / 4 GB RAM | 1-2 | 25-50 | 50-100 |
| 4 vCPU / 8 GB RAM | 2-4 | 30-75 | 100-250 |
| 8 vCPU / 16 GB RAM | 4-6 | 40-100 | 250-600 |
| 16+ vCPU / 32+ GB RAM | 6-10 | 50-150 | 500-1200 |

Persistence sizing heuristics used by the runner:
- PostgreSQL pool `min_size = min(4, WORKERS)`, `max_size = max(4, WORKERS * 4)`.
- Redis client `max_connections = max(16, WORKERS * 8)`.

Operational guidance:
- Increase `WORKERS` only after verifying browser stability and service latency under load.
- Keep `MAX_STEPS_PER_WORKER` moderate to improve route diversity across workers.
- If PostgreSQL/Redis are remote, start with lower worker counts (for example 1-2) and scale up gradually.
- With `WORKERS=1`, shared Redis visited-state writes and action-path locks are bypassed; use `WORKERS>1` when cross-worker coordination is required.
- For high-latency remote Redis endpoints, prefer a co-located/local Redis instance or keep worker counts low.
- Prefer `HEADLESS=true` for higher worker density and lower memory pressure.

## 📊 Features

- Smart action loop: `get_page_state -> decide_next_action -> execute_action`.
- Async multi-worker loop with semaphore-bounded concurrency (`--workers`).
- 1920x1080 browser windows with isolated per-worker persistent contexts under `playwright_user_data/session_<timestamp>/worker-XX/`.
- Modal handling with fallback strategy: close button, cancel/dismiss, then `Escape`.
- Form submission paths (`submit` click and `Enter` fallback).
- Label/type-aware form filling with constraint-aware valid, invalid, boundary, and unexpected-type values; deterministic create/update/delete payloads and CRUD-intent detection.
- Baseline startup summaries with golden DOM/visual route counts and preexisting-readiness checks.
- Cross-worker deduplication for accessibility findings and context anomalies, with occurrence and step-range detail.
- Natural-language scenarios (inline, `.txt`, or `.json`) that bypass LLM discovery and drive deterministic flows; resolved scenarios are written to `scenario.json`.
- Minimal stdio MCP server (`python -m monkeylm.mcp_scenario`) exposing `create_test_scenario` to MCP clients.
- Fuzzing with blended payloads (realistic + OWASP-style attack strings).
- Network fault injection for API calls (latency and injected 5xx responses).
- Accessibility checks with axe-core (`critical`/`serious` filtering).
- Performance telemetry (CDP metrics, long tasks, JS heap, FPS sampling).
- Visual regression and layout instability detection via screenshot diffing.
- OpenCV screenshot analysis: SSIM-based diff-region localization (bounding boxes), blank/crash-screen detection, perceptual-hash visual freeze detection, and template-matching verification of expected page chrome.
- Visual-novelty exploration guidance: perceptual state hashing steers the monkey away from visually-identical screens even when DOM hashes differ (canvas apps, randomized IDs).
- Optional OCR error-text extraction on failed steps (RapidOCR, ONNX-based) for content the DOM cannot reach.
- Cross-detector confidence tiers: defects corroborated by both DOM and CV signals are marked `confirmed` in compiled tickets.
- Markdown and JSON reporting (`test_report.md`, `results.json`) plus per-step screenshots.
- Selective smart screenshot annotation: FAILED/CRASH/security/visual/layout steps are annotated with a red bounding box + arrow — drawn directly from high-confidence CV localization when available, otherwise via a vision model.
- Executive PDF audit report (`test_execution_audit.pdf`) with run stats, defect logs, and inline annotated visual proof plates.
- Worker startup and boundary-recovery retries with exponential backoff for transient navigation/service failures.

## 🔁 Execution Workflow

1. Allocate global step budget across workers with per-worker caps.
2. Launch one isolated browser context per active worker (sandbox-first, optional no-sandbox fallback).
3. Open target URL and wait for readiness (with retry/backoff).
4. For each worker step, capture state and screenshot (`PageSnapshot`).
5. Ask Ollama for next action in JSON format.
6. Apply state-aware policy to avoid loops and diversify exploration.
7. Execute action and collect post-action telemetry.
8. Record defects, logs, and artifacts per worker, then merge globally.
9. Repeat until allocated global budget is consumed, then generate reports.
10. If `PDF_GENERATE=true`, selectively annotate critical screenshots and emit `test_execution_audit.pdf`.

## 🔍 Gap Analysis

Based on architecture review, these gaps are currently the highest-impact improvements:

1. Config parity gap:
	- Core runtime knobs are now env-driven and CLI-overridable in the advanced runner.
2. Optional Node fallback dependency gap:
	- Screenshot diff fallback expects `pngjs` and `pixelmatch` in Node, but this is not documented as an optional setup path.
3. Reproducibility gap:
	- No seeded randomness flag, making exact replay difficult.
4. CI ergonomics gap:
	- CLI overrides are available; a ready-made CI workflow template is still not included.
5. Test scope clarity gap:
	- No explicit policy section for allowed domains/rate limits when running against production-like systems.

## 🛠️ Troubleshooting

### Error: `Execution context was destroyed`

Why it happens:
- Navigation occurred between state capture and DOM evaluation.

How this project handles it:
- `get_page_state` retries after waiting for `networkidle` and falls back to a minimal loading snapshot.

What to do if it persists:
- Increase action cooldown.
- Add stricter post-action waits for known route transitions.
- Reduce action aggressiveness for heavy SPA flows.

### Browser launch fails in Linux sandbox mode

Symptoms:
- Launch exception before test loop starts.

Fix options:
- Keep strict mode: set `STRICT_SANDBOX=true` and fix host sandbox support.
- Allow fallback: set `ALLOW_NO_SANDBOX_FALLBACK=true` for environments where sandbox is unavailable.

### No a11y scan results

Possible causes:
- axe-core CDN blocked or CSP restrictions.

Fix:
- Verify outbound access to the axe CDN URL.
- Watch for `axe-injection-failed` entries in report defects.

### Visual diff fallback errors mentioning Node modules

Cause:
- Python pixelmatch path unavailable and Node fallback missing dependencies.

Fix:
- Ensure Python image diff dependencies are installed from `requirements.txt`.
- Or install Node-side fallback modules if you rely on subprocess diffing.

### Configuration validation fails at startup

Common causes:
- `MAX_STEPS_PER_WORKER` is greater than `MAX_STEPS`.
- A retry count is greater than the allowed maximum (`10`).
- `RETRY_BASE_DELAY_SECONDS` is `0` or greater than `10` seconds.

Fix:
- Ensure `MAX_STEPS_PER_WORKER <= MAX_STEPS`.
- Keep retry counts in the `0-10` range.
- Keep `RETRY_BASE_DELAY_SECONDS` between `0.1` and `10.0` seconds.

Example safe values:

```bash
MAX_STEPS=100 \
MAX_STEPS_PER_WORKER=50 \
WORKER_NAVIGATION_RETRIES=2 \
WORKER_QDRANT_INIT_RETRIES=1 \
WORKER_BOUNDARY_RECOVERY_RETRIES=1 \
RETRY_BASE_DELAY_SECONDS=0.75 \
python -m monkeylm
```

### PostgreSQL database does not exist

Symptom:
- Startup log shows `PostgreSQL initialization failed: database "monkeylm" does not exist`.

Fix:
- Create the database before the first run. For example:

```bash
psql -h latitude -U postgres -c "CREATE DATABASE monkeylm;"
```

- The runner will create the required tables (`app_baselines`, `regression_drift_log`) automatically on first connection.

### Redis authentication fails

Symptom:
- Startup log shows `Redis initialization failed: Authentication required`.

Fix:
- Include the password in `REDIS_URL`, naming the `default` user:

```bash
REDIS_URL=redis://default:your_password@latitude:6379/0
```

- Why `default:` and not `redis://:your_password@host`? Both work for the app
  (`redis-py` treats an empty username as "default user"), but **`redis-cli`
  does not**: with `redis-cli 7.x` the password-only form sends
  `AUTH "" <password>` and fails with
  `AUTH failed: WRONGPASS invalid username-password pair` or
  `NOAUTH Authentication required`. Writing the username explicitly keeps the
  same URL usable from the CLI.

- Inspecting Redis from the shell without leaking the password into the
  process list:

```bash
REDISCLI_AUTH='your_password' redis-cli -h latitude -p 6379 PING
REDISCLI_AUTH='your_password' redis-cli -h latitude -p 6379 --scan --pattern 'monkey:*'
```

### No keys appear in Redis under `REDIS_PREFIX`

Symptom:
- Startup prints `Redis not used: WORKERS=1 …`, and `SCAN monkey:*` returns
  nothing.

Is Redis required?
- **Only when `WORKERS >= 2`.** The cache has no read path of its own; it exists
  purely to coordinate *between* workers:

  | Write | Key | Purpose |
  |---|---|---|
  | `increment_visited_state` | `{REDIS_PREFIX}monkeylm:visited_states:<run timestamp>` | shares revisit counts so every worker sees total visits (drives loop-breaking) |
  | `claim_action_path_lock` | `{REDIS_PREFIX}monkeylm:active_path:<hash>` | stops two workers performing the same action (TTL: `REDIS_PATH_LOCK_TTL_SECONDS`) |

- With `WORKERS=1` there is nothing to coordinate, so the connection is skipped
  entirely — no keys, no idle socket. `STRICT_PERSISTENCE=true` does **not**
  demand Redis in that case. Both Redis primitives also fail open: if the
  service is unreachable mid-run, the workers proceed without coordination.

Checking by prefix is a complete test: both writers go through
`build_redis_key()`, so no write can escape the namespace. The resulting key
shapes are:

```
{REDIS_PREFIX}monkeylm:visited_states:<run timestamp>   hash, TTL REDIS_STATE_TTL_SECONDS
{REDIS_PREFIX}monkeylm:active_path:<hash>               string, TTL REDIS_PATH_LOCK_TTL_SECONDS
```

With `REDIS_PREFIX=monkey:` that reads `monkey:monkeylm:visited_states:…`
(the doubled `monkey` is expected: prefix + base namespace). Leaving
`REDIS_PREFIX=` empty is honoured as "no prefix" and yields `monkeylm:…`.
Timing matters when you look: locks expire in `REDIS_PATH_LOCK_TTL_SECONDS`
(45s by default), and each run writes a **new** timestamped key, so older keys
can linger until the 24h visited-state TTL.

Fix:
- Raise `WORKERS` above 1 if you want the cache populated, then re-check with a
  password-safe scan (`REDISCLI_AUTH` keeps the secret out of `argv`):

```bash
REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli -h latitude -p 6379 --scan --pattern 'monkey:*'
```

- Do **not** use `redis-cli -u "$REDIS_URL"` when `REDIS_URL` uses the
  password-only form (`redis://:pw@host`): `redis-cli` sends an empty username
  and authentication fails, even though the app connects fine. Either name the
  `default` user in `REDIS_URL` or use the `-h/-p` form above.
- Note the shared-instance caveat: on a Redis server also used by other
  services, `DBSIZE` reflects *their* keys too — scan for your prefix instead.

### Edited `.env` seems to have no effect

Symptom:
- You change `REDIS_URL` (or `POSTGRES_DSN`) in `.env`, but the run still
  connects to another host.
- Startup prints `REDIS_URL is exported in this shell but .env takes
  precedence: using …, ignoring the shell value …`.

Cause:
- `.env` is authoritative, so the edit is in effect and the exported value is
  being ignored. The remaining reasons a change appears inert are:
  - `MONKEYLM_ENV_PRECEDENCE=shell` is set in the environment (CI rollback
    switch), which restores `CLI > shell > .env > default`.
  - The key is commented out or absent in `.env`, leaving the shell export as
    the only source.
  - A CLI flag (`--redis-url`, `--postgres-dsn`) overrides the file.

Fix:
- Check the startup notice, then run without the override:

```bash
env -u MONKEYLM_ENV_PRECEDENCE python -m monkeylm
```

### Ollama inference is slow or times out under concurrent workers

Symptom:
- Steps hang, `Ollama inference timed out` warnings appear, or 503/overload errors occur.

Fix:
- Increase the per-call timeout:

```bash
OLLAMA_TIMEOUT_SECONDS=30 python -m monkeylm
```

- Tune the Ollama server for parallel batch inference. Match parallelism to `WORKERS`:

```bash
export OLLAMA_NUM_PARALLEL=4
export OLLAMA_KV_CACHE_TYPE=q4_0
ollama serve
```

- Reduce `WORKERS` if the model or GPU cannot sustain the concurrent request load.

### Two workers click the same element at the same time

Symptom:
- Duplicate navigation or form submissions appear in logs when `WORKERS > 1`.

Fix:
- Cross-worker deduplication is enabled by default. Each worker claims a Redis
  lock for `(page_route, action, target)` before executing a click, type,
  navigation, modal, or form action. Increase the lock TTL if your pages are
  slow to react:

```bash
REDIS_PATH_LOCK_TTL_SECONDS=90 python -m monkeylm
```

- Ensure `REDIS_URL` is reachable from all workers and that `REDIS_PREFIX` is
  consistent across processes.

### Stopping a run gracefully

Press **Ctrl+C** (or send `SIGTERM`) once to request a graceful shutdown. Each
worker finishes its current monkey step, closes its browser context, and
returns the completed steps and findings collected so far. The coordinator
merges partial results and generates `test_report.md` and `results.json` before
exiting. Pressing Ctrl+C a second time forces immediate termination.

## 📁 Output Artifacts

Each run creates:
- `test_report.md`: human-readable report.
- `results.json`: structured machine-readable summary.
- `test_execution_audit.pdf`: executive PDF with run stats, defect logs, and annotated visual proof plates (when `PDF_GENERATE=true`).
- `step_*.png`: screenshots for before/after/final phases.
- `step_*_annotated.png`: annotated screenshots for critical regression/failure steps (CV-localized or vision-model).
- `visual_diff_step_*.png`: pixelmatch visual diff images when enabled.
- `cv_diff_step_*.png`: SSIM diff images with bounding boxes around changed regions.

## 🤖 CI Example (GitHub Actions)

Use this workflow as a starting point for scheduled or manual deep monkey runs.

```yaml
name: MonkeyLM Deep Test

on:
	workflow_dispatch:
		inputs:
			target_url:
				description: "Target URL"
				required: true
				default: "https://example.com/dashboard"
			ollama_model:
				description: "Ollama model"
				required: true
				default: "llama3.2"
			max_steps:
				description: "Max monkey steps"
				required: true
				default: "50"
	schedule:
		- cron: "0 2 * * *"

jobs:
	monkey-test:
		runs-on: ubuntu-latest
		timeout-minutes: 45

		steps:
			- name: Checkout
				uses: actions/checkout@v4

			- name: Setup Python
				uses: actions/setup-python@v5
				with:
					python-version: "3.11"

			- name: Install dependencies
				run: |
					python -m pip install --upgrade pip
					pip install -r requirements.txt
					playwright install chromium

			- name: Run monkey test
				env:
					TARGET_URL: ${{ github.event.inputs.target_url || 'https://example.com/dashboard' }}
					OLLAMA_MODEL: ${{ github.event.inputs.ollama_model || 'llama3.2' }}
					MAX_STEPS: ${{ github.event.inputs.max_steps || '50' }}
					HEADLESS: "true"
					BROWSER_WINDOW_SIZE: "1920,1080"
					NO_VIEWPORT: "true"
					ALLOW_NO_SANDBOX_FALLBACK: "true"
				run: |
					python -m monkeylm --headless --no-viewport

			- name: Upload artifacts
				if: always()
				uses: actions/upload-artifact@v4
				with:
					name: monkeylm-artifacts
					path: |
						testrun_*/test_report.md
						testrun_*/results.json
						testrun_*/**/*.png
```

Tips:
- If your runner has no local Ollama service, use a model/backend configuration reachable from CI.
- For stricter Linux hardening, set `STRICT_SANDBOX=true` and remove no-sandbox fallback.

### How To Trigger CI

1. Open the repository in GitHub.
2. Go to Actions -> MonkeyLM Deep Test.
3. Click Run workflow.
4. Provide `target_url`, `ollama_model`, and `max_steps`.
5. Download artifacts from the run summary (`test_report.md`, `results.json`, screenshots).

## 📚 Development Docs

- `docs/ARCHITECTURE.md`
- `docs/EXTENSION_GUIDE.md`
- `docs/TESTING_STRATEGY.md`
- `docs/PROMPT_LIBRARY.md`
- `scripts/demo_form_crud.py` — local create/update/delete demo for label-aware form payloads.

These docs are designed for fast “vibe coding” iteration while preserving architectural intent.
