"""PDF executive audit report generation for MonkeyLM."""

from __future__ import annotations
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from monkeylm.config import (
    Image,
    PIL_Image,
    _REPORTLAB_AVAILABLE,
)
from monkeylm.reporting.accountability import summarize_vibe_coding_accountability, _derive_severity


if _REPORTLAB_AVAILABLE:
    from reportlab.lib import colors  # type: ignore[import-untyped]
    from reportlab.lib.pagesizes import letter  # type: ignore[import-untyped]
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # type: ignore[import-untyped]
    from reportlab.lib.units import inch  # type: ignore[import-untyped]
    from reportlab.platypus import (  # type: ignore[import-untyped]
        Image as RLImage,
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )


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

        def _xml_escape(text: str) -> str:
            return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;")

        accountability = summarize_vibe_coding_accountability(defects)
        duration_seconds = (end_time - start_time).total_seconds()
        total_steps = len(test_logs)

        defect_step_numbers_pdf: Set[int] = set()
        for cat in ["security_risks", "context_anomalies", "ux_flow_freezes",
                     "validation_failures", "race_findings", "boundary_drift",
                     "console_findings", "accessibility_violations"]:
            collection = getattr(defects, cat, None)
            if not collection:
                continue
            for d in collection:
                severity = _derive_severity(cat, d)
                if severity in ("CRITICAL", "HIGH", "MEDIUM"):
                    step_num = d.get("step")
                    if step_num is not None:
                        defect_step_numbers_pdf.add(step_num)

        failed_pdf_set: Set[int] = set()
        for log in test_logs:
            if log["status"] in ["FAILED", "CRASH"]:
                failed_pdf_set.add(log.get("step", 0))
        failed_pdf_set.update(defect_step_numbers_pdf)
        failed_steps_count_pdf = len(failed_pdf_set)
        success_rate = ((total_steps - failed_steps_count_pdf) / total_steps * 100) if total_steps > 0 else 0.0

        story.append(Paragraph("MonkeyLM Executive Quality Audit", styles["Title"]))
        story.append(Spacer(1, 0.15 * inch))
        story.append(Paragraph(f"<b>Target URL:</b> {_xml_escape(settings.target_url)}", styles["Normal"]))
        story.append(Paragraph(f"<b>Execution Seed:</b> {_xml_escape(settings.active_seed or 'none')}", styles["Normal"]))
        story.append(Paragraph(f"<b>Run Date:</b> {start_time.strftime('%Y-%m-%d %H:%M:%S')}", styles["Normal"]))
        story.append(Paragraph(f"<b>Duration:</b> {duration_seconds:.2f} seconds", styles["Normal"]))
        story.append(Spacer(1, 0.2 * inch))

        summary_data = [
            ["Metric", "Value"],
            ["Total Steps", str(total_steps)],
            ["Failed / Crashed Steps", str(failed_steps_count_pdf)],
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
        story.append(Spacer(1, 0.2 * inch))

        story.append(Paragraph("<b>LLM Configuration</b>", styles["Heading2"]))
        story.append(Spacer(1, 0.1 * inch))
        llm_data = [
            ["Model Role", "Model Name"],
            ["Decision Model", _xml_escape(settings.ollama_model)],
            ["Vision Model", _xml_escape(settings.vision_model)],
            ["PDF Vision Model", _xml_escape(settings.pdf_vision_model)],
        ]
        llm_table = Table(llm_data, colWidths=[3.0 * inch, 3.0 * inch])
        llm_table.setStyle(
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
        story.append(llm_table)
        story.append(Spacer(1, 0.3 * inch))

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
                        Paragraph(f"\U0001f6e0\ufe0f REMEDIATION TASK: {_xml_escape(remediation_advice)}", remediation_style),
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
                if Image is not None and PIL_Image is not None:
                    with PIL_Image.open(image_path) as img:
                        orig_w, orig_h = img.size
                        aspect = orig_h / max(1, orig_w)
                        img_height = min(img_width * aspect, 3.5 * inch)
                return RLImage(image_path, width=img_width, height=img_height)
            except Exception:
                return None

        embedded_screenshots: Set[str] = set()

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
            ("Context Anomalies", defects.context_anomalies),
            ("UX Flow Freezes", defects.ux_flow_freezes),
            ("Validation Failures", defects.validation_failures),
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

        annotated_logs = [
            log for log in test_logs
            if (log.get("screenshot_annotated") or str(log.get("screenshot", "")).endswith("_annotated.png"))
            and log.get("status", "UNKNOWN") != "SUCCESS"
        ]
        proof_plate_logs = [log for log in annotated_logs if log.get("screenshot", "") not in embedded_screenshots]

        if proof_plate_logs:
            story.append(PageBreak())
            story.append(Paragraph("Visual Proof Plates", styles["Heading2"]))
            story.append(Spacer(1, 0.1 * inch))
            story.append(Paragraph(
                "Each plate below is the actual screenshot from the failing step, "
                "annotated with a red bounding box (the issue region), a pointer arrow, "
                "and a wrapping text label that explains what the vision model located. "
                "Use these to triage without leaving the PDF.",
                styles["BodyText"],
            ))
            story.append(Spacer(1, 0.15 * inch))

            step_defect_reasons: Dict[int, List[str]] = {}
            for cat_name, cat_items in [
                ("security_risk", defects.security_risks),
                ("visual_regression", defects.visual_regressions),
                ("layout_instability", defects.layout_instability),
                ("a11y_violation", defects.accessibility_violations),
                ("perf_bottleneck", defects.performance_bottlenecks),
                ("console_finding", defects.console_findings),
                ("race_condition", defects.race_findings),
                ("boundary_drift", defects.boundary_drift),
            ]:
                for it in cat_items or []:
                    sn = it.get("step")
                    if sn is None:
                        continue
                    label = f"{cat_name}:{it.get('type', 'unknown')}"
                    step_defect_reasons.setdefault(int(sn), []).append(label)

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
                description = log.get("screenshot_description", "")
                reasons = step_defect_reasons.get(int(step) if isinstance(step, int) else -1, [])

                story.append(Paragraph(
                    f"Step {step}: {_xml_escape(action)} on '{_xml_escape(target)}' — status {status}",
                    styles["Heading3"],
                ))

                if reasons:
                    reason_line = "Why this plate was drawn: " + ", ".join(
                        _xml_escape(r) for r in reasons[:6]
                    )
                    if len(reasons) > 6:
                        reason_line += f" (+{len(reasons) - 6} more)"
                    story.append(Paragraph(reason_line, description_style))

                if description:
                    story.append(Paragraph(
                        f"<b>Vision annotation:</b> {_xml_escape(description)}",
                        description_style,
                    ))

                if error:
                    story.append(Paragraph(
                        f"<font color='red'>Error:</font> {_xml_escape(error[:200])}",
                        styles["BodyText"],
                    ))

                img_flowable = _build_scaled_image(screenshot_name)
                if img_flowable is not None:
                    story.append(Spacer(1, 0.05 * inch))
                    story.append(img_flowable)
                else:
                    story.append(Paragraph("⚠️ Screenshot not available", styles["BodyText"]))
                story.append(Spacer(1, 0.2 * inch))

        doc.build(story)
        os.chmod(pdf_path, 0o640)
        print(f"📄 PDF audit report generated: {pdf_path}")
    except Exception as exc:
        print(f"⚠️ PDF generation failed: {exc}")
