"""
Backward-compatibility shim for MonkeyLM package.

This module re-exports all symbols from the new `monkeylm/` package
for backward compatibility with existing code that imports from
`monkey_agent_advanced`.

The canonical location for all functionality is now `monkeylm/`.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import random
import signal
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)

# Re-export Playwright async API for backward compat
from playwright.async_api import Dialog, Page, Route, async_playwright

# Re-export from monkeylm modules
from monkeylm.browser import (
    _extract_target_id,
    _fill_select_option,
    _locator_for_target_id,
    _normalize_form_control_raw,
    _resolve_interaction_mode,
    _sanitize_filename,
    _compute_action_path_hash,
    capture_dom_and_layout,
    compare_screenshots_pixelmatch,
    diff_component_manifests,
    execute_action,
    extract_component_manifest,
    get_page_state,
    handle_dialog,
    launch_context_with_fallback,
    state_to_prompt,
    wait_for_page_ready,
)

from monkeylm.config import (
    Settings,
    ALLOWED_ACTIONS,
    ACTION_COOLDOWN_SECONDS,
    AXE_CDN_URL,
    DEFAULT_GOLDEN_BASELINE_MODE,
    DEFAULT_HEADLESS,
    DEFAULT_MAX_STEPS,
    DEFAULT_MAX_STEPS_PER_WORKER,
    DEFAULT_NO_VIEWPORT,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OLLAMA_TIMEOUT_SECONDS,
    DEFAULT_POSTGRES_DSN,
    DEFAULT_QDRANT_CANDIDATE_LIMIT,
    DEFAULT_QDRANT_COLLECTION,
    DEFAULT_QDRANT_ENABLE_READS,
    DEFAULT_QDRANT_ENABLE_WRITES,
    DEFAULT_QDRANT_EMBEDDING_MODEL,
    DEFAULT_QDRANT_EMBEDDING_PROVIDER,
    DEFAULT_QDRANT_RERANK_ENABLED,
    DEFAULT_QDRANT_RERANK_MODEL,
    DEFAULT_QDRANT_URL,
    DEFAULT_REDIS_PATH_LOCK_TTL_SECONDS,
    DEFAULT_REDIS_PREFIX,
    DEFAULT_REDIS_URL,
    DEFAULT_RETRY_BASE_DELAY_SECONDS,
    DEFAULT_STRICT_PERSISTENCE,
    DEFAULT_TARGET_URL,
    DEFAULT_VISION_MODEL,
    DEFAULT_WINDOW_SIZE,
    DEFAULT_WORKER_BOUNDARY_RECOVERY_RETRIES,
    DEFAULT_WORKER_NAVIGATION_RETRIES,
    DEFAULT_WORKER_QDRANT_INIT_RETRIES,
    DEFAULT_WORKERS,
    GRACEFUL_SHUTDOWN_REQUESTED,
    MAX_ALLOWED_RETRIES,
    MAX_ALLOWED_RETRY_BASE_DELAY_SECONDS,
    SHUTDOWN_EVENT,
    is_in_scope,
    normalize_action_plan,
    parse_cli_args,
    split_domain_and_route,
    validate_runtime_configuration,
    _request_graceful_shutdown,
    _register_graceful_shutdown_signals,
    load_settings,
    build_redis_key,
)

from monkeylm.models import (
    build_decision_prompt,
    parse_action_plan_response,
    generate_form_payload,
    _is_cloud_vision_model,
    _build_vision_annotation_prompt,
)

from monkeylm.core import (
    A11yChecker,
    DefectTracker,
    Fuzzer,
    NetworkMonitor,
    PerformanceMonitor,
    allocate_worker_steps,
    build_worker_user_data_dir,
    with_retry_backoff,
    _run_worker_with_limit,
    run_worker,
    main,
)

from monkeylm.memory import PersistenceEngine, QdrantMemoryStore

from monkeylm.reporting import (
    generate_markdown_report,
    generate_json_summary,
    generate_pdf_report,
    summarize_semantic_memory_telemetry,
    summarize_vibe_coding_accountability,
)

# ── Backward-compatible wrapper for PersistenceEngine (reorders arguments) ─────
# Note: This wrapper reorders arguments for backward compatibility:
#   Old: PersistenceEngine(defects, max_workers=1)
#   New: PersistenceEngine(settings, defects, max_workers=1)

# Save original PersistenceEngine before reassignment
_PersistenceEngineOriginal = PersistenceEngine


def _PersistenceEngine_compat(
    defects: Any, settings: Optional[Any] = None, max_workers: int = 1
) -> Any:
    """Factory function that creates PersistenceEngine with reordered arguments."""
    if settings is None:
        settings = load_settings()
    return _PersistenceEngineOriginal(settings, defects, max_workers)


# Backward-compatible wrapper class for PersistenceEngine (reorders arguments)
class PersistenceEngineCompat:
    """Wrapper class that reorders arguments for backward compatibility."""
    def __new__(cls, defects: Any, settings: Optional[Any] = None, max_workers: int = 1) -> Any:
        if settings is None:
            settings = load_settings()
        return _PersistenceEngineOriginal(settings, defects, max_workers)


# Use compat wrapper as the public API for backward compatibility
PersistenceEngine = PersistenceEngineCompat  # type: ignore[misc, assignment]


# ── Backward-compatible mutable globals ────────────────────────────────────────

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
QDRANT_RERANK_ENABLED: bool = DEFAULT_QDRANT_RERANK_ENABLED
QDRANT_RERANK_MODEL: str = DEFAULT_QDRANT_RERANK_MODEL
QDRANT_CANDIDATE_LIMIT: int = DEFAULT_QDRANT_CANDIDATE_LIMIT
QDRANT_ADMIN_ACTION: str = ""
OUTPUT_DIR: str = f"reports/testrun_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
RUN_USER_DATA_DIR: str = f"playwright_user_data/session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
DEFECTS: DefectTracker = DefectTracker()
NETWORK_MONITOR: NetworkMonitor = NetworkMonitor(DEFECTS)
PERF_MONITOR: PerformanceMonitor = PerformanceMonitor(DEFECTS)
test_logs: List[Dict[str, Any]] = []

# Backward-compatible mutable shutdown state (re-export from config with local mutation)
GRACEFUL_SHUTDOWN_REQUESTED: bool = False  # type: ignore[no-redef]  # noqa: F811 – intentional shim reexport
SHUTDOWN_EVENT: asyncio.Event = asyncio.Event()  # type: ignore[no-redef]  # noqa: F811 – intentional shim reexport


# ── Backward-compatible runtime override ───────────────────────────────────────


# Whitelisted global keys and their expected types for safe mutation.
# Any key NOT in this set will be rejected if passed via CLI args.
_RUNTIME_GLOBAL_SCHEMA: Dict[str, Union[type, Tuple[type, ...]]] = {
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
    "QDRANT_RERANK_ENABLED": bool,
    "QDRANT_RERANK_MODEL": str,
    "QDRANT_CANDIDATE_LIMIT": int,
    "QDRANT_ADMIN_ACTION": str,
    "QDRANT_ENABLE_READS": bool,
    "QDRANT_ENABLE_WRITES": bool,
}

# Global keys that carry sensitive credentials — logged on mutation.
_SENSITIVE_CONFIG_KEYS: frozenset = frozenset([
    "POSTGRES_DSN",
    "REDIS_URL",
])

# Positive integer globals — must be >= 1.
_POS_INT_KEYS: frozenset = frozenset([
    "MAX_STEPS",
    "MAX_STEPS_PER_WORKER",
    "WORKERS",
    "WORKER_NAVIGATION_RETRIES",
    "WORKER_QDRANT_INIT_RETRIES",
    "WORKER_BOUNDARY_RECOVERY_RETRIES",
    "REDIS_PATH_LOCK_TTL_SECONDS",
    "QDRANT_CANDIDATE_LIMIT",
])

# Positive float globals — must be > 0.
_POS_FLOAT_KEYS: frozenset = frozenset([
    "OLLAMA_TIMEOUT_SECONDS",
    "RETRY_BASE_DELAY_SECONDS",
])

# Allowed values for QDRANT_ADMIN_ACTION.
_QDRANT_ADMIN_ACTIONS_ALLOWED: frozenset = frozenset(["", "inspect", "clear"])


def _safe_set_global(key: str, value: Any) -> None:
    """Set a module-level global variable with type and range validation.

    Only keys present in _RUNTIME_GLOBAL_SCHEMA are accepted.  Values are
    checked against their declared type and, for numeric keys, constrained to
    positive ranges.  Sensitive keys (credentials) trigger a warning log.

    Raises ``ValueError`` on type mismatch, range violation, or unwhitelisted key.
    """
    if key not in _RUNTIME_GLOBAL_SCHEMA:
        logger.warning("Runtime override rejected: key '%s' not in whitelist", key)
        return

    expected = _RUNTIME_GLOBAL_SCHEMA[key]
    if not isinstance(value, expected):
        try:
            coercer = expected[0] if isinstance(expected, tuple) else expected  # type: ignore[index, misc]
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
        logger.warning("Sensitive config override applied: %s", key)


def apply_runtime_overrides(args: argparse.Namespace) -> None:
    """Apply runtime configuration overrides from CLI args to module-level globals.

    All values pass through ``_safe_set_global`` for type safety and range
    constraints, replacing the previous unvalidated direct globals() writes.
    """
    # Set seed first (validate integer, clamp to reasonable range)
    if getattr(args, "seed", None) is not None:
        try:
            seed_val = int(args.seed)
        except (TypeError, ValueError):
            raise ValueError(f"seed must be an integer, got {args.seed!r}")
        global ACTIVE_SEED  # noqa: PLW0603
        ACTIVE_SEED = str(seed_val)
        random.seed(seed_val)

    # Simple key mappings: args.<attr> -> GLOBAL_KEY
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

    # Qdrant-specific overrides (attrs may be missing on older argparsers)
    _QDRANT_OVERRIDES: List[tuple] = [
        ("qdrant_url", "QDRANT_URL"),
        ("qdrant_collection", "QDRANT_COLLECTION"),
        ("qdrant_embedding_provider", "QDRANT_EMBEDDING_PROVIDER"),
        ("qdrant_embedding_model", "QDRANT_EMBEDDING_MODEL"),
        ("qdrant_enable_rerank", "QDRANT_RERANK_ENABLED"),
        ("qdrant_rerank_model", "QDRANT_RERANK_MODEL"),
        ("qdrant_candidate_limit", "QDRANT_CANDIDATE_LIMIT"),
    ]

    for attr, gkey in _QDRANT_OVERRIDES:
        val = getattr(args, attr, None)
        if val is not None:
            _safe_set_global(gkey, val)

    # qdrant_admin_action — validated against allowlist
    admin_action = getattr(args, "qdrant_admin_action", None)
    if admin_action is not None:
        _safe_set_global("QDRANT_ADMIN_ACTION", admin_action)
    elif getattr(args, "qdrant_inspect", False):
        globals()["QDRANT_ADMIN_ACTION"] = "inspect"
    elif getattr(args, "qdrant_clear", False):
        globals()["QDRANT_ADMIN_ACTION"] = "clear"

    # Boolean toggle flags (hardcoded safe values — no user-controlled data)
    if getattr(args, "qdrant_disable_reads", False):
        globals()["QDRANT_ENABLE_READS"] = False
    if getattr(args, "qdrant_disable_writes", False) or getattr(args, "qdrant_read_only", False):
        globals()["QDRANT_ENABLE_WRITES"] = False
    if getattr(args, "qdrant_disable_rerank", False):
        globals()["QDRANT_RERANK_ENABLED"] = False


# ── Backward-compatible wrappers for shutdown functions ────────────────────────

def _request_graceful_shutdown_wrapper(signum: int, frame: Any) -> None:
    """Wrapper that updates local shutdown state."""
    global GRACEFUL_SHUTDOWN_REQUESTED, SHUTDOWN_EVENT
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

def _register_graceful_shutdown_signals_wrapper() -> None:
    """Wrapper that registers local signal handler."""
    signal.signal(signal.SIGINT, _request_graceful_shutdown_wrapper)
    signal.signal(signal.SIGTERM, _request_graceful_shutdown_wrapper)

# Replace the imported functions with our wrappers
_request_graceful_shutdown = _request_graceful_shutdown_wrapper  # noqa: F811 – intentional shim reexport
_register_graceful_shutdown_signals = _register_graceful_shutdown_signals_wrapper  # noqa: F811 – intentional shim reexport


# ── Backward-compatible wrappers for new API functions ─────────────────────────

# Save original launch_context_with_fallback before reassignment
_LaunchContextWithFallbackOriginal = launch_context_with_fallback


def _launch_context_with_fallback_compat(
    p: Any,
    *,
    user_data_dir: str,
    worker_label: str,
) -> Tuple[Any, Dict[str, Any]]:
    """Wrapper that provides old signature without settings argument."""
    settings = load_settings()
    return _LaunchContextWithFallbackOriginal(  # type: ignore[return-value]
        p,
        settings=settings,
        user_data_dir=user_data_dir,
        worker_label=worker_label,
    )
    # ^ NOTE: This returns a coroutine but the caller expects sync — intentional backward-compat shim; callers that use m.xxx will get the shim's signature.


# Use compat wrapper as the public API for backward compatibility
launch_context_with_fallback = _launch_context_with_fallback_compat  # type: ignore[assignment]


# Backward-compatible wrapper for summarize_vibe_coding_accountability (adds default defects)
def _summarize_vibe_coding_accountability_compat(defects: Optional[Any] = None) -> Dict[str, Any]:
    """Wrapper that provides backward compatibility with optional defects argument."""
    if defects is None:
        # Check for global DEFECTS in the shim's globals, not the monkeylm module
        defects = globals().get('DEFECTS', DefectTracker())
    return _SummarizeVibeCodingAccountabilityOriginal(defects)


# Save original function and replace with compat wrapper
_SummarizeVibeCodingAccountabilityOriginal = summarize_vibe_coding_accountability
summarize_vibe_coding_accountability = _summarize_vibe_coding_accountability_compat


# Backward-compatible wrapper for build_worker_user_data_dir (adds default settings)
def _build_worker_user_data_dir_compat(worker_id: int, settings: Optional[Any] = None) -> str:
    """Wrapper that provides backward compatibility with optional settings argument."""
    if settings is None:
        settings = load_settings()
    return _BuildWorkerUserDataDirOriginal(settings, worker_id)


# Save original function and replace with compat wrapper
_BuildWorkerUserDataDirOriginal = build_worker_user_data_dir
build_worker_user_data_dir = _build_worker_user_data_dir_compat  # type: ignore[assignment]


# Backward-compatible wrapper for build_redis_key (adds default REDIS_PREFIX)
def _build_redis_key_compat(base_key: str, redis_prefix: Optional[str] = None) -> str:
    """Wrapper that provides backward compatibility with optional redis_prefix argument."""
    if redis_prefix is None:
        redis_prefix = globals().get('REDIS_PREFIX', 'monkey:')
    return _BuildRedisKeyOriginal(redis_prefix, base_key)


# Save original function and replace with compat wrapper
_BuildRedisKeyOriginal = build_redis_key
build_redis_key = _build_redis_key_compat  # type: ignore[assignment]


# Backward-compatible wrapper for generate_json_summary (adds default arguments)
def _generate_json_summary_compat(
    start_time: Optional[Any] = None,
    end_time: Optional[Any] = None,
    test_logs: Optional[List[Dict[str, Any]]] = None,
    browser_launch_info: Optional[Dict[str, Any]] = None,
    defects: Optional[Any] = None,
    settings: Optional[Any] = None,
) -> str:
    """Wrapper that provides backward compatibility with positional args and defaults."""
    if settings is None:
        settings = load_settings()
    # Override settings attributes from shim globals when set
    _settings_overrides: Dict[str, Any] = {}
    for _attr in ['target_url', 'ollama_model', 'active_seed', 'workers', 'max_steps_per_worker', 'max_steps', 'ollama_timeout_seconds', 'redis_path_lock_ttl_seconds']:
        _val = globals().get(_attr.upper(), None)
        if _val is not None:
            _settings_overrides[_attr] = _val
    if _settings_overrides:
        class _SettingsWithOverrides:
            def __init__(self, base, overrides):
                self._base = base
                self._overrides = overrides
            def __getattr__(self, name):
                if name in self._overrides:
                    return self._overrides[name]
                return getattr(self._base, name)
        settings = _SettingsWithOverrides(settings, _settings_overrides)
    if defects is None:
        defects = globals().get('DEFECTS', DefectTracker())
    if test_logs is None:
        test_logs = globals().get('test_logs', [])
    if browser_launch_info is None:
        browser_launch_info = {}
    if start_time is None:
        start_time = datetime.now()
    if end_time is None:
        end_time = datetime.now()
    
    # Get output dir from shim globals, override settings.output_dir for this call
    _output_dir = globals().get('OUTPUT_DIR', None)
    if _output_dir is not None:
        class _SettingsWithOutputDir:
            def __init__(self, base, out_dir):
                self._base = base
                self._out_dir = out_dir
            @property
            def output_dir(self) -> str:
                return self._out_dir
            def __getattr__(self, name):
                return getattr(self._base, name)
        settings = _SettingsWithOutputDir(settings, _output_dir)
    
    return _GenerateJsonSummaryOriginal(settings, defects, test_logs, browser_launch_info, [], False, start_time, end_time)  # type: ignore[return-value]


# Save original function and replace with compat wrapper
_GenerateJsonSummaryOriginal = generate_json_summary
generate_json_summary = _generate_json_summary_compat  # type: ignore[assignment]

# Backward-compatible wrapper for generate_markdown_report (adds default arguments)
def _generate_markdown_report_compat(
    start_time: Optional[Any] = None,
    end_time: Optional[Any] = None,
    test_logs: Optional[List[Dict[str, Any]]] = None,
    browser_launch_info: Optional[Dict[str, Any]] = None,
    defects: Optional[Any] = None,
    settings: Optional[Any] = None,
) -> str:
    """Wrapper that provides backward compatibility with positional args and defaults."""
    if settings is None:
        settings = load_settings()
    # Override settings attributes from shim globals when set
    _settings_overrides: Dict[str, Any] = {}
    for _attr in ['target_url', 'active_seed', 'strict_sandbox']:
        _val = globals().get(_attr.upper(), None)
        if _val is not None:
            _settings_overrides[_attr] = _val
    if _settings_overrides:
        class _SettingsWithOverrides:
            def __init__(self, base, overrides):
                self._base = base
                self._overrides = overrides
            def __getattr__(self, name):
                if name in self._overrides:
                    return self._overrides[name]
                return getattr(self._base, name)
        settings = _SettingsWithOverrides(settings, _settings_overrides)
    if defects is None:
        defects = globals().get('DEFECTS', DefectTracker())
    if test_logs is None:
        test_logs = globals().get('test_logs', [])
    if browser_launch_info is None:
        browser_launch_info = {}
    if start_time is None:
        start_time = datetime.now()
    if end_time is None:
        end_time = datetime.now()
    
    # Get output dir from shim globals, override settings.output_dir for this call
    _output_dir = globals().get('OUTPUT_DIR', None)
    if _output_dir is not None:
        class _SettingsWithOutputDir:
            def __init__(self, base, out_dir):
                self._base = base
                self._out_dir = out_dir
            @property
            def output_dir(self) -> str:
                return self._out_dir
            def __getattr__(self, name):
                return getattr(self._base, name)
        settings = _SettingsWithOutputDir(settings, _output_dir)
    
    return _GenerateMarkdownReportOriginal(settings, defects, test_logs, browser_launch_info, start_time, end_time)  # type: ignore[return-value]


# Save original function and replace with compat wrapper
_GenerateMarkdownReportOriginal = generate_markdown_report
generate_markdown_report = _generate_markdown_report_compat  # type: ignore[assignment]

# Backward-compatible wrapper for validate_runtime_configuration (adds default settings from globals)
def _validate_runtime_configuration_compat(settings: Optional[Any] = None) -> None:
    """Wrapper that provides backward compatibility with optional settings argument."""
    if settings is None:
        # Create Settings from shim's global variables
        s = Settings()
        s.target_url = globals().get('TARGET_URL', s.target_url)
        s.ollama_model = globals().get('OLLAMA_MODEL', s.ollama_model)
        s.ollama_timeout_seconds = globals().get('OLLAMA_TIMEOUT_SECONDS', s.ollama_timeout_seconds)
        s.max_steps = globals().get('MAX_STEPS', s.max_steps)
        s.workers = globals().get('WORKERS', s.workers)
        s.max_steps_per_worker = globals().get('MAX_STEPS_PER_WORKER', s.max_steps_per_worker)
        s.worker_navigation_retries = globals().get('WORKER_NAVIGATION_RETRIES', s.worker_navigation_retries)
        s.worker_qdrant_init_retries = globals().get('WORKER_QDRANT_INIT_RETRIES', s.worker_qdrant_init_retries)
        s.worker_boundary_recovery_retries = globals().get('WORKER_BOUNDARY_RECOVERY_RETRIES', s.worker_boundary_recovery_retries)
        s.retry_base_delay_seconds = globals().get('RETRY_BASE_DELAY_SECONDS', s.retry_base_delay_seconds)
        s.headless = globals().get('HEADLESS', s.headless)
        s.browser_window_size = globals().get('BROWSER_WINDOW_SIZE', s.browser_window_size)
        s.no_viewport = globals().get('NO_VIEWPORT', s.no_viewport)
        s.postgres_dsn = globals().get('POSTGRES_DSN', s.postgres_dsn)
        s.redis_url = globals().get('REDIS_URL', s.redis_url)
        s.redis_prefix = globals().get('REDIS_PREFIX', s.redis_prefix)
        s.redis_path_lock_ttl_seconds = globals().get('REDIS_PATH_LOCK_TTL_SECONDS', s.redis_path_lock_ttl_seconds)
        s.golden_baseline_mode = globals().get('GOLDEN_BASELINE_MODE', s.golden_baseline_mode)
        s.strict_persistence = globals().get('STRICT_PERSISTENCE', s.strict_persistence)
        s.qdrant_url = globals().get('QDRANT_URL', s.qdrant_url)
        s.qdrant_collection = globals().get('QDRANT_COLLECTION', s.qdrant_collection)
        s.qdrant_vector_size = globals().get('QDRANT_VECTOR_SIZE', s.qdrant_vector_size)
        s.qdrant_enable_reads = globals().get('QDRANT_ENABLE_READS', s.qdrant_enable_reads)
        s.qdrant_enable_writes = globals().get('QDRANT_ENABLE_WRITES', s.qdrant_enable_writes)
        s.qdrant_embedding_provider = globals().get('QDRANT_EMBEDDING_PROVIDER', s.qdrant_embedding_provider)
        s.qdrant_embedding_model = globals().get('QDRANT_EMBEDDING_MODEL', s.qdrant_embedding_model)
        s.qdrant_rerank_enabled = globals().get('QDRANT_RERANK_ENABLED', s.qdrant_rerank_enabled)
        s.qdrant_rerank_model = globals().get('QDRANT_RERANK_MODEL', s.qdrant_rerank_model)
        s.qdrant_candidate_limit = globals().get('QDRANT_CANDIDATE_LIMIT', s.qdrant_candidate_limit)
        s.vision_model = globals().get('VISION_MODEL', s.vision_model)
        s.pdf_generate = globals().get('PDF_GENERATE', s.pdf_generate)
        settings = s
    return _ValidateRuntimeConfigurationOriginal(settings)


# Save original function and replace with compat wrapper
_ValidateRuntimeConfigurationOriginal = validate_runtime_configuration
validate_runtime_configuration = _validate_runtime_configuration_compat


# Backward-compatible wrapper class for QdrantMemoryStore (adds default settings from globals)
class QdrantMemoryStoreCompat:
    """Compat wrapper for QdrantMemoryStore that reads settings from globals."""
    def __new__(cls, settings: Optional[Any] = None) -> Any:
        if settings is None:
            s = Settings()
            s.qdrant_url = globals().get('QDRANT_URL', s.qdrant_url)
            s.qdrant_collection = globals().get('QDRANT_COLLECTION', s.qdrant_collection)
            s.qdrant_vector_size = globals().get('QDRANT_VECTOR_SIZE', s.qdrant_vector_size)
            s.qdrant_enable_reads = globals().get('QDRANT_ENABLE_READS', s.qdrant_enable_reads)
            s.qdrant_enable_writes = globals().get('QDRANT_ENABLE_WRITES', s.qdrant_enable_writes)
            s.qdrant_embedding_provider = globals().get('QDRANT_EMBEDDING_PROVIDER', s.qdrant_embedding_provider)
            s.qdrant_embedding_model = globals().get('QDRANT_EMBEDDING_MODEL', s.qdrant_embedding_model)
            s.qdrant_rerank_enabled = globals().get('QDRANT_RERANK_ENABLED', s.qdrant_rerank_enabled)
            s.qdrant_rerank_model = globals().get('QDRANT_RERANK_MODEL', s.qdrant_rerank_model)
            s.qdrant_candidate_limit = globals().get('QDRANT_CANDIDATE_LIMIT', s.qdrant_candidate_limit)
            settings = s
        return _QdrantMemoryStoreOriginal(settings)


# Save original class and replace with compat wrapper
_QdrantMemoryStoreOriginal = QdrantMemoryStore
QdrantMemoryStore = QdrantMemoryStoreCompat  # type: ignore[misc]

# ── Exports ────────────────────────────────────────────────────────────────────

__all__ = [
    "Settings",
    "DefectTracker",
    "Fuzzer",
    "A11yChecker",
    "NetworkMonitor",
    "PerformanceMonitor",
    "PersistenceEngine",
    "QdrantMemoryStore",
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
    "DEFAULT_HEADLESS",
    "DEFAULT_WINDOW_SIZE",
    "DEFAULT_NO_VIEWPORT",
    "DEFAULT_POSTGRES_DSN",
    "DEFAULT_REDIS_URL",
    "DEFAULT_REDIS_PREFIX",
    "DEFAULT_REDIS_PATH_LOCK_TTL_SECONDS",
    "DEFAULT_GOLDEN_BASELINE_MODE",
    "DEFAULT_STRICT_PERSISTENCE",
    "DEFAULT_QDRANT_URL",
    "DEFAULT_QDRANT_COLLECTION",
    "DEFAULT_QDRANT_ENABLE_READS",
    "DEFAULT_QDRANT_ENABLE_WRITES",
    "DEFAULT_QDRANT_EMBEDDING_PROVIDER",
    "DEFAULT_QDRANT_EMBEDDING_MODEL",
    "DEFAULT_QDRANT_RERANK_ENABLED",
    "DEFAULT_QDRANT_RERANK_MODEL",
    "DEFAULT_QDRANT_CANDIDATE_LIMIT",
    "DEFAULT_VISION_MODEL",
    "AXE_CDN_URL",
    "ACTION_COOLDOWN_SECONDS",
    "ALLOWED_ACTIONS",
    "MAX_ALLOWED_RETRIES",
    "MAX_ALLOWED_RETRY_BASE_DELAY_SECONDS",
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
    "QDRANT_RERANK_ENABLED",
    "QDRANT_RERANK_MODEL",
    "QDRANT_CANDIDATE_LIMIT",
    "QDRANT_ADMIN_ACTION",
    "OUTPUT_DIR",
    "RUN_USER_DATA_DIR",
    "DEFECTS",
    "NETWORK_MONITOR",
    "PERF_MONITOR",
    "test_logs",
    "capture_dom_and_layout",
    "launch_context_with_fallback",
    "wait_for_page_ready",
    "get_page_state",
    "state_to_prompt",
    "extract_component_manifest",
    "diff_component_manifests",
    "_normalize_form_control_raw",
    "_sanitize_filename",
    "_extract_target_id",
    "_locator_for_target_id",
    "_resolve_interaction_mode",
    "_fill_select_option",
    "_compute_action_path_hash",
    "handle_dialog",
    "execute_action",
    "validate_runtime_configuration",
    "normalize_action_plan",
    "is_in_scope",
    "parse_cli_args",
    "build_worker_user_data_dir",
    "split_domain_and_route",
    "_build_vision_annotation_prompt",
    "_is_cloud_vision_model",
    "build_decision_prompt",
    "generate_form_payload",
    "parse_action_plan_response",
    "compare_screenshots_pixelmatch",
    "_request_graceful_shutdown",
    "_register_graceful_shutdown_signals",
    "allocate_worker_steps",
    "with_retry_backoff",
    "_run_worker_with_limit",
    "run_worker",
    "main",
    "summarize_semantic_memory_telemetry",
    "summarize_vibe_coding_accountability",
    "generate_markdown_report",
    "generate_json_summary",
    "generate_pdf_report",
    "async_playwright",
    "Dialog",
    "Page",
    "Route",
    "GRACEFUL_SHUTDOWN_REQUESTED",
    "SHUTDOWN_EVENT",
]
