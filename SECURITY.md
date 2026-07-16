# MonkeyLM Security Architecture

This document outlines the security architecture, defensive controls, and hardened surfaces implemented across MonkeyLM (Steps 1–8).

## Overview

MonkeyLM is an automated UI-testing framework using browser automation and LLM-based analysis. Because it processes untrusted web content at scale, every input surface must be validated, sanitized, and contained.

---

## 1. Config Hardening (`monkeylm/config.py`) — Step 1

| Control | Detail |
|---|---|
| `.env` auto-loading | `load_dotenv()` from project root with graceful fallback |
| API key prefix validation | `_validate_api_key()` blocks unknown prefixes (`sk-`, `gsk_`, `ollama-`, etc.) |
| Path hardening | `_validate_path()` rejects absolute paths and `..` traversal that escapes project root |
| HTTPS enforcement | `_validate_url()` warns on HTTP for production endpoints; fixed regex (removed redundant `\|http`) |
| Typing corrections | `Optional[int]` / `Optional[float]` applied where bare primitives were used |

## 2. Core Hardening (`monkeylm/core.py`) — Step 2

| Control | Detail |
|---|---|
| `sanitize_for_storage()` | HTML-entity encoding (`&`, `<`, `>`, `"`), non-string rejection, 1024-char cap |
| Anomaly sensor sanitization | `_on_page_error`, `_on_console`, `_on_response` all store only sanitized values |
| Fuzzer type safety | `Fuzzer.next_payload()` wraps Faker outputs in `str()`, caps payload length to 1024 |
| Axe-core read limit | `A11yChecker._reinject_via_evaluate` bounds file reads to 10 MB |
| Validation prober guards | Runtime type validation for all probe parameters; empty-payload rejection |

### Known Test Vectors (not vulnerabilities)
Fuzzer payloads (`/etc/passwd`, SQLi strings, XSS tags) are intentional malicious test inputs used to stress-test target applications — not leaked secrets or exploitable paths.

## 3. Browser Hardening (`monkeylm/browser.py`) — Step 3

| Control | Detail |
|---|---|
| SSRF / open redirect guard | `_validate_navigation_url()` rejects non-http(s) schemes, blocks private/reserved IPs |
| Safe evaluation | `window.scrollBy` migrated from f-string to `arguments[0]` pattern |
| Dialog sanitization | Messages sanitized via `sanitize_for_storage()` |
| Form action scrubbing | Strips `javascript:` and `data:` URIs before storage |
| Subprocess path validation | `compare_screenshots_pixelmatch()` validates paths via `os.path.abspath()` |
| Window size validation | Regex anchored to `^\d+x\d+$` |

## 4. Model Hardening (`monkeylm/models.py`) — Step 4

| Control | Detail |
|---|---|
| `_redact_secrets()` | Scrubs visible secret material before exception logging in Ollama calls |
| `_sanitize_prompt_input()` | Strips control chars / RTL overrides, removes injection boundary tags, size-caps input |
| Prompt-injection fences | Untrusted data wrapped in `<<<UNTRUSTED_*_START/END>>>` with SEC BOUNDARY directive |
| Safe JSON parsing | `_safe_json_parse()` uses brace-depth scanner (replaces fragile greedy regex) |
| Resource limits | Input capped at 512K, prompt data at 64K; timeout + exponential backoff on 503/overload |

## 5. Path Traversal & Secure Writes (`monkeylm/memory.py`) — Step 5

| Control | Detail |
|---|---|
| `_sanitize_path_component()` | Keeps `[a-zA-Z0-9._-]`, collapses `..` / multi-dot runs to `_`, strips leading dots, rejects empty/`.`/`..`/`-`/`_` shapes, 128-char cap |
| `_baseline_lookup_path()` containment | Resolves candidate and base absolutely; enforces `candidate.relative_to(base_dir)` — escape attempts refused with logged warning |
| `_secure_atomic_write()` | Temp→fsync→chmod(0o600)→atomic rename. No partial/corrupt target on crash |
| `_secure_atomic_write_json()` | Convenience wrapper for JSON payloads (same atomic + 0o600 guarantees) |

> **Real bug fixed:** Previous sanitizer did not strip `..`, allowing path traversal out of the baseline data directory via crafted URLs. Neutralized in Step 5.

## 6. Reporting Hardening (`monkeylm/reporting.py`) — Step 6

| Control | Detail |
|---|---|
| `redact_sensitive_content()` | Regex-based scrub of API keys (`sk-`, `gsk_`), Ollama tokens, passwords, credentials, JWT/session IDs — applied before any file write |
| Secure markdown report | `_secure_atomic_write(path, redacted_md_content, mode=0o640)` |
| Secure JSON summary | `_secure_atomic_write(path, redacted_json, mode=0o600)` |
| Restrictive PDF permissions | `os.chmod(pdf_path, 0o640)` after ReportLab build |
| Secure HTML report | `_secure_atomic_write(path, redacted_html_content, mode=0o640)` |

## 7. Agent Hardening (`monkey_agent_advanced.py`) — Step 7

| Control | Detail |
|---|---|
| Typed globals whitelist | `_RUNTIME_GLOBAL_SCHEMA` typed dict — only declared keys accepted; unknown keys rejected with log warning |
| Safe mutation primitive | `_safe_set_global()` enforces type coercion, positive-numeric range validation |
| Credential override warning | Writes to `POSTGRES_DSN` / `REDIS_URL` emit logging warnings |
| Seed validation | Input validated for integer type before use |
| No subprocess exposure | Audit confirmed zero `subprocess`, `shell=True`, or `os.environ` usage — file is a compatibility shim |

## 8. Dependency Audit & Pinning (`requirements.txt`) — Step 8

### CVE Remediation

| Package | Old Version | New Version | CVE(s) Mitigated | Severity |
|---|---|---|---|---|
| **Pillow** | 11.3.0 | 12.3.0 | CVE-2026-55798 (command injection), CVE-2026-55379/80 (decompression bomb) | CRITICAL |

### Version Upgrades (latest stable)

| Package | Old → New | Notes |
|---|---|---|
| Faker | 37.8.0 → 40.31.0 | No known CVEs; upgrade for bug fixes |
| pixelmatch | 0.3.0 → 0.4.0 | Minor library bump |
| playwright | 1.60.0 → 1.61.0 | CVE-2025-9611 affects `@playwright/mcp` (npm), not PyPI package; upgrade for hygiene |
| asyncpg | 0.30.0 → 0.31.0 | CVE-2020-17446 already addressed in 0.21.0+; upgrade for latest |
| redis | 6.2.0 → 8.0.1 | No known client-side CVEs; full feature update |
| python-dotenv | 1.0.1 → 1.2.2 | Minor improvements |
| reportlab | 4.4.0 → 5.0.0 | CVE-2023-33733 (RCE) fixed in 3.6.13+; upgrade to latest major |

### Unchanged Packages

| Package | Version | Rationale |
|---|---|---|
| ollama | 0.6.2 | CVE-2026-5757 affects the Ollama server binary (GGUF quantization engine), not this Python HTTP client library |
| httpx | 0.28.1 | Already latest; SSRF CVEs found in downstream consumers, not in httpx itself |

> **All 10 dependencies strictly pinned with `==`. Zero wildcards or loose version constraints.**

---

## Threat Model Summary

| Threat | Mitigation | File(s) |
|---|---|---|
| Path Traversal / LFI | `_sanitize_path_component()` + `relative_to` containment | `memory.py` |
| Secret Exposure (reports) | `redact_sensitive_content()` pre-write redaction | `reporting.py`, `models.py` |
| XSS in stored data | `sanitize_for_storage()` HTML-entity encoding | `core.py`, `browser.py` |
| SSRF via browser nav | `_validate_navigation_url()` IP/scheme blocking | `browser.py` |
| Prompt Injection (LLM) | Fence-wrapped UNTRUSTED DATA with BOUNDARY directive | `models.py` |
| Unsafe file writes (TOCTOU / partials) | Atomic temp→fsync→rename pipeline | `memory.py`, `reporting.py` |
| Supply-chain (outdated deps) | Strict pinning (`==`) + CVE remediation at every audit cycle | `requirements.txt` |
| Globals mutation from CLI args | `_RUNTIME_GLOBAL_SCHEMA` typed whitelist + `_safe_set_global()` | `monkey_agent_advanced.py` |

---

## Static Analysis Baseline

- **Linter (ruff):** 0 errors (E402/E501/F841 suppressed for pre-existing structural patterns via `ruff.toml`)
- **Type checker (mypy 2.3.0):** 63 errors — all pre-existing (None guards, union attrs). Zero new findings from hardening changes.

---

## Development Guidelines

When adding new features or modifying existing code:

1. Always use `_secure_atomic_write()` for file I/O with `mode=0o640` (reports) or `mode=0o600` (sensitive data).
2. Apply `sanitize_for_storage()` before persisting any untrusted string from the browser, console, or page errors.
3. Guard user-supplied paths/domains/routes through `_sanitize_path_component()` and verify containment with `relative_to()`.
4. Never log raw Ollama responses or exception text — pass through `_redact_secrets()` first.
5. Keep baseline data isolated within the designated output/baseline directory; never trust URL-derived path components.
6. Run `pip-audit` (or manually cross-reference PyPI advisories) before merging dependency changes. Re-pin with `==`.

---

## Cycle Status ✓

All 8 steps of the AppSec Hardening cycle are complete as of 2026-07-16.