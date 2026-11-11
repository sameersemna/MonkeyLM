"""Static defaults, optional dependency imports, and .env loading."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from monkeylm.types import Settings


PROJECT_ROOT = Path(__file__).resolve().parents[2]

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


_PRE_DOTENV_ENV_KEYS: "frozenset[str]" = frozenset()
_PRE_DOTENV_ENV: Dict[str, str] = {}

#: ``MONKEYLM_ENV_PRECEDENCE`` values that restore the old shell-first order.
_SHELL_PRECEDENCE_VALUES: "frozenset[str]" = frozenset({"shell", "environment", "env"})


def _dotenv_overrides_shell() -> bool:
    """Whether ``.env`` is authoritative over exported shell variables.

    ``.env`` is the configuration source of truth, so a key it defines wins
    over an exported shell variable of the same name. ``load_dotenv`` must
    therefore run with ``override=True``; otherwise a value captured by an
    editor-injected terminal before an edit would keep beating the file.

    Set ``MONKEYLM_ENV_PRECEDENCE=shell`` in the *process environment* before
    start-up to restore the previous ``CLI > shell > .env > default`` order,
    for example when CI injects real credentials while the checkout carries a
    placeholder ``.env``.
    """
    raw = os.getenv("MONKEYLM_ENV_PRECEDENCE", "dotenv")
    return (raw or "").strip().lower() not in _SHELL_PRECEDENCE_VALUES


def _load_dotenv() -> None:
    dotenv = _optional_import("dotenv")
    if dotenv is None:
        return
    try:
        env_path = PROJECT_ROOT / ".env"
        if env_path.is_file():
            _pre_dotenv_env_keys_update()
            dotenv.load_dotenv(env_path, override=_dotenv_overrides_shell())
    except Exception:
        pass


def _pre_dotenv_env_keys_update() -> None:
    """Snapshot the environment as it was a real shell export before ``.env`` load.

    The values are kept, not just the keys, because ``load_dotenv`` with
    ``override=True`` replaces an exported value with the ``.env`` one. Without
    the snapshot the shadowed value is unrecoverable and no diagnostic can name
    what was overridden.
    """
    global _PRE_DOTENV_ENV_KEYS, _PRE_DOTENV_ENV
    _PRE_DOTENV_ENV = dict(os.environ)
    _PRE_DOTENV_ENV_KEYS = frozenset(_PRE_DOTENV_ENV)


def _pre_dotenv_env_keys() -> frozenset:
    """Keys that were exported by the real shell before ``.env`` load.

    ``load_dotenv`` copies every ``.env`` entry into :data:`os.environ`, which
    makes an ``.env`` value indistinguishable from a genuine shell export to
    any later ``os.getenv`` call. Settings resolution needs the distinction to
    keep the documented precedence (CLI > ``.env`` > shell > default) and to
    report when an exported value was shadowed by the file.
    """
    return _PRE_DOTENV_ENV_KEYS


def _pre_dotenv_env_values() -> Dict[str, str]:
    """Shell values captured before ``.env`` load, for shadowing diagnostics."""
    return _PRE_DOTENV_ENV


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
cv2 = _optional_import("cv2")
np = _optional_import("numpy")
RapidOCR = _optional_import("rapidocr_onnxruntime", "RapidOCR")

try:
    _REPORTLAB_AVAILABLE = True
except Exception:
    _REPORTLAB_AVAILABLE = False

# ── Default constants ──────────────────────────────────────────────────────────

# ── Configuration defaults ────────────────────────────────────────────────────
#
# ``Settings`` (``monkeylm/types.py``) owns the literal values; every constant
# below is derived from it, so a default exists in exactly one place. These
# used to be retyped here *and* in ``monkeylm/__init__.py``, where the copies
# could drift silently — and the copy here was not even consulted during
# resolution, so editing it changed nothing.
#
# ``Settings`` imports only ``monkeylm.defect_types`` (stdlib-only), so this
# import cannot create a cycle: ``monkeylm/config/__init__.py`` imports
# ``monkeylm.types`` before ``.defaults``.
#
# Credentials are deliberately ABSENT from these defaults: connection strings
# come from the untracked ``.env``. Never commit a service password here.
_DEFAULTS = Settings()

DEFAULT_TARGET_URL = _DEFAULTS.target_url
DEFAULT_TARGET_USERNAME = _DEFAULTS.target_username
DEFAULT_TARGET_PASSWORD = _DEFAULTS.target_password
DEFAULT_OLLAMA_MODEL = _DEFAULTS.ollama_model
# Kept literal: the dataclass field is a list, but this seeds comma-separated
# ``.env`` parsing, so its default stays a string.
DEFAULT_OLLAMA_FALLBACK_MODELS = ""
DEFAULT_OLLAMA_TIMEOUT_SECONDS = _DEFAULTS.ollama_timeout_seconds
DEFAULT_OPENAI_BASE_URL = _DEFAULTS.openai_base_url
DEFAULT_OPENAI_API_KEY = _DEFAULTS.openai_api_key
DEFAULT_MAX_STEPS = _DEFAULTS.max_steps
DEFAULT_WORKERS = _DEFAULTS.workers
DEFAULT_MAX_STEPS_PER_WORKER = _DEFAULTS.max_steps_per_worker
DEFAULT_WORKER_NAVIGATION_RETRIES = _DEFAULTS.worker_navigation_retries
DEFAULT_WORKER_QDRANT_INIT_RETRIES = _DEFAULTS.worker_qdrant_init_retries
DEFAULT_WORKER_BOUNDARY_RECOVERY_RETRIES = _DEFAULTS.worker_boundary_recovery_retries
DEFAULT_RETRY_BASE_DELAY_SECONDS = _DEFAULTS.retry_base_delay_seconds
DEFAULT_STEP_TIMEOUT_SECONDS = _DEFAULTS.step_timeout_seconds
DEFAULT_STUCK_STATE_THRESHOLD = _DEFAULTS.stuck_state_threshold
DEFAULT_HEADLESS = _DEFAULTS.headless
DEFAULT_WINDOW_SIZE = _DEFAULTS.browser_window_size
DEFAULT_NO_VIEWPORT = _DEFAULTS.no_viewport
DEFAULT_POSTGRES_DSN = _DEFAULTS.postgres_dsn
DEFAULT_REDIS_URL = _DEFAULTS.redis_url
DEFAULT_REDIS_PREFIX = _DEFAULTS.redis_prefix
DEFAULT_REDIS_PATH_LOCK_TTL_SECONDS = _DEFAULTS.redis_path_lock_ttl_seconds
DEFAULT_GOLDEN_BASELINE_MODE = _DEFAULTS.golden_baseline_mode
DEFAULT_STRICT_PERSISTENCE = _DEFAULTS.strict_persistence
DEFAULT_REDIS_STATE_TTL_SECONDS = _DEFAULTS.redis_state_ttl_seconds
DEFAULT_QDRANT_URL = _DEFAULTS.qdrant_url
DEFAULT_QDRANT_COLLECTION = _DEFAULTS.qdrant_collection
DEFAULT_QDRANT_VECTOR_SIZE = _DEFAULTS.qdrant_vector_size
DEFAULT_QDRANT_ENABLE_READS = _DEFAULTS.qdrant_enable_reads
DEFAULT_QDRANT_ENABLE_WRITES = _DEFAULTS.qdrant_enable_writes
DEFAULT_QDRANT_EMBEDDING_PROVIDER = _DEFAULTS.qdrant_embedding_provider
DEFAULT_QDRANT_EMBEDDING_MODEL = _DEFAULTS.qdrant_embedding_model
DEFAULT_QDRANT_EMBEDDING_LITELLM_BASE_URL = _DEFAULTS.qdrant_embedding_litellm_base_url
DEFAULT_QDRANT_EMBEDDING_LITELLM_API_KEY = _DEFAULTS.qdrant_embedding_litellm_api_key
DEFAULT_QDRANT_RERANK_ENABLED = _DEFAULTS.qdrant_rerank_enabled
DEFAULT_QDRANT_RERANK_MODEL = _DEFAULTS.qdrant_rerank_model
DEFAULT_QDRANT_CANDIDATE_LIMIT = _DEFAULTS.qdrant_candidate_limit
DEFAULT_PDF_GENERATE = _DEFAULTS.pdf_generate
DEFAULT_PDF_VISION_MODEL = _DEFAULTS.pdf_vision_model
DEFAULT_VISION_MODEL = _DEFAULTS.vision_model
DEFAULT_PDF_VISION_TIMEOUT_SECONDS = _DEFAULTS.pdf_vision_timeout_seconds
DEFAULT_CV_ANALYSIS_ENABLED = _DEFAULTS.cv_analysis_enabled
DEFAULT_CV_SSIM_THRESHOLD = _DEFAULTS.cv_ssim_threshold
DEFAULT_CV_BLANK_SCREEN_STDDEV = _DEFAULTS.cv_blank_screen_stddev
DEFAULT_CV_PHASH_DEDUP = _DEFAULTS.cv_phash_dedup
DEFAULT_CV_TEMPLATE_VERIFY = _DEFAULTS.cv_template_verify
DEFAULT_CV_TEMPLATE_MIN_SCORE = _DEFAULTS.cv_template_min_score
DEFAULT_CV_TEMPLATE_SCALE = _DEFAULTS.cv_template_scale
DEFAULT_CV_TEMPLATE_EVERY_N_STEPS = _DEFAULTS.cv_template_every_n_steps
DEFAULT_OCR_ENABLED = _DEFAULTS.ocr_enabled
DEFAULT_OCR_CONTRAST_ENABLED = _DEFAULTS.ocr_contrast_enabled
DEFAULT_REPLAY_FROM = _DEFAULTS.replay_from

# ── Code invariants (deliberately NOT configurable) ───────────────────────────
#
# These are policy, not configuration: they are excluded from the runtime
# override whitelist in ``config/overrides.py`` so a stray ``.env`` entry or CLI
# flag cannot widen what the agent is allowed to do.
MAX_ALLOWED_RETRIES = 10
MAX_ALLOWED_RETRY_BASE_DELAY_SECONDS = 10.0
AXE_CDN_URL = "https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.9.1/axe.min.js"
VISUAL_DIFF_THRESHOLD_RATIO = 0.01
LAYOUT_SHIFT_THRESHOLD_PX = 50
STATE_LOOP_THRESHOLD = 3
ACTION_COOLDOWN_SECONDS = 1.0
OLLAMA_DECISION_OPTIONS: Dict[str, Any] = {
    "temperature": 0.2,
    "top_p": 0.9,
}

ALLOWED_ACTIONS = {
    "click",
    "type",
    "submit_form",
    "handle_modal",
    "press_key",
    "scroll",
    "back",
    "random_jump",
    "restart_target",
}


__all__ = [
    "Faker",
    "Image",
    "ImageDraw",
    "pil_pixelmatch",
    "asyncpg",
    "redis_asyncio",
    "httpx",
    "cv2",
    "np",
    "RapidOCR",
    "_REPORTLAB_AVAILABLE",
    "DEFAULT_TARGET_URL",
    "DEFAULT_TARGET_USERNAME",
    "DEFAULT_TARGET_PASSWORD",
    "DEFAULT_OLLAMA_MODEL",
    "DEFAULT_OLLAMA_FALLBACK_MODELS",
    "DEFAULT_OLLAMA_TIMEOUT_SECONDS",
    "DEFAULT_OPENAI_BASE_URL",
    "DEFAULT_OPENAI_API_KEY",
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
    "DEFAULT_CV_ANALYSIS_ENABLED",
    "DEFAULT_CV_SSIM_THRESHOLD",
    "DEFAULT_CV_BLANK_SCREEN_STDDEV",
    "DEFAULT_CV_PHASH_DEDUP",
    "DEFAULT_CV_TEMPLATE_VERIFY",
    "DEFAULT_CV_TEMPLATE_MIN_SCORE",
    "DEFAULT_CV_TEMPLATE_SCALE",
    "DEFAULT_CV_TEMPLATE_EVERY_N_STEPS",
    "DEFAULT_OCR_ENABLED",
    "DEFAULT_OCR_CONTRAST_ENABLED",
    "DEFAULT_REPLAY_FROM",
    "DEFAULT_VISION_MODEL",
    "DEFAULT_PDF_VISION_TIMEOUT_SECONDS",
    "AXE_CDN_URL",
    "VISUAL_DIFF_THRESHOLD_RATIO",
    "LAYOUT_SHIFT_THRESHOLD_PX",
    "STATE_LOOP_THRESHOLD",
    "ACTION_COOLDOWN_SECONDS",
    "OLLAMA_DECISION_OPTIONS",
    "ALLOWED_ACTIONS",
]
