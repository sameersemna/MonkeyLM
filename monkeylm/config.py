"""Configuration, constants, dataclasses, and CLI argument parsing for MonkeyLM."""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import signal
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

# ── Optional third-party imports ───────────────────────────────────────────────


def _optional_import(module_name: str, attr_name: Optional[str] = None):
    try:
        import importlib

        module = importlib.import_module(module_name)
        return getattr(module, attr_name) if attr_name else module
    except Exception:
        return None


def _load_dotenv() -> None:
    """Load environment variables from a local `.env` file if python-dotenv is available."""
    dotenv = _optional_import("dotenv")
    if dotenv is None:
        return
    try:
        env_path = Path(__file__).resolve().parent / ".env"
        if env_path.is_file():
            dotenv.load_dotenv(env_path, override=False)
    except Exception:
        pass


_load_dotenv()

# Expose optional imports for downstream modules
Faker = _optional_import("faker", "Faker")
Image = _optional_import("PIL", "Image")
ImageDraw = _optional_import("PIL", "ImageDraw")
pil_pixelmatch = _optional_import("pixelmatch.contrib.PIL", "pixelmatch")
asyncpg = _optional_import("asyncpg")
redis_asyncio = _optional_import("redis.asyncio")
httpx = _optional_import("httpx")

try:
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

    _REPORTLAB_AVAILABLE = True
except Exception:
    _REPORTLAB_AVAILABLE = False

# ── Default constants ──────────────────────────────────────────────────────────

# IMPORTANT: These DEFAULT_* constants serve as fallbacks only.
# Primary configuration is fetched dynamically from .env file at runtime via load_settings().
# See load_settings() for the fetching order: .env file → environment variables → defaults

DEFAULT_TARGET_URL = "https://noblequran-85hu2yge.manus.space/"
DEFAULT_OLLAMA_MODEL = "minimax-m3:cloud"
DEFAULT_OLLAMA_TIMEOUT_SECONDS = 15.0
DEFAULT_MAX_STEPS = 10
DEFAULT_WORKERS = 1
DEFAULT_MAX_STEPS_PER_WORKER = 10
DEFAULT_WORKER_NAVIGATION_RETRIES = 2
DEFAULT_WORKER_QDRANT_INIT_RETRIES = 1
DEFAULT_WORKER_BOUNDARY_RECOVERY_RETRIES = 1
DEFAULT_RETRY_BASE_DELAY_SECONDS = 0.75
MAX_ALLOWED_RETRIES = 10
MAX_ALLOWED_RETRY_BASE_DELAY_SECONDS = 10.0
DEFAULT_HEADLESS = True
DEFAULT_WINDOW_SIZE = "1920,1080"
DEFAULT_NO_VIEWPORT = True
DEFAULT_POSTGRES_DSN = "postgresql://postgres:postgres@latitude:5432/monkeylm"
DEFAULT_REDIS_URL = "redis://:LatitudeRedis1407@latitude:6379/0"
DEFAULT_REDIS_PREFIX = "monkey:"
DEFAULT_REDIS_PATH_LOCK_TTL_SECONDS = 45
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

DEFAULT_PDF_GENERATE = False
DEFAULT_PDF_VISION_MODEL = "llama3.2-vision"
DEFAULT_VISION_MODEL = "gemini-3-flash-preview"
DEFAULT_PDF_VISION_TIMEOUT_SECONDS = 30.0

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

# ── Environment helpers ────────────────────────────────────────────────────────


def _env_to_bool(value: Any, default: bool = False) -> bool:
    """Convert a value (string, bool, or None) to boolean."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _env_to_float(value: Any, default: float) -> float:
    """Convert a value (string, float, or None) to float."""
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except Exception:
            return default
    try:
        return float(value)
    except Exception:
        return default


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


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value.strip())
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


# ── Settings & Cognitive Testing Persona dataclasses ──────────────────────────

from dataclasses import dataclass, field


@dataclass
class PersonaGoal:
    """A single testing persona with intent and expected reactions."""
    name: str  # e.g., "Rush User", "SQL Injection Attacker"
    description: str  # human-like motivation
    behaviors: List[str]  # example: ["double-clicks submit", "skips validation"]


@dataclass
class CriticalFlow:
    """A critical user flow to test with persona-driven actions."""
    name: str  # e.g., "user_registration"
    description: str
    steps: List[str]  # e.g., ["fill_form", "validate", "submit"]


@dataclass
class TestingStrategy:
    """Application discovery output — LLM-generated testing strategy before the action loop."""
    app_domain: str  # inferred domain, e.g., "e-commerce checkout"
    primary_personas: List[PersonaGoal]  # target user personas for testing
    critical_flows: List[CriticalFlow]  # key flows to exercise
    edge_cases_to_test: List[str]  # specific edge cases to explore
    security_focus: List[str]  # security concerns to probe
    strategy_summary: str = ""  # one-line summary of the overall approach


@dataclass
class Settings:
    """Single source of truth for all MonkeyLM runtime configuration."""

    # Target & model
    target_url: str = DEFAULT_TARGET_URL
    ollama_model: str = DEFAULT_OLLAMA_MODEL
    ollama_timeout_seconds: float = DEFAULT_OLLAMA_TIMEOUT_SECONDS
    vision_model: str = DEFAULT_VISION_MODEL
    pdf_vision_model: str = DEFAULT_PDF_VISION_MODEL
    pdf_vision_timeout_seconds: float = DEFAULT_PDF_VISION_TIMEOUT_SECONDS

    # Execution
    max_steps: int = DEFAULT_MAX_STEPS
    workers: int = DEFAULT_WORKERS
    max_steps_per_worker: int = DEFAULT_MAX_STEPS_PER_WORKER
    worker_navigation_retries: int = DEFAULT_WORKER_NAVIGATION_RETRIES
    worker_qdrant_init_retries: int = DEFAULT_WORKER_QDRANT_INIT_RETRIES
    worker_boundary_recovery_retries: int = DEFAULT_WORKER_BOUNDARY_RECOVERY_RETRIES
    retry_base_delay_seconds: float = DEFAULT_RETRY_BASE_DELAY_SECONDS

    # Browser
    headless: bool = DEFAULT_HEADLESS
    browser_window_size: str = DEFAULT_WINDOW_SIZE
    no_viewport: bool = DEFAULT_NO_VIEWPORT
    strict_sandbox: bool = False
    allow_no_sandbox_fallback: bool = False

    # Persistence
    postgres_dsn: str = DEFAULT_POSTGRES_DSN
    redis_url: str = DEFAULT_REDIS_URL
    redis_prefix: str = DEFAULT_REDIS_PREFIX
    redis_path_lock_ttl_seconds: int = DEFAULT_REDIS_PATH_LOCK_TTL_SECONDS
    redis_state_ttl_seconds: int = DEFAULT_REDIS_STATE_TTL_SECONDS
    strict_persistence: bool = DEFAULT_STRICT_PERSISTENCE
    golden_baseline_mode: str = DEFAULT_GOLDEN_BASELINE_MODE

    # Qdrant
    qdrant_url: str = DEFAULT_QDRANT_URL
    qdrant_collection: str = DEFAULT_QDRANT_COLLECTION
    qdrant_vector_size: int = DEFAULT_QDRANT_VECTOR_SIZE
    qdrant_enable_reads: bool = DEFAULT_QDRANT_ENABLE_READS
    qdrant_enable_writes: bool = DEFAULT_QDRANT_ENABLE_WRITES
    qdrant_embedding_provider: str = DEFAULT_QDRANT_EMBEDDING_PROVIDER
    qdrant_embedding_model: str = DEFAULT_QDRANT_EMBEDDING_MODEL
    qdrant_rerank_enabled: bool = DEFAULT_QDRANT_RERANK_ENABLED
    qdrant_rerank_model: str = DEFAULT_QDRANT_RERANK_MODEL
    qdrant_candidate_limit: int = DEFAULT_QDRANT_CANDIDATE_LIMIT
    qdrant_admin_action: str = ""

    # PDF
    pdf_generate: bool = DEFAULT_PDF_GENERATE

    # Runtime state (mutable)
    active_seed: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now().strftime("%Y%m%d_%H%M%S"))

    @property
    def output_dir(self) -> str:
        import os

        return os.path.abspath(f"reports/testrun_{self.timestamp}")

    @property
    def user_data_root(self) -> str:
        import os

        return os.path.abspath("./playwright_user_data")

    @property
    def run_user_data_dir(self) -> str:
        import os

        return os.path.join(self.user_data_root, f"session_{self.timestamp}")


# ── CLI parsing ────────────────────────────────────────────────────────────────


def parse_cli_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Advanced monkey testing agent")
    parser.add_argument("--target-url", help="Target URL to test")
    parser.add_argument("--ollama-model", help="Ollama model name to use")
    parser.add_argument(
        "--vision-model",
        help="Vision model name for screenshot annotation (cloud or local)",
        default=None,
    )
    parser.add_argument(
        "--ollama-timeout-seconds",
        type=float,
        default=None,
        help="Hard timeout for each Ollama inference call in seconds (default: 15)",
    )
    parser.add_argument("--max-steps", type=int, help="Maximum monkey steps to execute")
    parser.add_argument(
        "--workers", type=int, default=None, help="Maximum concurrent worker contexts (default: 1)"
    )
    parser.add_argument(
        "--max-steps-per-worker",
        type=int,
        default=None,
        help="Per-worker maximum step cap; must be <= --max-steps",
    )
    parser.add_argument(
        "--worker-navigation-retries", type=int, default=None, help="Retry count for initial worker navigation"
    )
    parser.add_argument(
        "--worker-qdrant-init-retries", type=int, default=None, help="Retry count for worker Qdrant initialization"
    )
    parser.add_argument(
        "--worker-boundary-recovery-retries",
        type=int,
        default=None,
        help="Retry count for worker boundary-recovery navigation",
    )
    parser.add_argument(
        "--retry-base-delay-seconds",
        type=float,
        default=None,
        help="Base retry delay in seconds for worker backoff",
    )
    parser.add_argument("--seed", type=int, help="Random seed for deterministic test replay")
    parser.add_argument("--window-size", help="Browser window size as WIDTH,HEIGHT or WIDTHxHEIGHT")
    parser.add_argument("--postgres-dsn", help="PostgreSQL connection string")
    parser.add_argument("--redis-url", help="Redis connection URL")
    parser.add_argument("--redis-prefix", help="Optional prefix for all Redis keys (default: empty)")
    parser.add_argument(
        "--redis-path-lock-ttl-seconds",
        type=int,
        default=None,
        help="TTL in seconds for cross-worker action-path Redis locks (default: 45)",
    )
    parser.add_argument(
        "--golden-baseline-mode",
        choices=["preexisting", "auto_upsert"],
        help="Golden baseline strategy: compare only preexisting goldens or auto-seed when missing",
    )
    parser.add_argument("--qdrant-url", help="Qdrant base URL, for example http://127.0.0.1:6333")
    parser.add_argument("--qdrant-collection", help="Qdrant collection name for semantic memory logs")
    parser.add_argument(
        "--qdrant-embedding-provider", choices=["hash", "ollama"], help="Embedding backend for Qdrant vectors"
    )
    parser.add_argument("--qdrant-embedding-model", help="Local Ollama embedding model name, e.g. nomic-embed-text")
    parser.add_argument("--qdrant-rerank-model", help="Local Ollama model for reranking retrieved memories")
    parser.add_argument(
        "--qdrant-candidate-limit", type=int, help="Number of candidates to fetch from Qdrant before reranking"
    )

    qdrant_rerank_group = parser.add_mutually_exclusive_group()
    qdrant_rerank_group.add_argument(
        "--qdrant-enable-rerank", action="store_true", help="Enable second-stage reranking of Qdrant memory candidates"
    )
    qdrant_rerank_group.add_argument(
        "--qdrant-disable-rerank", action="store_true", help="Disable reranking and use raw vector ranking only"
    )

    qdrant_rw_group = parser.add_mutually_exclusive_group()
    qdrant_rw_group.add_argument(
        "--qdrant-read-only", action="store_true", help="Enable Qdrant reads but disable writes"
    )
    qdrant_rw_group.add_argument(
        "--qdrant-disable-writes", action="store_true", help="Disable writing step memories to Qdrant"
    )
    parser.add_argument("--qdrant-disable-reads", action="store_true", help="Disable semantic search reads from Qdrant")

    qdrant_admin_group = parser.add_mutually_exclusive_group()
    qdrant_admin_group.add_argument(
        "--qdrant-inspect", action="store_true", help="Inspect Qdrant collection status and exit"
    )
    qdrant_admin_group.add_argument(
        "--qdrant-clear", action="store_true", help="Delete and recreate Qdrant collection, then exit"
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


def load_settings(cli_args: Optional[argparse.Namespace] = None) -> Settings:
    """Build a Settings object from .env file, environment variables, optionally overridden by CLI args."""
    import os

    # ── Load .env file values for dynamic fetching ──────────────────────
    # Read .env file contents to fetch values dynamically
    # .env is in parent directory (project root), not in monkeylm/
    from dotenv import dotenv_values
    
    project_root = Path(__file__).resolve().parent.parent
    env_path = project_root / ".env"
    env_vars = {}
    if env_path.is_file():
        env_vars = dotenv_values(env_path)
    
    # ── Create Settings object with .env values as defaults ────────────
    s = Settings()

    # ── Apply .env-driven defaults (primary source) ────────────────────
    # Fetch from .env first, then environment variables, then Settings defaults
    s.target_url = env_vars.get("TARGET_URL", os.getenv("TARGET_URL", s.target_url))
    s.ollama_model = env_vars.get("OLLAMA_MODEL", os.getenv("OLLAMA_MODEL", s.ollama_model))
    ollama_timeout = _env_float("OLLAMA_TIMEOUT_SECONDS", s.ollama_timeout_seconds)
    s.ollama_timeout_seconds = max(1.0, _env_to_float(env_vars.get("OLLAMA_TIMEOUT_SECONDS"), ollama_timeout))
    
    s.max_steps = max(1, int(env_vars.get("MAX_STEPS", s.max_steps)))
    
    s.workers = max(1, int(env_vars.get("WORKERS", s.workers)))
    
    s.max_steps_per_worker = max(
        1, int(env_vars.get("MAX_STEPS_PER_WORKER", min(s.max_steps, DEFAULT_MAX_STEPS_PER_WORKER)))
    )
    
    s.worker_navigation_retries = max(0, int(env_vars.get("WORKER_NAVIGATION_RETRIES", s.worker_navigation_retries)))
    s.worker_qdrant_init_retries = max(0, int(env_vars.get("WORKER_QDRANT_INIT_RETRIES", s.worker_qdrant_init_retries)))
    s.worker_boundary_recovery_retries = max(
        0, int(env_vars.get("WORKER_BOUNDARY_RECOVERY_RETRIES", s.worker_boundary_recovery_retries))
    )
    s.retry_base_delay_seconds = max(0.1, float(env_vars.get("RETRY_BASE_DELAY_SECONDS", s.retry_base_delay_seconds)))
    s.headless = _env_bool("HEADLESS", default=_env_to_bool(env_vars.get("HEADLESS"), s.headless))
    s.browser_window_size = _normalize_window_size(env_vars.get("BROWSER_WINDOW_SIZE", os.getenv("BROWSER_WINDOW_SIZE", s.browser_window_size)))
    s.no_viewport = _env_bool("NO_VIEWPORT", default=_env_to_bool(env_vars.get("NO_VIEWPORT"), s.no_viewport))
    s.postgres_dsn = _env_str("POSTGRES_DSN", env_vars.get("POSTGRES_DSN", s.postgres_dsn))
    s.redis_url = _env_str("REDIS_URL", env_vars.get("REDIS_URL", s.redis_url))
    s.redis_prefix = _env_str("REDIS_PREFIX", env_vars.get("REDIS_PREFIX", s.redis_prefix))
    s.redis_path_lock_ttl_seconds = max(1, int(env_vars.get("REDIS_PATH_LOCK_TTL_SECONDS", s.redis_path_lock_ttl_seconds)))
    s.golden_baseline_mode = _env_str("GOLDEN_BASELINE_MODE", env_vars.get("GOLDEN_BASELINE_MODE", s.golden_baseline_mode)).lower()
    s.strict_persistence = _env_bool("STRICT_PERSISTENCE", default=_env_to_bool(env_vars.get("STRICT_PERSISTENCE"), s.strict_persistence))
    s.redis_state_ttl_seconds = max(60, int(env_vars.get("REDIS_STATE_TTL_SECONDS", s.redis_state_ttl_seconds)))
    s.qdrant_url = _env_str("QDRANT_URL", env_vars.get("QDRANT_URL", s.qdrant_url))
    s.qdrant_collection = _env_str("QDRANT_COLLECTION", env_vars.get("QDRANT_COLLECTION", s.qdrant_collection))
    s.qdrant_vector_size = max(64, int(env_vars.get("QDRANT_VECTOR_SIZE", s.qdrant_vector_size)))
    s.qdrant_enable_reads = _env_bool("QDRANT_ENABLE_READS", default=_env_to_bool(env_vars.get("QDRANT_ENABLE_READS"), s.qdrant_enable_reads))
    s.qdrant_enable_writes = _env_bool("QDRANT_ENABLE_WRITES", default=_env_to_bool(env_vars.get("QDRANT_ENABLE_WRITES"), s.qdrant_enable_writes))
    s.qdrant_embedding_provider = _env_str("QDRANT_EMBEDDING_PROVIDER", env_vars.get("QDRANT_EMBEDDING_PROVIDER", s.qdrant_embedding_provider)).lower()
    s.qdrant_embedding_model = _env_str("QDRANT_EMBEDDING_MODEL", env_vars.get("QDRANT_EMBEDDING_MODEL", s.qdrant_embedding_model))
    s.qdrant_admin_action = _env_str("QDRANT_ADMIN_ACTION", env_vars.get("QDRANT_ADMIN_ACTION", "")).lower()
    s.qdrant_rerank_enabled = _env_bool("QDRANT_RERANK_ENABLED", default=_env_to_bool(env_vars.get("QDRANT_RERANK_ENABLED"), s.qdrant_rerank_enabled))
    s.qdrant_rerank_model = _env_str("QDRANT_RERANK_MODEL", env_vars.get("QDRANT_RERANK_MODEL", s.qdrant_rerank_model))
    s.qdrant_candidate_limit = max(3, int(env_vars.get("QDRANT_CANDIDATE_LIMIT", s.qdrant_candidate_limit)))
    s.pdf_generate = _env_bool("PDF_GENERATE", default=_env_to_bool(env_vars.get("PDF_GENERATE"), s.pdf_generate))
    s.pdf_vision_model = _env_str("PDF_VISION_MODEL", env_vars.get("PDF_VISION_MODEL", s.pdf_vision_model))
    s.vision_model = _env_str("VISION_MODEL", env_vars.get("VISION_MODEL", s.vision_model))
    s.pdf_vision_timeout_seconds = max(
        1.0, float(env_vars.get("PDF_VISION_TIMEOUT_SECONDS", s.pdf_vision_timeout_seconds))
    )
    s.strict_sandbox = _env_bool("STRICT_SANDBOX", default=_env_to_bool(env_vars.get("STRICT_SANDBOX"), s.strict_sandbox))
    s.allow_no_sandbox_fallback = _env_bool("ALLOW_NO_SANDBOX_FALLBACK", default=_env_to_bool(env_vars.get("ALLOW_NO_SANDBOX_FALLBACK"), s.allow_no_sandbox_fallback))

    # ── Apply CLI overrides ─────────────────────────────────────────────
    if cli_args is not None:
        if getattr(cli_args, "target_url", None):
            s.target_url = cli_args.target_url
        if getattr(cli_args, "ollama_model", None):
            s.ollama_model = cli_args.ollama_model
        if getattr(cli_args, "vision_model", None):
            s.vision_model = cli_args.vision_model.strip()
        if getattr(cli_args, "ollama_timeout_seconds", None) is not None:
            s.ollama_timeout_seconds = max(1.0, float(cli_args.ollama_timeout_seconds))
        if getattr(cli_args, "max_steps", None) is not None:
            s.max_steps = max(1, cli_args.max_steps)
        if getattr(cli_args, "workers", None) is not None:
            s.workers = max(1, int(cli_args.workers))
        if getattr(cli_args, "max_steps_per_worker", None) is not None:
            s.max_steps_per_worker = max(1, int(cli_args.max_steps_per_worker))
        if getattr(cli_args, "worker_navigation_retries", None) is not None:
            s.worker_navigation_retries = max(0, int(cli_args.worker_navigation_retries))
        if getattr(cli_args, "worker_qdrant_init_retries", None) is not None:
            s.worker_qdrant_init_retries = max(0, int(cli_args.worker_qdrant_init_retries))
        if getattr(cli_args, "worker_boundary_recovery_retries", None) is not None:
            s.worker_boundary_recovery_retries = max(0, int(cli_args.worker_boundary_recovery_retries))
        if getattr(cli_args, "retry_base_delay_seconds", None) is not None:
            s.retry_base_delay_seconds = max(0.1, float(cli_args.retry_base_delay_seconds))
        if getattr(cli_args, "headless", None) is not None:
            s.headless = bool(cli_args.headless)
        if getattr(cli_args, "window_size", None):
            s.browser_window_size = _normalize_window_size(
                cli_args.window_size, fallback=s.browser_window_size
            )
        if getattr(cli_args, "no_viewport", None) is not None:
            s.no_viewport = bool(cli_args.no_viewport)
        if getattr(cli_args, "seed", None) is not None:
            random.seed(cli_args.seed)
            s.active_seed = str(cli_args.seed)
        if getattr(cli_args, "postgres_dsn", None):
            s.postgres_dsn = cli_args.postgres_dsn.strip()
        if getattr(cli_args, "redis_url", None):
            s.redis_url = cli_args.redis_url.strip()
        if getattr(cli_args, "redis_prefix", None) is not None:
            s.redis_prefix = cli_args.redis_prefix
        if getattr(cli_args, "redis_path_lock_ttl_seconds", None) is not None:
            s.redis_path_lock_ttl_seconds = max(1, int(cli_args.redis_path_lock_ttl_seconds))
        if getattr(cli_args, "golden_baseline_mode", None):
            s.golden_baseline_mode = cli_args.golden_baseline_mode.strip().lower()
        if getattr(cli_args, "strict_persistence", None) is not None:
            s.strict_persistence = bool(cli_args.strict_persistence)
        if getattr(cli_args, "qdrant_url", None):
            s.qdrant_url = cli_args.qdrant_url.strip().rstrip("/")
        if getattr(cli_args, "qdrant_collection", None):
            s.qdrant_collection = cli_args.qdrant_collection.strip()
        if getattr(cli_args, "qdrant_embedding_provider", None):
            s.qdrant_embedding_provider = cli_args.qdrant_embedding_provider.strip().lower()
        if getattr(cli_args, "qdrant_embedding_model", None):
            s.qdrant_embedding_model = cli_args.qdrant_embedding_model.strip()
        if getattr(cli_args, "qdrant_rerank_model", None):
            s.qdrant_rerank_model = cli_args.qdrant_rerank_model.strip()
        if getattr(cli_args, "qdrant_candidate_limit", None) is not None:
            s.qdrant_candidate_limit = max(3, int(cli_args.qdrant_candidate_limit))
        if getattr(cli_args, "qdrant_disable_reads", False):
            s.qdrant_enable_reads = False
        if getattr(cli_args, "qdrant_disable_writes", False):
            s.qdrant_enable_writes = False
        if getattr(cli_args, "qdrant_enable_rerank", False):
            s.qdrant_rerank_enabled = True
        if getattr(cli_args, "qdrant_disable_rerank", False):
            s.qdrant_rerank_enabled = False
        if getattr(cli_args, "qdrant_read_only", False):
            s.qdrant_enable_reads = True
            s.qdrant_enable_writes = False
        if getattr(cli_args, "qdrant_inspect", False):
            s.qdrant_admin_action = "inspect"
        if getattr(cli_args, "qdrant_clear", False):
            s.qdrant_admin_action = "clear"

    # ── Ensure output directories exist ─────────────────────────────────
    import os

    os.makedirs(s.output_dir, exist_ok=True)
    os.makedirs(s.user_data_root, exist_ok=True)
    os.makedirs(s.run_user_data_dir, exist_ok=True)

    return s


def validate_runtime_configuration(settings: Settings) -> None:
    if settings.max_steps_per_worker > settings.max_steps:
        raise ValueError(
            "MAX_STEPS_PER_WORKER must be less than or equal to MAX_STEPS "
            f"(got MAX_STEPS_PER_WORKER={settings.max_steps_per_worker}, MAX_STEPS={settings.max_steps})."
        )
    if settings.retry_base_delay_seconds <= 0.0:
        raise ValueError("RETRY_BASE_DELAY_SECONDS must be greater than 0.")
    retry_settings = {
        "WORKER_NAVIGATION_RETRIES": settings.worker_navigation_retries,
        "WORKER_QDRANT_INIT_RETRIES": settings.worker_qdrant_init_retries,
        "WORKER_BOUNDARY_RECOVERY_RETRIES": settings.worker_boundary_recovery_retries,
    }
    for setting_name, setting_value in retry_settings.items():
        if setting_value > MAX_ALLOWED_RETRIES:
            raise ValueError(
                f"{setting_name} must be less than or equal to {MAX_ALLOWED_RETRIES} " f"(got {setting_value})."
            )
    if settings.retry_base_delay_seconds > MAX_ALLOWED_RETRY_BASE_DELAY_SECONDS:
        raise ValueError(
            "RETRY_BASE_DELAY_SECONDS is too high for safe runtime defaults; "
            f"must be <= {MAX_ALLOWED_RETRY_BASE_DELAY_SECONDS} "
            f"(got {settings.retry_base_delay_seconds})."
        )
    if settings.redis_path_lock_ttl_seconds < 1 or settings.redis_path_lock_ttl_seconds > 300:
        raise ValueError(
            "REDIS_PATH_LOCK_TTL_SECONDS must be between 1 and 300 seconds "
            f"(got {settings.redis_path_lock_ttl_seconds})."
        )


# ── Graceful shutdown coordination ─────────────────────────────────────────────

SHUTDOWN_EVENT: asyncio.Event = asyncio.Event()
GRACEFUL_SHUTDOWN_REQUESTED: bool = False


def _request_graceful_shutdown(signum: int, frame: Any) -> None:
    """Signal handler that requests a graceful shutdown."""
    global GRACEFUL_SHUTDOWN_REQUESTED
    if GRACEFUL_SHUTDOWN_REQUESTED:
        signal.default_int_handler()
        return

    GRACEFUL_SHUTDOWN_REQUESTED = True
    print("\n🛑 Graceful shutdown requested (signal {}). Finishing in-flight steps...".format(signum))
    try:
        loop = asyncio.get_event_loop()
        loop.call_soon_threadsafe(SHUTDOWN_EVENT.set)
    except Exception:
        try:
            SHUTDOWN_EVENT.set()
        except Exception:
            pass


def _register_graceful_shutdown_signals() -> None:
    """Register SIGINT/SIGTERM handlers for graceful shutdown."""
    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGINT, _request_graceful_shutdown)
        loop.add_signal_handler(signal.SIGTERM, _request_graceful_shutdown)
    except NotImplementedError:
        signal.signal(signal.SIGINT, _request_graceful_shutdown)
        try:
            signal.signal(signal.SIGTERM, _request_graceful_shutdown)
        except Exception:
            pass


# ── Utility helpers ────────────────────────────────────────────────────────────


def normalize_action_plan(raw_plan: Any) -> Dict[str, Any]:
    if not isinstance(raw_plan, dict):
        return {"action": "scroll", "target": "", "value": "", "action_strategy": "", "input_payloads": []}

    action = str(raw_plan.get("action", "scroll")).strip().lower()
    if action not in ALLOWED_ACTIONS:
        action = "scroll"

    target = raw_plan.get("target", "")
    value = raw_plan.get("value", "")
    if target is None:
        target = ""
    if value is None:
        value = ""

    strategy = str(raw_plan.get("action_strategy", "")).strip().upper()
    if strategy not in {"HAPPY_UPSERT", "EDGE_CASE_FUZZ"}:
        strategy = ""

    payloads = raw_plan.get("input_payloads", [])
    if not isinstance(payloads, list):
        payloads = []
    normalized_payloads = []
    for p in payloads:
        if isinstance(p, dict):
            normalized_payloads.append(
                {"target": str(p.get("target", "")), "value": str(p.get("value", "")), "reason": str(p.get("reason", ""))}
            )

    # Cognitive Testing Personas - optional fields (backward compatible)
    persona_intent = raw_plan.get("persona_intent", "")
    if persona_intent is None:
        persona_intent = ""
    expected_reaction = raw_plan.get("expected_reaction", "")
    if expected_reaction is None:
        expected_reaction = ""

    # Structured Thinking Reasoning Fields (Phase B — Intent → Strategy Reference → Execution Target)
    reasoning_obj = raw_plan.get("reasoning", {})
    if not isinstance(reasoning_obj, dict):
        reasoning_obj = {}
    reasoning_intent = reasoning_obj.get("intent", "")
    if reasoning_intent is None:
        reasoning_intent = ""
    reasoning_strategy_ref = reasoning_obj.get("strategy_reference", "")
    if reasoning_strategy_ref is None:
        reasoning_strategy_ref = ""
    reasoning_target_justification = reasoning_obj.get("target_justification", "")
    if reasoning_target_justification is None:
        reasoning_target_justification = ""

    return {
        "action": action,
        "target": str(target),
        "value": str(value),
        "action_strategy": strategy,
        "input_payloads": normalized_payloads,
        "persona_intent": str(persona_intent),
        "expected_reaction": str(expected_reaction),
        "reasoning_intent": str(reasoning_intent),
        "reasoning_strategy_reference": str(reasoning_strategy_ref),
        "reasoning_target_justification": str(reasoning_target_justification),
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


def build_redis_key(redis_prefix: str, base_key: str) -> str:
    """Prepend the configured REDIS_PREFIX to a Redis key name."""
    if redis_prefix:
        return f"{redis_prefix}{base_key}"
    return base_key


# ── Dataclasses ────────────────────────────────────────────────────────────────


@dataclass
class FormControlRecord:
    """Structured metadata for a single form control extracted from the DOM."""

    control_id: int
    form_id: Optional[str]
    tag_name: str
    input_type: str
    name_attr: str
    id_attr: str
    placeholder: str
    aria_label: str
    aria_labelledby: str
    required: bool
    disabled: bool
    readonly: bool
    minlength: Optional[int]
    maxlength: Optional[int]
    pattern: str
    min_value: str
    max_value: str
    step: str
    resolved_label: str
    label_confidence: float
    semantic_kind: str
    visible: bool = True
    options: List[str] = field(default_factory=list)


@dataclass
class FormRecord:
    """Structured metadata for a single form and its associated controls."""

    form_id: str
    action: str
    method: str
    control_ids: List[int] = field(default_factory=list)
    submit_candidate_id: Optional[int] = None


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
    form_controls: List[FormControlRecord] = field(default_factory=list)
    forms: List[FormRecord] = field(default_factory=list)


@dataclass
class WorkerRunResult:
    worker_id: int
    allocated_steps: int
    completed_steps: int
    logs: List[Dict[str, Any]]
    defects: "DefectTracker"  # noqa: F821
    network_injections: List[Dict[str, Any]]
    launch_info: Dict[str, Any]


# ── Defect Ticket for Remediation Blueprints ───────────────────────────────────


@dataclass
class DefectTicket:
    """Structured engineering defect ticket with remediation blueprint.

    Compiles raw defects into actionable cards optimized for both human review
    (scannable Markdown/PDF) and machine ingestion by coding agents
    (structured JSON spec blocks).
    """

    # Identity & severity
    defect_uid: str  # e.g., "DEFECT-001"
    category: str  # e.g., "security_risks", "context_anomalies"
    severity: str  # CRITICAL, HIGH, MEDIUM, LOW
    title: str  # concise human-readable defect name

    # Narrative description
    description: str = ""

    # Context triangulation
    target_url: str = ""
    page_state_name: str = ""
    target_selector: str = ""
    html_snippet: str = ""

    # Reproduction sequence (list of step dicts)
    reproduction_steps: List[Dict[str, Any]] = field(default_factory=list)

    # Visual proofs
    before_screenshot: Optional[str] = None
    after_screenshot: Optional[str] = None
    expected_screenshot: Optional[str] = None

    # Root cause & remediation
    root_cause_analysis: str = ""
    remediation_instruction: str = ""

    # Raw defect payload (for reference)
    raw_defects: List[Dict[str, Any]] = field(default_factory=list)

    # Agent-discovered metadata
    impact: str = ""  # e.g., "Security Risk / Data Pollution"
    discovered_context_url: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Return a flat dictionary suitable for JSON serialization."""
        return {
            "defect_uid": self.defect_uid,
            "category": self.category,
            "severity": self.severity,
            "title": self.title,
            "description": self.description,
            "impact": self.impact,
            "target_url": self.target_url,
            "page_state_name": self.page_state_name,
            "discovered_context_url": self.discovered_context_url,
            "target_selector": self.target_selector,
            "html_snippet": self.html_snippet,
            "reproduction_steps": self.reproduction_steps,
            "before_screenshot": self.before_screenshot,
            "after_screenshot": self.after_screenshot,
            "expected_screenshot": self.expected_screenshot,
            "root_cause_analysis": self.root_cause_analysis,
            "remediation_instruction": self.remediation_instruction,
        }

    @property
    def spec_block(self) -> Dict[str, Any]:
        """Build the Remediation Spec Block for machine ingestion by coding agents.

        Structured as a JSON-ready map with defect_type, target_element schema,
        reproduction_sequence, root_cause_analysis, and remediation_instruction.
        """
        # Derive target_element from selector + html_snippet
        target_element: Dict[str, Any] = {}
        if self.target_selector:
            target_element["selector"] = self.target_selector
        if self.html_snippet:
            # Extract tag and attributes from snippet for agent parsing
            import re as _re

            tag_match = _re.search(r"<(\w+)", self.html_snippet[:200])
            if tag_match:
                target_element["tag"] = tag_match.group(1)
            attr_matches = _re.findall(
                r'(\w+)\s*=\s*(?:&quot;|")([^"&]*)?(?:&quot;|")',
                self.html_snippet[:500],
            )
            if attr_matches:
                target_element["attributes"] = dict(attr_matches)

        return {
            "defect_type": self.category.replace("_", ""),
            "severity": self.severity,
            "target_element": target_element,
            "target_url": self.target_url,
            "page_state_name": self.page_state_name,
            "reproduction_sequence": [
                {
                    "step": s.get("step", i + 1),
                    "action": s.get("action", ""),
                    "selector": s.get("target", ""),
                    "value": s.get("value", ""),
                    "url": s.get("url", ""),
                }
                for i, s in enumerate(self.reproduction_steps)
            ],
            "root_cause_analysis": self.root_cause_analysis,
            "remediation_instruction": self.remediation_instruction,
        }

    def to_markdown(self) -> str:
        """Render this ticket as a Remediation Blueprint card in Markdown."""
        lines = []
        sev_icon = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "⚠️ ", "LOW": "ℹ️ "}.get(
            self.severity, "⚪"
        )

        lines.append(f"## [{self.severity}] {sev_icon} {self.defect_uid}: {self.title}")
        if self.impact:
            lines.append(f"- **Impact:** {self.impact}")
        if self.target_selector:
            lines.append(f"- **Target Selector:** `{self.target_selector}`")
        if self.target_url:
            lines.append(f"- **Discovered Context:** {self.target_url}")
        if self.page_state_name:
            lines.append(f"- **Page State:** {self.page_state_name}")

        # Reproduction steps
        if self.reproduction_steps:
            lines.append("")
            lines.append("**Reproduction Steps:**")
            for s in self.reproduction_steps:
                step_num = s.get("step", "?")
                action = s.get("action", "")
                target = s.get("target", "")
                value = s.get("value", "")
                url = s.get("url", "")
                line = f"{step_num}. `{action}"
                if target:
                    line += f" on `{target}`"
                if value:
                    line += f" value=`{value[:60]}`"
                line += "`"
                if url:
                    line += f" — {url}"
                lines.append(f"- {line}")

        # Root cause analysis
        if self.root_cause_analysis:
            lines.append("")
            lines.append(f"**Root Cause Analysis:** {self.root_cause_analysis}")

        # Remediation instruction
        if self.remediation_instruction:
            lines.append("")
            lines.append(f"**Remediation Instruction:** {self.remediation_instruction}")

        # Visual proofs
        screenshots = []
        for label, path in [
            ("Before", self.before_screenshot),
            ("After", self.after_screenshot),
            ("Expected", self.expected_screenshot),
        ]:
            if path:
                screenshots.append(f"`!{label} [{path}](./{path})`")
        if screenshots:
            lines.append("")
            lines.append("**Visual Proofs:** " + ", ".join(screenshots))

        # Machine-readable spec block (JSON)
        lines.append("")
        lines.append("```json")
        lines.append(json_dumps(self.spec_block, indent=2))
        lines.append("```")

        return "\n".join(lines)


# Ensure json.dumps is available for to_markdown()
import json as _json_module

json_dumps = _json_module.dumps


# ── Defect normalization (used by core.py DefectTracker) ───────────────────────


def _normalize_defect(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize any ad-hoc defect payload into the canonical five-field schema."""
    out = dict(payload)

    if "selector" not in out or not out["selector"]:
        for candidate_key in ("target", "broken_selectors"):
            val = out.get(candidate_key)
            if isinstance(val, list) and val:
                out["selector"] = ", ".join(str(v) for v in val[:5])
                break
            elif isinstance(val, str) and val.strip():
                out["selector"] = val.strip()
                break
    if not out.get("selector"):
        out["selector"] = "(none)"

    if "html_snippet" not in out or not out["html_snippet"]:
        for candidate_key in ("payload_preview",):
            val = out.get(candidate_key)
            if isinstance(val, str) and val.strip():
                out["html_snippet"] = val.strip()
                break
    if not out.get("html_snippet"):
        out["html_snippet"] = ""

    if "failure_reason" not in out or not out["failure_reason"]:
        for candidate_key in ("description", "message", "error"):
            val = out.get(candidate_key)
            if isinstance(val, str) and val.strip():
                out["failure_reason"] = val.strip()
                break
    if not out.get("failure_reason"):
        parts: List[str] = []
        if out.get("type"):
            parts.append(str(out["type"]))
        if out.get("severity"):
            parts.append(str(out["severity"]))
        out["failure_reason"] = ": ".join(parts) if parts else "defect detected"

    if "remediation_advice" not in out or not out["remediation_advice"]:
        for candidate_key in ("remediation", "help", "failureSummary"):
            val = out.get(candidate_key)
            if isinstance(val, str) and val.strip():
                out["remediation_advice"] = val.strip()
                break
    if not out.get("remediation_advice"):
        out["remediation_advice"] = "Manual review required."

    if "screenshot_path" not in out or not out["screenshot_path"]:
        for candidate_key in ("diff_image",):
            val = out.get(candidate_key)
            if isinstance(val, str) and val.strip():
                out["screenshot_path"] = val.strip()
                break
    if not out.get("screenshot_path"):
        out["screenshot_path"] = ""

    return out


# ── Service logging helper ─────────────────────────────────────────────────────


def _local_service_log(message: str, output_dir: str = "") -> None:
    timestamp = datetime.now().isoformat(timespec="seconds")
    log_line = f"[{timestamp}] {message}"
    print("\u26a0\ufe0f", log_line)
    if output_dir:
        try:
            import os

            log_path = os.path.join(output_dir, "service_connectivity.log")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(log_line + "\n")
        except Exception:
            pass


def split_domain_and_route(url: str) -> tuple[str, str]:
    try:
        parsed = urlparse(url)
    except Exception:
        return "", "/"

    domain = (parsed.netloc or "").lower().strip()
    route = parsed.path or "/"
    if parsed.query:
        route = f"{route}?{parsed.query}"
    return domain, route
