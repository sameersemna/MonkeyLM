"""Markdown, JSON, and PDF report generators for MonkeyLM test runs."""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from monkeylm.config import (
    Image,
    _REPORTLAB_AVAILABLE,
    _local_service_log,
)

# Conditional ReportLab imports
if _REPORTLAB_AVAILABLE:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        Image as RLImage,
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )


def summarize_semantic_memory_telemetry(test_logs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate Qdrant retrieval/write telemetry from test logs."""

    def _avg(values: List[float]) -> float:
        if not values:
            return 0.0
        return float(sum(values) / len(values))

    retrieval_events = [
        log.get("memory_retrieval")
        for log in test_logs
        if isinstance(log.get("memory_retrieval"), dict)
    ]
    write_events = [
        log.get("memory_write")
        for log in test_logs
        if isinstance(log.get("memory_write"), dict)
    ]

    retrieval_ok = [evt for evt in retrieval_events if evt.get("status") == "ok"]
    retrieval_returned = [int(evt.get("returned_count", 0)) for evt in retrieval_ok]
    retrieval_total_ms = [float(evt.get("total_ms", 0.0)) for evt in retrieval_events]
    retrieval_search_ms = [float(evt.get("qdrant_search_ms", 0.0)) for evt in retrieval_ok]
    retrieval_rerank_ms = [float(evt.get("rerank_ms", 0.0)) for evt in retrieval_ok]

    write_ok = [evt for evt in write_events if evt.get("status") == "ok"]
    write_total_ms = [float(evt.get("total_ms", 0.0)) for evt in write_events]
    write_upsert_ms = [float(evt.get("qdrant_upsert_ms", 0.0)) for evt in write_ok]

    provider_counts: Dict[str, int] = {}
    for evt in retrieval_ok + write_ok:
        provider = str(evt.get("provider_used", "unknown"))
        provider_counts[provider] = provider_counts.get(provider, 0) + 1

    rerank_applied_count = len([evt for evt in retrieval_ok if evt.get("rerank_applied")])

    return {
        "retrieval": {
            "events": len(retrieval_events),
            "ok": len(retrieval_ok),
            "avg_total_ms": round(_avg(retrieval_total_ms), 3),
            "avg_qdrant_search_ms": round(_avg(retrieval_search_ms), 3),
            "avg_rerank_ms": round(_avg(retrieval_rerank_ms), 3),
            "avg_returned_count": round(_avg([float(x) for x in retrieval_returned]), 3),
            "rerank_applied_count": rerank_applied_count,
        },
        "write": {
            "events": len(write_events),
            "ok": len(write_ok),
            "avg_total_ms": round(_avg(write_total_ms), 3),
            "avg_qdrant_upsert_ms": round(_avg(write_upsert_ms), 3),
        },
        "providers": provider_counts,
    }


def summarize_vibe_coding_accountability(defects: Any) -> Dict[str, Any]:
    """Compute regression drift index and details from the DefectTracker."""
    findings = defects.regression_findings

    total_missing = 0
    total_expected = 0
    drift_details: List[Dict[str, Any]] = []

    for item in findings:
        missing_components = item.get("missing_components", [])
        if not isinstance(missing_components, list):
            missing_components = []
        missing_count = len(missing_components)

        expected_components = item.get("expected_baseline_components", missing_count)
        try:
            expected_components = int(expected_components)
        except Exception:
            expected_components = missing_count
        expected_components = max(expected_components, missing_count)

        total_missing += missing_count
        total_expected += expected_components

        component_contrast: List[Dict[str, str]] = []
        for component in missing_components[:50]:
            if not isinstance(component, dict):
                continue
            component_contrast.append(
                {
                    "selector_hint": str(component.get("selector_hint", "")),
                    "kind": str(component.get("kind", "")),
                    "tag": str(component.get("tag", "")),
                    "text": str(component.get("text", "")),
                }
            )

        broken_selectors = item.get("broken_selectors", [])
        if not isinstance(broken_selectors, list):
            broken_selectors = []

        drift_details.append(
            {
                "step": item.get("step"),
                "domain": item.get("domain", ""),
                "page_route": item.get("page_route", ""),
                "missing_count": missing_count,
                "expected_baseline_components": expected_components,
                "broken_selectors": [str(x) for x in broken_selectors],
                "missing_component_contrast": component_contrast,
            }
        )

    drift_index = (float(total_missing) / float(total_expected) * 100.0) if total_expected > 0 else 0.0
    run_summary_status = (
        "FAILED: Structural Drift Detected" if drift_index > 0.0 else "PASSED: No Structural Drift Detected"
    )

    return {
        "regression_drift_index": round(drift_index, 3),
        "total_missing_historical_components": total_missing,
        "total_expected_baseline_components": total_expected,
        "run_summary_status": run_summary_status,
        "drift_details": drift_details,
    }


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
    failed_steps = [log for log in test_logs if log["status"] in ["FAILED", "CRASH"]]
    success_rate = ((total_steps - len(failed_steps)) / total_steps * 100) if total_steps > 0 else 0

    accountability = summarize_vibe_coding_accountability(defects)

    md_content = f"""# Deep Inspection Monkey Test Report

**Target URL:** {settings.target_url}  
**Date:** {start_time.strftime('%Y-%m-%d %H:%M:%S')}  
**Duration:** {duration_seconds:.2f} seconds  
**Total Steps:** {total_steps}  
**Success Rate:** {success_rate:.2f}%  
**Errors Found:** {len(failed_steps)}  
**Sandbox Policy:** {"strict" if settings.strict_sandbox else "sandbox-first"}  
**No-Sandbox Fallback:** {"enabled" if settings.allow_no_sandbox_fallback else "disabled"}  
**Browser Launch Mode:** {browser_launch_info.get('mode', 'unknown')}  
**Run Summary Status:** {accountability.get('run_summary_status')}  
**Regression Drift Index:** {accountability.get('regression_drift_index')}%  
**Graceful Shutdown:** {"requested" if browser_launch_info.get('graceful_shutdown_requested') else "not requested"}  
**Output Folder:** `{settings.output_dir}`

## Summary
The agent performed {total_steps} actions using **{settings.ollama_model}**.
Actions included: Clicking, Typing, Form Submission, Modal Handling, and State Escapes.
"""

    if failed_steps:
        md_content += "\n## Errors Detected\n"
        for log in failed_steps:
            md_content += f"\n### Step {log['step']}: {log['action']} failed\n"
            md_content += f"- **Target:** `{log['target']}`\n"
            md_content += f"- **Error:** `{log['error']}`\n"
            if log["screenshot"]:
                md_content += f"- **Screenshot:** `![Screenshot](./{log['screenshot']})`\n"

    md_content += "\n## Security Risks\n"
    if defects.security_risks:
        for item in defects.security_risks:
            md_content += f"- Step {item['step']}: {item['type']} on `{item.get('target', '')}` at {item['url']}\n"
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

    md_content += "\n## Accessibility Violations\n"
    if defects.accessibility_violations:
        for item in defects.accessibility_violations:
            selector = item.get("selector", "(unknown)")
            rule_id = item.get("id", "unknown")
            remediation = item.get("remediation", "")
            html_snippet = item.get("html_snippet", "")
            md_content += f"### Step {item['step']}: [{item['severity'].upper()}] `{rule_id}`\n\n"
            md_content += f"- **Selector:** `{selector}`\n"
            if html_snippet:
                md_content += f"- **HTML Snippet:**\n```html\n{html_snippet[:300]}\n```\n"
            if remediation:
                md_content += f"- **Remediation:** {remediation}\n"
            md_content += "\n"
    else:
        md_content += "- None detected.\n"

    md_content += "\n## Performance Bottlenecks\n"
    if defects.performance_bottlenecks:
        for item in defects.performance_bottlenecks:
            md_content += f"- Step {item['step']}: {item['type']} ({item.get('duration_ms', item.get('heap_delta_bytes', item.get('fps')) )})\n"
    else:
        md_content += "- None detected.\n"

    md_content += "\n## Visual Regressions\n"
    visual_items = defects.visual_regressions + defects.layout_instability
    if visual_items:
        for item in visual_items:
            md_content += f"- Step {item['step']}: {item['type']} on {item['url']}\n"
    else:
        md_content += "- None detected.\n"

    md_content += "\n## Baseline Regressions\n"
    if defects.regression_findings:
        for item in defects.regression_findings:
            md_content += (
                f"- Step {item['step']}: [{item['severity']}] {item['type']} "
                f"at {item['domain']}{item['page_route']} "
                f"(missing: {len(item.get('missing_components', []))})\n"
            )
    else:
        md_content += "- None detected.\n"

    telemetry = summarize_semantic_memory_telemetry(test_logs)
    retrieval = telemetry.get("retrieval", {})
    write = telemetry.get("write", {})
    providers = telemetry.get("providers", {})

    md_content += "\n## Semantic Memory Telemetry\n"
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

    md_content += "\n## Action Log\n\n| Step | Action | Target | Status |\n|---|---|---|---|\n"
    for log in test_logs:
        icon = "✅" if log["status"] == "SUCCESS" else "❌"
        md_content += f"| {log['step']} | {log['action']} | {log['target'][:30]}... | {icon} |\n"

    report_path = os.path.join(settings.output_dir, "test_report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    print(f"\n📄 Report generated: {report_path}")
    print(f"💾 All artifacts saved in: {settings.output_dir}")


def generate_json_summary(
    settings: Any,
    defects: Any,
    test_logs: List[Dict[str, Any]],
    browser_launch_info: Dict[str, Any],
    network_injections: List[Dict[str, Any]],
    graceful_shutdown_requested: bool,
    start_time: datetime,
    end_time: datetime,
) -> None:
    """Write results.json with full run data."""
    semantic_memory_telemetry = summarize_semantic_memory_telemetry(test_logs)
    accountability = summarize_vibe_coding_accountability(defects)

    summary = {
        "target_url": settings.target_url,
        "model": settings.ollama_model,
        "active_seed": settings.active_seed,
        "workers": settings.workers,
        "max_steps_per_worker": settings.max_steps_per_worker,
        "configured_max_steps": settings.max_steps,
        "ollama_timeout_seconds": settings.ollama_timeout_seconds,
        "redis_path_lock_ttl_seconds": settings.redis_path_lock_ttl_seconds,
        "graceful_shutdown_requested": graceful_shutdown_requested,
        "retry_policy": {
            "worker_navigation_retries": settings.worker_navigation_retries,
            "worker_qdrant_init_retries": settings.worker_qdrant_init_retries,
            "worker_boundary_recovery_retries": settings.worker_boundary_recovery_retries,
            "base_delay_seconds": settings.retry_base_delay_seconds,
        },
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "duration_seconds": (end_time - start_time).total_seconds(),
        "steps": len(test_logs),
        "failed_steps": len([log for log in test_logs if log["status"] != "SUCCESS"]),
        "run_summary_status": accountability.get("run_summary_status"),
        "regression_drift_index": accountability.get("regression_drift_index"),
        "browser_launch": browser_launch_info,
        "defects": {
            "security_risks": defects.security_risks,
            "accessibility_violations": defects.accessibility_violations,
            "performance_bottlenecks": defects.performance_bottlenecks,
            "visual_regressions": defects.visual_regressions,
            "layout_instability": defects.layout_instability,
            "regression_findings": defects.regression_findings,
            "race_findings": defects.race_findings,
            "console_findings": defects.console_findings,
            "boundary_drift": defects.boundary_drift,
        },
        "network_injections": network_injections,
        "semantic_memory_telemetry": semantic_memory_telemetry,
        "vibe_coding_accountability": accountability,
        "logs": test_logs,
    }
    output_path = os.path.join(settings.output_dir, "results.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(f"📦 JSON summary generated: {output_path}")


def generate_pdf_report(
    settings: Any,
    defects: Any,
    test_logs: List[Dict[str, Any]],
    start_time: datetime,
    end_time: datetime,
) -> None:
    """Build a sleek executive PDF audit report using ReportLab."""
    if not settings.pdf_generate:
        return
    if not _REPORTLAB_AVAILABLE:
        print("⚠️ PDF_GENERATE=true but reportlab is not installed; skipping PDF audit report.")
        return

    try:
        pdf_path = os.path.join(settings.output_dir, "test_execution_audit.pdf")
        doc = SimpleDocTemplate(
            pdf_path,
            pagesize=letter,
            rightMargin=0.6 * inch,
            leftMargin=0.6 * inch,
            topMargin=0.8 * inch,
            bottomMargin=0.8 * inch,
        )
        styles = getSampleStyleSheet()
        story: List[Any] = []

        accountability = summarize_vibe_coding_accountability(defects)
        duration_seconds = (end_time - start_time).total_seconds()
        total_steps = len(test_logs)
        failed_steps = [log for log in test_logs if log["status"] in ["FAILED", "CRASH"]]
        success_rate = ((total_steps - len(failed_steps)) / total_steps * 100) if total_steps > 0 else 0.0

        # Header Block
        story.append(Paragraph("MonkeyLM Executive Quality Audit", styles["Title"]))
        story.append(Spacer(1, 0.15 * inch))
        story.append(Paragraph(f"<b>Target URL:</b> {settings.target_url}", styles["Normal"]))
        story.append(Paragraph(f"<b>Execution Seed:</b> {settings.active_seed or 'none'}", styles["Normal"]))
        story.append(Paragraph(f"<b>Run Date:</b> {start_time.strftime('%Y-%m-%d %H:%M:%S')}", styles["Normal"]))
        story.append(Paragraph(f"<b>Duration:</b> {duration_seconds:.2f} seconds", styles["Normal"]))
        story.append(Spacer(1, 0.2 * inch))

        # Summary metric table
        summary_data = [
            ["Metric", "Value"],
            ["Total Steps", str(total_steps)],
            ["Failed / Crashed Steps", str(len(failed_steps))],
            ["Success Rate", f"{success_rate:.2f}%"],
            ["Workers", str(settings.workers)],
            ["Regression Drift Index", f"{accountability.get('regression_drift_index', 0.0)}%"],
            ["Run Summary Status", str(accountability.get("run_summary_status", "UNKNOWN"))],
        ]
        summary_table = Table(summary_data, colWidths=[3.0 * inch, 3.0 * inch])
        summary_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                    ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, 0), 11),
                    ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
                    ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#f8f9fa")),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ]
            )
        )
        story.append(summary_table)
        story.append(Spacer(1, 0.3 * inch))

        # ─── Unified Defect Audit Cards with Inline Screenshots ──────────
        card_width = 7.8 * inch

        header_critical_style = ParagraphStyle(
            "auditHeaderCritical", parent=styles["BodyText"], fontName="Helvetica-Bold", textColor=colors.whitesmoke, fontSize=10, leading=13
        )
        header_serious_style = ParagraphStyle(
            "auditHeaderSerious", parent=styles["BodyText"], fontName="Helvetica-Bold", textColor=colors.whitesmoke, fontSize=10, leading=13
        )
        header_warning_style = ParagraphStyle(
            "auditHeaderWarning", parent=styles["BodyText"], fontName="Helvetica-Bold", textColor=colors.whitesmoke, fontSize=10, leading=13
        )
        header_info_style = ParagraphStyle(
            "auditHeaderInfo", parent=styles["BodyText"], fontName="Helvetica-Bold", textColor=colors.whitesmoke, fontSize=10, leading=13
        )
        selector_style = ParagraphStyle(
            "auditSelector", parent=styles["BodyText"], fontName="Courier", fontSize=8, leading=11, textColor=colors.HexColor("#2c3e50")
        )
        code_block_style = ParagraphStyle(
            "auditCodeBlock",
            parent=styles["BodyText"],
            fontName="Courier",
            fontSize=7.5,
            leading=9,
            wordWrap="CJK",
            textColor=colors.HexColor("#333333"),
        )
        remediation_style = ParagraphStyle(
            "auditRemediation", parent=styles["BodyText"], fontName="Helvetica", fontSize=9, leading=12, textColor=colors.HexColor("#27ae60")
        )
        description_style = ParagraphStyle(
            "auditDescription",
            parent=styles["BodyText"],
            fontName="Helvetica-Oblique",
            fontSize=8.5,
            leading=11,
            textColor=colors.HexColor("#555555"),
        )

        def _xml_escape(text: str) -> str:
            return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;")

        def _severity_color(severity: str) -> tuple:
            sev = (severity or "").lower()
            if sev in {"critical", "error"}:
                return header_critical_style, colors.HexColor("#c0392b")
            if sev in {"serious", "high", "failed", "crash"}:
                return header_serious_style, colors.HexColor("#d35400")
            if sev in {"warning", "moderate"}:
                return header_warning_style, colors.HexColor("#f39c12")
            return header_info_style, colors.HexColor("#2c3e50")

        def _build_audit_card(item: Dict[str, Any], category_label: str) -> tuple:
            step = item.get("step", "n/a")
            item_type = item.get("type", "unknown")
            severity = item.get("severity", "info")
            selector = item.get("selector", "(none)")
            html_snippet = item.get("html_snippet", "")
            failure_reason = item.get("failure_reason", "")
            remediation_advice = item.get("remediation_advice", "Manual review required.")
            url = item.get("url", "")
            screenshot_basename = item.get("screenshot_path", "")

            header_style, header_bg = _severity_color(severity)
            header_text = f"[{severity.upper()}] {category_label}: Step {step} — {item_type}"

            row_specs: List[tuple] = []
            row_specs.append((Paragraph(header_text, header_style), header_bg))

            selector_line_parts = [f"Selector: {_xml_escape(selector)}"]
            if url:
                selector_line_parts.append(f"URL: {_xml_escape(url)}")
            row_specs.append((Paragraph(" | ".join(selector_line_parts), selector_style), colors.white))

            if html_snippet:
                truncated_html = _xml_escape(html_snippet[:400])
                if len(html_snippet) > 400:
                    truncated_html += " ... (truncated)"
                row_specs.append((Paragraph(truncated_html, code_block_style), colors.HexColor("#f0f0f0")))

            if not html_snippet and failure_reason:
                row_specs.append(
                    (Paragraph(_xml_escape(failure_reason[:300]), description_style), colors.HexColor("#fafafa"))
                )

            if remediation_advice and remediation_advice != "Manual review required.":
                row_specs.append(
                    (
                        Paragraph(f"\U0001f6e0\ufe0f REMEDIATION TASK: {remediation_advice}", remediation_style),
                        colors.HexColor("#f0fff4"),
                    )
                )

            card_rows = [[cell] for cell, _ in row_specs]
            ticket_table = Table(card_rows, colWidths=[card_width], repeatRows=0)

            style_cmds = [
                ("BOX", (0, 0), (-1, -1), 1.0, colors.HexColor("#bdc3c7")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d5dbdb")),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
            for row_idx, (_cell, bg_color) in enumerate(row_specs):
                style_cmds.append(("BACKGROUND", (0, row_idx), (0, row_idx), bg_color))

            ticket_table.setStyle(TableStyle(style_cmds))
            return ticket_table, screenshot_basename

        def _build_scaled_image(basename: str, max_width: float = 6.5) -> Optional[Any]:
            if not basename:
                return None
            image_path = os.path.join(settings.output_dir, basename)
            if not os.path.exists(image_path):
                return None

            try:
                img_width = max_width * inch
                img_height = 4.0 * inch
                if Image is not None:
                    with Image.open(image_path) as img:
                        orig_w, orig_h = img.size
                        aspect = orig_h / max(1, orig_w)
                        img_height = min(img_width * aspect, 3.5 * inch)
                return RLImage(image_path, width=img_width, height=img_height)
            except Exception:
                return None

        embedded_screenshots: set = set()

        all_defect_sections = [
            ("Security Risks", defects.security_risks),
            ("Accessibility Violations", defects.accessibility_violations),
            ("Performance Bottlenecks", defects.performance_bottlenecks),
            ("Baseline Regressions", defects.regression_findings),
            ("Visual Regressions", defects.visual_regressions),
            ("Layout Instability", defects.layout_instability),
            ("Race Findings", defects.race_findings),
            ("Console Findings", defects.console_findings),
            ("Boundary Drift", defects.boundary_drift),
        ]

        any_defects = False
        for category_label, items in all_defect_sections:
            if not items:
                continue
            any_defects = True
            story.append(Paragraph(category_label, styles["Heading3"]))
            story.append(Spacer(1, 0.05 * inch))

            for item in items[:50]:
                ticket_table, screenshot_basename = _build_audit_card(item, category_label)
                story.append(ticket_table)

                if screenshot_basename:
                    embedded_screenshots.add(screenshot_basename)
                    img_flowable = _build_scaled_image(screenshot_basename)
                    if img_flowable is not None:
                        story.append(Spacer(1, 0.05 * inch))
                        story.append(img_flowable)

                story.append(Spacer(1, 0.1 * inch))

            story.append(Spacer(1, 0.15 * inch))

        if not any_defects:
            story.append(Paragraph("Defect Logs", styles["Heading2"]))
            story.append(Spacer(1, 0.1 * inch))
            story.append(Paragraph("No defects detected during this run.", styles["BodyText"]))
            story.append(Spacer(1, 0.2 * inch))

        # ── Visual Proof Plates ──────────────────────────────────────────
        annotated_logs = [
            log for log in test_logs if log.get("screenshot_annotated") or str(log.get("screenshot", "")).endswith("_annotated.png")
        ]
        proof_plate_logs = [log for log in annotated_logs if log.get("screenshot", "") not in embedded_screenshots]

        if proof_plate_logs:
            story.append(PageBreak())
            story.append(Paragraph("Visual Proof Plates", styles["Heading2"]))
            story.append(Spacer(1, 0.1 * inch))

            for log in proof_plate_logs:
                screenshot_name = log.get("screenshot", "")
                if not screenshot_name:
                    continue
                image_path = os.path.join(settings.output_dir, screenshot_name)
                if not os.path.exists(image_path):
                    continue

                step = log.get("step", "n/a")
                status = log.get("status", "UNKNOWN")
                action = log.get("action", "")
                target = log.get("target", "")
                error = log.get("error", "")
                story.append(
                    Paragraph(f"Step {step}: {action} on '{target}' — status {status}", styles["Heading3"])
                )
                if error:
                    story.append(Paragraph(f"<font color='red'>Error:</font> {error[:200]}", styles["BodyText"]))

                img_flowable = _build_scaled_image(screenshot_name)
                if img_flowable is not None:
                    story.append(img_flowable)
                else:
                    story.append(Paragraph("⚠️ Screenshot not available", styles["BodyText"]))
                story.append(Spacer(1, 0.15 * inch))

        doc.build(story)
        print(f"📄 PDF audit report generated: {pdf_path}")
    except Exception as exc:
        print(f"⚠️ PDF generation failed: {exc}")
