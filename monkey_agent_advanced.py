import argparse
import asyncio
import hashlib
import importlib
import json
import os
import random
import re
import subprocess
import time
from urllib.parse import urlparse
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import ollama
from playwright.async_api import Dialog, Page, Route, async_playwright


def _optional_import(module_name: str, attr_name: Optional[str] = None):
    try:
        module = importlib.import_module(module_name)
        return getattr(module, attr_name) if attr_name else module
    except Exception:
        return None


Faker = _optional_import("faker", "Faker")
Image = _optional_import("PIL", "Image")
pil_pixelmatch = _optional_import("pixelmatch.contrib.PIL", "pixelmatch")

# CONFIGURATION
DEFAULT_TARGET_URL = "https://noblequran-85hu2yge.manus.space/"
DEFAULT_OLLAMA_MODEL = "minimax-m3:cloud"
DEFAULT_MAX_STEPS = 100
DEFAULT_HEADLESS = True
DEFAULT_WINDOW_SIZE = "1920,1080"
DEFAULT_NO_VIEWPORT = True

AXE_CDN_URL = "https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.9.1/axe.min.js"
VISUAL_DIFF_THRESHOLD_RATIO = 0.01
LAYOUT_SHIFT_THRESHOLD_PX = 50
STATE_LOOP_THRESHOLD = 3
ACTION_COOLDOWN_SECONDS = 1.0
OLLAMA_DECISION_OPTIONS: Dict[str, Any] = {
    "temperature": 0.2,
    "top_p": 0.9,
    "repeat_penalty": 1.05,
    "num_ctx": 4096,
}


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value.strip())
    except Exception:
        return default


def _normalize_window_size(raw: str, fallback: str = DEFAULT_WINDOW_SIZE) -> str:
    if not raw:
        return fallback
    candidate = raw.strip().lower().replace("x", ",")
    parts = [p.strip() for p in candidate.split(",")]
    if len(parts) != 2:
        return fallback
    try:
        width = int(parts[0])
        height = int(parts[1])
    except Exception:
        return fallback
    if width < 320 or height < 200:
        return fallback
    return f"{width},{height}"


TARGET_URL = os.getenv("TARGET_URL", DEFAULT_TARGET_URL)
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)
MAX_STEPS = max(1, _env_int("MAX_STEPS", DEFAULT_MAX_STEPS))
HEADLESS = _env_bool("HEADLESS", default=DEFAULT_HEADLESS)
BROWSER_WINDOW_SIZE = _normalize_window_size(os.getenv("BROWSER_WINDOW_SIZE", DEFAULT_WINDOW_SIZE))
NO_VIEWPORT = _env_bool("NO_VIEWPORT", default=DEFAULT_NO_VIEWPORT)
ACTIVE_SEED: Optional[str] = None


STRICT_SANDBOX = _env_bool("STRICT_SANDBOX", default=False)
ALLOW_NO_SANDBOX_FALLBACK = _env_bool("ALLOW_NO_SANDBOX_FALLBACK", default=False)

# 📁 TIMESTAMPED OUTPUT FOLDER
TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
OUTPUT_DIR = os.path.abspath(f"testrun_{TIMESTAMP}")
os.makedirs(OUTPUT_DIR, exist_ok=True)
USER_DATA_ROOT = os.path.abspath("./playwright_user_data")
RUN_USER_DATA_DIR = os.path.join(USER_DATA_ROOT, f"session_{TIMESTAMP}")
os.makedirs(USER_DATA_ROOT, exist_ok=True)
os.makedirs(RUN_USER_DATA_DIR, exist_ok=True)

test_logs: List[Dict[str, Any]] = []
BROWSER_LAUNCH_INFO: Dict[str, Any] = {
    "mode": "unknown",
    "args": [],
    "error": None,
    "window_size": BROWSER_WINDOW_SIZE,
    "no_viewport": NO_VIEWPORT,
    "strict_sandbox": STRICT_SANDBOX,
    "allow_no_sandbox_fallback": ALLOW_NO_SANDBOX_FALLBACK,
    "user_data_dir": RUN_USER_DATA_DIR,
}

ALLOWED_ACTIONS = {
    "click",
    "type",
    "submit_form",
    "handle_modal",
    "scroll",
    "back",
    "random_jump",
    "restart_target",
}


def normalize_action_plan(raw_plan: Any) -> Dict[str, str]:
    if not isinstance(raw_plan, dict):
        return {"action": "scroll", "target": "", "value": ""}

    action = str(raw_plan.get("action", "scroll")).strip().lower()
    if action not in ALLOWED_ACTIONS:
        action = "scroll"

    target = raw_plan.get("target", "")
    value = raw_plan.get("value", "")
    if target is None:
        target = ""
    if value is None:
        value = ""

    return {
        "action": action,
        "target": str(target),
        "value": str(value),
    }


def is_in_scope(current_url: str, target_url: str) -> bool:
    """Return True when current_url stays within target_url netloc/domain boundary."""
    try:
        current = urlparse(current_url)
        target = urlparse(target_url)
    except Exception:
        return False

    if not current.netloc or not target.netloc:
        return False

    return current.netloc.lower() == target.netloc.lower()


def parse_cli_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Advanced monkey testing agent")
    parser.add_argument("--target-url", help="Target URL to test")
    parser.add_argument("--ollama-model", help="Ollama model name to use")
    parser.add_argument("--max-steps", type=int, help="Maximum monkey steps to execute")
    parser.add_argument("--seed", type=int, help="Random seed for deterministic test replay")
    parser.add_argument("--window-size", help="Browser window size as WIDTH,HEIGHT or WIDTHxHEIGHT")

    headless_group = parser.add_mutually_exclusive_group()
    headless_group.add_argument("--headless", dest="headless", action="store_true", help="Run in headless mode")
    headless_group.add_argument("--headed", dest="headless", action="store_false", help="Run with UI")
    parser.set_defaults(headless=None)

    viewport_group = parser.add_mutually_exclusive_group()
    viewport_group.add_argument(
        "--no-viewport",
        dest="no_viewport",
        action="store_true",
        help="Use browser window size directly (Playwright no_viewport=True)",
    )
    viewport_group.add_argument(
        "--use-viewport",
        dest="no_viewport",
        action="store_false",
        help="Enable Playwright viewport emulation",
    )
    parser.set_defaults(no_viewport=None)

    return parser.parse_args()


def apply_runtime_overrides(args: argparse.Namespace) -> None:
    global TARGET_URL, OLLAMA_MODEL, MAX_STEPS, HEADLESS, BROWSER_WINDOW_SIZE, NO_VIEWPORT, ACTIVE_SEED

    if args.target_url:
        TARGET_URL = args.target_url
    if args.ollama_model:
        OLLAMA_MODEL = args.ollama_model
    if args.max_steps is not None:
        MAX_STEPS = max(1, args.max_steps)
    if args.headless is not None:
        HEADLESS = bool(args.headless)
    if args.window_size:
        BROWSER_WINDOW_SIZE = _normalize_window_size(args.window_size, fallback=BROWSER_WINDOW_SIZE)
    if args.no_viewport is not None:
        NO_VIEWPORT = bool(args.no_viewport)
    if args.seed is not None:
        random.seed(args.seed)
        ACTIVE_SEED = str(args.seed)


@dataclass
class PageSnapshot:
    """Normalized, lightweight representation of page state used for diffing and planning."""

    url: str
    title: str
    dom_hash: str
    structure_hash: str
    elements: List[str] = field(default_factory=list)
    layout_anchors: Dict[str, Dict[str, float]] = field(default_factory=dict)
    modal_count: int = 0
    spinner_count: int = 0
    disabled_controls: int = 0
    screenshot_path: str = ""
    timestamp: float = 0.0


class DefectTracker:
    """Centralized defect tracker to keep reporting deterministic and CI-friendly."""

    def __init__(self) -> None:
        self.layout_instability: List[Dict[str, Any]] = []
        self.visual_regressions: List[Dict[str, Any]] = []
        self.security_risks: List[Dict[str, Any]] = []
        self.accessibility_violations: List[Dict[str, Any]] = []
        self.performance_bottlenecks: List[Dict[str, Any]] = []
        self.console_findings: List[Dict[str, Any]] = []
        self.race_findings: List[Dict[str, Any]] = []
        self.boundary_drift: List[Dict[str, Any]] = []

    def add(self, category: str, payload: Dict[str, Any]) -> None:
        collection = getattr(self, category, None)
        if collection is not None:
            collection.append(payload)


class Fuzzer:
    """Produces mixed benign and malicious payloads for resilience and security testing."""

    def __init__(self) -> None:
        self.fake = Faker() if Faker else None
        self.owasp_payloads = [
            "' OR 1=1 --",
            "\" OR \"1\"=\"1\" --",
            "<script>alert('xss')</script>",
            "<img src=x onerror=alert(1)>",
            "javascript:alert(1)",
            "../../../../etc/passwd",
            "${7*7}",
            "{{7*7}}",
            "%0d%0aSet-Cookie:evil=true",
            "'; DROP TABLE users; --",
            "A" * 12000,
        ]

    def next_payload(self) -> str:
        # Blend realistic and malicious payloads so we exercise both validation and sanitization paths.
        candidates = list(self.owasp_payloads)
        if self.fake:
            candidates.extend(
                [
                    self.fake.email(),
                    self.fake.user_name(),
                    self.fake.name(),
                    self.fake.uri(),
                    self.fake.pystr(min_chars=20, max_chars=100),
                ]
            )
        return random.choice(candidates)


class A11yChecker:
    """Injects axe-core and executes periodic scans to surface high-severity a11y defects."""

    def __init__(self, defects: DefectTracker) -> None:
        self.injected_pages: set[int] = set()
        self.defects = defects

    async def ensure_injected(self, page: Page) -> None:
        page_id = id(page)
        if page_id in self.injected_pages:
            return
        try:
            await page.add_script_tag(url=AXE_CDN_URL)
            self.injected_pages.add(page_id)
        except Exception as exc:
            self.defects.add(
                "console_findings",
                {
                    "step": -1,
                    "type": "axe-injection-warning",
                    "severity": "warning",
                    "message": f"Unable to inject axe-core (likely CSP/network): {exc}",
                    "url": page.url,
                },
            )

    async def scan(self, page: Page, step_num: int) -> List[Dict[str, Any]]:
        await self.ensure_injected(page)
        try:
            results = await page.evaluate(
                """async () => {
                    try {
                        if (!window.axe) return { error: 'axe_missing', violations: [] };
                        const result = await window.axe.run(document, {
                            resultTypes: ['violations'],
                            runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'best-practice'] }
                        });
                        return result;
                    } catch (err) {
                        return {
                            error: 'axe_runtime_error',
                            errorMessage: String(err || 'unknown axe error'),
                            violations: []
                        };
                    }
                }"""
            )
        except Exception as exc:
            self.defects.add(
                "console_findings",
                {
                    "step": step_num,
                    "type": "axe-runtime-warning",
                    "severity": "warning",
                    "message": str(exc),
                    "url": page.url,
                },
            )
            return []

        if results.get("error"):
            self.defects.add(
                "console_findings",
                {
                    "step": step_num,
                    "type": "axe-runtime-warning",
                    "severity": "warning",
                    "message": results.get("errorMessage", results.get("error")),
                    "url": page.url,
                },
            )
            return []

        filtered: List[Dict[str, Any]] = []
        for violation in results.get("violations", []):
            impact = (violation.get("impact") or "").lower()
            if impact in {"critical", "serious"}:
                finding = {
                    "step": step_num,
                    "severity": impact,
                    "id": violation.get("id"),
                    "description": violation.get("description"),
                    "help": violation.get("help"),
                    "nodes": len(violation.get("nodes", [])),
                    "url": page.url,
                }
                filtered.append(finding)
        for finding in filtered:
            self.defects.add("accessibility_violations", finding)
        return filtered


class NetworkMonitor:
    """Intercepts API calls to inject realistic latency/failure and monitors stale loading UI."""

    def __init__(self, defects: DefectTracker) -> None:
        self.defects = defects
        self.injected_events: List[Dict[str, Any]] = []
        self.route_enabled = False

    async def install(self, page: Page) -> None:
        if self.route_enabled:
            return

        async def _route_handler(route: Route) -> None:
            request = route.request
            resource_type = request.resource_type
            url = request.url
            # Limit fault injection to API/XHR/fetch traffic to avoid breaking static assets constantly.
            if resource_type in {"xhr", "fetch"} or "/api/" in url:
                roll = random.random()
                if roll < 0.15:
                    delay_seconds = random.randint(2, 5)
                    self.injected_events.append(
                        {
                            "type": "delay",
                            "url": url,
                            "delay_seconds": delay_seconds,
                            "timestamp": time.time(),
                        }
                    )
                    await asyncio.sleep(delay_seconds)
                    await route.continue_()
                    return
                if roll < 0.25:
                    status_code = random.choice([500, 503])
                    self.injected_events.append(
                        {
                            "type": "http_error",
                            "url": url,
                            "status": status_code,
                            "timestamp": time.time(),
                        }
                    )
                    await route.fulfill(
                        status=status_code,
                        content_type="application/json",
                        body=json.dumps({"error": "injected fault", "status": status_code}),
                    )
                    return
            await route.continue_()

        await page.route("**/*", _route_handler)
        self.route_enabled = True

    async def detect_zombie_ui(self, page: Page, step_num: int) -> Optional[Dict[str, Any]]:
        # A zombie UI is when loading indicators remain active and controls stay disabled for too long.
        try:
            before = await page.evaluate(
                """() => {
                    const spinnerSel = '[aria-busy="true"], .spinner, .loading, [data-testid*="spinner" i]';
                    const spinnerCount = document.querySelectorAll(spinnerSel).length;
                    const disabledCount = document.querySelectorAll('button:disabled, input:disabled, select:disabled, textarea:disabled').length;
                    return { spinnerCount, disabledCount };
                }"""
            )
            await asyncio.sleep(3.0)
            after = await page.evaluate(
                """() => {
                    const spinnerSel = '[aria-busy="true"], .spinner, .loading, [data-testid*="spinner" i]';
                    const spinnerCount = document.querySelectorAll(spinnerSel).length;
                    const disabledCount = document.querySelectorAll('button:disabled, input:disabled, select:disabled, textarea:disabled').length;
                    return { spinnerCount, disabledCount };
                }"""
            )
        except Exception:
            return None

        if before.get("spinnerCount", 0) > 0 and after.get("spinnerCount", 0) >= before.get("spinnerCount", 0):
            finding = {
                "step": step_num,
                "type": "zombie-ui",
                "description": "Potential zombie UI: spinners persisted for >3s after action.",
                "before": before,
                "after": after,
                "url": page.url,
            }
            self.defects.add("race_findings", finding)
            return finding

        if before.get("disabledCount", 0) > 0 and after.get("disabledCount", 0) >= before.get("disabledCount", 0):
            finding = {
                "step": step_num,
                "type": "disabled-stuck",
                "description": "Potential zombie UI: disabled controls persisted for >3s after action.",
                "before": before,
                "after": after,
                "url": page.url,
            }
            self.defects.add("race_findings", finding)
            return finding
        return None


class PerformanceMonitor:
    """Collects long-task and memory telemetry through CDP and in-page observers."""

    def __init__(self, defects: DefectTracker) -> None:
        self.defects = defects
        self.cdp = None

    async def install(self, page: Page) -> None:
        if self.cdp is not None:
            return
        self.cdp = await page.context.new_cdp_session(page)
        await self.cdp.send("Performance.enable")
        try:
            await self.cdp.send("Page.enable")
        except Exception:
            pass
        await page.add_init_script(
            """
            () => {
                window.__deepLongTasks = [];
                try {
                    const obs = new PerformanceObserver(list => {
                        for (const entry of list.getEntries()) {
                            window.__deepLongTasks.push({
                                startTime: entry.startTime,
                                duration: entry.duration,
                                name: entry.name || 'longtask'
                            });
                        }
                    });
                    obs.observe({ type: 'longtask', buffered: true });
                } catch (e) {}
            }
            """
        )

    async def snapshot(self, page: Page) -> Dict[str, Any]:
        metrics = await self.cdp.send("Performance.getMetrics") if self.cdp else {"metrics": []}
        navigation: Dict[str, Any] = {"entries": []}
        if self.cdp:
            try:
                history = await self.cdp.send("Page.getNavigationHistory")
                entries = history.get("entries", [])
                navigation = {
                    "current_index": history.get("currentIndex", -1),
                    "entries": [
                        {
                            "id": item.get("id"),
                            "url": item.get("url"),
                            "title": item.get("title"),
                            "transition_type": item.get("transitionType"),
                        }
                        for item in entries
                    ],
                }
            except Exception as exc:
                navigation = {"entries": [], "error": str(exc)}
        memory = await page.evaluate(
            """() => {
                const mem = performance.memory || {};
                return {
                    usedJSHeapSize: mem.usedJSHeapSize || 0,
                    totalJSHeapSize: mem.totalJSHeapSize || 0,
                    jsHeapSizeLimit: mem.jsHeapSizeLimit || 0,
                };
            }"""
        )
        long_tasks = await page.evaluate("() => window.__deepLongTasks || []")
        fps = await page.evaluate(
            """() => new Promise(resolve => {
                const start = performance.now();
                let frames = 0;
                function tick(now) {
                    frames += 1;
                    if (now - start >= 600) {
                        const fps = frames / ((now - start) / 1000);
                        resolve({ fps });
                        return;
                    }
                    requestAnimationFrame(tick);
                }
                requestAnimationFrame(tick);
            })"""
        )
        return {
            "metrics": metrics.get("metrics", []),
            "navigation": navigation,
            "memory": memory,
            "long_tasks": long_tasks,
            "fps": fps,
        }

    async def detect_bottlenecks(
        self,
        before: Dict[str, Any],
        after: Dict[str, Any],
        step_num: int,
        action: str,
        url: str,
    ) -> List[Dict[str, Any]]:
        findings: List[Dict[str, Any]] = []

        before_heap = before.get("memory", {}).get("usedJSHeapSize", 0)
        after_heap = after.get("memory", {}).get("usedJSHeapSize", 0)
        heap_delta = after_heap - before_heap
        if heap_delta > 30_000_000:
            findings.append(
                {
                    "step": step_num,
                    "type": "memory-spike",
                    "action": action,
                    "heap_delta_bytes": heap_delta,
                    "url": url,
                }
            )

        before_long_count = len(before.get("long_tasks", []))
        new_long_tasks = after.get("long_tasks", [])[before_long_count:]
        for task in new_long_tasks:
            duration = task.get("duration", 0)
            if duration > 50:
                findings.append(
                    {
                        "step": step_num,
                        "type": "long-task",
                        "action": action,
                        "duration_ms": duration,
                        "url": url,
                    }
                )
            if duration > 2000:
                findings.append(
                    {
                        "step": step_num,
                        "type": "main-thread-blocked",
                        "action": action,
                        "duration_ms": duration,
                        "url": url,
                    }
                )

        fps = after.get("fps", {}).get("fps", 60)
        if fps < 20:
            findings.append(
                {
                    "step": step_num,
                    "type": "fps-drop",
                    "action": action,
                    "fps": fps,
                    "url": url,
                }
            )

        for finding in findings:
            self.defects.add("performance_bottlenecks", finding)
        return findings


def state_to_prompt(snapshot: PageSnapshot) -> str:
    return (
        f"URL: {snapshot.url}\n"
        f"Title: {snapshot.title}\n"
        f"DOMHash: {snapshot.dom_hash}\n"
        f"Modals: {snapshot.modal_count}\n"
        f"Spinners: {snapshot.spinner_count}\n"
        f"DisabledControls: {snapshot.disabled_controls}\n"
        "Elements:\n"
        + "\n".join(snapshot.elements)
    )


def _extract_target_id(target: Any) -> Optional[int]:
    if isinstance(target, int):
        return target if target >= 0 else None
    target_str = str(target or "").strip()
    if not target_str:
        return None

    if target_str.isdigit():
        return int(target_str)

    match = re.search(r"\[id\s*=\s*(\d+)\]", target_str, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


async def _locator_for_target_id(page: Page, target_id: Any) -> Optional[Any]:
    parsed_id = _extract_target_id(target_id)
    if parsed_id is None:
        return None

    selector = "button, a, input, select, textarea, [role='button'], [onclick], form"
    candidates = page.locator(selector)
    count = await candidates.count()
    visible_index = 0
    for idx in range(count):
        candidate = candidates.nth(idx)
        bbox = await candidate.bounding_box()
        if not bbox:
            continue
        if bbox.get("width", 0) <= 0 or bbox.get("height", 0) <= 0:
            continue
        if visible_index == parsed_id:
            return candidate
        visible_index += 1
    return None


def _sanitize_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name)[:120]


async def wait_for_page_ready(page: Page, phase: str, strict: bool = False) -> None:
    """
    Robust page readiness wait.
    networkidle is ideal when available, but many SPAs keep polling and never become idle.
    We therefore fall back to domcontentloaded/load to avoid hard startup failures.
    """
    try:
        await page.wait_for_load_state("networkidle", timeout=8000)
        return
    except Exception:
        pass

    try:
        await page.wait_for_load_state("domcontentloaded", timeout=10000)
        return
    except Exception:
        pass

    try:
        await page.wait_for_load_state("load", timeout=12000)
        return
    except Exception as exc:
        msg = f"⚠️ Readiness fallback failed during {phase}: {exc}"
        if strict:
            raise RuntimeError(msg) from exc
        print(msg)


async def launch_context_with_fallback(playwright_instance):
    """
    Launch Chromium with sandbox enabled first.
    If that fails, no-sandbox fallback is only used when explicitly allowed.
    """
    global BROWSER_LAUNCH_INFO

    base_args = [f"--window-size={BROWSER_WINDOW_SIZE}", "--disable-blink-features=AutomationControlled"]
    sandbox_args = list(base_args)
    no_sandbox_args = base_args + ["--no-sandbox", "--disable-setuid-sandbox"]

    try:
        context = await playwright_instance.chromium.launch_persistent_context(
            user_data_dir=RUN_USER_DATA_DIR,
            headless=HEADLESS,
            args=sandbox_args,
            no_viewport=NO_VIEWPORT,
        )
        BROWSER_LAUNCH_INFO = {
            "mode": "sandbox",
            "args": sandbox_args,
            "error": None,
            "window_size": BROWSER_WINDOW_SIZE,
            "no_viewport": NO_VIEWPORT,
            "headless": HEADLESS,
            "user_data_dir": RUN_USER_DATA_DIR,
        }
        print("🛡️ Browser launch mode: sandbox")
        return context
    except Exception as sandbox_exc:
        if STRICT_SANDBOX or not ALLOW_NO_SANDBOX_FALLBACK:
            mode = "sandbox-required-failed" if STRICT_SANDBOX else "sandbox-failed-no-fallback"
            BROWSER_LAUNCH_INFO = {
                "mode": mode,
                "args": sandbox_args,
                "error": str(sandbox_exc),
                "window_size": BROWSER_WINDOW_SIZE,
                "no_viewport": NO_VIEWPORT,
                "headless": HEADLESS,
                "strict_sandbox": STRICT_SANDBOX,
                "allow_no_sandbox_fallback": ALLOW_NO_SANDBOX_FALLBACK,
            }
            policy_hint = (
                "STRICT_SANDBOX is enabled" if STRICT_SANDBOX else "ALLOW_NO_SANDBOX_FALLBACK is disabled"
            )
            raise RuntimeError(
                "Sandbox launch failed and no-sandbox fallback is blocked "
                f"({policy_hint}). Set ALLOW_NO_SANDBOX_FALLBACK=true if you want to permit fallback."
            ) from sandbox_exc

        print(f"⚠️ Sandbox launch failed, retrying with no-sandbox: {sandbox_exc}")
        context = await playwright_instance.chromium.launch_persistent_context(
            user_data_dir=RUN_USER_DATA_DIR,
            headless=HEADLESS,
            args=no_sandbox_args,
            no_viewport=NO_VIEWPORT,
        )
        BROWSER_LAUNCH_INFO = {
            "mode": "no-sandbox-fallback",
            "args": no_sandbox_args,
            "error": str(sandbox_exc),
            "window_size": BROWSER_WINDOW_SIZE,
            "no_viewport": NO_VIEWPORT,
            "headless": HEADLESS,
            "strict_sandbox": STRICT_SANDBOX,
            "allow_no_sandbox_fallback": ALLOW_NO_SANDBOX_FALLBACK,
            "user_data_dir": RUN_USER_DATA_DIR,
        }
        print("🔓 Browser launch mode: no-sandbox-fallback")
        return context


async def capture_dom_and_layout(page: Page) -> Dict[str, Any]:
    return await page.evaluate(
        """() => {
            const collectText = (el) => {
                let txt = el.innerText?.trim()
                    || el.getAttribute('aria-label')
                    || el.getAttribute('name')
                    || el.placeholder
                    || el.getAttribute('title')
                    || el.value
                    || '';
                if (txt.length > 80) txt = txt.slice(0, 80) + '...';
                return txt;
            };

            const interactives = Array.from(document.querySelectorAll(
                'button, a, input, select, textarea, [role="button"], [onclick], form'
            ));
            const tags = [];
            const anchors = {};
            let visibleIndex = 0;

            interactives.forEach((el) => {
                const rect = el.getBoundingClientRect();
                if (rect.width <= 0 || rect.height <= 0) return;
                const itemId = visibleIndex;
                visibleIndex += 1;
                const text = collectText(el);
                let typeInfo = el.tagName;
                if (el.tagName === 'INPUT') typeInfo = `INPUT[type=${el.type}]`;
                tags.push(`[id=${itemId}] <${typeInfo} text="${text}" />`);

                // Use a deterministic anchor key to track layout shifts across actions.
                const idPart = el.id ? `#${el.id}` : '';
                const clsPart = (el.className && typeof el.className === 'string')
                    ? '.' + el.className.split(/\\s+/).slice(0, 2).join('.')
                    : '';
                const key = `${itemId}::${el.tagName}${idPart}${clsPart}::${text.slice(0, 20)}`;
                anchors[key] = { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
            });

            const modals = Array.from(document.querySelectorAll('[role="dialog"], .modal, .popup, .alert'))
                .filter(el => {
                    const r = el.getBoundingClientRect();
                    return r.width > 0 && r.height > 0;
                });

            const spinnerSel = '[aria-busy="true"], .spinner, .loading, [data-testid*="spinner" i]';
            const spinnerCount = document.querySelectorAll(spinnerSel).length;
            const disabledControls = document.querySelectorAll(
                'button:disabled, input:disabled, select:disabled, textarea:disabled'
            ).length;

            const structure = tags.map(t => t.replace(/text=\".*?\"/, 'text=""')).join('|');

            return {
                url: window.location.href,
                title: document.title,
                elements: tags,
                structure,
                layoutAnchors: anchors,
                modalCount: modals.length,
                spinnerCount,
                disabledControls,
            };
        }"""
    )


async def get_page_state(page: Page, step_num: int, phase: str = "before") -> PageSnapshot:
    """Collects state plus screenshot; resilient to transient navigation context resets."""
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=5000)
    except Exception:
        pass

    try:
        raw = await capture_dom_and_layout(page)
    except Exception as exc:
        if "Execution context was destroyed" in str(exc):
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
                raw = await capture_dom_and_layout(page)
            except Exception:
                raw = {
                    "url": page.url,
                    "title": "Loading",
                    "elements": [],
                    "structure": "",
                    "layoutAnchors": {},
                    "modalCount": 0,
                    "spinnerCount": 0,
                    "disabledControls": 0,
                }
        else:
            raise

    elements = raw.get("elements", [])
    structure = raw.get("structure", "")
    dom_fingerprint_source = "|".join(elements)
    dom_hash = hashlib.sha256(dom_fingerprint_source.encode("utf-8")).hexdigest()
    structure_hash = hashlib.sha256(structure.encode("utf-8")).hexdigest()

    screenshot_name = _sanitize_filename(f"step_{step_num:03d}_{phase}.png")
    screenshot_path = os.path.join(OUTPUT_DIR, screenshot_name)
    try:
        await page.screenshot(path=screenshot_path, full_page=True)
    except Exception:
        screenshot_path = ""

    return PageSnapshot(
        url=raw.get("url", page.url),
        title=raw.get("title", ""),
        dom_hash=dom_hash,
        structure_hash=structure_hash,
        elements=elements,
        layout_anchors=raw.get("layoutAnchors", {}),
        modal_count=raw.get("modalCount", 0),
        spinner_count=raw.get("spinnerCount", 0),
        disabled_controls=raw.get("disabledControls", 0),
        screenshot_path=screenshot_path,
        timestamp=time.time(),
    )


def compute_max_layout_shift(before: PageSnapshot, after: PageSnapshot) -> float:
    max_shift = 0.0
    common_keys = set(before.layout_anchors.keys()) & set(after.layout_anchors.keys())
    for key in common_keys:
        b = before.layout_anchors[key]
        a = after.layout_anchors[key]
        shift = max(abs(a["x"] - b["x"]), abs(a["y"] - b["y"]))
        max_shift = max(max_shift, shift)
    return max_shift


def compare_screenshots_pixelmatch(before_path: str, after_path: str, step_num: int) -> Dict[str, Any]:
    """
    Compares screenshots using pixelmatch python binding first, then subprocess fallback.
    Returns diff metadata for reporting and alerting.
    """
    result: Dict[str, Any] = {
        "step": step_num,
        "before": before_path,
        "after": after_path,
        "diff_pixels": 0,
        "diff_ratio": 0.0,
        "engine": "none",
        "diff_image": "",
        "error": None,
    }
    if not before_path or not after_path or not os.path.exists(before_path) or not os.path.exists(after_path):
        result["error"] = "missing_screenshot"
        return result

    diff_image_path = os.path.join(OUTPUT_DIR, _sanitize_filename(f"visual_diff_step_{step_num:03d}.png"))
    result["diff_image"] = diff_image_path

    if pil_pixelmatch and Image:
        try:
            before_img = Image.open(before_path).convert("RGBA")
            after_img = Image.open(after_path).convert("RGBA")
            if before_img.size != after_img.size:
                after_img = after_img.resize(before_img.size)
            diff_img = Image.new("RGBA", before_img.size)
            mismatch = pil_pixelmatch(before_img, after_img, diff_img, threshold=0.1)
            total = before_img.size[0] * before_img.size[1]
            result["diff_pixels"] = int(mismatch)
            result["diff_ratio"] = float(mismatch) / float(total)
            result["engine"] = "python-pixelmatch"
            diff_img.save(diff_image_path)
            return result
        except Exception as exc:
            result["error"] = f"python_pixelmatch_failed: {exc}"

    # Subprocess fallback enables using pixelmatch when node tooling is available.
    try:
        node_script = (
            "const fs=require('fs');"
            "const {PNG}=require('pngjs');"
            "const pixelmatch=require('pixelmatch');"
            "const a=PNG.sync.read(fs.readFileSync(process.argv[1]));"
            "const b=PNG.sync.read(fs.readFileSync(process.argv[2]));"
            "const w=Math.min(a.width,b.width),h=Math.min(a.height,b.height);"
            "const out=new PNG({width:w,height:h});"
            "const m=pixelmatch(a.data,b.data,out.data,w,h,{threshold:0.1});"
            "fs.writeFileSync(process.argv[3],PNG.sync.write(out));"
            "console.log(JSON.stringify({mismatch:m,total:w*h}));"
        )
        completed = subprocess.run(
            ["node", "-e", node_script, before_path, after_path, diff_image_path],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
        data = json.loads(completed.stdout.strip())
        result["diff_pixels"] = int(data.get("mismatch", 0))
        total = int(data.get("total", 1))
        result["diff_ratio"] = float(result["diff_pixels"]) / float(max(total, 1))
        result["engine"] = "node-pixelmatch"
    except Exception as exc:
        result["error"] = f"node_pixelmatch_failed: {exc}"
    return result


async def decide_next_action(page_state: str) -> dict:
    prompt = build_decision_prompt(page_state)

    for _ in range(2):
        try:
            response = ollama.chat(
                model=OLLAMA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                format="json",
                options=OLLAMA_DECISION_OPTIONS,
            )
            content = response["message"]["content"]
            parsed = parse_action_plan_response(content)
            if parsed is not None:
                return parsed
        except Exception:
            continue

    return normalize_action_plan({"action": "scroll", "target": "", "value": ""})


def build_decision_prompt(page_state: str) -> str:
    return f"""
You are an Advanced Monkey Testing Agent. Your goal is to deeply test the app by filling forms, submitting data, and handling modals.

Current Page State:
{page_state}

Choose ONE action from this list:
1. "click": Click a button or link.
2. "type": Type random text into an input field.
3. "submit_form": Find a form and submit it (trigger a 'submit' button or press Enter).
4. "handle_modal": If a modal/dialog is detected, try to close it (click 'X', 'Cancel', 'Close') or accept it.
5. "scroll": Scroll the page.

Rules:
- If you see a <FORM>, prioritize "submit_form" or "type" inside it.
- If you see a <MODAL>, prioritize "handle_modal".
- For "type", generate a random string like "test_123".
- Each element line starts with [id=N]. Use that numeric id for target selection.
- For actions that need a target, return "target" as [id=N] (example: [id=3]).
- Never return raw text labels as target.

Respond ONLY with JSON: {{"action": "...", "target": "...", "value": "..."}}
"""


def parse_action_plan_response(raw_content: Any) -> Optional[Dict[str, str]]:
    if not isinstance(raw_content, str):
        return None

    content = raw_content.replace("```json", "").replace("```", "").strip()
    if not content:
        return None

    try:
        parsed = json.loads(content)
    except Exception:
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except Exception:
            return None

    normalized = normalize_action_plan(parsed)
    action = normalized.get("action", "scroll")
    target = normalized.get("target", "")
    if action in {"click", "type"} and _extract_target_id(target) is None:
        return None

    return normalized


def apply_state_aware_policy(
    action_plan: Dict[str, Any],
    snapshot: PageSnapshot,
    state_counts: Dict[str, int],
    seen_click_targets: set,
) -> Dict[str, Any]:
    # State memory key uses URL + structure hash to identify semantic revisits.
    state_key = f"{snapshot.url}::{snapshot.structure_hash}"
    revisit_count = state_counts.get(state_key, 0)
    if revisit_count > STATE_LOOP_THRESHOLD:
        # Avoid forcing back into the browser's initial about:blank entry.
        forced = random.choice(["random_jump", "restart_target"])
        return {"action": forced, "target": "", "value": ""}

    action = action_plan.get("action", "scroll")
    if action == "click" and action_plan.get("target") in seen_click_targets:
        # Encourage exploration by choosing unseen clickable targets if possible.
        clickable = [x for x in snapshot.elements if "<BUTTON" in x or "<A" in x]
        unseen = [x for x in clickable if x not in seen_click_targets]
        if unseen:
            pick = random.choice(unseen)
            id_match = re.search(r'\[id=(\d+)\]', pick)
            if id_match:
                action_plan["target"] = f"[id={id_match.group(1)}]"
    return action_plan

async def execute_action(
    page: Page,
    action_plan: Dict[str, Any],
    step_num: int,
    fuzzer: Fuzzer,
    defects: DefectTracker,
    network_monitor: NetworkMonitor,
    perf_monitor: PerformanceMonitor,
) -> Tuple[Optional[PageSnapshot], Dict[str, Any]]:
    action = action_plan.get("action", "scroll")
    target = action_plan.get("target", "")
    value = action_plan.get("value", "")

    before_snapshot = await get_page_state(page, step_num, phase="before")
    perf_before = await perf_monitor.snapshot(page)
    
    log_entry = {
        "step": step_num, "action": action, "target": target,
        "value": value if action == "type" else None,
        "status": "SUCCESS", "error": None, "screenshot": None, "url": page.url
    }

    print(f"🤖 Step {step_num}: Executing {action} on '{target}'")

    try:
        if action == "scroll":
            await page.evaluate(f"window.scrollBy(0, {random.choice([-500, 500])})")

        elif action == "back":
            history_length = await page.evaluate("() => window.history.length")
            if page.url == "about:blank" or history_length <= 2:
                await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=45000)
                await wait_for_page_ready(page, "back-recovery")
            else:
                previous_page = await page.go_back(timeout=5000)
                if page.url == "about:blank" or previous_page is None:
                    await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=45000)
                    await wait_for_page_ready(page, "back-recovery")

        elif action == "restart_target":
            await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=45000)
            await wait_for_page_ready(page, "restart-target")

        elif action == "random_jump":
            links = page.locator("a[href]:visible")
            if await links.count() > 0:
                idx = random.randint(0, min(await links.count() - 1, 10))
                await links.nth(idx).click(timeout=3000)
            else:
                await page.evaluate("window.scrollTo(0, 0)")
            
        elif action == "handle_modal":
            # Strategy A: Close button
            close_btn = page.locator("button[aria-label='Close'], .close, [title='Close']").first
            if await close_btn.count() > 0:
                await close_btn.click(timeout=2000)
            else:
                # Strategy B: Cancel/No button
                cancel_btn = page.get_by_role("button", name=re.compile("cancel|close|no|dismiss", re.I)).first
                if await cancel_btn.count() > 0:
                    await cancel_btn.click(timeout=2000)
                else:
                    # Strategy C: Press Escape
                    await page.keyboard.press("Escape")
                    print("   -> Sent Escape key to close modal")
                    
        elif action == "submit_form":
            # Find any visible form
            form = page.locator("form:visible").first
            if await form.count() > 0:
                # Try to find a submit button inside
                submit_btn = form.locator("button[type='submit'], input[type='submit']").first
                if await submit_btn.count() > 0:
                    await submit_btn.click(timeout=3000)
                else:
                    # No submit button? Press Enter on the last input
                    inputs = form.locator("input:visible, textarea:visible")
                    if await inputs.count() > 0:
                        await inputs.last.press("Enter")
                    else:
                        raise Exception("Form found but no inputs or submit button")
            else:
                raise Exception("No visible form found to submit")

        elif action == "click":
            locator = await _locator_for_target_id(page, target)
            
            if locator:
                await locator.click(timeout=3000)
            else:
                raise Exception(f"Element '{target}' not found")
                
        elif action == "type":
            locator = await _locator_for_target_id(page, target)
            if locator:
                tag_name = (await locator.evaluate("el => el.tagName.toLowerCase()"))
                if tag_name not in {"input", "textarea"}:
                    locator = None
            if locator is None:
                # Fallback: find any visible input
                locator = page.locator("input:visible, textarea:visible").first
            
            if await locator.count() > 0:
                payload = value or fuzzer.next_payload()
                await locator.fill(payload)
                log_entry["value"] = payload[:120]

                # Heuristic logging for high-risk payload paths.
                if any(marker in payload.lower() for marker in ["<script", "onerror", " or 1=1", "drop table"]):
                    defects.add(
                        "security_risks",
                        {
                            "step": step_num,
                            "type": "fuzz-payload-injected",
                            "target": target,
                            "payload_preview": payload[:200],
                            "url": page.url,
                        },
                    )
            else:
                raise Exception(f"Input '{target}' not found")

        try:
            await page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
        log_entry["url"] = page.url

        after_snapshot = await get_page_state(page, step_num, phase="after")
        perf_after = await perf_monitor.snapshot(page)

        # Layout instability detection: major shifts without URL navigation can indicate fragile UI.
        max_shift = compute_max_layout_shift(before_snapshot, after_snapshot)
        if before_snapshot.url == after_snapshot.url and max_shift > LAYOUT_SHIFT_THRESHOLD_PX:
            finding = {
                "step": step_num,
                "type": "layout-instability",
                "max_shift_px": max_shift,
                "url": after_snapshot.url,
                "before_hash": before_snapshot.structure_hash,
                "after_hash": after_snapshot.structure_hash,
            }
            defects.add("layout_instability", finding)

        # Unexpected structure collapse (e.g., containers disappearing) without route change.
        if (
            before_snapshot.url == after_snapshot.url
            and len(after_snapshot.elements) < max(1, int(len(before_snapshot.elements) * 0.5))
        ):
            defects.add(
                "layout_instability",
                {
                    "step": step_num,
                    "type": "dom-collapse",
                    "before_elements": len(before_snapshot.elements),
                    "after_elements": len(after_snapshot.elements),
                    "url": after_snapshot.url,
                },
            )

        visual_diff = compare_screenshots_pixelmatch(
            before_snapshot.screenshot_path,
            after_snapshot.screenshot_path,
            step_num,
        )
        if visual_diff.get("diff_ratio", 0.0) > VISUAL_DIFF_THRESHOLD_RATIO and before_snapshot.url == after_snapshot.url:
            defects.add(
                "visual_regressions",
                {
                    "step": step_num,
                    "type": "visual-diff",
                    "diff_ratio": visual_diff.get("diff_ratio"),
                    "diff_pixels": visual_diff.get("diff_pixels"),
                    "engine": visual_diff.get("engine"),
                    "diff_image": os.path.basename(visual_diff.get("diff_image", "")),
                    "url": after_snapshot.url,
                },
            )

        perf_findings = await perf_monitor.detect_bottlenecks(
            perf_before,
            perf_after,
            step_num,
            action,
            after_snapshot.url,
        )
        log_entry["performance_findings"] = len(perf_findings)

        zombie = await network_monitor.detect_zombie_ui(page, step_num)
        if zombie:
            log_entry["zombie_ui"] = zombie["type"]

        log_entry["before_dom_hash"] = before_snapshot.dom_hash
        log_entry["after_dom_hash"] = after_snapshot.dom_hash
        log_entry["visual_diff_ratio"] = visual_diff.get("diff_ratio", 0.0)
        log_entry["screenshot"] = os.path.basename(after_snapshot.screenshot_path)

    except Exception as e:
        error_msg = str(e)
        log_entry["status"] = "FAILED"
        log_entry["error"] = error_msg
        print(f"💥 Error: {error_msg}")
        
        screenshot_name = f"error_step_{step_num}.png"
        try:
            await page.screenshot(path=os.path.join(OUTPUT_DIR, screenshot_name))
            log_entry["screenshot"] = screenshot_name
        except:
            pass

    test_logs.append(log_entry)
    try:
        return await get_page_state(page, step_num, phase="final"), log_entry
    except Exception:
        return None, log_entry

# 🚨 Global Dialog Handler for Native Alerts
async def handle_dialog(dialog: Dialog):
    print(f"   -> 🚨 Native Dialog Detected: {dialog.message}")
    # Randomly accept or dismiss to test both paths
    if random.random() > 0.5:
        await dialog.accept()
        print("   -> Accepted dialog")
    else:
        await dialog.dismiss()
        print("   -> Dismissed dialog")

def generate_markdown_report(start_time, end_time):
    duration_seconds = (end_time - start_time).total_seconds()
    total_steps = len(test_logs)
    failed_steps = [log for log in test_logs if log["status"] in ["FAILED", "CRASH"]]
    success_rate = ((total_steps - len(failed_steps)) / total_steps * 100) if total_steps > 0 else 0

    md_content = f"""# Deep Inspection Monkey Test Report

**Target URL:** {TARGET_URL}  
**Date:** {start_time.strftime('%Y-%m-%d %H:%M:%S')}  
**Duration:** {duration_seconds:.2f} seconds  
**Total Steps:** {total_steps}  
**Success Rate:** {success_rate:.2f}%  
**Errors Found:** {len(failed_steps)}  
**Sandbox Policy:** {"strict" if STRICT_SANDBOX else "sandbox-first"}  
**No-Sandbox Fallback:** {"enabled" if ALLOW_NO_SANDBOX_FALLBACK else "disabled"}  
**Browser Launch Mode:** {BROWSER_LAUNCH_INFO.get('mode', 'unknown')}  
**Output Folder:** `{OUTPUT_DIR}`

## Summary
The agent performed {total_steps} actions using **{OLLAMA_MODEL}**.
Actions included: Clicking, Typing, Form Submission, Modal Handling, and State Escapes.
"""

    if failed_steps:
        md_content += "\n## Errors Detected\n"
        for log in failed_steps:
            md_content += f"\n### Step {log['step']}: {log['action']} failed\n"
            md_content += f"- **Target:** `{log['target']}`\n"
            md_content += f"- **Error:** `{log['error']}`\n"
            if log['screenshot']:
                md_content += f"- **Screenshot:** `![Screenshot](./{log['screenshot']})`\n"

    md_content += "\n## Security Risks\n"
    if DEFECTS.security_risks:
        for item in DEFECTS.security_risks:
            md_content += f"- Step {item['step']}: {item['type']} on `{item.get('target', '')}` at {item['url']}\n"
    else:
        md_content += "- None detected.\n"

    md_content += "\n## Accessibility Violations\n"
    if DEFECTS.accessibility_violations:
        for item in DEFECTS.accessibility_violations:
            md_content += f"- Step {item['step']}: [{item['severity']}] {item.get('id')} ({item.get('nodes', 0)} nodes)\n"
    else:
        md_content += "- None detected.\n"

    md_content += "\n## Performance Bottlenecks\n"
    if DEFECTS.performance_bottlenecks:
        for item in DEFECTS.performance_bottlenecks:
            md_content += f"- Step {item['step']}: {item['type']} ({item.get('duration_ms', item.get('heap_delta_bytes', item.get('fps')) )})\n"
    else:
        md_content += "- None detected.\n"

    md_content += "\n## Visual Regressions\n"
    visual_items = DEFECTS.visual_regressions + DEFECTS.layout_instability
    if visual_items:
        for item in visual_items:
            md_content += f"- Step {item['step']}: {item['type']} on {item['url']}\n"
    else:
        md_content += "- None detected.\n"

    md_content += "\n## Action Log\n\n| Step | Action | Target | Status |\n|---|---|---|---|\n"
    for log in test_logs:
        icon = "✅" if log["status"] == "SUCCESS" else "❌"
        md_content += f"| {log['step']} | {log['action']} | {log['target'][:30]}... | {icon} |\n"

    report_path = os.path.join(OUTPUT_DIR, "test_report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(md_content)
    
    print(f"\n📄 Report generated: {report_path}")
    print(f"💾 All artifacts saved in: {OUTPUT_DIR}")


def generate_json_summary(start_time: datetime, end_time: datetime) -> None:
    summary = {
        "target_url": TARGET_URL,
        "model": OLLAMA_MODEL,
        "active_seed": ACTIVE_SEED,
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "duration_seconds": (end_time - start_time).total_seconds(),
        "steps": len(test_logs),
        "failed_steps": len([log for log in test_logs if log["status"] != "SUCCESS"]),
        "browser_launch": BROWSER_LAUNCH_INFO,
        "defects": {
            "security_risks": DEFECTS.security_risks,
            "accessibility_violations": DEFECTS.accessibility_violations,
            "performance_bottlenecks": DEFECTS.performance_bottlenecks,
            "visual_regressions": DEFECTS.visual_regressions,
            "layout_instability": DEFECTS.layout_instability,
            "race_findings": DEFECTS.race_findings,
            "console_findings": DEFECTS.console_findings,
            "boundary_drift": DEFECTS.boundary_drift,
        },
        "network_injections": NETWORK_MONITOR.injected_events,
        "logs": test_logs,
    }
    output_path = os.path.join(OUTPUT_DIR, "results.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(f"📦 JSON summary generated: {output_path}")


DEFECTS = DefectTracker()
FUZZER = Fuzzer()
NETWORK_MONITOR = NetworkMonitor(DEFECTS)
A11Y_CHECKER = A11yChecker(DEFECTS)
PERF_MONITOR = PerformanceMonitor(DEFECTS)

async def main():
    start_time = datetime.now()
    
    async with async_playwright() as p:
        context = await launch_context_with_fallback(p)
        
        page = context.pages[0]
        page.on("dialog", handle_dialog)

        def _console_listener(msg) -> None:
            text = msg.text
            if "content security policy" in text.lower() or "csp" in text.lower():
                DEFECTS.add(
                    "console_findings",
                    {
                        "step": -1,
                        "type": "csp-warning",
                        "message": text,
                        "url": page.url,
                    },
                )

        print(f"🚀 Starting Advanced Monkey Test on {TARGET_URL}...")
        await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=45000)
        await wait_for_page_ready(page, "initial-navigation")

        page.on("console", _console_listener)
        await NETWORK_MONITOR.install(page)
        await PERF_MONITOR.install(page)

        visited_states: Dict[str, int] = {}
        seen_click_targets: set = set()
        
        for step in range(1, MAX_STEPS + 1):
            print(f"\n--- Step {step}/{MAX_STEPS} ---")
            
            try:
                snapshot = await get_page_state(page, step, phase="plan")
                state_key = f"{snapshot.url}::{snapshot.structure_hash}"
                visited_states[state_key] = visited_states.get(state_key, 0) + 1
                state = state_to_prompt(snapshot)
            except Exception as e:
                print(f"   -> 🚨 Failed to get state: {e}. Skipping step.")
                continue

            plan = await decide_next_action(state)
            plan = apply_state_aware_policy(plan, snapshot, visited_states, seen_click_targets)
            if plan.get("action") == "click" and plan.get("target"):
                seen_click_targets.add(plan.get("target"))

            _, log_entry = await execute_action(
                page,
                plan,
                step,
                FUZZER,
                DEFECTS,
                NETWORK_MONITOR,
                PERF_MONITOR,
            )

            if step % 5 == 0:
                violations = await A11Y_CHECKER.scan(page, step)
                if violations:
                    print(f"   -> ♿ A11y findings at step {step}: {len(violations)} serious/critical")
            
            await wait_for_page_ready(page, f"post-step-{step}")

            current_url = page.url
            if not is_in_scope(current_url, TARGET_URL):
                DEFECTS.add(
                    "boundary_drift",
                    {
                        "step": step,
                        "type": "Boundary Drift",
                        "current_url": current_url,
                        "target_url": TARGET_URL,
                    },
                )
                await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=45000)
                await wait_for_page_ready(page, f"boundary-recovery-{step}")

            # Detect potential reflected input issues by checking if payload echoes unsafely in markup.
            if log_entry.get("value"):
                payload_probe = log_entry["value"]
                try:
                    body_html = await page.content()
                    if payload_probe in body_html and "<" in payload_probe:
                        DEFECTS.add(
                            "security_risks",
                            {
                                "step": step,
                                "type": "possible-reflected-input",
                                "payload_preview": payload_probe[:200],
                                "url": page.url,
                            },
                        )
                except Exception:
                    pass
            
            await asyncio.sleep(ACTION_COOLDOWN_SECONDS)

        await context.close()

    end_time = datetime.now()
    generate_markdown_report(start_time, end_time)
    generate_json_summary(start_time, end_time)

if __name__ == "__main__":
    cli_args = parse_cli_args()
    apply_runtime_overrides(cli_args)
    asyncio.run(main())