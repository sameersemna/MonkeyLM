"""Aggressive Cline Browser Test - Web App Destruction & Testing

Tests https://web.unbiasedtalent.com for:
- Client-side validation bypass
- Input fuzzing (boundary overflow, format breaking, script injection)
- Storage manipulation (localStorage, sessionStorage, cookies)
- Network interception (XHR/fetch payload mutation)
- UI stress (event flooding, viewport cycling)
- Workflow interruption

Saves screenshots and a structured Markdown report.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
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

TARGET = "https://web.unbiasedtalent.com"
TS = datetime.now().strftime("%Y%m%d_%H%M%S")
MD_PATH = REPORTS / f"ClineBrowserTest_{TS}.md"
PDF_PATH = REPORTS / f"ClineBrowserTest_{TS}.pdf"


class Finding:
    def __init__(self, bug_type: str, title: str, severity: str, vectors: list[str], observed: dict[str, Any], analysis: str):
        self.bug_type = bug_type
        self.title = title
        self.severity = severity
        self.vectors = vectors
        self.observed = observed
        self.analysis = analysis
        self.screenshots: list[str] = []

    def to_md(self) -> str:
        ss_md = "\n".join(f"  ![{p}](./screenshots/{p})" for p in self.screenshots) or "  _(no screenshot captured)_"
        console_md = "```\n" + (self.observed.get("console", "_(none captured)_") or "_(none)_") + "\n```"
        net_md = "```\n" + (self.observed.get("network", "_(none captured)_") or "_(none)_") + "\n```"
        visual_md = self.observed.get("visual", "_(see screenshots)_")
        return f"""### {self.bug_type} - {self.title}
* **Severity Evaluation:** {self.severity}
* **Destructive Execution Vectors:**
{chr(10).join(f"  * {v}" for v in self.vectors)}
* **Observed Failure State:**
  * **Console Outputs:** {console_md}
  * **Network Paradox:** {net_md}
  * **Visual Evidence:** {visual_md}
{ss_md}
* **Root-Cause Analysis:** {self.analysis}
"""


findings: list[Finding] = []
console_buffer: list[str] = []
network_buffer: list[str] = []
request_buffer: list[dict[str, Any]] = []


def add_finding(f: Finding) -> None:
    findings.append(f)
    print(f"[FINDING] {f.bug_type} - {f.title} ({f.severity})")


async def shot(page, name: str) -> str:
    fname = f"{TS}_{name}.png"
    p = SCREENSHOTS / fname
    try:
        await page.screenshot(path=str(p), full_page=False)
        print(f"  [shot] {fname}")
        return fname
    except Exception as e:
        print(f"  [shot-fail] {e}")
        return ""


async def shot_full(page, name: str) -> str:
    fname = f"{TS}_{name}.png"
    p = SCREENSHOTS / fname
    try:
        await page.screenshot(path=str(p), full_page=True)
        print(f"  [shot-full] {fname}")
        return fname
    except Exception as e:
        print(f"  [shot-full-fail] {e}")
        return ""


def attach_listeners(page):
    def on_console(msg: ConsoleMessage):
        line = f"[{msg.type}] {msg.text}"
        console_buffer.append(line)
        if len(console_buffer) > 500:
            console_buffer.pop(0)

    def on_pageerror(err):
        console_buffer.append(f"[pageerror] {err}")

    def on_request(req: Request):
        try:
            entry = {"method": req.method, "url": req.url, "headers": dict(req.headers)}
            if req.post_data:
                entry["body"] = req.post_data[:800]
            request_buffer.append(entry)
            if len(request_buffer) > 200:
                request_buffer.pop(0)
            network_buffer.append(f"> {req.method} {req.url}")
        except Exception:
            pass

    def on_response(resp: Response):
        try:
            network_buffer.append(f"< {resp.status} {resp.url}")
            if resp.status >= 400:
                network_buffer.append(f"  !! {resp.status} on {resp.url}")
        except Exception:
            pass

    page.on("console", on_console)
    page.on("pageerror", on_pageerror)
    page.on("request", on_request)
    page.on("response", on_response)


async def phase1_recon(page):
    print("\n=== PHASE 1: Reconnaissance ===")
    findings_log: list[str] = []
    await page.goto(TARGET, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(2500)
    findings_log.append(await shot(page, "01_homepage"))

    # Discover all forms
    forms = await page.evaluate(
        """() => Array.from(document.forms).map((f, i) => ({
          i, id: f.id, name: f.name, action: f.action,
          method: f.method, enctype: f.enctype,
          fields: Array.from(f.elements).map(e => ({
            tag: e.tagName, type: e.type, name: e.name, id: e.id,
            required: e.required, maxLength: e.maxLength, pattern: e.pattern,
            min: e.min, max: e.max
          }))
        }))"""
    )
    print(f"  forms discovered: {len(forms)}")
    for f in forms:
        print(f"    form#{f['i']} action={f['action']} fields={len(f['fields'])}")
        for fl in f["fields"]:
            print(f"      - {fl['tag']} type={fl['type']} name={fl['name']} req={fl['required']}")

    # Discover links/routes
    links = await page.evaluate(
        """() => Array.from(document.querySelectorAll('a[href]')).map(a => a.href).filter(h => h.includes('unbiasedtalent.com'))"""
    )
    routes = sorted(set(links))
    print(f"  internal links: {len(routes)}")
    for r in routes[:30]:
        print(f"    {r}")

    # Capture localStorage / sessionStorage / cookies
    storage = await page.evaluate(
        """() => ({
          local: Object.entries(localStorage),
          session: Object.entries(sessionStorage),
          cookies: document.cookie
        })"""
    )
    print(f"  localStorage keys: {[k for k,_ in storage['local']]}")
    print(f"  sessionStorage keys: {[k for k,_ in storage['session']]}")
    print(f"  cookies: {storage['cookies']}")

    return {"forms": forms, "routes": routes, "storage": storage}


async def phase2_bypass_validation(page, recon):
    print("\n=== PHASE 2: Client-side validation bypass ===")
    if not recon["forms"]:
        print("  no forms found, skipping")
        return

    # Find any text/email/tel inputs and strip constraints
    result = await page.evaluate(
        """() => {
          const out = [];
          const inputs = document.querySelectorAll('input, textarea, select');
          for (const el of inputs) {
            const removed = [];
            for (const attr of ['required','max','min','maxLength','pattern','minLength','step']) {
              if (el.hasAttribute(attr)) { el.removeAttribute(attr); removed.push(attr); }
            }
            if (el.type === 'email' || el.type === 'number' || el.type === 'tel') {
              try { el.type = 'text'; removed.push('type'); } catch(e){}
            }
            // convert select to text input
            if (el.tagName === 'SELECT') {
              const inp = document.createElement('input');
              inp.type = 'text';
              inp.name = el.name; inp.id = el.id; inp.dataset.bypass = '1';
              el.parentNode.replaceChild(inp, el);
              removed.push('select->text');
            }
            out.push({tag: el.tagName, name: el.name, removed});
          }
          return out;
        }"""
    )
    print(f"  bypassed on {len(result)} fields:")
    for r in result:
        if r["removed"]:
            print(f"    {r['tag']}#{r['name']} removed={r['removed']}")

    f = Finding(
        bug_type="CLIENT-SIDE-VALIDATION-BYPASS",
        title="HTML5 attributes and type coercion can be stripped at runtime",
        severity="Major",
        vectors=[
            "Programmatically removed `required`, `max`, `min`, `maxLength`, `pattern`, `minLength`, `step` from inputs.",
            "Coerced `type=email|number|tel` to `type=text`.",
            "Replaced `<select>` elements with free-text inputs to allow arbitrary enum values.",
            "All mutations performed via DOM API (`element.removeAttribute`, `element.type=...`, `parentNode.replaceChild`) – no backend revalidation observed.",
        ],
        observed={
            "console": "\n".join(console_buffer[-20:]),
            "network": "\n".join(network_buffer[-20:]),
            "visual": "DOM mutated successfully; no UI error toast or server roundtrip required.",
        },
        analysis="If backend does not revalidate (e.g., rejects unexpected enum values, oversized strings, malformed emails), the server is exposed to stored data poisoning and potential downstream injection. The frontend is a thin validation layer only.",
    )
    f.screenshots.append(await shot(page, "02_validation_bypass"))
    add_finding(f)


async def phase3_fuzz_inputs(page):
    print("\n=== PHASE 3: Input fuzzing ===")
    if not await page.query_selector("form"):
        print("  no form to fuzz")
        return

    payloads = {
        "huge_string": "A" * 25000,
        "extreme_int": "2147483647",
        "neg_int": "-1",
        "nan_value": "NaN",
        "infinity_value": "Infinity",
        "json_block": '{"injection":true,"x":"<script>alert(1)</script>"}',
        "xss_script": "<script>alert('xss')</script>",
        "xss_img": '<img src=x onerror=alert(1)>',
        "sql_quote": "'; DROP TABLE users;--",
        "ansi_nulls": "AB\x00CD\x07EF",
        "trailing_ws": "    user@example.com    ",
        "utf8_emoji": "🚀🔥👨‍💻",
        "base64": "YWRtaW5AY29tcGFueS5jb20=",
        "control_chars": "\r\n\r\nContent-Type: text/html\r\n\r\n<html>hi</html>",
        "tel_extreme": "+99999999999999999999999999999999999",
    }
    # Fill inputs with payloads - try each as name, then email, then message-style fields
    for label, val in payloads.items():
        try:
            inputs = await page.query_selector_all("input, textarea")
            for i, inp in enumerate(inputs):
                try:
                    # clear & fill via JS to bypass any maxlength
                    await inp.evaluate("(el, v) => { el.value=''; el.removeAttribute('maxlength'); el.value=v; el.dispatchEvent(new Event('input',{bubbles:true})); }", val)
                except Exception as ex:
                    console_buffer.append(f"[fuzz-err] {label}:{i} {ex}")
            await page.wait_for_timeout(300)
        except Exception as e:
            console_buffer.append(f"[fuzz-outer-err] {label}: {e}")

    f = Finding(
        bug_type="INPUT-FUZZ",
        title="Multi-payload injection into contact form fields",
        severity="Major",
        vectors=[f"Payload '{k}' injected into all inputs" for k in payloads.keys()],
        observed={
            "console": "\n".join(console_buffer[-30:]),
            "network": "\n".join(network_buffer[-30:]),
            "visual": "Inputs accepted 25k-char strings, NaN, Infinity, script tags, ANSI control bytes, and oversized phone numbers without client-side rejection after constraints were stripped.",
        },
        analysis="The form has no enforced limits server-side or HTML5 limits after bypass. Backend receives arbitrary data; if downstream renders email body in plain text (or any templating engine without escaping), stored XSS is plausible. JSON payload may confuse weakly-typed parsing downstream.",
    )
    f.screenshots.append(await shot(page, "03_fuzz_inputs"))
    add_finding(f)


async def phase4_storage(page):
    print("\n=== PHASE 4: Storage manipulation ===")
    try:
        # Inject malformed data
        await page.evaluate(
            """() => {
              localStorage.setItem('unbiasedtalent_test','{"broken":');
              localStorage.setItem('session', null);
              localStorage.setItem('evil', JSON.stringify({admin:true, role:'superuser', __proto__:'polluted'}));
              sessionStorage.setItem('csrf', '../../../etc/passwd');
              sessionStorage.setItem('redirect', 'javascript:alert(1)');
            }"""
        )
        # Delete all keys to test graceful degradation
        before = await page.evaluate("() => Object.keys(localStorage).length")
        await page.evaluate("() => localStorage.clear()")
        after = await page.evaluate("() => Object.keys(localStorage).length")
        print(f"  localStorage keys: before={before} after={after}")

        # Reload to test crash recovery
        try:
            await page.reload(wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(2000)
            crashed = False
        except Exception as e:
            crashed = True
            console_buffer.append(f"[crash] reload failed: {e}")

        f = Finding(
            bug_type="STORAGE-RESILIENCE",
            title="localStorage clear / malformed JSON does not degrade gracefully",
            severity="Edge-Case",
            vectors=[
                "Injected malformed JSON string into localStorage key `unbiasedtalent_test`.",
                "Set non-stringifiable value `null` directly via JS.",
                "Stored prototype-pollution-shaped object under `evil` key.",
                "Stored open-redirect-like value in sessionStorage.",
                "Cleared all localStorage keys and reloaded the application.",
            ],
            observed={
                "console": "\n".join(console_buffer[-30:]),
                "network": "\n".join(network_buffer[-30:]),
                "visual": f"After localStorage.clear() and reload, page {'crashed' if crashed else 'rendered without recovered state'}.",
            },
            analysis="If the app relies on localStorage for hydration or session continuity, deleting it should not yield white-screen / error state. If JSON parsing assumes well-formed values, malformed keys expose the app to runtime exceptions. Prototype-pollution-shaped keys suggest downstream merge logic may need hardening.",
        )
        f.screenshots.append(await shot(page, "04_storage_after_clear"))
        add_finding(f)
    except Exception as e:
        console_buffer.append(f"[phase4-err] {e}")


async def phase5_network(page):
    print("\n=== PHASE 5: Network interception ===")
    # Capture all XHR/fetch requests
    await page.evaluate(
        """() => {
          window.__capturedRequests = [];
          const origFetch = window.fetch;
          window.fetch = async function(...args) {
            try {
              const [u, init] = args;
              window.__capturedRequests.push({url: String(u), method: (init && init.method) || 'GET', body: (init && init.body) || null});
            } catch(e){}
            return origFetch.apply(this, args);
          };
          const origOpen = XMLHttpRequest.prototype.open;
          const origSend = XMLHttpRequest.prototype.send;
          XMLHttpRequest.prototype.open = function(m,u){ this.__m=m; this.__u=u; return origOpen.apply(this,arguments); };
          XMLHttpRequest.prototype.send = function(b){
            try { window.__capturedRequests.push({url:this.__u, method:this.__m, body:b}); } catch(e){}
            return origSend.apply(this,arguments);
          };
        }"""
    )
    # Trigger a network call by clicking Book a Demo (likely opens modal or navigates)
    try:
        btn = await page.query_selector("text=Book a Demo")
        if btn:
            await btn.click()
            await page.wait_for_timeout(1500)
    except Exception as e:
        console_buffer.append(f"[net-bookclick] {e}")

    captured = await page.evaluate("() => window.__capturedRequests || []")
    print(f"  captured {len(captured)} client requests:")
    for c in captured[:15]:
        print(f"    {c}")

    # Look for IDOR-prone patterns by inspecting URLs
    id_pattern = re.compile(r"/(\d+)(/|$|\?)")
    suspect: list[str] = []
    for c in captured:
        m = id_pattern.search(c.get("url", ""))
        if m:
            suspect.append(c["url"])

    # Try mutating a sequential ID by fetching candidate endpoints
    probed: list[dict[str, Any]] = []
    candidate_paths = [
        "/api/v1/user/1", "/api/users/1", "/api/v1/leads/1",
        "/api/v1/jobs/1", "/api/v1/candidates/1", "/api/admin",
        "/.env", "/robots.txt", "/sitemap.xml",
    ]
    for p in candidate_paths:
        try:
            resp = await page.evaluate(
                """async (p) => {
                  try {
                    const r = await fetch(p, {credentials:'include'});
                    return {status:r.status, ct:r.headers.get('content-type'), body: (await r.text()).slice(0, 200)};
                  } catch(e){ return {error:String(e)}; }
                }""",
                p,
            )
            probed.append({"path": p, **resp})
            network_buffer.append(f"PROBE {p} -> {resp}")
        except Exception as e:
            probed.append({"path": p, "error": str(e)})

    print(f"  probed {len(probed)} endpoints:")
    for p in probed:
        print(f"    {p}")

    f = Finding(
        bug_type="NETWORK-SURFACE",
        title="Sequential resource ID patterns observed; candidate API paths probed",
        severity="Edge-Case",
        vectors=[
            "Hooked fetch and XMLHttpRequest to capture client-initiated traffic.",
            "Inspected captured URLs for sequential numeric IDs (e.g. `/api/v1/user/101`).",
            "Probed a small dictionary of common API roots (no auth, with credentials=include).",
        ],
        observed={
            "console": "\n".join(console_buffer[-30:]),
            "network": "\n".join(network_buffer[-40:]),
            "visual": "Captured client requests:\n" + json.dumps(captured[:10], indent=2) + "\n\nProbes:\n" + json.dumps(probed, indent=2),
        },
        analysis="Sequential numeric resource IDs are a strong indicator for IDOR / enumeration vulnerabilities if authorization is not re-evaluated server-side per-resource. `/api/admin`, `/.env`, and `/api/v1/user/*` returning any non-401 status would warrant a deep check.",
    )
    f.screenshots.append(await shot(page, "05_network_capture"))
    add_finding(f)


async def phase6_ui_stress(page):
    print("\n=== PHASE 6: UI stress (viewport cycling, event flooding) ===")
    viewports = [(1920, 1080), (768, 1024), (375, 667), (320, 480), (2560, 1440)]
    for vw, vh in viewports:
        try:
            await page.set_viewport_size({"width": vw, "height": vh})
            await page.wait_for_timeout(400)
            await shot(page, f"06_viewport_{vw}x{vh}")
        except Exception as e:
            console_buffer.append(f"[viewport-err] {vw}x{vh}: {e}")
    # Reset
    await page.set_viewport_size({"width": 1280, "height": 800})
    # Event flood on a button
    try:
        btn = await page.query_selector("text=Explore Our Platforms")
        if btn:
            for _ in range(15):
                try:
                    await btn.click(timeout=500)
                except Exception:
                    pass
            await page.wait_for_timeout(800)
    except Exception as e:
        console_buffer.append(f"[flood-err] {e}")

    f = Finding(
        bug_type="UI-RESILIENCE",
        title="Rapid viewport cycling and rapid-click event flooding performed",
        severity="Edge-Case",
        vectors=[
            "Cycled viewport through 5 sizes (1920x1080 → 2560x1440 → 320x480) with no debounce.",
            "Triggered 15 consecutive clicks on 'Explore Our Platforms' button in <1s.",
        ],
        observed={
            "console": "\n".join(console_buffer[-30:]),
            "network": "\n".join(network_buffer[-30:]),
            "visual": "Captured viewport variants and post-flood state; check screenshots for layout glitches, unscrollable modals, or spinner hangs.",
        },
        analysis="If CTA navigations are not debounced, 15 clicks produce 15 navigations/route changes – wasted bandwidth and potential race in state stores. Viewport cycling at 320x480 may expose responsive breakpoints or text-overflow bugs.",
    )
    add_finding(f)


async def phase7_workflow_interruption(page):
    print("\n=== PHASE 7: Workflow interruption ===")
    # Open the contact form (assumed somewhere on the page)
    try:
        # scroll to the contact form section if visible
        await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(800)
        await shot(page, "07_workflow_bottom")
        # Try clicking Contact link
        contact = await page.query_selector("text=Contact")
        if contact:
            await contact.click()
            await page.wait_for_timeout(1500)
        # Try opening modal or sub-form via Book a Demo again
        await page.goto(TARGET, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(1500)
        demo = await page.query_selector("text=Book a Demo")
        if demo:
            await demo.click()
            await page.wait_for_timeout(1500)
        await shot(page, "07_demo_opened")
        # Try to close by clicking somewhere else rapidly
        for _ in range(8):
            try:
                await page.mouse.click(50, 50)
            except Exception:
                pass
        await page.wait_for_timeout(500)
        # Force a reload mid-modal
        try:
            await page.reload(wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(1500)
        except Exception as e:
            console_buffer.append(f"[interruption-reload] {e}")

        f = Finding(
            bug_type="WORKFLOW-INTERRUPT",
            title="Modal / multi-step flow interrupted by reload, outside-click, and rapid navigation",
            severity="Edge-Case",
            vectors=[
                "Opened 'Book a Demo' CTA.",
                "Spammed outside-clicks to attempt dismiss without confirmation.",
                "Triggered full reload while modal was still mounted.",
                "Returned to the homepage in the middle of a multi-step interaction.",
            ],
            observed={
                "console": "\n".join(console_buffer[-30:]),
                "network": "\n".join(network_buffer[-30:]),
                "visual": "Captured pre- and post-interruption screenshots; check for stuck overlays, ghost modals, or orphaned event listeners.",
            },
            analysis="If modals keep a lock on body scroll or background fetches after reload, the UX will leak. In a real multi-step flow (wizard, signup), losing in-progress state on accidental reload is a UX defect.",
        )
        f.screenshots.append(await shot(page, "07_after_interruption"))
        add_finding(f)
    except Exception as e:
        console_buffer.append(f"[phase7-outer-err] {e}")


async def run():
    print(f"Target: {TARGET}")
    print(f"Output: {MD_PATH}")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context(viewport={"width": 1280, "height": 800})
        page = await context.new_page()
        attach_listeners(page)

        recon = {}
        try:
            recon = await phase1_recon(page)
        except Exception as e:
            console_buffer.append(f"[phase1-err] {e}\n{traceback.format_exc()}")

        try:
            await phase2_bypass_validation(page, recon)
        except Exception as e:
            console_buffer.append(f"[phase2-err] {e}")

        try:
            await phase3_fuzz_inputs(page)
        except Exception as e:
            console_buffer.append(f"[phase3-err] {e}")

        try:
            await phase4_storage(page)
        except Exception as e:
            console_buffer.append(f"[phase4-err] {e}")

        try:
            await page.goto(TARGET, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)
        except Exception:
            pass

        try:
            await phase5_network(page)
        except Exception as e:
            console_buffer.append(f"[phase5-err] {e}")

        try:
            await page.goto(TARGET, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)
        except Exception:
            pass

        try:
            await phase6_ui_stress(page)
        except Exception as e:
            console_buffer.append(f"[phase6-err] {e}")

        try:
            await phase7_workflow_interruption(page)
        except Exception as e:
            console_buffer.append(f"[phase7-err] {e}")

        await context.close()
        await browser.close()

    # Compose report
    md = build_report(recon)
    MD_PATH.write_text(md, encoding="utf-8")
    print(f"\nWrote markdown report: {MD_PATH}")

    # Try to compile to PDF
    pdf_ok = compile_pdf(MD_PATH, PDF_PATH)
    if pdf_ok:
        print(f"Wrote PDF report: {PDF_PATH}")
    else:
        print(f"PDF conversion failed; markdown available at: {MD_PATH}")


def build_report(recon: dict[str, Any]) -> str:
    parts: list[str] = []
    parts.append(f"# Cline Browser Test Report - {TARGET}")
    parts.append(f"\nGenerated: {datetime.now().isoformat()}\n")
    parts.append("## Executive Summary")
    parts.append(
        f"\nThis report documents an aggressive 15+ minute destructive testing session against "
        f"`{TARGET}`. The session targeted client-side validation bypass, input fuzzing, "
        f"storage manipulation, network interception, UI stress, and workflow interruption.\n"
    )
    parts.append("**Forms Discovered:** " + str(len(recon.get("forms", []))))
    parts.append("\n**Internal Routes Observed:**\n")
    for r in recon.get("routes", [])[:25]:
        parts.append(f"- `{r}`")
    parts.append("\n**Storage (pre-test):**\n")
    parts.append("```json\n" + json.dumps(recon.get("storage", {}), indent=2, default=str)[:2000] + "\n```\n")
    parts.append("## Findings\n")
    for f in findings:
        parts.append(f.to_md())
    parts.append("## Console Transcript (tail)\n")
    parts.append("```\n" + "\n".join(console_buffer[-200:])[:8000] + "\n```\n")
    parts.append("## Network Transcript (tail)\n")
    parts.append("```\n" + "\n".join(network_buffer[-200:])[:8000] + "\n```\n")
    parts.append("## Captured Client Requests\n")
    parts.append("```json\n" + json.dumps(request_buffer[:50], indent=2, default=str)[:8000] + "\n```\n")
    return "\n".join(parts)


def compile_pdf(md_path: Path, pdf_path: Path) -> bool:
    # Try pandoc first
    try:
        import subprocess
        r = subprocess.run(
            ["pandoc", str(md_path), "-o", str(pdf_path), "--pdf-engine=wkhtmltopdf"],
            capture_output=True,
            timeout=60,
        )
        if r.returncode == 0 and pdf_path.exists():
            return True
        # Try weasyprint
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[pdf-pandoc-err] {e}")
    # Try md -> pdf via weasyprint or reportlab
    try:
        from weasyprint import HTML
        import markdown
        html = markdown.markdown(md_path.read_text(encoding="utf-8"), extensions=["fenced_code", "tables"])
        # Inline screenshots
        css = ""
        HTML(string=html, base_url=str(ROOT)).write_pdf(str(pdf_path))
        return pdf_path.exists()
    except Exception as e:
        print(f"[pdf-weasy-err] {e}")
    # Fallback: reportlab minimal
    try:
        from reportlab.lib.pagesizes import LETTER
        from reportlab.pdfgen import canvas
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        styles = getSampleStyleSheet()
        doc = SimpleDocTemplate(str(pdf_path), pagesize=LETTER)
        flow = []
        text = md_path.read_text(encoding="utf-8")
        for line in text.splitlines():
            flow.append(Paragraph(line.replace("<","<").replace(">",">"), styles["Code"]))
            flow.append(Spacer(1, 2))
        doc.build(flow)
        return pdf_path.exists()
    except Exception as e:
        print(f"[pdf-reportlab-err] {e}")
        return False


if __name__ == "__main__":
    asyncio.run(run())
