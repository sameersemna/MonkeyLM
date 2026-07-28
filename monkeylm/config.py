"""Configuration, constants, CLI argument parsing, and utility helpers for MonkeyLM.

All structured dataclasses (Settings, PageSnapshot, DefectTicket, etc.) now
live in :mod:`monkeylm.types` to avoid circular imports. This module provides
the runtime machinery: env loading, CLI parsing, validation, and helpers.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import shutil
import signal
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from monkeylm.types import (
    FormControlRecord,
    FormRecord,
    PageSnapshot,
    PersonaGoal,
    CriticalFlow,
    TestingStrategy,
    Settings,
    WorkerRunResult,
    DefectTicket,
)


# ── Optional third-party imports ───────────────────────────────────────────────


def _optional_import(module_name: str, attr_name: Optional[str] = None):
    """Import a module or submodule, returning ``None`` on any failure.

    Supports dotted names like ``"PIL.Image"`` so that packages with lazy
    submodule imports (e.g., Pillow 11+) can be loaded correctly. When
    ``module_name`` is a package and ``attr_name`` is given, the full
    ``module_name.attr_name`` path is imported to bypass lazy-loading
    inconsistencies, then the attribute is resolved.
    """
    try:
        import importlib

        if attr_name is None:
            return importlib.import_module(module_name)

        # First try the lazy-friendly path: import the full dotted name so
        # Pillow 11+ (and similar) actually loads the submodule.
        try:
            full = importlib.import_module(f"{module_name}.{attr_name}")
            return getattr(full, attr_name, full)
        except Exception:
            pass

        # Fallback: import the parent and look up the attribute (matches
        # the legacy behavior for packages that expose submodules eagerly).
        module = importlib.import_module(module_name)
        return getattr(module, attr_name, module)
    except Exception:
        return None


def _load_dotenv() -> None:
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

Faker = _optional_import("faker", "Faker")
# ``PIL_Image`` is the ``PIL.Image`` module (provides module-level helpers like
# ``Image.open``); ``Image`` is the class itself (provides ``Image.new`` etc.).
# ``PIL_ImageDraw`` and ``ImageDraw`` follow the same module/class split for
# ``ImageDraw.Draw``. Both forms are needed because Pillow splits helpers and
# the corresponding class between the module and the class.
PIL_Image = _optional_import("PIL.Image")
Image = _optional_import("PIL.Image", "Image") or PIL_Image
PIL_ImageDraw = _optional_import("PIL.ImageDraw")
ImageDraw = _optional_import("PIL.ImageDraw", "ImageDraw") or PIL_ImageDraw
PIL_ImageFont = _optional_import("PIL.ImageFont")
pil_pixelmatch = _optional_import("pixelmatch.contrib.PIL", "pixelmatch")
asyncpg = _optional_import("asyncpg")
redis_asyncio = _optional_import("redis.asyncio")
httpx = _optional_import("httpx")

try:
    _REPORTLAB_AVAILABLE = True
except Exception:
    _REPORTLAB_AVAILABLE = False

# ── Default constants ──────────────────────────────────────────────────────────

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
DEFAULT_STEP_TIMEOUT_SECONDS = 30.0
DEFAULT_STUCK_STATE_THRESHOLD = 6
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
DEFAULT_QDRANT_EMBEDDING_LITELLM_BASE_URL = "http://localhost:11435"
DEFAULT_QDRANT_EMBEDDING_LITELLM_API_KEY = ""
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
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _env_to_float(value: Any, default: float) -> float:
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
    parser.add_argument(
        "--step-timeout-seconds",
        type=float,
        default=None,
        help="Per-step timeout for the worker loop before the run fails fast",
    )
    parser.add_argument(
        "--stuck-state-threshold",
        type=int,
        default=None,
        help="Consecutive repeated-state steps before a stuck-state failure is declared",
    )
    parser.add_argument("--seed", type=int, help="Random seed for deterministic test replay")
    parser.add_argument(
        "--inspect-runtime",
        action="store_true",
        help="Inspect optional runtime dependencies and exit without starting the monkey run",
    )
    parser.add_argument(
        "--inspect-runtime-json",
        action="store_true",
        help="Emit runtime dependency inspection as JSON and exit without starting the monkey run",
    )
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
        "--qdrant-embedding-provider", choices=["hash", "ollama", "litellm"], help="Embedding backend for Qdrant vectors"
    )
    parser.add_argument("--qdrant-embedding-model", help="Embedding model name, e.g. nomic-embed-text or ollama/nomic-embed-text for litellm")
    parser.add_argument("--qdrant-embedding-litellm-base-url", help="LiteLLM base URL for the litellm embedding provider")
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
    from dotenv import dotenv_values

    project_root = Path(__file__).resolve().parent.parent
    env_path = project_root / ".env"
    env_vars = {}
    if env_path.is_file():
        env_vars = dotenv_values(env_path)

    s = Settings()

    raw_target: str = env_vars.get("TARGET_URL") or os.getenv("TARGET_URL") or s.target_url
    s.target_url = raw_target
    raw_ollama: str = env_vars.get("OLLAMA_MODEL") or os.getenv("OLLAMA_MODEL") or s.ollama_model
    s.ollama_model = raw_ollama
    ollama_timeout = _env_float("OLLAMA_TIMEOUT_SECONDS", s.ollama_timeout_seconds)
    s.ollama_timeout_seconds = max(1.0, _env_to_float(env_vars.get("OLLAMA_TIMEOUT_SECONDS"), ollama_timeout))

    raw_max_steps = env_vars.get("MAX_STEPS")
    s.max_steps = max(1, int(raw_max_steps) if raw_max_steps is not None else s.max_steps)

    raw_workers = env_vars.get("WORKERS")
    s.workers = max(1, int(raw_workers) if raw_workers is not None else s.workers)

    raw_mspw = env_vars.get("MAX_STEPS_PER_WORKER")
    s.max_steps_per_worker = max(1, int(raw_mspw) if raw_mspw is not None else min(s.max_steps, DEFAULT_MAX_STEPS_PER_WORKER))

    raw_wnr = env_vars.get("WORKER_NAVIGATION_RETRIES")
    s.worker_navigation_retries = max(0, int(raw_wnr) if raw_wnr is not None else s.worker_navigation_retries)
    raw_wqi = env_vars.get("WORKER_QDRANT_INIT_RETRIES")
    s.worker_qdrant_init_retries = max(0, int(raw_wqi) if raw_wqi is not None else s.worker_qdrant_init_retries)
    raw_wbr = env_vars.get("WORKER_BOUNDARY_RECOVERY_RETRIES")
    s.worker_boundary_recovery_retries = max(0, int(raw_wbr) if raw_wbr is not None else s.worker_boundary_recovery_retries)

    raw_rbd = env_vars.get("RETRY_BASE_DELAY_SECONDS")
    s.retry_base_delay_seconds = max(0.1, float(raw_rbd) if raw_rbd is not None else s.retry_base_delay_seconds)
    raw_step_timeout = env_vars.get("STEP_TIMEOUT_SECONDS")
    s.step_timeout_seconds = max(1.0, float(raw_step_timeout) if raw_step_timeout is not None else s.step_timeout_seconds)
    raw_stuck_threshold = env_vars.get("STUCK_STATE_THRESHOLD")
    s.stuck_state_threshold = max(2, int(raw_stuck_threshold) if raw_stuck_threshold is not None else s.stuck_state_threshold)
    ev_headless = env_vars.get("HEADLESS")
    s.headless = _env_bool("HEADLESS", default=_env_to_bool(ev_headless, s.headless))
    raw_bws = env_vars.get("BROWSER_WINDOW_SIZE") or os.getenv("BROWSER_WINDOW_SIZE") or s.browser_window_size
    s.browser_window_size = _normalize_window_size(raw_bws)
    ev_nv = env_vars.get("NO_VIEWPORT")
    s.no_viewport = _env_bool("NO_VIEWPORT", default=_env_to_bool(ev_nv, s.no_viewport))
    s.postgres_dsn = _env_str("POSTGRES_DSN", env_vars.get("POSTGRES_DSN") or s.postgres_dsn)
    s.redis_url = _env_str("REDIS_URL", env_vars.get("REDIS_URL") or s.redis_url)
    s.redis_prefix = _env_str("REDIS_PREFIX", env_vars.get("REDIS_PREFIX") or s.redis_prefix)
    ev_rp = env_vars.get("REDIS_PATH_LOCK_TTL_SECONDS")
    s.redis_path_lock_ttl_seconds = max(1, int(ev_rp) if ev_rp is not None else s.redis_path_lock_ttl_seconds)
    s.golden_baseline_mode = _env_str("GOLDEN_BASELINE_MODE", env_vars.get("GOLDEN_BASELINE_MODE") or s.golden_baseline_mode).lower()
    ev_sp = env_vars.get("STRICT_PERSISTENCE")
    s.strict_persistence = _env_bool("STRICT_PERSISTENCE", default=_env_to_bool(ev_sp, s.strict_persistence))
    ev_rsttl = env_vars.get("REDIS_STATE_TTL_SECONDS")
    s.redis_state_ttl_seconds = max(60, int(ev_rsttl) if ev_rsttl is not None else s.redis_state_ttl_seconds)
    s.qdrant_url = _env_str("QDRANT_URL", env_vars.get("QDRANT_URL") or s.qdrant_url)
    s.qdrant_collection = _env_str("QDRANT_COLLECTION", env_vars.get("QDRANT_COLLECTION") or s.qdrant_collection)
    ev_qvs = env_vars.get("QDRANT_VECTOR_SIZE")
    s.qdrant_vector_size = max(64, int(ev_qvs) if ev_qvs is not None else s.qdrant_vector_size)
    ev_ger = env_vars.get("QDRANT_ENABLE_READS")
    s.qdrant_enable_reads = _env_bool("QDRANT_ENABLE_READS", default=_env_to_bool(ev_ger, s.qdrant_enable_reads))
    ev_gew = env_vars.get("QDRANT_ENABLE_WRITES")
    s.qdrant_enable_writes = _env_bool("QDRANT_ENABLE_WRITES", default=_env_to_bool(ev_gew, s.qdrant_enable_writes))
    s.qdrant_embedding_provider = _env_str("QDRANT_EMBEDDING_PROVIDER", env_vars.get("QDRANT_EMBEDDING_PROVIDER") or s.qdrant_embedding_provider).lower()
    s.qdrant_embedding_model = _env_str("QDRANT_EMBEDDING_MODEL", env_vars.get("QDRANT_EMBEDDING_MODEL") or s.qdrant_embedding_model)
    s.qdrant_embedding_litellm_base_url = _env_str("QDRANT_EMBEDDING_LITELLM_BASE_URL", env_vars.get("QDRANT_EMBEDDING_LITELLM_BASE_URL") or s.qdrant_embedding_litellm_base_url).rstrip("/")
    s.qdrant_embedding_litellm_api_key = _env_str("QDRANT_EMBEDDING_LITELLM_API_KEY", env_vars.get("QDRANT_EMBEDDING_LITELLM_API_KEY") or s.qdrant_embedding_litellm_api_key)
    s.qdrant_admin_action = _env_str("QDRANT_ADMIN_ACTION", env_vars.get("QDRANT_ADMIN_ACTION") or "").lower()
    ev_qre = env_vars.get("QDRANT_RERANK_ENABLED")
    s.qdrant_rerank_enabled = _env_bool("QDRANT_RERANK_ENABLED", default=_env_to_bool(ev_qre, s.qdrant_rerank_enabled))
    s.qdrant_rerank_model = _env_str("QDRANT_RERANK_MODEL", env_vars.get("QDRANT_RERANK_MODEL") or s.qdrant_rerank_model)
    ev_qcl = env_vars.get("QDRANT_CANDIDATE_LIMIT")
    s.qdrant_candidate_limit = max(3, int(ev_qcl) if ev_qcl is not None else s.qdrant_candidate_limit)
    ev_pg = env_vars.get("PDF_GENERATE")
    s.pdf_generate = _env_bool("PDF_GENERATE", default=_env_to_bool(ev_pg, s.pdf_generate))
    s.pdf_vision_model = _env_str("PDF_VISION_MODEL", env_vars.get("PDF_VISION_MODEL") or s.pdf_vision_model)
    s.vision_model = _env_str("VISION_MODEL", env_vars.get("VISION_MODEL") or s.vision_model)
    ev_pvt = env_vars.get("PDF_VISION_TIMEOUT_SECONDS")
    s.pdf_vision_timeout_seconds = max(1.0, float(ev_pvt) if ev_pvt is not None else s.pdf_vision_timeout_seconds)
    s.strict_sandbox = _env_bool("STRICT_SANDBOX", default=_env_to_bool(env_vars.get("STRICT_SANDBOX"), s.strict_sandbox))
    s.allow_no_sandbox_fallback = _env_bool("ALLOW_NO_SANDBOX_FALLBACK", default=_env_to_bool(env_vars.get("ALLOW_NO_SANDBOX_FALLBACK"), s.allow_no_sandbox_fallback))

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
        if getattr(cli_args, "step_timeout_seconds", None) is not None:
            s.step_timeout_seconds = max(1.0, float(cli_args.step_timeout_seconds))
        if getattr(cli_args, "stuck_state_threshold", None) is not None:
            s.stuck_state_threshold = max(2, int(cli_args.stuck_state_threshold))
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
        if getattr(cli_args, "qdrant_embedding_litellm_base_url", None):
            s.qdrant_embedding_litellm_base_url = cli_args.qdrant_embedding_litellm_base_url.strip().rstrip("/")
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

    os.makedirs(s.output_dir, exist_ok=True)
    os.makedirs(s.user_data_root, exist_ok=True)
    os.makedirs(s.run_user_data_dir, exist_ok=True)

    return s


def inspect_optional_runtime_dependencies() -> Dict[str, Dict[str, Any]]:
    """Check for optional runtime tools and Python packages that support the harness."""
    report: Dict[str, Dict[str, Any]] = {}

    python_bin = shutil.which("python") or shutil.which("python3")
    if python_bin:
        report["python"] = {
            "status": "ok",
            "path": python_bin,
            "detail": "Python runtime is available for the harness entrypoint.",
        }
    else:
        report["python"] = {
            "status": "missing",
            "path": None,
            "detail": "Python interpreter was not found on PATH.",
        }

    node_bin = shutil.which("node")
    if node_bin:
        report["node"] = {
            "status": "ok",
            "path": node_bin,
            "detail": "Node.js is available for optional pixelmatch visual diff fallback.",
        }
    else:
        report["node"] = {
            "status": "missing",
            "path": None,
            "detail": "Node.js is not installed; visual diff fallback will rely on Python-only paths.",
        }

    for module_name in ("playwright", "dotenv"):
        try:
            import importlib

            importlib.import_module(module_name)
            report[module_name] = {
                "status": "ok",
                "path": None,
                "detail": f"Python package '{module_name}' is importable.",
            }
        except Exception as exc:  # pragma: no cover - import availability varies by environment
            report[module_name] = {
                "status": "missing",
                "path": None,
                "detail": f"Python package '{module_name}' is not importable: {exc}",
            }

    return report


def validate_runtime_configuration(settings: Settings) -> None:
    dependency_report = inspect_optional_runtime_dependencies()
    if dependency_report.get("node", {}).get("status") != "ok":
        print(
            "⚠️ Optional runtime dependency check: Node.js not found on PATH; visual diff fallback may be limited."
        )

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
    global GRACEFUL_SHUTDOWN_REQUESTED
    if GRACEFUL_SHUTDOWN_REQUESTED:
        signal.default_int_handler(signum, frame)
        return

    GRACEFUL_SHUTDOWN_REQUESTED = True
    print("\n\U0001f6d1 Graceful shutdown requested (signal {}). Finishing in-flight steps...".format(signum))
    try:
        loop = asyncio.get_event_loop()
        loop.call_soon_threadsafe(SHUTDOWN_EVENT.set)
    except Exception:
        try:
            SHUTDOWN_EVENT.set()
        except Exception:
            pass


def _register_graceful_shutdown_signals() -> None:
    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGINT, lambda: _request_graceful_shutdown(signal.SIGINT, None))
        loop.add_signal_handler(signal.SIGTERM, lambda: _request_graceful_shutdown(signal.SIGTERM, None))
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

    persona_intent = raw_plan.get("persona_intent", "")
    if persona_intent is None:
        persona_intent = ""
    expected_reaction = raw_plan.get("expected_reaction", "")
    if expected_reaction is None:
        expected_reaction = ""

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
    try:
        current = urlparse(current_url)
        target = urlparse(target_url)
    except Exception:
        return False

    if not current.netloc or not target.netloc:
        return False

    return current.netloc.lower() == target.netloc.lower()


def build_redis_key(redis_prefix: str, base_key: str) -> str:
    if redis_prefix:
        return f"{redis_prefix}{base_key}"
    return base_key


# ── Defect normalization (used by core.py DefectTracker) ─────────────────────


def _normalize_defect(payload: Dict[str, Any]) -> Dict[str, Any]:
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


# ── Runtime override globals (for backward-compatible CLI usage) ──────────────

import logging as _logging

_logger = _logging.getLogger("monkeylm.config")

ACTIVE_SEED: Optional[str] = None
TARGET_URL: str = DEFAULT_TARGET_URL
OLLAMA_MODEL: str = DEFAULT_OLLAMA_MODEL
OLLAMA_TIMEOUT_SECONDS: float = DEFAULT_OLLAMA_TIMEOUT_SECONDS
VISION_MODEL: str = DEFAULT_VISION_MODEL
MAX_STEPS: int = DEFAULT_MAX_STEPS
MAX_STEPS_PER_WORKER: int = DEFAULT_MAX_STEPS_PER_WORKER
WORKERS: int = DEFAULT_WORKERS
WORKER_NAVIGATION_RETRIES: int = DEFAULT_WORKER_NAVIGATION_RETRIES
WORKER_QDRANT_INIT_RETRIES: int = DEFAULT_WORKER_QDRANT_INIT_RETRIES
WORKER_BOUNDARY_RECOVERY_RETRIES: int = DEFAULT_WORKER_BOUNDARY_RECOVERY_RETRIES
RETRY_BASE_DELAY_SECONDS: float = DEFAULT_RETRY_BASE_DELAY_SECONDS
HEADLESS: bool = DEFAULT_HEADLESS
BROWSER_WINDOW_SIZE: str = DEFAULT_WINDOW_SIZE
NO_VIEWPORT: bool = DEFAULT_NO_VIEWPORT
POSTGRES_DSN: str = DEFAULT_POSTGRES_DSN
REDIS_URL: str = DEFAULT_REDIS_URL
REDIS_PREFIX: str = DEFAULT_REDIS_PREFIX
REDIS_PATH_LOCK_TTL_SECONDS: int = DEFAULT_REDIS_PATH_LOCK_TTL_SECONDS
GOLDEN_BASELINE_MODE: str = DEFAULT_GOLDEN_BASELINE_MODE
STRICT_PERSISTENCE: bool = DEFAULT_STRICT_PERSISTENCE
QDRANT_URL: str = DEFAULT_QDRANT_URL
QDRANT_COLLECTION: str = DEFAULT_QDRANT_COLLECTION
QDRANT_ENABLE_READS: bool = DEFAULT_QDRANT_ENABLE_READS
QDRANT_ENABLE_WRITES: bool = DEFAULT_QDRANT_ENABLE_WRITES
QDRANT_EMBEDDING_PROVIDER: str = DEFAULT_QDRANT_EMBEDDING_PROVIDER
QDRANT_EMBEDDING_MODEL: str = DEFAULT_QDRANT_EMBEDDING_MODEL
QDRANT_EMBEDDING_LITELLM_BASE_URL: str = DEFAULT_QDRANT_EMBEDDING_LITELLM_BASE_URL
QDRANT_EMBEDDING_LITELLM_API_KEY: str = DEFAULT_QDRANT_EMBEDDING_LITELLM_API_KEY
QDRANT_RERANK_ENABLED: bool = DEFAULT_QDRANT_RERANK_ENABLED
QDRANT_RERANK_MODEL: str = DEFAULT_QDRANT_RERANK_MODEL
QDRANT_CANDIDATE_LIMIT: int = DEFAULT_QDRANT_CANDIDATE_LIMIT
QDRANT_ADMIN_ACTION: str = ""
OUTPUT_DIR: str = f"reports/testrun_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
RUN_USER_DATA_DIR: str = f"playwright_user_data/session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

_RUNTIME_GLOBAL_SCHEMA: Dict[str, Any] = {
    "TARGET_URL": str,
    "OLLAMA_MODEL": str,
    "OLLAMA_TIMEOUT_SECONDS": (int, float),
    "VISION_MODEL": str,
    "MAX_STEPS": int,
    "MAX_STEPS_PER_WORKER": int,
    "WORKERS": int,
    "WORKER_NAVIGATION_RETRIES": int,
    "WORKER_QDRANT_INIT_RETRIES": int,
    "WORKER_BOUNDARY_RECOVERY_RETRIES": int,
    "RETRY_BASE_DELAY_SECONDS": (int, float),
    "HEADLESS": bool,
    "BROWSER_WINDOW_SIZE": str,
    "NO_VIEWPORT": bool,
    "POSTGRES_DSN": str,
    "REDIS_URL": str,
    "REDIS_PREFIX": str,
    "REDIS_PATH_LOCK_TTL_SECONDS": int,
    "GOLDEN_BASELINE_MODE": str,
    "STRICT_PERSISTENCE": bool,
    "QDRANT_URL": str,
    "QDRANT_COLLECTION": str,
    "QDRANT_EMBEDDING_PROVIDER": str,
    "QDRANT_EMBEDDING_MODEL": str,
    "QDRANT_EMBEDDING_LITELLM_BASE_URL": str,
    "QDRANT_EMBEDDING_LITELLM_API_KEY": str,
    "QDRANT_RERANK_ENABLED": bool,
    "QDRANT_RERANK_MODEL": str,
    "QDRANT_CANDIDATE_LIMIT": int,
    "QDRANT_ADMIN_ACTION": str,
    "QDRANT_ENABLE_READS": bool,
    "QDRANT_ENABLE_WRITES": bool,
}

_SENSITIVE_CONFIG_KEYS: frozenset = frozenset(["POSTGRES_DSN", "REDIS_URL"])
_POS_INT_KEYS: frozenset = frozenset([
    "MAX_STEPS", "MAX_STEPS_PER_WORKER", "WORKERS",
    "WORKER_NAVIGATION_RETRIES", "WORKER_QDRANT_INIT_RETRIES",
    "WORKER_BOUNDARY_RECOVERY_RETRIES", "REDIS_PATH_LOCK_TTL_SECONDS",
    "QDRANT_CANDIDATE_LIMIT",
])
_POS_FLOAT_KEYS: frozenset = frozenset(["OLLAMA_TIMEOUT_SECONDS", "RETRY_BASE_DELAY_SECONDS"])
_QDRANT_ADMIN_ACTIONS_ALLOWED: frozenset = frozenset(["", "inspect", "clear"])


def _safe_set_global(key: str, value: Any) -> None:
    if key not in _RUNTIME_GLOBAL_SCHEMA:
        _logger.warning("Runtime override rejected: key '%s' not in whitelist", key)
        return

    expected = _RUNTIME_GLOBAL_SCHEMA[key]
    if not isinstance(value, expected):
        try:
            coercer = expected[0] if isinstance(expected, tuple) else expected
            value = coercer(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Invalid type for {key}: expected {expected}, got {type(value).__name__}"
            ) from exc

    if key in _POS_INT_KEYS and value <= 0:
        raise ValueError(f"{key} must be a positive integer, got {value}")

    if key in _POS_FLOAT_KEYS and value <= 0:
        raise ValueError(f"{key} must be positive, got {value}")

    if key == "QDRANT_ADMIN_ACTION" and value not in _QDRANT_ADMIN_ACTIONS_ALLOWED:
        raise ValueError(
            f"QDRANT_ADMIN_ACTION must be one of {_QDRANT_ADMIN_ACTIONS_ALLOWED - {''}}, got {value!r}"
        )

    globals()[key] = value

    if key in _SENSITIVE_CONFIG_KEYS:
        _logger.warning("Sensitive config override applied: %s", key)


def apply_runtime_overrides(args: argparse.Namespace) -> None:
    if getattr(args, "seed", None) is not None:
        try:
            seed_val = int(args.seed)
        except (TypeError, ValueError):
            raise ValueError(f"seed must be an integer, got {args.seed!r}")
        global ACTIVE_SEED
        ACTIVE_SEED = str(seed_val)
        random.seed(seed_val)

    _SIMPLE_OVERRIDES: List[tuple] = [
        ("target_url", "TARGET_URL"),
        ("ollama_model", "OLLAMA_MODEL"),
        ("ollama_timeout_seconds", "OLLAMA_TIMEOUT_SECONDS"),
        ("vision_model", "VISION_MODEL"),
        ("max_steps", "MAX_STEPS"),
        ("max_steps_per_worker", "MAX_STEPS_PER_WORKER"),
        ("workers", "WORKERS"),
        ("worker_navigation_retries", "WORKER_NAVIGATION_RETRIES"),
        ("worker_qdrant_init_retries", "WORKER_QDRANT_INIT_RETRIES"),
        ("worker_boundary_recovery_retries", "WORKER_BOUNDARY_RECOVERY_RETRIES"),
        ("retry_base_delay_seconds", "RETRY_BASE_DELAY_SECONDS"),
        ("headless", "HEADLESS"),
        ("window_size", "BROWSER_WINDOW_SIZE"),
        ("no_viewport", "NO_VIEWPORT"),
        ("postgres_dsn", "POSTGRES_DSN"),
        ("redis_url", "REDIS_URL"),
        ("redis_prefix", "REDIS_PREFIX"),
        ("redis_path_lock_ttl_seconds", "REDIS_PATH_LOCK_TTL_SECONDS"),
        ("golden_baseline_mode", "GOLDEN_BASELINE_MODE"),
        ("strict_persistence", "STRICT_PERSISTENCE"),
    ]

    for attr, gkey in _SIMPLE_OVERRIDES:
        val = getattr(args, attr, None)
        if val is not None:
            _safe_set_global(gkey, val)

    _QDRANT_OVERRIDES: List[tuple] = [
        ("qdrant_url", "QDRANT_URL"),
        ("qdrant_collection", "QDRANT_COLLECTION"),
        ("qdrant_embedding_provider", "QDRANT_EMBEDDING_PROVIDER"),
        ("qdrant_embedding_model", "QDRANT_EMBEDDING_MODEL"),
        ("qdrant_embedding_litellm_base_url", "QDRANT_EMBEDDING_LITELLM_BASE_URL"),
        ("qdrant_enable_rerank", "QDRANT_RERANK_ENABLED"),
        ("qdrant_rerank_model", "QDRANT_RERANK_MODEL"),
        ("qdrant_candidate_limit", "QDRANT_CANDIDATE_LIMIT"),
    ]

    for attr, gkey in _QDRANT_OVERRIDES:
        val = getattr(args, attr, None)
        if val is not None:
            _safe_set_global(gkey, val)

    admin_action = getattr(args, "qdrant_admin_action", None)
    if admin_action is not None:
        _safe_set_global("QDRANT_ADMIN_ACTION", admin_action)
    elif getattr(args, "qdrant_inspect", False):
        globals()["QDRANT_ADMIN_ACTION"] = "inspect"
    elif getattr(args, "qdrant_clear", False):
        globals()["QDRANT_ADMIN_ACTION"] = "clear"

    if getattr(args, "qdrant_disable_reads", False):
        globals()["QDRANT_ENABLE_READS"] = False
    if getattr(args, "qdrant_disable_writes", False) or getattr(args, "qdrant_read_only", False):
        globals()["QDRANT_ENABLE_WRITES"] = False
    if getattr(args, "qdrant_disable_rerank", False):
        globals()["QDRANT_RERANK_ENABLED"] = False


__all__ = [
    "Faker",
    "Image",
    "ImageDraw",
    "pil_pixelmatch",
    "asyncpg",
    "redis_asyncio",
    "httpx",
    "_REPORTLAB_AVAILABLE",
    "DEFAULT_TARGET_URL",
    "DEFAULT_OLLAMA_MODEL",
    "DEFAULT_OLLAMA_TIMEOUT_SECONDS",
    "DEFAULT_MAX_STEPS",
    "DEFAULT_WORKERS",
    "DEFAULT_MAX_STEPS_PER_WORKER",
    "DEFAULT_WORKER_NAVIGATION_RETRIES",
    "DEFAULT_WORKER_QDRANT_INIT_RETRIES",
    "DEFAULT_WORKER_BOUNDARY_RECOVERY_RETRIES",
    "DEFAULT_RETRY_BASE_DELAY_SECONDS",
    "MAX_ALLOWED_RETRIES",
    "MAX_ALLOWED_RETRY_BASE_DELAY_SECONDS",
    "DEFAULT_HEADLESS",
    "DEFAULT_WINDOW_SIZE",
    "DEFAULT_NO_VIEWPORT",
    "DEFAULT_POSTGRES_DSN",
    "DEFAULT_REDIS_URL",
    "DEFAULT_REDIS_PREFIX",
    "DEFAULT_REDIS_PATH_LOCK_TTL_SECONDS",
    "DEFAULT_GOLDEN_BASELINE_MODE",
    "DEFAULT_STRICT_PERSISTENCE",
    "DEFAULT_REDIS_STATE_TTL_SECONDS",
    "DEFAULT_QDRANT_URL",
    "DEFAULT_QDRANT_COLLECTION",
    "DEFAULT_QDRANT_VECTOR_SIZE",
    "DEFAULT_QDRANT_ENABLE_READS",
    "DEFAULT_QDRANT_ENABLE_WRITES",
    "DEFAULT_QDRANT_EMBEDDING_PROVIDER",
    "DEFAULT_QDRANT_EMBEDDING_MODEL",
    "DEFAULT_QDRANT_EMBEDDING_LITELLM_BASE_URL",
    "DEFAULT_QDRANT_EMBEDDING_LITELLM_API_KEY",
    "DEFAULT_QDRANT_RERANK_ENABLED",
    "DEFAULT_QDRANT_RERANK_MODEL",
    "DEFAULT_QDRANT_CANDIDATE_LIMIT",
    "DEFAULT_PDF_GENERATE",
    "DEFAULT_PDF_VISION_MODEL",
    "DEFAULT_VISION_MODEL",
    "DEFAULT_PDF_VISION_TIMEOUT_SECONDS",
    "AXE_CDN_URL",
    "VISUAL_DIFF_THRESHOLD_RATIO",
    "LAYOUT_SHIFT_THRESHOLD_PX",
    "STATE_LOOP_THRESHOLD",
    "ACTION_COOLDOWN_SECONDS",
    "OLLAMA_DECISION_OPTIONS",
    "ALLOWED_ACTIONS",
    "SHUTDOWN_EVENT",
    "GRACEFUL_SHUTDOWN_REQUESTED",
    "parse_cli_args",
    "load_settings",
    "validate_runtime_configuration",
    "_request_graceful_shutdown",
    "_register_graceful_shutdown_signals",
    "normalize_action_plan",
    "is_in_scope",
    "build_redis_key",
    "_normalize_defect",
    "_local_service_log",
    "split_domain_and_route",
    "FormControlRecord",
    "FormRecord",
    "PageSnapshot",
    "PersonaGoal",
    "CriticalFlow",
    "TestingStrategy",
    "Settings",
    "WorkerRunResult",
    "DefectTicket",
    "ACTIVE_SEED",
    "TARGET_URL",
    "OLLAMA_MODEL",
    "OLLAMA_TIMEOUT_SECONDS",
    "VISION_MODEL",
    "MAX_STEPS",
    "MAX_STEPS_PER_WORKER",
    "WORKERS",
    "WORKER_NAVIGATION_RETRIES",
    "WORKER_QDRANT_INIT_RETRIES",
    "WORKER_BOUNDARY_RECOVERY_RETRIES",
    "RETRY_BASE_DELAY_SECONDS",
    "HEADLESS",
    "BROWSER_WINDOW_SIZE",
    "NO_VIEWPORT",
    "POSTGRES_DSN",
    "REDIS_URL",
    "REDIS_PREFIX",
    "REDIS_PATH_LOCK_TTL_SECONDS",
    "GOLDEN_BASELINE_MODE",
    "STRICT_PERSISTENCE",
    "QDRANT_URL",
    "QDRANT_COLLECTION",
    "QDRANT_ENABLE_READS",
    "QDRANT_ENABLE_WRITES",
    "QDRANT_EMBEDDING_PROVIDER",
    "QDRANT_EMBEDDING_MODEL",
    "QDRANT_EMBEDDING_LITELLM_BASE_URL",
    "QDRANT_EMBEDDING_LITELLM_API_KEY",
    "QDRANT_RERANK_ENABLED",
    "QDRANT_RERANK_MODEL",
    "QDRANT_CANDIDATE_LIMIT",
    "QDRANT_ADMIN_ACTION",
    "OUTPUT_DIR",
    "RUN_USER_DATA_DIR",
    "apply_runtime_overrides",
    "_safe_set_global",
]
