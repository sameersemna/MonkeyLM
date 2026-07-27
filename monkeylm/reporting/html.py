"""Interactive HTML accessibility dashboard generation for MonkeyLM."""

from __future__ import annotations
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from monkeylm.memory import _secure_atomic_write
from monkeylm.reporting.utils import redact_sensitive_content
from monkeylm.reporting.accessibility import _compile_accessibility_violations


def generate_interactive_html_report(
    settings: Any,
    defects: Any,
    test_logs: List[Dict[str, Any]],
    start_time: datetime,
    end_time: datetime,
) -> Optional[str]:
    """Generate interactive single-file HTML accessibility dashboard with embedded CSS/JS.

    Returns file path written or None if no violations exist.
    Features: metric cards grid, quick-reference table, collapsible rule cards,
             inline vanilla JS for toggle functionality (no external deps).
    """
    if not getattr(defects, "accessibility_violations", None):
        return None

    compiled = _compile_accessibility_violations(defects.accessibility_violations)
    rules = compiled.get("rules", [])
    if not rules:
        return None

    def esc(text: str) -> str:
        return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

    target_url = "unknown"
    for log in reversed(test_logs):
        if isinstance(log, dict) and log.get("url"):
            target_url = log["url"]
            break

    timestamp_str = end_time.strftime("%Y-%m-%d %H:%M:%S")
    critical_count = compiled.get("severity_totals", {}).get("critical", 0)
    serious_count = compiled.get("severity_totals", {}).get("serious", 0)
    impact_score = compiled.get("impact_score", 0)
    total_raw = compiled.get("total_raw_violations", 0)
    unique_rules = compiled.get("unique_rules_found", 0)

    rules_html_parts = []
    for idx, rule in enumerate(rules):
        rule_id = esc(str(rule.get("id", "unknown")))
        description = esc(rule.get("description", "")) or "No description available."
        help_url = str(rule.get("helpUrl", "") or "")
        impact = esc(str(rule.get("impact", "N/A")))
        severity_dist = rule.get("severity_distribution", {})
        critical_rule = severity_dist.get("critical", 0)
        serious_rule = severity_dist.get("serious", 0)
        rule_score = rule.get("impact_score_contribution", 0)
        first_seen = rule.get("occurrence_steps", [])[0] if rule.get("occurrence_steps") else "?"

        selectors_html = ""
        for sel in rule.get("unique_selectors", [])[:10]:
            selectors_html += f'<li style="margin-bottom:4px; font-family:monospace; background:#f5f5f5; padding:4px 8px; border-radius:3px;">{esc(sel)}</li>\n        '

        snippets_html = ""
        for snippet in rule.get("html_snippets", [])[:5]:
            snippets_html += f'<div style="background:#fff3cd; padding:6px 10px; border-radius:4px; margin-bottom:6px; font-family:monospace; font-size:12px; overflow-x:auto;">{esc(snippet)}</div>\n        '

        remediation = esc(rule.get("remediation_advice", "No specific guidance available."))

        card_html = f'''
    <div class="rule-card">
      <div class="rule-header" onclick="toggleRule({idx})" style="cursor:pointer;">
        <span class="rule-title">
          <span class="impact-badge impact-{str(rule.get("impact", "")).lower()}">{impact}</span>
          {rule_id}
        </span>
        <span class="rule-meta">Critical: {critical_rule} | Serious: {serious_rule} | Score: {rule_score:.0f}</span>
        <span class="toggle-icon" id="toggle-{idx}">▼</span>
      </div>
      <div class="rule-body" id="rule-body-{idx}" style="display:none;">
        <p><strong>Description:</strong> {description}</p>
        {'<p><a href="' + help_url + '" target="_blank" rel="noopener noreferrer" style="color:#0066cc;">📖 View WCAG Documentation</a></p>' if help_url else ''}
        <div class="detail-section">
          <h4>Affected Selectors ({len(rule.get("unique_selectors", []))})</h4>
          <ul>{selectors_html}</ul>
        </div>
        {'<div class="detail-section"><h4>HTML Snippets</h4>' + snippets_html + '</div>' if snippets_html else ''}
        <div class="detail-section">
          <h4>Remediation Advice</h4>
          <p>{remediation}</p>
        </div>
        <p style="color:#666; font-size:12px;"><strong>First Seen:</strong> Step {first_seen}</p>
      </div>
    </div>'''
        rules_html_parts.append(card_html)

    rules_html = "\n".join(rules_html_parts)

    table_rows = ""
    for rule in rules:
        rule_id = esc(str(rule.get("id", "unknown")))
        impact = esc(str(rule.get("impact", "N/A")))
        severity_dist = rule.get("severity_distribution", {})
        critical_rule = severity_dist.get("critical", 0)
        serious_rule = severity_dist.get("serious", 0)
        rule_score = rule.get("impact_score_contribution", 0)
        first_seen = rule.get("occurrence_steps", [])[0] if rule.get("occurrence_steps") else "?"

        critical_color = "#dc3545" if critical_rule > 0 else "#666"
        table_rows += f'''<tr>
          <td><code>{rule_id}</code></td>
          <td><span class="impact-badge impact-{impact.lower()}">{impact}</span></td>
          <td style="text-align:center; font-weight:bold; color:{critical_color};">{critical_rule}</td>
          <td style="text-align:center;">{serious_rule}</td>
          <td style="text-align:center; font-weight:bold;">{rule_score:.0f}</td>
          <td>Step {first_seen}</td>
        </tr>\n'''

    html_content = f'''<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>MonkeyLM Accessibility Audit Report</title>
  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      background: #f5f7fa;
      color: #333;
      line-height: 1.6;
    }}
    .container {{
      max-width: 1200px;
      margin: 0 auto;
      padding: 20px;
    }}
    header {{
      background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
      color: white;
      padding: 30px;
      border-radius: 8px;
      margin-bottom: 20px;
    }}
    header h1 {{ font-size: 28px; margin-bottom: 10px; }}
    header p {{ opacity: 0.9; font-size: 14px; }}
    header a {{ color: #ffd700; text-decoration: none; }}
    .metrics-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 15px;
      margin-bottom: 20px;
    }}
    .metric-card {{
      background: white;
      padding: 20px;
      border-radius: 8px;
      box-shadow: 0 2px 4px rgba(0,0,0,0.1);
      text-align: center;
    }}
    .metric-card .value {{
      font-size: 36px;
      font-weight: bold;
      color: #667eea;
    }}
    .metric-card .label {{
      font-size: 12px;
      color: #666;
      text-transform: uppercase;
      letter-spacing: 1px;
    }}
    .metric-card.critical .value {{ color: #dc3545; }}
    .metric-card.serious .value {{ color: #ffc107; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: white;
      border-radius: 8px;
      overflow: hidden;
      box-shadow: 0 2px 4px rgba(0,0,0,0.1);
      margin-bottom: 20px;
    }}
    th, td {{
      padding: 12px 15px;
      text-align: left;
      border-bottom: 1px solid #eee;
    }}
    th {{
      background: #667eea;
      color: white;
      font-weight: 600;
      text-transform: uppercase;
      font-size: 12px;
    }}
    .impact-badge {{
      display: inline-block;
      padding: 3px 8px;
      border-radius: 12px;
      font-size: 11px;
      font-weight: bold;
      color: white;
    }}
    .impact-critical {{ background: #dc3545; }}
    .impact-serious {{ background: #ffc107; color: #333; }}
    .impact-moderate {{ background: #fd7e14; }}
    .impact-minor {{ background: #28a745; }}
    .rule-card {{
      background: white;
      border-radius: 8px;
      margin-bottom: 10px;
      box-shadow: 0 2px 4px rgba(0,0,0,0.1);
      overflow: hidden;
    }}
    .rule-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 15px 20px;
    }}
    .rule-title {{
      font-weight: bold;
      font-size: 16px;
    }}
    .rule-meta {{ color: #666; font-size: 13px; }}
    .toggle-icon {{ transition: transform 0.3s; }}
    .detail-section {{
      margin-top: 12px;
      padding: 12px;
      background: #f8f9fa;
      border-radius: 6px;
    }}
    .detail-section h4 {{
      font-size: 13px;
      color: #667eea;
      margin-bottom: 8px;
    }}
    footer {{
      text-align: center;
      padding: 20px;
      color: #666;
      font-size: 12px;
    }}
    @media print {{
      .rule-card .rule-body {{ display: block !important; }}
      .toggle-icon {{ display: none; }}
    }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <h1>🔍 MonkeyLM Accessibility Audit Report</h1>
      <p><strong>Target URL:</strong> <a href="{esc(target_url)}" target="_blank">{esc(target_url)}</a></p>
      <p><strong>Generated:</strong> {timestamp_str} | <strong>Total Steps Tested:</strong> {len(test_logs) if test_logs else 0}</p>
    </header>

    <div class="metrics-grid">
      <div class="metric-card">
        <div class="value">{total_raw}</div>
        <div class="label">Raw Violations</div>
      </div>
      <div class="metric-card">
        <div class="value">{unique_rules}</div>
        <div class="label">Unique Rules</div>
      </div>
      <div class="metric-card critical">
        <div class="value">{critical_count}</div>
        <div class="label">Critical</div>
      </div>
      <div class="metric-card serious">
        <div class="value">{serious_count}</div>
        <div class="label">Serious</div>
      </div>
      <div class="metric-card">
        <div class="value">{impact_score:.0f}</div>
        <div class="label">Impact Score</div>
      </div>
    </div>

    <h2 style="margin-bottom:15px;">📊 Quick Reference Table</h2>
    <table>
      <thead>
        <tr>
          <th>Rule ID</th>
          <th>Impact</th>
          <th style="text-align:center;">Critical</th>
          <th style="text-align:center;">Serious</th>
          <th style="text-align:center;">Score</th>
          <th>First Seen</th>
        </tr>
      </thead>
      <tbody>
{table_rows}
      </tbody>
    </table>

    <h2 style="margin-bottom:15px;">📋 Detailed Rule Analysis (Click to expand)</h2>
    {rules_html}

    <footer>
      <p>Generated by MonkeyLM | Accessibility Audit Dashboard</p>
    </footer>
  </div>

  <script>
    function toggleRule(index) {{
      var body = document.getElementById("rule-body-" + index);
      var icon = document.getElementById("toggle-" + index);
      if (body.style.display === "none") {{
        body.style.display = "block";
        icon.textContent = "▲";
      }} else {{
        body.style.display = "none";
        icon.textContent = "▼";
      }}
    }}
  </script>
</body>
</html>'''

    output_dir = getattr(settings, "output_dir", None) or os.getcwd()
    report_path = os.path.join(output_dir, "accessibility_report.html")
    redacted_html = redact_sensitive_content(html_content)
    _secure_atomic_write(report_path, redacted_html, mode=0o640)

    print(f"🌐 Interactive HTML report generated: {report_path}")
    return report_path
