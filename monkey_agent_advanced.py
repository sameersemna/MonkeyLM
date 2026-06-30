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
asyncpg = _optional_import("asyncpg")
redis_asyncio = _optional_import("redis.asyncio")
httpx = _optional_import("httpx")

# CONFIGURATION
DEFAULT_TARGET_URL = "https://noblequran-85hu2yge.manus.space/"
DEFAULT_OLLAMA_MODEL = "minimax-m3:cloud"
DEFAULT_MAX_STEPS = 100
DEFAULT_HEADLESS = True
DEFAULT_WINDOW_SIZE = "1920,1080"
DEFAULT_NO_VIEWPORT = True
DEFAULT_POSTGRES_DSN = "postgresql://localhost:5432/monkeylm"
DEFAULT_REDIS_URL = "redis://localhost:6379/0"
DEFAULT_GOLDEN_BASELINE_MODE = "preexisting"
DEFAULT_STRICT_PERSISTENCE = False
DEFAULT_REDIS_STATE_TTL_SECONDS = 86400
DEFAULT_QDRANT_URL = "http://127.0.0.1:6333"
DEFAULT_QDRANT_COLLECTION = "monkeylm_semantic_memory"
DEFAULT_QDRANT_VECTOR_SIZE = 256
DEFAULT_QDRANT_ENABLE_READS = True
DEFAULT_QDRANT_ENABLE_WRITES = True
DEFAULT_QDRANT_EMBEDDING_PROVIDER = "hash"
DEFAULT_QDRANT_EMBEDDING_MODEL = "nomic-embed-text"
DEFAULT_QDRANT_RERANK_ENABLED = False
DEFAULT_QDRANT_RERANK_MODEL = "qwen2.5:3b"
DEFAULT_QDRANT_CANDIDATE_LIMIT = 20

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


def _env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None:
        return default
    stripped = value.strip()
    return stripped if stripped else default


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
POSTGRES_DSN = _env_str("POSTGRES_DSN", DEFAULT_POSTGRES_DSN)
REDIS_URL = _env_str("REDIS_URL", DEFAULT_REDIS_URL)
GOLDEN_BASELINE_MODE = _env_str("GOLDEN_BASELINE_MODE", DEFAULT_GOLDEN_BASELINE_MODE).lower()
STRICT_PERSISTENCE = _env_bool("STRICT_PERSISTENCE", default=DEFAULT_STRICT_PERSISTENCE)
REDIS_STATE_TTL_SECONDS = max(60, _env_int("REDIS_STATE_TTL_SECONDS", DEFAULT_REDIS_STATE_TTL_SECONDS))
QDRANT_URL = _env_str("QDRANT_URL", DEFAULT_QDRANT_URL)
QDRANT_COLLECTION = _env_str("QDRANT_COLLECTION", DEFAULT_QDRANT_COLLECTION)
QDRANT_VECTOR_SIZE = max(64, _env_int("QDRANT_VECTOR_SIZE", DEFAULT_QDRANT_VECTOR_SIZE))
QDRANT_ENABLE_READS = _env_bool("QDRANT_ENABLE_READS", default=DEFAULT_QDRANT_ENABLE_READS)
QDRANT_ENABLE_WRITES = _env_bool("QDRANT_ENABLE_WRITES", default=DEFAULT_QDRANT_ENABLE_WRITES)
QDRANT_EMBEDDING_PROVIDER = _env_str("QDRANT_EMBEDDING_PROVIDER", DEFAULT_QDRANT_EMBEDDING_PROVIDER).lower()
QDRANT_EMBEDDING_MODEL = _env_str("QDRANT_EMBEDDING_MODEL", DEFAULT_QDRANT_EMBEDDING_MODEL)
QDRANT_ADMIN_ACTION = _env_str("QDRANT_ADMIN_ACTION", "").lower()
QDRANT_RERANK_ENABLED = _env_bool("QDRANT_RERANK_ENABLED", default=DEFAULT_QDRANT_RERANK_ENABLED)
QDRANT_RERANK_MODEL = _env_str("QDRANT_RERANK_MODEL", DEFAULT_QDRANT_RERANK_MODEL)
QDRANT_CANDIDATE_LIMIT = max(3, _env_int("QDRANT_CANDIDATE_LIMIT", DEFAULT_QDRANT_CANDIDATE_LIMIT))


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
    parser.add_argument("--postgres-dsn", help="PostgreSQL connection string")
    parser.add_argument("--redis-url", help="Redis connection URL")
    parser.add_argument(
        "--golden-baseline-mode",
        choices=["preexisting", "auto_upsert"],
        help="Golden baseline strategy: compare only preexisting goldens or auto-seed when missing",
    )
    parser.add_argument("--qdrant-url", help="Qdrant base URL, for example http://127.0.0.1:6333")
    parser.add_argument("--qdrant-collection", help="Qdrant collection name for semantic memory logs")
    parser.add_argument(
        "--qdrant-embedding-provider",
        choices=["hash", "ollama"],
        help="Embedding backend for Qdrant vectors",
    )
    parser.add_argument(
        "--qdrant-embedding-model",
        help="Local Ollama embedding model name, e.g. nomic-embed-text",
    )
    parser.add_argument(
        "--qdrant-rerank-model",
        help="Local Ollama model for reranking retrieved memories",
    )
    parser.add_argument(
        "--qdrant-candidate-limit",
        type=int,
        help="Number of candidates to fetch from Qdrant before reranking",
    )

    qdrant_rerank_group = parser.add_mutually_exclusive_group()
    qdrant_rerank_group.add_argument(
        "--qdrant-enable-rerank",
        action="store_true",
        help="Enable second-stage reranking of Qdrant memory candidates",
    )
    qdrant_rerank_group.add_argument(
        "--qdrant-disable-rerank",
        action="store_true",
        help="Disable reranking and use raw vector ranking only",
    )

    qdrant_rw_group = parser.add_mutually_exclusive_group()
    qdrant_rw_group.add_argument(
        "--qdrant-read-only",
        action="store_true",
        help="Enable Qdrant reads but disable writes",
    )
    qdrant_rw_group.add_argument(
        "--qdrant-disable-writes",
        action="store_true",
        help="Disable writing step memories to Qdrant",
    )
    parser.add_argument(
        "--qdrant-disable-reads",
        action="store_true",
        help="Disable semantic search reads from Qdrant",
    )

    qdrant_admin_group = parser.add_mutually_exclusive_group()
    qdrant_admin_group.add_argument(
        "--qdrant-inspect",
        action="store_true",
        help="Inspect Qdrant collection status and exit",
    )
    qdrant_admin_group.add_argument(
        "--qdrant-clear",
        action="store_true",
        help="Delete and recreate Qdrant collection, then exit",
    )

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

    persistence_group = parser.add_mutually_exclusive_group()
    persistence_group.add_argument(
        "--strict-persistence",
        dest="strict_persistence",
        action="store_true",
        help="Fail startup if PostgreSQL or Redis is unavailable",
    )
    persistence_group.add_argument(
        "--lenient-persistence",
        dest="strict_persistence",
        action="store_false",
        help="Continue run if PostgreSQL or Redis is unavailable",
    )
    parser.set_defaults(strict_persistence=None)

    return parser.parse_args()


def apply_runtime_overrides(args: argparse.Namespace) -> None:
    global TARGET_URL, OLLAMA_MODEL, MAX_STEPS, HEADLESS, BROWSER_WINDOW_SIZE, NO_VIEWPORT, ACTIVE_SEED
    global POSTGRES_DSN, REDIS_URL, GOLDEN_BASELINE_MODE, STRICT_PERSISTENCE
    global QDRANT_URL, QDRANT_COLLECTION, QDRANT_ENABLE_READS, QDRANT_ENABLE_WRITES
    global QDRANT_EMBEDDING_PROVIDER, QDRANT_EMBEDDING_MODEL, QDRANT_ADMIN_ACTION
    global QDRANT_RERANK_ENABLED, QDRANT_RERANK_MODEL, QDRANT_CANDIDATE_LIMIT

    if getattr(args, "target_url", None):
        TARGET_URL = args.target_url
    if getattr(args, "ollama_model", None):
        OLLAMA_MODEL = args.ollama_model
    if getattr(args, "max_steps", None) is not None:
        MAX_STEPS = max(1, args.max_steps)
    if getattr(args, "headless", None) is not None:
        HEADLESS = bool(args.headless)
    if getattr(args, "window_size", None):
        BROWSER_WINDOW_SIZE = _normalize_window_size(args.window_size, fallback=BROWSER_WINDOW_SIZE)
    if getattr(args, "no_viewport", None) is not None:
        NO_VIEWPORT = bool(args.no_viewport)
    if getattr(args, "seed", None) is not None:
        random.seed(args.seed)
        ACTIVE_SEED = str(args.seed)
    if getattr(args, "postgres_dsn", None):
        POSTGRES_DSN = args.postgres_dsn.strip()
    if getattr(args, "redis_url", None):
        REDIS_URL = args.redis_url.strip()
    if getattr(args, "golden_baseline_mode", None):
        GOLDEN_BASELINE_MODE = args.golden_baseline_mode.strip().lower()
    if getattr(args, "strict_persistence", None) is not None:
        STRICT_PERSISTENCE = bool(args.strict_persistence)
    if getattr(args, "qdrant_url", None):
        QDRANT_URL = args.qdrant_url.strip().rstrip("/")
    if getattr(args, "qdrant_collection", None):
        QDRANT_COLLECTION = args.qdrant_collection.strip()
    if getattr(args, "qdrant_embedding_provider", None):
        QDRANT_EMBEDDING_PROVIDER = args.qdrant_embedding_provider.strip().lower()
    if getattr(args, "qdrant_embedding_model", None):
        QDRANT_EMBEDDING_MODEL = args.qdrant_embedding_model.strip()
    if getattr(args, "qdrant_rerank_model", None):
        QDRANT_RERANK_MODEL = args.qdrant_rerank_model.strip()
    if getattr(args, "qdrant_candidate_limit", None) is not None:
        QDRANT_CANDIDATE_LIMIT = max(3, int(args.qdrant_candidate_limit))
    if getattr(args, "qdrant_disable_reads", False):
        QDRANT_ENABLE_READS = False
    if getattr(args, "qdrant_disable_writes", False):
        QDRANT_ENABLE_WRITES = False
    if getattr(args, "qdrant_enable_rerank", False):
        QDRANT_RERANK_ENABLED = True
    if getattr(args, "qdrant_disable_rerank", False):
        QDRANT_RERANK_ENABLED = False
    if getattr(args, "qdrant_read_only", False):
        QDRANT_ENABLE_READS = True
        QDRANT_ENABLE_WRITES = False
    if getattr(args, "qdrant_inspect", False):
        QDRANT_ADMIN_ACTION = "inspect"
    if getattr(args, "qdrant_clear", False):
        QDRANT_ADMIN_ACTION = "clear"


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
        self.regression_findings: List[Dict[str, Any]] = []
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


def _local_service_log(message: str) -> None:
    timestamp = datetime.now().isoformat(timespec="seconds")
    log_line = f"[{timestamp}] {message}"
    print(f"⚠️ {log_line}")
    try:
        log_path = os.path.join(OUTPUT_DIR, "service_connectivity.log")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(log_line + "\n")
    except Exception:
        pass


def split_domain_and_route(url: str) -> Tuple[str, str]:
    try:
        parsed = urlparse(url)
    except Exception:
        return "", "/"

    domain = (parsed.netloc or "").lower().strip()
    route = parsed.path or "/"
    if parsed.query:
        route = f"{route}?{parsed.query}"
    return domain, route


def _normalize_manifest_text(value: Any, max_len: int = 120) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).strip())[:max_len]


def _manifest_component_key(component: Dict[str, Any]) -> str:
    return "::".join(
        [
            _normalize_manifest_text(component.get("kind", "")).lower(),
            _normalize_manifest_text(component.get("tag", "")).lower(),
            _normalize_manifest_text(component.get("text", "")).lower(),
            _normalize_manifest_text(component.get("selector_hint", "")).lower(),
        ]
    )


def diff_component_manifests(
    golden_manifest: List[Dict[str, Any]],
    current_manifest: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    current_keys = {_manifest_component_key(component) for component in current_manifest}
    missing_components: List[Dict[str, Any]] = []
    for component in golden_manifest:
        if _manifest_component_key(component) not in current_keys:
            missing_components.append(component)

    broken_selectors = sorted(
        {
            _normalize_manifest_text(component.get("selector_hint", ""))
            for component in missing_components
            if _normalize_manifest_text(component.get("selector_hint", ""))
        }
    )
    return missing_components, broken_selectors


async def extract_component_manifest(page: Page) -> List[Dict[str, Any]]:
    try:
        manifest = await page.evaluate(
            """() => {
                const normalizeText = (value) => {
                    const text = String(value || '').replace(/\\s+/g, ' ').trim();
                    return text.slice(0, 120);
                };

                const selectorHint = (el) => {
                    if (!el) return '';
                    if (el.id) return `#${el.id}`;
                    const dataTestId = el.getAttribute('data-testid') || el.getAttribute('data-test-id');
                    if (dataTestId) return `[data-testid="${dataTestId}"]`;
                    const name = el.getAttribute('name');
                    if (name) return `${el.tagName.toLowerCase()}[name="${name}"]`;
                    const classes = (el.className && typeof el.className === 'string')
                        ? el.className.trim().split(/\\s+/).slice(0, 2).join('.')
                        : '';
                    return classes ? `${el.tagName.toLowerCase()}.${classes}` : el.tagName.toLowerCase();
                };

                const isVisible = (el) => {
                    if (!el) return false;
                    const rect = el.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                };

                const result = [];
                const pushComponent = (kind, el, textValue) => {
                    if (!isVisible(el)) return;
                    result.push({
                        kind,
                        tag: el.tagName,
                        text: normalizeText(textValue),
                        selector_hint: normalizeText(selectorHint(el)),
                    });
                };

                const buttonLike = document.querySelectorAll('button, [role="button"], input[type="button"], input[type="submit"], a');
                buttonLike.forEach((el) => {
                    const text = el.innerText || el.getAttribute('aria-label') || el.getAttribute('value') || '';
                    pushComponent('button', el, text);
                });

                const forms = document.querySelectorAll('form');
                forms.forEach((el) => {
                    const text = el.getAttribute('name') || el.getAttribute('id') || '';
                    pushComponent('form', el, text);
                });

                const textNodes = document.querySelectorAll('h1, h2, h3, h4, h5, h6, p, label, li, span');
                textNodes.forEach((el) => {
                    const text = normalizeText(el.innerText || el.textContent || '');
                    if (text.length < 2) return;
                    pushComponent('text', el, text);
                });

                return result.slice(0, 1500);
            }"""
        )
    except Exception as exc:
        _local_service_log(f"Failed to extract component manifest: {exc}")
        return []

    if isinstance(manifest, list):
        sanitized: List[Dict[str, Any]] = []
        for item in manifest:
            if not isinstance(item, dict):
                continue
            sanitized.append(
                {
                    "kind": _normalize_manifest_text(item.get("kind", ""), max_len=30),
                    "tag": _normalize_manifest_text(item.get("tag", ""), max_len=30),
                    "text": _normalize_manifest_text(item.get("text", ""), max_len=120),
                    "selector_hint": _normalize_manifest_text(item.get("selector_hint", ""), max_len=160),
                }
            )
        return sanitized
    return []


class PersistenceEngine:
    def __init__(self, defects: DefectTracker) -> None:
        self.defects = defects
        self.pg_pool = None
        self.redis_client = None

    async def initialize(self) -> None:
        await self._initialize_postgres()
        await self._initialize_redis()

        if STRICT_PERSISTENCE:
            missing: List[str] = []
            if self.pg_pool is None:
                missing.append("PostgreSQL")
            if self.redis_client is None:
                missing.append("Redis")
            if missing:
                raise RuntimeError(
                    "Persistence strict mode requires services to be reachable. Missing: " + ", ".join(missing)
                )

    async def _initialize_postgres(self) -> None:
        if asyncpg is None:
            _local_service_log("asyncpg not installed; PostgreSQL persistence disabled.")
            return

        try:
            self.pg_pool = await asyncpg.create_pool(
                POSTGRES_DSN,
                min_size=1,
                max_size=4,
                command_timeout=30,
            )
            async with self.pg_pool.acquire() as conn:
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS app_baselines (
                        id BIGSERIAL PRIMARY KEY,
                        domain TEXT NOT NULL,
                        page_route TEXT NOT NULL,
                        dom_structure_hash TEXT NOT NULL,
                        component_manifest JSONB NOT NULL,
                        is_golden_standard BOOLEAN NOT NULL DEFAULT FALSE,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        UNIQUE(domain, page_route, dom_structure_hash, is_golden_standard)
                    )
                    """
                )
                await conn.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_app_baselines_one_golden_per_route
                    ON app_baselines(domain, page_route)
                    WHERE is_golden_standard = TRUE
                    """
                )
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS regression_drift_log (
                        id BIGSERIAL PRIMARY KEY,
                        domain TEXT NOT NULL,
                        page_route TEXT NOT NULL,
                        defect_tag TEXT NOT NULL,
                        severity TEXT NOT NULL,
                        missing_components JSONB NOT NULL DEFAULT '[]'::jsonb,
                        broken_selectors JSONB NOT NULL DEFAULT '[]'::jsonb,
                        drift_alert JSONB NOT NULL DEFAULT '{}'::jsonb,
                        step_number INTEGER NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                await conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_regression_drift_log_route_time
                    ON regression_drift_log(domain, page_route, created_at DESC)
                    """
                )
            print("✅ PostgreSQL baseline tables are ready.")
        except Exception as exc:
            _local_service_log(f"PostgreSQL initialization failed: {exc}")
            if self.pg_pool is not None:
                try:
                    await self.pg_pool.close()
                except Exception:
                    pass
            self.pg_pool = None

    async def _initialize_redis(self) -> None:
        if redis_asyncio is None:
            _local_service_log("redis package not installed; Redis state cache disabled.")
            return

        try:
            self.redis_client = redis_asyncio.from_url(REDIS_URL, decode_responses=True)
            await self.redis_client.ping()
            print("✅ Redis state cache is ready.")
        except Exception as exc:
            _local_service_log(f"Redis initialization failed: {exc}")
            self.redis_client = None

    async def close(self) -> None:
        if self.pg_pool is not None:
            try:
                await self.pg_pool.close()
            except Exception as exc:
                _local_service_log(f"Failed to close PostgreSQL pool cleanly: {exc}")
            self.pg_pool = None

        if self.redis_client is not None:
            try:
                await self.redis_client.close()
            except Exception as exc:
                _local_service_log(f"Failed to close Redis client cleanly: {exc}")
            self.redis_client = None

    async def increment_visited_state(self, state_key: str) -> Optional[int]:
        if self.redis_client is None:
            return None

        redis_key = f"monkeylm:visited_states:{TIMESTAMP}"
        try:
            count = await self.redis_client.hincrby(redis_key, state_key, 1)
            await self.redis_client.expire(redis_key, REDIS_STATE_TTL_SECONDS)
            return int(count)
        except Exception as exc:
            _local_service_log(f"Redis visited-state update failed: {exc}")
            return None

    async def _fetch_golden_baseline(self, domain: str, page_route: str) -> Optional[Dict[str, Any]]:
        if self.pg_pool is None:
            return None

        try:
            async with self.pg_pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT dom_structure_hash, component_manifest
                    FROM app_baselines
                    WHERE domain = $1 AND page_route = $2 AND is_golden_standard = TRUE
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    domain,
                    page_route,
                )
            if row is None:
                return None

            manifest_value = row["component_manifest"]
            if isinstance(manifest_value, str):
                try:
                    manifest_value = json.loads(manifest_value)
                except Exception:
                    manifest_value = []

            return {
                "dom_structure_hash": row["dom_structure_hash"],
                "component_manifest": manifest_value if isinstance(manifest_value, list) else [],
            }
        except Exception as exc:
            _local_service_log(f"Failed to fetch golden baseline: {exc}")
            return None

    async def _upsert_baseline(
        self,
        domain: str,
        page_route: str,
        dom_structure_hash: str,
        component_manifest: List[Dict[str, Any]],
        is_golden_standard: bool,
    ) -> None:
        if self.pg_pool is None:
            return

        try:
            manifest_json = json.dumps(component_manifest)
            async with self.pg_pool.acquire() as conn:
                if is_golden_standard:
                    async with conn.transaction():
                        await conn.execute(
                            """
                            DELETE FROM app_baselines
                            WHERE domain = $1 AND page_route = $2 AND is_golden_standard = TRUE
                            """,
                            domain,
                            page_route,
                        )
                        await conn.execute(
                            """
                            INSERT INTO app_baselines (
                                domain,
                                page_route,
                                dom_structure_hash,
                                component_manifest,
                                is_golden_standard,
                                updated_at
                            ) VALUES ($1, $2, $3, $4::jsonb, TRUE, NOW())
                            """,
                            domain,
                            page_route,
                            dom_structure_hash,
                            manifest_json,
                        )
                else:
                    await conn.execute(
                        """
                        INSERT INTO app_baselines (
                            domain,
                            page_route,
                            dom_structure_hash,
                            component_manifest,
                            is_golden_standard,
                            updated_at
                        ) VALUES ($1, $2, $3, $4::jsonb, FALSE, NOW())
                        ON CONFLICT (domain, page_route, dom_structure_hash, is_golden_standard)
                        DO UPDATE SET
                            component_manifest = EXCLUDED.component_manifest,
                            updated_at = NOW()
                        """,
                        domain,
                        page_route,
                        dom_structure_hash,
                        manifest_json,
                    )
        except Exception as exc:
            _local_service_log(f"Failed to upsert baseline data: {exc}")

    async def _insert_regression_drift_log(
        self,
        *,
        domain: str,
        page_route: str,
        step_number: int,
        defect_tag: str,
        severity: str,
        missing_components: List[Dict[str, Any]],
        broken_selectors: List[str],
        drift_alert: Dict[str, Any],
    ) -> None:
        if self.pg_pool is None:
            return

        try:
            async with self.pg_pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO regression_drift_log (
                        domain,
                        page_route,
                        defect_tag,
                        severity,
                        missing_components,
                        broken_selectors,
                        drift_alert,
                        step_number
                    ) VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7::jsonb, $8)
                    """,
                    domain,
                    page_route,
                    defect_tag,
                    severity,
                    json.dumps(missing_components),
                    json.dumps(broken_selectors),
                    json.dumps(drift_alert),
                    step_number,
                )
        except Exception as exc:
            _local_service_log(f"Failed to insert regression drift log row: {exc}")

    async def analyze_route_regression(self, page: Page, snapshot: PageSnapshot, step_num: int) -> None:
        domain, page_route = split_domain_and_route(snapshot.url)
        if not domain:
            return

        component_manifest = await extract_component_manifest(page)
        await self._upsert_baseline(
            domain=domain,
            page_route=page_route,
            dom_structure_hash=snapshot.structure_hash,
            component_manifest=component_manifest,
            is_golden_standard=False,
        )

        golden = await self._fetch_golden_baseline(domain, page_route)
        if golden is None:
            if GOLDEN_BASELINE_MODE == "auto_upsert":
                await self._upsert_baseline(
                    domain=domain,
                    page_route=page_route,
                    dom_structure_hash=snapshot.structure_hash,
                    component_manifest=component_manifest,
                    is_golden_standard=True,
                )
                _local_service_log(f"Auto-seeded golden baseline for {domain}{page_route}.")
            else:
                _local_service_log(f"Golden baseline missing for {domain}{page_route}; comparison skipped.")
            return

        missing_components, broken_selectors = diff_component_manifests(
            golden_manifest=golden.get("component_manifest", []),
            current_manifest=component_manifest,
        )
        if not missing_components:
            return

        expected_baseline_components = len(golden.get("component_manifest", []) or [])
        expected_baseline_components = max(expected_baseline_components, len(missing_components))

        defect_tag = "Vibe-Code-Regression-Missing-Component"
        drift_alert = {
            "current_dom_structure_hash": snapshot.structure_hash,
            "golden_dom_structure_hash": golden.get("dom_structure_hash", ""),
            "missing_count": len(missing_components),
            "expected_baseline_components": expected_baseline_components,
            "missing_preview": missing_components[:10],
        }

        self.defects.add(
            "regression_findings",
            {
                "step": step_num,
                "type": defect_tag,
                "severity": "high",
                "domain": domain,
                "page_route": page_route,
                "missing_components": missing_components,
                "broken_selectors": broken_selectors,
                "expected_baseline_components": expected_baseline_components,
                "current_component_count": len(component_manifest),
                "url": snapshot.url,
            },
        )

        await self._insert_regression_drift_log(
            domain=domain,
            page_route=page_route,
            step_number=step_num,
            defect_tag=defect_tag,
            severity="high",
            missing_components=missing_components,
            broken_selectors=broken_selectors,
            drift_alert=drift_alert,
        )


def _stable_text_vector(text: str, vector_size: int) -> List[float]:
    vector = [0.0] * vector_size
    tokens = re.findall(r"[a-zA-Z0-9_#.-]+", text.lower())
    if not tokens:
        return vector

    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        idx = int.from_bytes(digest[:4], "big") % vector_size
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        weight = 1.0 + (digest[5] / 255.0)
        vector[idx] += sign * weight

    norm = sum(value * value for value in vector) ** 0.5
    if norm > 0.0:
        vector = [value / norm for value in vector]
    return vector


def simplify_layout_query(page_state: str) -> str:
    lines = [line.strip() for line in page_state.splitlines() if line.strip()]
    selected: List[str] = []
    for line in lines:
        if line.startswith("URL:") or line.startswith("Title:"):
            selected.append(line)
            continue
        if line.startswith("[id="):
            selected.append(line)
    if not selected:
        selected = lines[:80]
    return "\n".join(selected[:120])


class QdrantMemoryStore:
    def __init__(self) -> None:
        self.client = None
        self.enabled = False
        self.reads_enabled = QDRANT_ENABLE_READS
        self.writes_enabled = QDRANT_ENABLE_WRITES
        self.embedding_provider = QDRANT_EMBEDDING_PROVIDER
        self.embedding_model = QDRANT_EMBEDDING_MODEL
        self.vector_size = QDRANT_VECTOR_SIZE
        self.rerank_enabled = QDRANT_RERANK_ENABLED
        self.rerank_model = QDRANT_RERANK_MODEL
        self.candidate_limit = QDRANT_CANDIDATE_LIMIT
        self._ollama_embedding_warned = False
        self._ollama_rerank_warned = False
        self._last_search_telemetry: Dict[str, Any] = {}
        self._last_write_telemetry: Dict[str, Any] = {}

    def consume_last_search_telemetry(self) -> Dict[str, Any]:
        telemetry = dict(self._last_search_telemetry)
        self._last_search_telemetry = {}
        return telemetry

    def consume_last_write_telemetry(self) -> Dict[str, Any]:
        telemetry = dict(self._last_write_telemetry)
        self._last_write_telemetry = {}
        return telemetry

    def _extract_embedding_from_response(self, result: Any) -> Optional[List[float]]:
        if isinstance(result, dict):
            embedding = result.get("embedding")
            if isinstance(embedding, list) and embedding:
                return [float(x) for x in embedding]

            embeddings = result.get("embeddings")
            if isinstance(embeddings, list) and embeddings:
                first = embeddings[0]
                if isinstance(first, list) and first:
                    return [float(x) for x in first]
        return None

    def _ollama_embed_sync(self, text: str) -> Optional[List[float]]:
        try:
            response = ollama.embed(model=self.embedding_model, input=text)
            vector = self._extract_embedding_from_response(response)
            if vector:
                return vector
        except Exception:
            pass

        try:
            response = ollama.embeddings(model=self.embedding_model, prompt=text)
            vector = self._extract_embedding_from_response(response)
            if vector:
                return vector
        except Exception:
            pass
        return None

    async def _vectorize(self, text: str) -> List[float]:
        if self.embedding_provider == "ollama":
            try:
                vector = await asyncio.to_thread(self._ollama_embed_sync, text)
                if vector:
                    return vector
            except Exception as exc:
                if not self._ollama_embedding_warned:
                    _local_service_log(f"Ollama embedding failed, falling back to hash vectors: {exc}")
                    self._ollama_embedding_warned = True

        return _stable_text_vector(text, self.vector_size)

    async def _vectorize_with_telemetry(self, text: str) -> Tuple[List[float], Dict[str, Any]]:
        started = time.perf_counter()
        provider_used = self.embedding_provider
        fallback_used = False

        if self.embedding_provider == "ollama":
            try:
                vector = await asyncio.to_thread(self._ollama_embed_sync, text)
                if vector:
                    elapsed = (time.perf_counter() - started) * 1000.0
                    return vector, {
                        "provider_used": "ollama",
                        "fallback_used": False,
                        "vector_size": len(vector),
                        "vectorize_ms": round(elapsed, 3),
                    }
            except Exception:
                fallback_used = True

            provider_used = "hash"

        vector = _stable_text_vector(text, self.vector_size)
        elapsed = (time.perf_counter() - started) * 1000.0
        return vector, {
            "provider_used": provider_used,
            "fallback_used": fallback_used,
            "vector_size": len(vector),
            "vectorize_ms": round(elapsed, 3),
        }

    async def _ensure_collection(self) -> None:
        if self.client is None:
            return
        payload = {
            "vectors": {
                "size": self.vector_size,
                "distance": "Cosine",
            }
        }
        response = await self.client.put(
            f"{QDRANT_URL}/collections/{QDRANT_COLLECTION}",
            json=payload,
        )
        if response.status_code == 409:
            return
        if response.status_code >= 400:
            raise RuntimeError(f"collection create returned {response.status_code}: {response.text[:200]}")

    async def initialize(self, for_admin: bool = False) -> None:
        self.reads_enabled = QDRANT_ENABLE_READS
        self.writes_enabled = QDRANT_ENABLE_WRITES
        self.embedding_provider = QDRANT_EMBEDDING_PROVIDER
        self.embedding_model = QDRANT_EMBEDDING_MODEL
        self.vector_size = QDRANT_VECTOR_SIZE
        self.rerank_enabled = QDRANT_RERANK_ENABLED
        self.rerank_model = QDRANT_RERANK_MODEL
        self.candidate_limit = QDRANT_CANDIDATE_LIMIT

        if not for_admin and not (self.reads_enabled or self.writes_enabled):
            _local_service_log("Qdrant reads and writes are disabled by configuration.")
            self.enabled = False
            return

        if httpx is None:
            _local_service_log("httpx is unavailable; Qdrant semantic memory is disabled.")
            self.enabled = False
            return

        try:
            self.client = httpx.AsyncClient(timeout=6.0)
            health = await self.client.get(f"{QDRANT_URL}/collections")
            if health.status_code >= 400:
                raise RuntimeError(f"collections endpoint returned {health.status_code}")

            if self.embedding_provider == "ollama":
                probe_vector = await asyncio.to_thread(self._ollama_embed_sync, "monkeylm semantic memory bootstrap")
                if probe_vector:
                    self.vector_size = len(probe_vector)
                else:
                    _local_service_log(
                        "Unable to resolve Ollama embedding vector size during startup; falling back to hash vectors."
                    )
                    self.embedding_provider = "hash"

            await self._ensure_collection()

            self.enabled = True
            print("✅ Qdrant semantic memory is ready.")
        except Exception as exc:
            _local_service_log(f"Qdrant initialization failed: {exc}")
            self.enabled = False

    async def close(self) -> None:
        if self.client is not None:
            try:
                await self.client.aclose()
            except Exception as exc:
                _local_service_log(f"Failed to close Qdrant client cleanly: {exc}")
            self.client = None
        self.enabled = False

    async def inspect_collection(self) -> Dict[str, Any]:
        if self.client is None:
            return {"collection": QDRANT_COLLECTION, "exists": False, "error": "client_not_initialized"}

        try:
            response = await self.client.get(f"{QDRANT_URL}/collections/{QDRANT_COLLECTION}")
            if response.status_code == 404:
                return {"collection": QDRANT_COLLECTION, "exists": False}
            if response.status_code >= 400:
                return {
                    "collection": QDRANT_COLLECTION,
                    "exists": False,
                    "error": f"status={response.status_code}",
                    "raw": response.text[:200],
                }

            data = response.json().get("result", {})
            config = data.get("config", {}).get("params", {}).get("vectors", {})
            return {
                "collection": QDRANT_COLLECTION,
                "exists": True,
                "points_count": data.get("points_count", 0),
                "indexed_vectors_count": data.get("indexed_vectors_count", 0),
                "vector_size": config.get("size", self.vector_size),
                "distance": config.get("distance", "Cosine"),
                "status": data.get("status", "unknown"),
            }
        except Exception as exc:
            return {"collection": QDRANT_COLLECTION, "exists": False, "error": str(exc)}

    async def clear_collection(self) -> Dict[str, Any]:
        if self.client is None:
            return {"ok": False, "error": "client_not_initialized"}

        try:
            delete_response = await self.client.delete(f"{QDRANT_URL}/collections/{QDRANT_COLLECTION}")
            if delete_response.status_code not in {200, 202, 404}:
                return {
                    "ok": False,
                    "error": f"delete_failed_status={delete_response.status_code}",
                    "raw": delete_response.text[:200],
                }

            await self._ensure_collection()
            info = await self.inspect_collection()
            info["ok"] = True
            info["action"] = "cleared_and_recreated"
            return info
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _parse_rerank_response(self, content: Any) -> List[int]:
        if not isinstance(content, str):
            return []
        cleaned = content.replace("```json", "").replace("```", "").strip()
        if not cleaned:
            return []

        parsed: Optional[Dict[str, Any]] = None
        try:
            value = json.loads(cleaned)
            if isinstance(value, dict):
                parsed = value
        except Exception:
            match = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if match:
                try:
                    value = json.loads(match.group(0))
                    if isinstance(value, dict):
                        parsed = value
                except Exception:
                    parsed = None

        if not parsed:
            return []

        ranked_indices = parsed.get("ranked_indices", [])
        normalized: List[int] = []
        if isinstance(ranked_indices, list):
            for item in ranked_indices:
                if isinstance(item, int) and item >= 0:
                    normalized.append(item)
        return normalized

    def _build_rerank_prompt(self, query_text: str, candidates: List[Dict[str, Any]], final_limit: int) -> str:
        candidate_rows: List[str] = []
        for idx, memory in enumerate(candidates):
            summary = str(memory.get("layout_summary", ""))[:500]
            action = str(memory.get("action", ""))[:120]
            outcome = str(memory.get("outcome", ""))[:220]
            score = float(memory.get("score", 0.0))
            candidate_rows.append(
                f"[{idx}] score={score:.4f} action={action} outcome={outcome} layout={summary}"
            )

        return (
            "You are ranking historical web-testing memories for relevance to a current page layout query.\n"
            "Return strictly JSON with key ranked_indices containing unique candidate indices in best-first order.\n"
            f"Select exactly {final_limit} indices when possible.\n\n"
            f"Query:\n{query_text[:1200]}\n\n"
            "Candidates:\n"
            + "\n".join(candidate_rows)
            + "\n\nOutput format:\n{\"ranked_indices\": [0, 2, 1]}"
        )

    async def _rerank_memories(
        self,
        query_text: str,
        candidates: List[Dict[str, Any]],
        final_limit: int,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        started = time.perf_counter()
        if not candidates:
            return [], {
                "rerank_enabled": self.rerank_enabled,
                "rerank_applied": False,
                "rerank_model": self.rerank_model,
                "rerank_ms": round((time.perf_counter() - started) * 1000.0, 3),
            }

        if not self.rerank_enabled:
            return candidates[:final_limit], {
                "rerank_enabled": False,
                "rerank_applied": False,
                "rerank_model": self.rerank_model,
                "rerank_ms": round((time.perf_counter() - started) * 1000.0, 3),
            }

        try:
            prompt = self._build_rerank_prompt(query_text, candidates, final_limit)
            response = await asyncio.to_thread(
                ollama.chat,
                model=self.rerank_model,
                messages=[{"role": "user", "content": prompt}],
                format="json",
                options={"temperature": 0.0, "top_p": 0.9},
            )
            ranked_indices = self._parse_rerank_response(response.get("message", {}).get("content", ""))
            if not ranked_indices:
                return candidates[:final_limit], {
                    "rerank_enabled": True,
                    "rerank_applied": False,
                    "rerank_model": self.rerank_model,
                    "rerank_ms": round((time.perf_counter() - started) * 1000.0, 3),
                    "reason": "empty_ranking",
                }

            picked: List[Dict[str, Any]] = []
            seen: set = set()
            for idx in ranked_indices:
                if idx in seen or idx < 0 or idx >= len(candidates):
                    continue
                seen.add(idx)
                item = dict(candidates[idx])
                item["rerank_model"] = self.rerank_model
                picked.append(item)
                if len(picked) >= final_limit:
                    break

            if len(picked) < final_limit:
                for item in candidates:
                    if item in picked:
                        continue
                    picked.append(item)
                    if len(picked) >= final_limit:
                        break
            return picked, {
                "rerank_enabled": True,
                "rerank_applied": True,
                "rerank_model": self.rerank_model,
                "rerank_ms": round((time.perf_counter() - started) * 1000.0, 3),
            }
        except Exception as exc:
            if not self._ollama_rerank_warned:
                _local_service_log(f"Ollama reranker failed, falling back to vector ranking: {exc}")
                self._ollama_rerank_warned = True
            return candidates[:final_limit], {
                "rerank_enabled": True,
                "rerank_applied": False,
                "rerank_model": self.rerank_model,
                "rerank_ms": round((time.perf_counter() - started) * 1000.0, 3),
                "error": str(exc),
            }

    async def search_similar_layouts(self, page_state: str, limit: int = 3) -> List[Dict[str, Any]]:
        started_total = time.perf_counter()
        if not self.enabled or self.client is None or not self.reads_enabled:
            self._last_search_telemetry = {
                "enabled": False,
                "reads_enabled": self.reads_enabled,
                "returned_count": 0,
                "total_ms": round((time.perf_counter() - started_total) * 1000.0, 3),
            }
            return []

        query_text = simplify_layout_query(page_state)
        query_vector, vector_meta = await self._vectorize_with_telemetry(query_text)
        candidate_limit = max(limit, self.candidate_limit)
        body = {
            "vector": query_vector,
            "limit": max(1, min(candidate_limit, 50)),
            "with_payload": True,
        }

        try:
            search_started = time.perf_counter()
            response = await self.client.post(
                f"{QDRANT_URL}/collections/{QDRANT_COLLECTION}/points/search",
                json=body,
            )
            search_ms = (time.perf_counter() - search_started) * 1000.0
            if response.status_code >= 400:
                _local_service_log(f"Qdrant search failed ({response.status_code}): {response.text[:200]}")
                self._last_search_telemetry = {
                    "enabled": True,
                    "reads_enabled": self.reads_enabled,
                    "returned_count": 0,
                    "status": "search_failed",
                    "status_code": response.status_code,
                    "vectorize_ms": vector_meta.get("vectorize_ms", 0.0),
                    "qdrant_search_ms": round(search_ms, 3),
                    "total_ms": round((time.perf_counter() - started_total) * 1000.0, 3),
                }
                return []

            data = response.json()
            points = data.get("result", [])
            memories: List[Dict[str, Any]] = []
            for point in points:
                payload = point.get("payload", {})
                memories.append(
                    {
                        "layout_summary": payload.get("layout_summary", ""),
                        "action": payload.get("action", ""),
                        "outcome": payload.get("outcome", ""),
                        "url": payload.get("url", ""),
                        "score": float(point.get("score", 0.0)),
                        "vector_rank": len(memories) + 1,
                    }
                )
            reranked, rerank_meta = await self._rerank_memories(query_text, memories, final_limit=max(1, min(limit, 10)))
            scores = [float(item.get("score", 0.0)) for item in reranked]
            self._last_search_telemetry = {
                "enabled": True,
                "reads_enabled": self.reads_enabled,
                "status": "ok",
                "provider_used": vector_meta.get("provider_used", self.embedding_provider),
                "fallback_used": bool(vector_meta.get("fallback_used", False)),
                "vector_size": int(vector_meta.get("vector_size", self.vector_size)),
                "vectorize_ms": float(vector_meta.get("vectorize_ms", 0.0)),
                "qdrant_search_ms": round(search_ms, 3),
                "rerank_ms": float(rerank_meta.get("rerank_ms", 0.0)),
                "rerank_enabled": bool(rerank_meta.get("rerank_enabled", False)),
                "rerank_applied": bool(rerank_meta.get("rerank_applied", False)),
                "candidate_count": len(memories),
                "returned_count": len(reranked),
                "top_score": round(scores[0], 6) if scores else 0.0,
                "avg_score": round(sum(scores) / len(scores), 6) if scores else 0.0,
                "total_ms": round((time.perf_counter() - started_total) * 1000.0, 3),
            }
            return reranked
        except Exception as exc:
            _local_service_log(f"Qdrant search error: {exc}")
            self._last_search_telemetry = {
                "enabled": True,
                "reads_enabled": self.reads_enabled,
                "status": "search_error",
                "error": str(exc),
                "vectorize_ms": float(vector_meta.get("vectorize_ms", 0.0)),
                "total_ms": round((time.perf_counter() - started_total) * 1000.0, 3),
                "returned_count": 0,
            }
            return []

    async def add_step_memory(
        self,
        *,
        page_state: str,
        action: str,
        outcome: str,
        url: str,
        step: int,
    ) -> None:
        started_total = time.perf_counter()
        if not self.enabled or self.client is None or not self.writes_enabled:
            self._last_write_telemetry = {
                "enabled": False,
                "writes_enabled": self.writes_enabled,
                "status": "skipped",
                "total_ms": round((time.perf_counter() - started_total) * 1000.0, 3),
            }
            return

        layout_summary = simplify_layout_query(page_state)
        vector, vector_meta = await self._vectorize_with_telemetry(layout_summary)
        point_id = int(time.time() * 1_000_000) + random.randint(0, 999)
        body = {
            "points": [
                {
                    "id": point_id,
                    "vector": vector,
                    "payload": {
                        "layout_summary": layout_summary,
                        "action": action,
                        "outcome": outcome,
                        "url": url,
                        "step": step,
                        "timestamp": datetime.now().isoformat(),
                    },
                }
            ]
        }

        try:
            upsert_started = time.perf_counter()
            response = await self.client.put(
                f"{QDRANT_URL}/collections/{QDRANT_COLLECTION}/points",
                json=body,
            )
            upsert_ms = (time.perf_counter() - upsert_started) * 1000.0
            if response.status_code >= 400:
                _local_service_log(f"Qdrant upsert failed ({response.status_code}): {response.text[:200]}")
                self._last_write_telemetry = {
                    "enabled": True,
                    "writes_enabled": self.writes_enabled,
                    "status": "upsert_failed",
                    "status_code": response.status_code,
                    "provider_used": vector_meta.get("provider_used", self.embedding_provider),
                    "vectorize_ms": float(vector_meta.get("vectorize_ms", 0.0)),
                    "qdrant_upsert_ms": round(upsert_ms, 3),
                    "total_ms": round((time.perf_counter() - started_total) * 1000.0, 3),
                }
                return

            self._last_write_telemetry = {
                "enabled": True,
                "writes_enabled": self.writes_enabled,
                "status": "ok",
                "provider_used": vector_meta.get("provider_used", self.embedding_provider),
                "vectorize_ms": float(vector_meta.get("vectorize_ms", 0.0)),
                "qdrant_upsert_ms": round(upsert_ms, 3),
                "total_ms": round((time.perf_counter() - started_total) * 1000.0, 3),
            }
        except Exception as exc:
            _local_service_log(f"Qdrant upsert error: {exc}")
            self._last_write_telemetry = {
                "enabled": True,
                "writes_enabled": self.writes_enabled,
                "status": "upsert_error",
                "error": str(exc),
                "provider_used": vector_meta.get("provider_used", self.embedding_provider),
                "vectorize_ms": float(vector_meta.get("vectorize_ms", 0.0)),
                "total_ms": round((time.perf_counter() - started_total) * 1000.0, 3),
            }


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
    memory_logs = await QDRANT_MEMORY.search_similar_layouts(page_state, limit=3)
    prompt = build_decision_prompt(page_state, memory_logs)

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


def build_decision_prompt(page_state: str, memory_logs: Optional[List[Dict[str, Any]]] = None) -> str:
    memory_logs = memory_logs or []
    memory_json = json.dumps(memory_logs, ensure_ascii=True, indent=2)
    return f"""
You are an Advanced Monkey Testing Agent. Your goal is to deeply test the app by filling forms, submitting data, and handling modals.

Current Page State:
{page_state}

## Memory Logs of Previous Vibe Changes
{memory_json}

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


def summarize_semantic_memory_telemetry() -> Dict[str, Any]:
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


def summarize_vibe_coding_accountability() -> Dict[str, Any]:
    findings = DEFECTS.regression_findings

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
        "FAILED: Structural Drift Detected"
        if drift_index > 0.0
        else "PASSED: No Structural Drift Detected"
    )

    return {
        "regression_drift_index": round(drift_index, 3),
        "total_missing_historical_components": total_missing,
        "total_expected_baseline_components": total_expected,
        "run_summary_status": run_summary_status,
        "drift_details": drift_details,
    }

def generate_markdown_report(start_time, end_time):
    duration_seconds = (end_time - start_time).total_seconds()
    total_steps = len(test_logs)
    failed_steps = [log for log in test_logs if log["status"] in ["FAILED", "CRASH"]]
    success_rate = ((total_steps - len(failed_steps)) / total_steps * 100) if total_steps > 0 else 0

    accountability = summarize_vibe_coding_accountability()

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
**Run Summary Status:** {accountability.get('run_summary_status')}  
**Regression Drift Index:** {accountability.get('regression_drift_index')}%  
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

    md_content += "\n## Baseline Regressions\n"
    if DEFECTS.regression_findings:
        for item in DEFECTS.regression_findings:
            md_content += (
                f"- Step {item['step']}: [{item['severity']}] {item['type']} "
                f"at {item['domain']}{item['page_route']} "
                f"(missing: {len(item.get('missing_components', []))})\n"
            )
    else:
        md_content += "- None detected.\n"

    telemetry = summarize_semantic_memory_telemetry()
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

    report_path = os.path.join(OUTPUT_DIR, "test_report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(md_content)
    
    print(f"\n📄 Report generated: {report_path}")
    print(f"💾 All artifacts saved in: {OUTPUT_DIR}")


def generate_json_summary(start_time: datetime, end_time: datetime) -> None:
    semantic_memory_telemetry = summarize_semantic_memory_telemetry()
    accountability = summarize_vibe_coding_accountability()

    summary = {
        "target_url": TARGET_URL,
        "model": OLLAMA_MODEL,
        "active_seed": ACTIVE_SEED,
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "duration_seconds": (end_time - start_time).total_seconds(),
        "steps": len(test_logs),
        "failed_steps": len([log for log in test_logs if log["status"] != "SUCCESS"]),
        "run_summary_status": accountability.get("run_summary_status"),
        "regression_drift_index": accountability.get("regression_drift_index"),
        "browser_launch": BROWSER_LAUNCH_INFO,
        "defects": {
            "security_risks": DEFECTS.security_risks,
            "accessibility_violations": DEFECTS.accessibility_violations,
            "performance_bottlenecks": DEFECTS.performance_bottlenecks,
            "visual_regressions": DEFECTS.visual_regressions,
            "layout_instability": DEFECTS.layout_instability,
            "regression_findings": DEFECTS.regression_findings,
            "race_findings": DEFECTS.race_findings,
            "console_findings": DEFECTS.console_findings,
            "boundary_drift": DEFECTS.boundary_drift,
        },
        "network_injections": NETWORK_MONITOR.injected_events,
        "semantic_memory_telemetry": semantic_memory_telemetry,
        "vibe_coding_accountability": accountability,
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
PERSISTENCE_ENGINE = PersistenceEngine(DEFECTS)
QDRANT_MEMORY = QdrantMemoryStore()

async def main():
    start_time = datetime.now()

    if QDRANT_ADMIN_ACTION in {"inspect", "clear"}:
        await QDRANT_MEMORY.initialize(for_admin=True)
        try:
            if QDRANT_ADMIN_ACTION == "inspect":
                info = await QDRANT_MEMORY.inspect_collection()
                print("🧠 Qdrant Inspect:")
                print(json.dumps(info, indent=2))
            else:
                info = await QDRANT_MEMORY.clear_collection()
                print("🧹 Qdrant Clear:")
                print(json.dumps(info, indent=2))
        finally:
            await QDRANT_MEMORY.close()
        return
    
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
        await PERSISTENCE_ENGINE.initialize()
        await QDRANT_MEMORY.initialize()

        visited_states: Dict[str, int] = {}
        seen_click_targets: set = set()

        try:
            for step in range(1, MAX_STEPS + 1):
                print(f"\n--- Step {step}/{MAX_STEPS} ---")

                try:
                    snapshot = await get_page_state(page, step, phase="plan")
                    state_key = f"{snapshot.url}::{snapshot.structure_hash}"
                    local_count = visited_states.get(state_key, 0) + 1
                    redis_count = await PERSISTENCE_ENGINE.increment_visited_state(state_key)
                    visited_states[state_key] = redis_count if redis_count is not None else local_count
                    state = state_to_prompt(snapshot)
                except Exception as e:
                    print(f"   -> 🚨 Failed to get state: {e}. Skipping step.")
                    continue

                plan = await decide_next_action(state)
                retrieval_telemetry = QDRANT_MEMORY.consume_last_search_telemetry()
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
                log_entry["memory_retrieval"] = retrieval_telemetry

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

                try:
                    baseline_snapshot = await get_page_state(page, step, phase="baseline")
                    await PERSISTENCE_ENGINE.analyze_route_regression(page, baseline_snapshot, step)
                except Exception as exc:
                    _local_service_log(f"Post-step baseline analysis failed at step {step}: {exc}")

                outcome_bits = [f"status={log_entry.get('status', 'UNKNOWN')}"]
                if log_entry.get("error"):
                    outcome_bits.append(f"error={log_entry['error'][:180]}")
                regression_hits = [
                    finding
                    for finding in DEFECTS.regression_findings
                    if int(finding.get("step", -1)) == step
                ]
                if regression_hits:
                    outcome_bits.append(
                        f"regressions={len(regression_hits)} tag=Vibe-Code-Regression-Missing-Component"
                    )

                await QDRANT_MEMORY.add_step_memory(
                    page_state=state,
                    action=str(plan.get("action", "scroll")),
                    outcome="; ".join(outcome_bits),
                    url=page.url,
                    step=step,
                )
                log_entry["memory_write"] = QDRANT_MEMORY.consume_last_write_telemetry()

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
        finally:
            await QDRANT_MEMORY.close()
            await PERSISTENCE_ENGINE.close()
            await context.close()

    end_time = datetime.now()
    generate_markdown_report(start_time, end_time)
    generate_json_summary(start_time, end_time)

if __name__ == "__main__":
    cli_args = parse_cli_args()
    apply_runtime_overrides(cli_args)
    asyncio.run(main())