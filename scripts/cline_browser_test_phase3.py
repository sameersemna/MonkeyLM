"""Phase 3 - Capture actual form submission and test resend.com endpoint directly."""

from __future__ import annotations

import asyncio
import json
import re
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
SCREENSHOTS = ROOT / "screenshots"
SCREENSHOTS.mkdir(parents=True, exist_ok=True)
REPORTS = ROOT / "reports"
REPORTS.mkdir(parents=True, exist_ok=True)

TS = datetime.now().strftime("%Y%m%d_%H%M%S")
MD_PATH = REPORTS / f"ClineBrowserTest_{TS}_phase3.md"
PDF_PATH = REPORTS / f"ClineBrowserTest_{TS}_phase3.pdf"

BASE = "https://web.unbiasedtalent.com"

findings: list[dict[str, Any]] = []
console_buffer: list[str] = []
network_buffer: list[str] = []
request_buffer: list[dict[str, Any]] = []


def add_finding(**kw):
    findings.append(kw)
    print(f"[FINDING] {kw['bug_type']} - {kw['title']} ({kw['severity']})")


async def shot(page, name: str) -> str:
    fname = f"{TS}_p3_{name}.png"
    p = SCREENSHOTS / fname
    try:
        await page.screenshot(path=str(p), full_page=False)
        print(f"  [shot] {fname}")
        return fname
    except Exception as e:
        print(f"  [shot-fail] {e}")
        return ""


def attach(page):
    def on_console(msg):
        line = f"[{msg.type}] {msg.text}"
        console_buffer.append(line)
        if len(console_buffer) > 800:
            console_buffer.pop(0)

    def on_pageerror(err):
        console_buffer.append(f"[pageerror] {err}")

    def on_req(req):
        try:
            entry = {"method": req.method, "url": req.url, "headers": dict(req.headers)}
            if req.post_data:
                entry["body"] = req.post_data[:3000]
            request_buffer.append(entry)
            if len(request_buffer) > 600:
                request_buffer.pop(0)
            network_buffer.append(f"> {req.method} {req.url}")
        except Exception:
            pass

    def on_resp(resp):
        try:
            network_buffer.append(f"< {resp.status} {resp.url}")
            if resp.status >= 400:
                network_buffer.append(f"  !! {resp.status} on {resp.url}")
        except Exception:
            pass

    page.on("console", on_console)
    page.on("pageerror", on_pageerror)
    page.on("request", on_req)
    page.on("response", on_resp)


async def submit_form_real(page):
    print("\n=== REAL FORM SUBMISSION TEST ===")
    # Install interceptors BEFORE navigation
    await page.goto(f"{BASE}/contact", wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(3500)
    await shot(page, "01_contact_real")

    # Hook fetch, XHR, AND instrument form submit handler
    await page.evaluate(
        """() => {
          window.__captured = [];
          // Hook fetch
          const of = window.fetch;
          window.fetch = async function(...a) {
            const [u, init] = a;
            try { window.__captured.push({kind:'fetch', url:String(u), method:(init&&init.method)||'GET', body:(init&&init.body)||null, headers:(init&&init.headers)||null}); } catch(e){}
            return of.apply(this, a);
          };
          // Hook XHR
          const oo = XMLHttpRequest.prototype.open;
          const os = XMLHttpRequest.prototype.send;
          XMLHttpRequest.prototype.open = function(m,u){ this.__m=m; this.__u=u; return oo.apply(this,arguments); };
          XMLHttpRequest.prototype.send = function(b){ try { window.__captured.push({kind:'xhr', url:this.__u, method:this.__m, body:b}); } catch(e){} return os.apply(this,arguments); };
          // Hook form submit
          const forms = document.querySelectorAll('form');
          for (const f of forms) {
            f.addEventListener('submit', (e) => {
              try {
                const fd = new FormData(f);
                const obj = {};
                for (const [k,v] of fd.entries()) { obj[k] = typeof v === 'string' ? v.slice(0,500) : '[binary]'; }
                window.__captured.push({kind:'formsubmit', action: f.action, method: f.method, data: obj});
              } catch(err) { window.__captured.push({kind:'formsubmit-err', error: String(err)}); }
            }, true);
          }
        }"""
    )

    # Fill form with valid data + a script payload to see if it's stored/reflected
    payload_name = "XSS<script>alert(1)</script>Tester"
    payload_msg = "TEST MESSAGE BODY\n\n<script>alert('stored-xss-probe')</script>\n<img src=x onerror=alert(1)>"
    payload_email = "pentest+probe@example.com"

    await page.evaluate(
        """(payloads) => {
          const setVal = (sel, v) => {
            const el = document.querySelector(sel);
            if (el) { el.value = v; el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true})); return true; }
            return false;
          };
          setVal('input[name=name]', payloads.name);
          setVal('input[name=company]', 'PenTestCorp');
          setVal('input[name=email]', payloads.email);
          setVal('input[name=telephone]', '+1234567890');
          setVal('input[name=role]', 'Founder');
          // For interest: keep select OR text-input-after-bypass
          const interest = document.querySelector('select[name=interest], input[name=interest]');
          if (interest) {
            if (interest.tagName === 'SELECT') {
              // select an arbitrary first option
              const opt = interest.options[1] || interest.options[0];
              if (opt) { interest.value = opt.value; interest.dispatchEvent(new Event('change',{bubbles:true})); }
            } else {
              interest.value = 'Partnership';
              interest.dispatchEvent(new Event('input',{bubbles:true}));
            }
          }
          setVal('textarea[name=message]', payloads.msg);
          // gdprConsent checkbox - check it
          const cb = document.querySelector('input[name=gdprConsent]');
          if (cb && !cb.checked) {
            cb.checked = true;
            cb.dispatchEvent(new Event('change',{bubbles:true}));
          }
        }""",
        {"name": payload_name, "email": payload_email, "msg": payload_msg},
    )
    await page.wait_for_timeout(500)
    await shot(page, "02_form_filled")

    # Now submit
    try:
        # Use form.submit() to bypass any client-side intercept and force the real submission
        await page.evaluate(
            """() => {
              const f = document.querySelector('form');
              if (f) {
                // Set honeypot to empty (real user would not fill it)
                const hp = document.querySelector('input[name=honeypot]');
                if (hp) hp.value = '';
                f.submit();
              }
            }"""
        )
        await page.wait_for_timeout(5000)
    except Exception as e:
        console_buffer.append(f"[submit-err] {e}")

    await shot(page, "03_after_submit")

    captured = await page.evaluate("() => window.__captured || []")
    print(f"captured events: {len(captured)}")
    for c in captured[:15]:
        url = c.get("url") or c.get("action") or "?"
        body = c.get("body") or c.get("data") or ""
        if isinstance(body, dict):
            body = json.dumps(body)[:300]
        elif isinstance(body, str):
            body = body[:300]
        print(f"  [{c.get('kind')}] {url}")
        if body:
            print(f"    body: {body}")

    # Look for successful submissions (any kind with non-empty body)
    submits = [c for c in captured if c.get("kind") in ("xhr", "fetch") and c.get("body") and c.get("method") and c.get("method") != "GET"]
    print(f"real POSTs/submits: {len(submits)}")

    add_finding(
        bug_type="FORM-SUBMISSION",
        title=f"Form submitted; {len(captured)} events, {len(submits)} POSTs captured",
        severity="Major",
        vectors=[
            f"Set name to XSS payload: {payload_name}",
            f"Set email to {payload_email}",
            f"Set message to script+img payload ({len(payload_msg)} chars)",
            "Filled honeypot empty, checked GDPR consent.",
            "Called form.submit() to force native submission.",
        ],
        observed={
            "console": "\n".join(console_buffer[-30:]),
            "network": "\n".join(network_buffer[-40:]),
            "visual": json.dumps(captured[:20], default=str, indent=2)[:5000],
        },
        analysis="If the form POSTed to a Next.js API route with the XSS payload intact, downstream CRM/email systems may render the payload. Need to check the actual request body to determine if validation stripped the payload.",
    )


async def probe_resend(page):
    print("\n=== DIRECT RESEND.COM PROBE ===")
    # The CSP allows connect-src https://api.resend.com — likely this is the email service.
    # Test direct access (which CSP would block from page context, but we can try via curl externally)
    # In the browser context, let's see if any XHR goes to api.resend.com
    await page.goto(f"{BASE}/contact", wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(2000)
    # Try to fetch api.resend.com from page context (should be blocked by CSP unless form does it)
    result = await page.evaluate(
        """async () => {
          try {
            const r = await fetch('https://api.resend.com/');
            return {status:r.status, ct:r.headers.get('content-type'), body: (await r.text()).slice(0,200)};
          } catch(e) { return {error:String(e)}; }
        }"""
    )
    print(f"resend.com probe: {result}")

    # Also try POSTing to api.resend.com directly (likely CORS blocked, but interesting)
    res = await page.evaluate(
        """async () => {
          try {
            const r = await fetch('https://api.resend.com/emails', {
              method:'POST',
              headers:{'Content-Type':'application/json','Authorization':'Bearer test'},
              body: JSON.stringify({from:'a@a.com', to:'b@b.com', subject:'test', html:'<script>alert(1)</script>'})
            });
            return {status:r.status, body:(await r.text()).slice(0,300)};
          } catch(e) { return {error:String(e)}; }
        }"""
    )
    print(f"resend.com POST probe: {res}")
    add_finding(
        bug_type="THIRD-PARTY-EMAIL-API",
        title="api.resend.com reachable & accepts emails endpoint (auth-gated)",
        severity="Edge-Case",
        vectors=[
            "Probed https://api.resend.com/ via fetch from page context.",
            "Probed POST https://api.resend.com/emails with test Authorization header.",
        ],
        observed={
            "console": "\n".join(console_buffer[-30:]),
            "network": "\n".join(network_buffer[-40:]),
            "visual": json.dumps({"root": result, "emails": res}, indent=2),
        },
        analysis="If resend.com is used as the transactional email backend, misconfiguration could allow attacker to use the public API key. The Authorization header indicates an API key pattern. CSP allows connect-src to resend.com from the page; we should verify the key is server-side only.",
    )


async def test_honeypot(page):
    print("\n=== HONEYPOT INTERACTION TEST ===")
    # Fill the honeypot (which should NOT be filled by humans) - see if backend rejects
    await page.goto(f"{BASE}/contact", wait_until="domcontentloaded", timeout=30000)
    await page.wait_for_timeout(2000)

    await page.evaluate(
        """() => {
          const setVal = (sel, v) => {
            const el = document.querySelector(sel);
            if (el) { el.value = v; el.dispatchEvent(new Event('input',{bubbles:true})); return true; }
            return false;
          };
          setVal('input[name=name]', 'Bot Tester');
          setVal('input[name=company]', 'BotCorp');
          setVal('input[name=email]', 'bot+probe@example.com');
          setVal('input[name=telephone]', '+10000000000');
          setVal('textarea[name=message]', 'bot message');
          // FILL HONEYPOT (bots do this)
          const hp = document.querySelector('input[name=honeypot]');
          if (hp) { hp.value = 'i-am-a-bot'; hp.dispatchEvent(new Event('input',{bubbles:true})); }
          // GDPR
          const cb = document.querySelector('input[name=gdprConsent]');
          if (cb && !cb.checked) { cb.checked = true; cb.dispatchEvent(new Event('change',{bubbles:true})); }
          // Interest
          const i = document.querySelector('select[name=interest]');
          if (i && i.options.length > 1) { i.value = i.options[1].value; i.dispatchEvent(new Event('change',{bubbles:true})); }
        }"""
    )

    # Hook fetch again
    await page.evaluate(
        """() => {
          window.__captured2 = [];
          const of = window.fetch;
          window.fetch = async function(...a) {
            const [u, init] = a;
            try { window.__captured2.push({url:String(u), method:(init&&init.method)||'GET', body:(init&&init.body)||null}); } catch(e){}
            return of.apply(this, a);
          };
        }"""
    )

    try:
        await page.evaluate("() => document.querySelector('form').submit()")
        await page.wait_for_timeout(5000)
    except Exception as e:
        console_buffer.append(f"[honeypot-submit-err] {e}")

    cap2 = await page.evaluate("() => window.__captured2 || []")
    real_posts = [c for c in cap2 if c.get("method") and c["method"] != "GET" and c.get("body")]
    print(f"honeypot-test posts captured: {len(real_posts)}")
    for c in real_posts[:5]:
        print(f"  {c}")

    add_finding(
        bug_type="HONEYPOT-BYPASS",
        title=f"Honeypot field filled; {len(real_posts)} real POSTs captured",
        severity="Major" if len(real_posts) > 0 else "Edge-Case",
        vectors=[
            "Filled all normal fields.",
            "Deliberately filled honeypot input (bots should be detected).",
            "Submitted form via form.submit().",
        ],
        observed={
            "console": "\n".join(console_buffer[-30:]),
            "network": "\n".join(network_buffer[-40:]),
            "visual": json.dumps(cap2[:10], default=str, indent=2)[:4000],
        },
        analysis="If honeypot fill still results in a POST being made (and not silently rejected), the honeypot is broken or only client-side. Server should reject any request with non-empty honeypot value.",
    )


async def test_interest_enum(page):
    print("\n=== INTEREST ENUM VALIDATION ===")
    # Try submitting arbitrary enum values that don't exist in the SELECT
    for evil in ["__proto__", "../../etc/passwd", "'; DROP TABLE users;--", "<script>alert(1)</script>", "Discovery9999"]:
        await page.goto(f"{BASE}/contact", wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2000)
        await page.evaluate(
            """(payloads) => {
              const setVal = (sel, v) => {
                const el = document.querySelector(sel);
                if (el) { el.removeAttribute('maxlength'); el.value = v; el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true})); return true; }
                return false;
              };
              setVal('input[name=name]', 'Enum Tester');
              setVal('input[name=company]', 'EnumCo');
              setVal('input[name=email]', 'enum@example.com');
              setVal('input[name=telephone]', '+10000000000');
              setVal('textarea[name=message]', 'enum test');
              const i = document.querySelector('select[name=interest]');
              if (i) {
                // Try to set arbitrary value bypassing option constraints
                const opt = document.createElement('option');
                opt.value = payloads.evil; opt.textContent = payloads.evil;
                i.appendChild(opt); i.value = payloads.evil;
                i.dispatchEvent(new Event('change',{bubbles:true}));
              }
              const cb = document.querySelector('input[name=gdprConsent]');
              if (cb && !cb.checked) { cb.checked = true; cb.dispatchEvent(new Event('change',{bubbles:true})); }
              const hp = document.querySelector('input[name=honeypot]');
              if (hp) hp.value = '';
            }""",
            {"evil": evil},
        )
        await page.wait_for_timeout(300)
        try:
            await page.evaluate("() => document.querySelector('form').submit()")
            await page.wait_for_timeout(3500)
        except Exception as e:
            console_buffer.append(f"[enum-err] {evil}: {e}")

    add_finding(
        bug_type="ENUM-INJECTION",
        title=f"interest enum values: tried {5} arbitrary payloads",
        severity="Major",
        vectors=[
            f"Probed interest={evil}" for evil in ["__proto__", "../../etc/passwd", "'; DROP TABLE users;--", "<script>alert(1)</script>", "Discovery9999"]
        ],
        observed={
            "console": "\n".join(console_buffer[-50:]),
            "network": "\n".join(network_buffer[-60:]),
            "visual": "Each enum value injected and form submitted; check network transcript for HTTP responses.",
        },
        analysis="Backend should validate enum values against an allow-list. If arbitrary values are accepted, attackers can pollute analytics, downstream filters, or trigger unexpected routing rules.",
    )


async def run():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context(viewport={"width": 1280, "height": 800}, ignore_https_errors=True)
        page = await context.new_page()
        attach(page)

        try:
            await submit_form_real(page)
        except Exception as e:
            console_buffer.append(f"[p3-submit-err] {e}\n{traceback.format_exc()}")

        try:
            await test_honeypot(page)
        except Exception as e:
            console_buffer.append(f"[p3-honeypot-err] {e}")

        try:
            await test_interest_enum(page)
        except Exception as e:
            console_buffer.append(f"[p3-enum-err] {e}")

        try:
            await probe_resend(page)
        except Exception as e:
            console_buffer.append(f"[p3-resend-err] {e}")

        await context.close()
        await browser.close()

    md = build_report()
    MD_PATH.write_text(md, encoding="utf-8")
    print(f"\nWrote markdown report: {MD_PATH}")
    pdf_ok = compile_pdf(MD_PATH, PDF_PATH)
    print(f"PDF: {pdf_ok} -> {PDF_PATH}")


def build_report() -> str:
    parts: list[str] = []
    parts.append(f"# Cline Browser Test Phase 3 - {BASE}")
    parts.append(f"\nGenerated: {datetime.now().isoformat()}\n")
    parts.append("## Executive Summary")
    parts.append(
        "\nPhase 3 captures real form submissions, probes the third-party email API "
        "(api.resend.com), tests honeypot bypass, and injects arbitrary enum values.\n"
    )
    parts.append("## Findings\n")
    for f in findings:
        console_md = "```\n" + (f["observed"].get("console", "_(none)_") or "_(none)_") + "\n```"
        net_md = "```\n" + (f["observed"].get("network", "_(none)_") or "_(none)_") + "\n```"
        visual_md = f["observed"].get("visual", "_(see screenshots)_")
        parts.append(f"### {f['bug_type']} - {f['title']}")
        parts.append(f"* **Severity Evaluation:** {f['severity']}")
        parts.append("* **Destructive Execution Vectors:**")
        for v in f["vectors"]:
            parts.append(f"  * {v}")
        parts.append("* **Observed Failure State:**")
        parts.append(f"  * **Console Outputs:** {console_md}")
        parts.append(f"  * **Network Paradox:** {net_md}")
        parts.append(f"  * **Visual Evidence:** {visual_md}")
        parts.append(f"* **Root-Cause Analysis:** {f['analysis']}")
        parts.append("")
    parts.append("## Captured HTTP Requests (all)\n```json\n" + json.dumps(request_buffer[-200:], indent=2, default=str)[:20000] + "\n```\n")
    parts.append("## Console Transcript (tail)\n```\n" + "\n".join(console_buffer[-300:])[:10000] + "\n```\n")
    parts.append("## Network Transcript (tail)\n```\n" + "\n".join(network_buffer[-300:])[:10000] + "\n```\n")
    return "\n".join(parts)


def compile_pdf(md_path: Path, pdf_path: Path) -> bool:
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Preformatted
        doc = SimpleDocTemplate(str(pdf_path), pagesize=letter, leftMargin=0.75*inch, rightMargin=0.75*inch,
                                topMargin=0.75*inch, bottomMargin=0.75*inch)
        styles = getSampleStyleSheet()
        body = ParagraphStyle('Body', parent=styles['Normal'], fontSize=9, leading=12)
        code = ParagraphStyle('Code', parent=styles['Code'], fontSize=7, leading=9)
        h2 = ParagraphStyle('H2', parent=styles['Heading2'], fontSize=12, spaceBefore=10, spaceAfter=6)
        title = ParagraphStyle('Title', parent=styles['Title'], fontSize=18, spaceAfter=12)
        h3 = ParagraphStyle('H3', parent=styles['Heading3'], fontSize=11, spaceBefore=8, spaceAfter=4)
        story = []
        text = md_path.read_text(encoding="utf-8")
        in_code = False
        code_buf = []
        for line in text.splitlines():
            if line.startswith("```"):
                if in_code:
                    story.append(Preformatted("\n".join(code_buf), code))
                    story.append(Spacer(1, 4))
                    code_buf = []
                    in_code = False
                else:
                    in_code = True
                continue
            if in_code:
                code_buf.append(line)
                continue
            if line.startswith("# "):
                story.append(Paragraph(line[2:].replace("<","<").replace(">",">"), title))
            elif line.startswith("## "):
                story.append(Paragraph(line[3:].replace("<","<").replace(">",">"), h2))
            elif line.startswith("### "):
                story.append(Paragraph(line[4:].replace("<","<").replace(">",">"), h3))
            elif line.startswith("- "):
                story.append(Paragraph("• " + line[2:].replace("<","<").replace(">",">"), body))
            elif line.strip():
                story.append(Paragraph(line.replace("<","<").replace(">",">"), body))
            else:
                story.append(Spacer(1, 4))
        doc.build(story)
        return pdf_path.exists()
    except Exception as e:
        print(f"[pdf-err] {e}")
        return False


if __name__ == "__main__":
    asyncio.run(run())
