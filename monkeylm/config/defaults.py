"""Static defaults, optional dependency imports, and .env loading."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional


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


def _load_dotenv() -> None:
    dotenv = _optional_import("dotenv")
    if dotenv is None:
        return
    try:
        env_path = PROJECT_ROOT / ".env"
        if env_path.is_file():
            _pre_dotenv_env_keys_update()
            dotenv.load_dotenv(env_path, override=False)
    except Exception:
        pass


def _pre_dotenv_env_keys_update() -> None:
    """Record the environment keys a real shell exported before ``.env`` load."""
    global _PRE_DOTENV_ENV_KEYS
    _PRE_DOTENV_ENV_KEYS = frozenset(os.environ)


def _pre_dotenv_env_keys() -> frozenset:
    """Keys that were already exported by the real shell before ``.env`` load.

    ``load_dotenv`` copies every ``.env`` entry into :data:`os.environ`, which
    makes an ``.env`` value indistinguishable from a genuine shell export to
    any later ``os.getenv`` call. Settings resolution needs the distinction to
    keep the documented precedence (CLI > shell > ``.env`` > default), so the
    pre-load key set is captured before the copy happens.
    """
    return _PRE_DOTENV_ENV_KEYS


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

DEFAULT_TARGET_URL = "https://noblequran-85hu2yge.manus.space/"
DEFAULT_TARGET_USERNAME = ""
DEFAULT_TARGET_PASSWORD = ""
DEFAULT_OLLAMA_MODEL = "minimax-m3:cloud"
DEFAULT_OLLAMA_FALLBACK_MODELS = ""
DEFAULT_OLLAMA_TIMEOUT_SECONDS = 15.0
DEFAULT_OPENAI_BASE_URL = "http://localhost:11434"
DEFAULT_OPENAI_API_KEY = ""
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

DEFAULT_CV_ANALYSIS_ENABLED = True
DEFAULT_CV_SSIM_THRESHOLD = 0.95
DEFAULT_CV_BLANK_SCREEN_STDDEV = 8.0
DEFAULT_CV_PHASH_DEDUP = True
DEFAULT_CV_TEMPLATE_VERIFY = True
DEFAULT_CV_TEMPLATE_MIN_SCORE = 0.75
DEFAULT_CV_TEMPLATE_SCALE = 0.5
DEFAULT_CV_TEMPLATE_EVERY_N_STEPS = 2
DEFAULT_OCR_ENABLED = False
DEFAULT_OCR_CONTRAST_ENABLED = False
DEFAULT_REPLAY_FROM = ""

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
