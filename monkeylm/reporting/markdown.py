"""Markdown report generation for MonkeyLM test runs."""

from __future__ import annotations
import json
import os
from datetime import datetime
from typing import Any, Dict, List

from monkeylm.memory import _secure_atomic_write
from monkeylm.reporting.utils import redact_sensitive_content
from monkeylm.reporting.telemetry import summarize_semantic_memory_telemetry
from monkeylm.reporting.accountability import summarize_vibe_coding_accountability, _derive_severity
from monkeylm.reporting.accessibility import _compile_accessibility_violations
from monkeylm.reporting.defects import _compile_defect_tickets
from monkeylm.reporting.dedup import dedupe_findings


def _fmt_occurrence(item: Dict[str, Any]) -> str:
    count = item.get("occurrence_count", 1)
    step_range = item.get("step_range")
    if count and count > 1 and step_range:
        return f" (seen {count}x, steps {step_range[0]}-{step_range[1]})"
    return ""


def generate_markdown_report(
    settings: Any,
    defects: Any,
    test_logs: List[Dict[str, Any]],
    browser_launch_info: Dict[str, Any],
    start_time: datetime,
    end_time: datetime,
) -> None:
    """Generate test_report.md in the output directory."""
    duration_seconds = (end_time - start_time).total_seconds()
    total_steps = len(test_logs)

    accountability = summarize_vibe_coding_accountability(defects)

    defect_step_numbers: set[int] = set()
    defect_categories_for_steps = [
        "security_risks", "context_anomalies", "ux_flow_freezes",
        "validation_failures", "race_findings", "boundary_drift",
        "console_findings", "accessibility_violations",
    ]
    for cat in defect_categories_for_steps:
        collection = getattr(defects, cat, None)
        if not collection:
            continue
        for d in collection:
            severity = _derive_severity(cat, d)
            if severity in ("CRITICAL", "HIGH", "MEDIUM"):
                step_num = d.get("step")
                if step_num is not None:
                    defect_step_numbers.add(step_num)

    failed_steps_set: set[int] = set()
    for log in test_logs:
        if log["status"] in ["FAILED", "CRASH"]:
            failed_steps_set.add(log.get("step", 0))
    failed_steps_set.update(defect_step_numbers)

    failed_steps_count = len(failed_steps_set)
    success_rate = ((total_steps - failed_steps_count) / total_steps * 100) if total_steps > 0 else 0
    failed_steps_list = [log for log in test_logs if log["status"] in ["FAILED", "CRASH"]]

    md_content = f"""# Deep Inspection Monkey Test Report

**Target URL:** {settings.target_url}  
**Date:** {start_time.strftime('%Y-%m-%d %H:%M:%S')}  
**Duration:** {duration_seconds:.2f} seconds  
**Total Steps:** {total_steps}  
**Success Rate:** {success_rate:.2f}%  
**Errors Found:** {failed_steps_count}  
**Application Defects (HIGH/MEDIUM/CRITICAL):** {accountability.get('app_defect_count', 0)}  
**Sandbox Policy:** {"strict" if settings.strict_sandbox else "sandbox-first"}  
**No-Sandbox Fallback:** {"enabled" if settings.allow_no_sandbox_fallback else "disabled"}  
**Browser Launch Mode:** {browser_launch_info.get('mode', 'unknown')}  
**Run Summary Status:** {accountability.get('run_summary_status')}  
**Regression Drift Index:** {accountability.get('regression_drift_index')}%  
**Graceful Shutdown:** {"requested" if browser_launch_info.get('graceful_shutdown_requested') else "not requested"}  
**Output Folder:** `{settings.output_dir}`

## LLM Configuration

| Model | Name |
|-------|------|
| **Decision Model** | `{settings.ollama_model}` |
| **Vision Model** | `{settings.vision_model}` |
| **PDF Vision Model** | `{settings.pdf_vision_model}` |

## Summary
The agent performed {total_steps} actions using **{settings.ollama_model}**.
Actions included: Clicking, Typing, Form Submission, Modal Handling, and State Escapes.
"""

    if failed_steps_list:
        md_content += "\n## Errors Detected\n"
        for log in failed_steps_list:
            md_content += f"\n### Step {log['step']}: {log['action']} failed\n"
            md_content += f"- **Target:** `{log['target']}`\n"
            md_content += f"- **Error:** `{log['error']}`\n"
            failure_context = log.get("failure_context") or {}
            if failure_context:
                md_content += f"- **Last URL:** `{failure_context.get('url', '')}`\n"
                md_content += f"- **DOM Context:** `{failure_context.get('dom_context', '')[:400]}`\n"
            if log["screenshot"]:
                md_content += f"- **Screenshot:** `![Screenshot](./{log['screenshot']})`\n"

    compiled_tickets = _compile_defect_tickets(defects, test_logs)
    if compiled_tickets:
        md_content += "\n---\n\n"
        md_content += "# 🔧 Engineering Defect Tickets — Remediation Blueprints\n\n"

        sev_counts: Dict[str, int] = {}
        for t in compiled_tickets:
            sev_counts[t.severity] = sev_counts.get(t.severity, 0) + 1
        sev_line_parts = []
        for sev in ["CRITICAL", "HIGH", "MEDIUM", "LOW"]:
            count = sev_counts.get(sev, 0)
            if count:
                icon = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "⚠️ ", "LOW": "ℹ️ "}[sev]
                sev_line_parts.append(f"{icon} {sev}: {count}")
        md_content += f"**Defect Summary:** {', '.join(sev_line_parts)} (Total: {len(compiled_tickets)})\n\n"

        md_content += "| UID | Severity | Category | Title | Target URL |\n"
        md_content += "|---|---|---|---|---|\n"
        for t in compiled_tickets:
            title_trunc = t.title[:60] + "..." if len(t.title) > 60 else t.title
            url_trunc = t.target_url[:50] + "..." if len(t.target_url) > 50 else t.target_url
            md_content += (
                f"| `{t.defect_uid}` | {t.severity} | {t.category} | {title_trunc} | {url_trunc} |\n"
            )

        md_content += "\n---\n\n"
        for t in compiled_tickets:
            md_content += t.to_markdown() + "\n\n"
            if t.severity in ("CRITICAL", "HIGH", "MEDIUM"):
                md_content += "\n```json\n"
                md_content += json.dumps(t.agent_context_block(), indent=2)
                md_content += "\n```\n"
            md_content += "\n---\n\n"
    else:
        md_content += "\n---\n\n# 🔧 Engineering Defect Tickets\n\n**No defects detected.** ✅\n\n---\n\n"

    runtime_preflight = browser_launch_info.get("runtime_preflight") or {}
    if runtime_preflight:
        md_content += "## Runtime Preflight\n"
        for name, payload in sorted(runtime_preflight.items()):
            md_content += f"- **{name}**: {payload.get('status', 'unknown')}"
            if payload.get("detail"):
                md_content += f" — {payload['detail']}"
            md_content += "\n"
        md_content += "\n"

    if browser_launch_info.get("worker_failures"):
        md_content += "## Runtime Failures\n"
        for item in browser_launch_info["worker_failures"]:
            md_content += f"- Worker {item.get('worker_id')}: {item.get('failure_reason')}"
            if item.get("failure_artifact"):
                md_content += f" (artifact: `{item['failure_artifact']}`)"
            md_content += "\n"
            ctx = item.get("failure_context") or {}
            if ctx:
                md_content += f"  - Context: `{json.dumps(ctx, sort_keys=True)}`\n"
        md_content += "\n"

    md_content += "## Security Risks\n"
    if defects.security_risks:
        for item in dedupe_findings(defects.security_risks):
            md_content += f"- Step {item['step']}: {item['type']} on `{item.get('target', '')}` at {item['url']}{_fmt_occurrence(item)}\n"
    else:
        md_content += "- None detected.\n"

    md_content += "\n### ⚠️ Vibe Coding Drift Summary\n"
    md_content += (
        f"- Regression Drift Index: {accountability.get('regression_drift_index', 0.0)}% "
        f"({accountability.get('total_missing_historical_components', 0)} missing / "
        f"{accountability.get('total_expected_baseline_components', 0)} expected historical baseline components)\n"
    )
    md_content += f"- Run Summary Status: {accountability.get('run_summary_status', 'UNKNOWN')}\n"

    drift_details = accountability.get("drift_details", [])
    if drift_details:
        for detail in drift_details:
            route_display = f"{detail.get('domain', '')}{detail.get('page_route', '')}"
            md_content += (
                f"- Route {route_display}: historical golden baseline expected "
                f"{detail.get('expected_baseline_components', 0)} interactive components, "
                f"but {detail.get('missing_count', 0)} have now vanished or broken in the current deployment.\n"
            )

            broken_selectors = detail.get("broken_selectors", [])
            if broken_selectors:
                selector_line = ", ".join([str(x) for x in broken_selectors[:15]])
                md_content += f"  - Broken selectors now failing: {selector_line}\n"

            for missing in detail.get("missing_component_contrast", [])[:15]:
                selector_hint = str(missing.get("selector_hint", ""))
                kind = str(missing.get("kind", ""))
                tag = str(missing.get("tag", ""))
                text = str(missing.get("text", ""))
                md_content += (
                    f"  - Historical component no longer operating: selector={selector_hint or 'n/a'}, "
                    f"kind={kind or 'n/a'}, tag={tag or 'n/a'}, text={text or 'n/a'}\n"
                )
    else:
        md_content += "- No missing historical components were detected against golden baselines in this run.\n"

    compiled_a11y = _compile_accessibility_violations(defects.accessibility_violations)
    md_content += "\n## Accessibility Violations\n"

    if not compiled_a11y["total_raw_violations"]:
        md_content += "- **None detected.** ✅\n"
    else:
        md_content += "### Summary\n\n"
        md_content += f"- **Total raw violations found:** {compiled_a11y['total_raw_violations']}\n"
        md_content += f"- **Unique rules after deduplication:** {compiled_a11y['unique_rules_found']}\n"
        md_content += f"- **Critical count:** {compiled_a11y['severity_totals']['critical']}\n"
        md_content += f"- **Serious count:** {compiled_a11y['severity_totals']['serious']}\n"
        md_content += f"- **Impact Score (weighted):** {compiled_a11y['impact_score']}\n\n"

    if compiled_a11y["rules"]:
        md_content += "| Rule ID | Impact | Critical | Serious | Impact Score | First Seen |\n"
        md_content += "|---|---|---|---|---|---|\n"
        for r in compiled_a11y["rules"]:
            first_step = r["occurrence_steps"][0] if r["occurrence_steps"] else "?"
            md_content += (
                f"| `{r['id']}` | **{r['impact'].upper()}** "
                f"| {r['severity_distribution']['critical']} | {r['severity_distribution']['serious']} "
                f"| {r['impact_score_contribution']} | Step {first_step} |\n"
            )

        md_content += "\n### Detailed Findings\n\n"
        for r in compiled_a11y["rules"]:
            impact_icon = "🔴" if r["impact"] == "critical" else "🟠"
            md_content += f"#### {impact_icon} `{r['id']}` — {r['description']} ({r['impact'].upper()})\n\n"

            md_content += f"- **Description:** {r['description']}\n"
            if r.get("helpUrl"):
                md_content += f"- **Documentation:** [{r['help']}]({r['helpUrl']})\n"
            md_crit = r["severity_distribution"].get("critical", 0)
            md_ser = r["severity_distribution"].get("serious", 0)
            md_content += f"- **Occurrences:** {md_crit} critical, {md_ser} serious\n"

            if r.get("unique_selectors"):
                md_content += f"\n**Affected Selectors ({len(r['unique_selectors'])} unique):**\n\n"
                for sel in r["unique_selectors"][:10]:
                    md_content += f"- `{sel}`\n"

            if r.get("html_snippets"):
                md_content += "\n**Sample HTML (first occurrence):**\n\n"
                for snippet in r["html_snippets"]:
                    md_content += f"```html\n{snippet}\n```\n"
                    break

            if r.get("remediation_advice"):
                md_content += f"\n**Remediation:** {r['remediation_advice']}\n"

            md_content += "\n---\n\n"

    md_content += "\n## Performance Bottlenecks\n"
    if defects.performance_bottlenecks:
        for item in dedupe_findings(defects.performance_bottlenecks):
            md_content += f"- Step {item['step']}: {item['type']} ({item.get('duration_ms', item.get('heap_delta_bytes', item.get('fps')) )}){_fmt_occurrence(item)}\n"
    else:
        md_content += "- None detected.\n"

    md_content += "\n## Visual Regressions\n"
    visual_items = dedupe_findings(defects.visual_regressions + defects.layout_instability)
    if visual_items:
        for item in visual_items:
            md_content += f"- Step {item['step']}: {item['type']} on {item['url']}{_fmt_occurrence(item)}\n"
    else:
        md_content += "- None detected.\n"

    md_content += "\n## Baseline Regressions\n"
    if defects.regression_findings:
        for item in dedupe_findings(defects.regression_findings):
            md_content += (
                f"- Step {item['step']}: [{item['severity']}] {item['type']} "
                f"at {item['domain']}{item['page_route']} "
                f"(missing: {len(item.get('missing_components', []))}){_fmt_occurrence(item)}\n"
            )
    else:
        md_content += "- None detected.\n"

    md_content += "\n## Context Anomalies\n"
    if defects.context_anomalies:
        for item in dedupe_findings(defects.context_anomalies)[:50]:
            action_ctx = item.get("action", "") or "?"
            step_num = item.get("step", "?")
            anomaly_type = item.get("type", "unknown")
            message = (item.get("message", "") or "")[:200]
            md_content += f"- Step {step_num}: [{anomaly_type}] on action `{action_ctx}` at {item.get('url', '?')}{_fmt_occurrence(item)}\n"
            if message:
                md_content += f"  - `{message}`\n"
    else:
        md_content += "- None detected.\n"

    md_content += "\n## UX Flow Freezes\n"
    if defects.ux_flow_freezes:
        for item in dedupe_findings(defects.ux_flow_freezes)[:50]:
            step_num = item.get("step", "?")
            desc = (item.get("description", "") or "")[:250]
            md_content += f"- Step {step_num}: `{desc}`{_fmt_occurrence(item)}\n"
    else:
        md_content += "- None detected.\n"

    md_content += "\n## Validation Failures\n"
    if defects.validation_failures:
        for item in dedupe_findings(defects.validation_failures)[:50]:
            step_num = item.get("step", "?")
            probe = item.get("probe_name", "") or "unknown"
            target = item.get("target", "") or "?"
            fail_type = item.get("type", "unknown")
            desc = (item.get("description", "") or "")[:250]
            md_content += f"- Step {step_num}: [{fail_type}] field `{target}` probe `{probe}` — `{desc}`{_fmt_occurrence(item)}\n"
    else:
        md_content += "- None detected.\n"

    md_content += "\n## Capture Diagnostics\n"
    capture_diagnostics = getattr(defects, "capture_diagnostics", [])
    if capture_diagnostics:
        for item in dedupe_findings(capture_diagnostics)[:50]:
            step_num = item.get("step", "?")
            msg = item.get("message", "") or item.get("type", "")
            md_content += f"- Step {step_num}: `{msg}`{_fmt_occurrence(item)}\n"
    else:
        md_content += "- None detected.\n"

    telemetry = summarize_semantic_memory_telemetry(test_logs)
    retrieval = telemetry.get("retrieval", {})
    write = telemetry.get("write", {})
    providers = telemetry.get("providers", {})

    md_content += "\n## Semantic Memory Telemetry\n"
    md_content += (
        f"- Configured embedding provider: `{settings.qdrant_embedding_provider}` "
        f"(model: `{settings.qdrant_embedding_model}`)\n"
    )
    md_content += f"- Retrieval events: {retrieval.get('events', 0)} (ok: {retrieval.get('ok', 0)})\n"
    md_content += f"- Avg retrieval total: {retrieval.get('avg_total_ms', 0.0)} ms\n"
    md_content += f"- Avg Qdrant search: {retrieval.get('avg_qdrant_search_ms', 0.0)} ms\n"
    md_content += f"- Avg rerank: {retrieval.get('avg_rerank_ms', 0.0)} ms\n"
    md_content += f"- Avg memories returned: {retrieval.get('avg_returned_count', 0.0)}\n"
    md_content += f"- Rerank applied count: {retrieval.get('rerank_applied_count', 0)}\n"
    md_content += f"- Write events: {write.get('events', 0)} (ok: {write.get('ok', 0)})\n"
    md_content += f"- Avg write total: {write.get('avg_total_ms', 0.0)} ms\n"
    md_content += f"- Avg Qdrant upsert: {write.get('avg_qdrant_upsert_ms', 0.0)} ms\n"
    if providers:
        provider_line = ", ".join([f"{name}: {count}" for name, count in sorted(providers.items())])
        md_content += f"- Providers observed: {provider_line}\n"
    else:
        md_content += "- Providers observed: none\n"
    fallback_count = telemetry.get("fallback_count", 0)
    if fallback_count:
        md_content += (
            f"- ⚠️ **{fallback_count} embedding call(s) silently fell back to hash vectors** "
            f"after the configured `{settings.qdrant_embedding_provider}` provider failed mid-run. "
            f"Check the service connectivity log for the underlying error.\n"
        )
    elif settings.qdrant_embedding_provider != "hash" and settings.qdrant_embedding_provider not in providers:
        md_content += (
            f"- ⚠️ Configured provider is `{settings.qdrant_embedding_provider}` but every observed "
            f"call used `hash` — the provider likely failed during the startup probe (see service "
            f"log) and got permanently downgraded before any steps ran.\n"
        )

    md_content += "\n## Action Log\n\n| Step | Action | Target | Status |\n|---|---|---|---|\n"
    for log in test_logs:
        icon = "✅" if log["status"] == "SUCCESS" else "❌"
        md_content += f"| {log['step']} | {log['action']} | {log['target'][:30]}... | {icon} |\n"

    report_path = os.path.join(settings.output_dir, "test_report.md")
    redacted_md_content = redact_sensitive_content(md_content)
    _secure_atomic_write(report_path, redacted_md_content, mode=0o640)

    print(f"\n📄 Report generated: {report_path}")
    print(f"💾 All artifacts saved in: {settings.output_dir}")
