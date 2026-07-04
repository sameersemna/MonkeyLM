"""
Backward-compatibility shim for MonkeyLM package.

This module re-exports all symbols from the new `monkeylm/` package
for backward compatibility with existing code that imports from
`monkey_agent_advanced`.

The canonical location for all functionality is now `monkeylm/`.
"""
from __future__ import annotations

import asyncio
import random
from datetime import datetime
from typing import Any, Dict, List, Optional

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
    _local_service_log,
    _normalize_window_size,
    _optional_import,
    is_in_scope,
    normalize_action_plan,
    parse_cli_args,
    split_domain_and_route,
    validate_runtime_configuration,
    _request_graceful_shutdown,
    _register_graceful_shutdown_signals,
    load_settings,
    build_redis_key,
    _normalize_defect,
)

from monkeylm.models import (
    build_decision_prompt,
    parse_action_plan_response,
    generate_form_payload,
    _is_cloud_vision_model,
    _build_vision_annotation_prompt,
    _is_ollama_overload_error,
    _extract_target_id,
    _extract_all_target_ids,
    _break_action_loop,
    _step_defects_summary,
    apply_state_aware_policy,
    _extract_box_from_prose,
    _draw_red_box_arrow,
)

from monkeylm.core import (
    A11yChecker,
    DefectTracker,
    Fuzzer,
    NetworkMonitor,
    PerformanceMonitor,
    WorkerRunResult,
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
PersistenceEngine = PersistenceEngineCompat


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
GRACEFUL_SHUTDOWN_REQUESTED: bool = False
SHUTDOWN_EVENT: asyncio.Event = asyncio.Event()


# ── Backward-compatible runtime override ───────────────────────────────────────


def apply_runtime_overrides(args: argparse.Namespace) -> None:
    """Apply runtime configuration overrides from CLI args to module-level globals.
    
    This function mutates the module-level mutable globals to reflect runtime
    configuration overrides, maintaining backward compatibility with existing
    code that expects global variable mutation for configuration.
    """
    # Set the seed first to initialize the global random state
    if args.seed is not None:
        global ACTIVE_SEED
        ACTIVE_SEED = str(args.seed)
        random.seed(int(args.seed))
    
    # Update runtime configuration globals
    if args.target_url is not None:
        globals()['TARGET_URL'] = args.target_url
    if args.ollama_model is not None:
        globals()['OLLAMA_MODEL'] = args.ollama_model
    if args.ollama_timeout_seconds is not None:
        globals()['OLLAMA_TIMEOUT_SECONDS'] = args.ollama_timeout_seconds
    if args.vision_model is not None:
        globals()['VISION_MODEL'] = args.vision_model
    if args.max_steps is not None:
        globals()['MAX_STEPS'] = args.max_steps
    if args.max_steps_per_worker is not None:
        globals()['MAX_STEPS_PER_WORKER'] = args.max_steps_per_worker
    if args.workers is not None:
        globals()['WORKERS'] = args.workers
    if args.worker_navigation_retries is not None:
        globals()['WORKER_NAVIGATION_RETRIES'] = args.worker_navigation_retries
    if args.worker_qdrant_init_retries is not None:
        globals()['WORKER_QDRANT_INIT_RETRIES'] = args.worker_qdrant_init_retries
    if args.worker_boundary_recovery_retries is not None:
        globals()['WORKER_BOUNDARY_RECOVERY_RETRIES'] = args.worker_boundary_recovery_retries
    if args.retry_base_delay_seconds is not None:
        globals()['RETRY_BASE_DELAY_SECONDS'] = args.retry_base_delay_seconds
    if args.headless is not None:
        globals()['HEADLESS'] = args.headless
    if args.window_size is not None:
        globals()['BROWSER_WINDOW_SIZE'] = args.window_size
    if args.no_viewport is not None:
        globals()['NO_VIEWPORT'] = args.no_viewport
    if args.postgres_dsn is not None:
        globals()['POSTGRES_DSN'] = args.postgres_dsn
    if args.redis_url is not None:
        globals()['REDIS_URL'] = args.redis_url
    if args.redis_prefix is not None:
        globals()['REDIS_PREFIX'] = args.redis_prefix
    if args.redis_path_lock_ttl_seconds is not None:
        globals()['REDIS_PATH_LOCK_TTL_SECONDS'] = args.redis_path_lock_ttl_seconds
    if args.golden_baseline_mode is not None:
        globals()['GOLDEN_BASELINE_MODE'] = args.golden_baseline_mode
    if args.strict_persistence is not None:
        globals()['STRICT_PERSISTENCE'] = args.strict_persistence
    if getattr(args, 'qdrant_url', None) is not None:
        globals()['QDRANT_URL'] = args.qdrant_url
    if getattr(args, 'qdrant_collection', None) is not None:
        globals()['QDRANT_COLLECTION'] = args.qdrant_collection
    if getattr(args, 'qdrant_embedding_provider', None) is not None:
        globals()['QDRANT_EMBEDDING_PROVIDER'] = args.qdrant_embedding_provider
    if getattr(args, 'qdrant_embedding_model', None) is not None:
        globals()['QDRANT_EMBEDDING_MODEL'] = args.qdrant_embedding_model
    if getattr(args, 'qdrant_enable_rerank', None) is not None:
        globals()['QDRANT_RERANK_ENABLED'] = args.qdrant_enable_rerank
    if getattr(args, 'qdrant_rerank_model', None) is not None:
        globals()['QDRANT_RERANK_MODEL'] = args.qdrant_rerank_model
    if getattr(args, 'qdrant_candidate_limit', None) is not None:
        globals()['QDRANT_CANDIDATE_LIMIT'] = args.qdrant_candidate_limit
    if getattr(args, 'qdrant_admin_action', None) is not None:
        globals()['QDRANT_ADMIN_ACTION'] = args.qdrant_admin_action
    # Handle qdrant_inspect flag (maps to QDRANT_ADMIN_ACTION = "inspect")
    if getattr(args, 'qdrant_inspect', False):
        globals()['QDRANT_ADMIN_ACTION'] = "inspect"
    # Handle qdrant_clear flag (maps to QDRANT_ADMIN_ACTION = "clear")
    if getattr(args, 'qdrant_clear', False):
        globals()['QDRANT_ADMIN_ACTION'] = "clear"
    
    # Handle boolean toggles (use getattr to handle missing attributes)
    if getattr(args, 'qdrant_disable_reads', False):
        globals()['QDRANT_ENABLE_READS'] = False
    if getattr(args, 'qdrant_disable_writes', False) or getattr(args, 'qdrant_read_only', False):
        globals()['QDRANT_ENABLE_WRITES'] = False
    if getattr(args, 'qdrant_disable_rerank', False):
        globals()['QDRANT_RERANK_ENABLED'] = False


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
_request_graceful_shutdown = _request_graceful_shutdown_wrapper
_register_graceful_shutdown_signals = _register_graceful_shutdown_signals_wrapper


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
    return _LaunchContextWithFallbackOriginal(
        p,
        settings=settings,
        user_data_dir=user_data_dir,
        worker_label=worker_label,
    )


# Use compat wrapper as the public API for backward compatibility
launch_context_with_fallback = _launch_context_with_fallback_compat


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
build_worker_user_data_dir = _build_worker_user_data_dir_compat


# Backward-compatible wrapper for build_redis_key (adds default REDIS_PREFIX)
def _build_redis_key_compat(base_key: str, redis_prefix: Optional[str] = None) -> str:
    """Wrapper that provides backward compatibility with optional redis_prefix argument."""
    if redis_prefix is None:
        redis_prefix = globals().get('REDIS_PREFIX', 'monkey:')
    return _BuildRedisKeyOriginal(redis_prefix, base_key)


# Save original function and replace with compat wrapper
_BuildRedisKeyOriginal = build_redis_key
build_redis_key = _build_redis_key_compat


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
    
    return _GenerateJsonSummaryOriginal(settings, defects, test_logs, browser_launch_info, {}, False, start_time, end_time)


# Save original function and replace with compat wrapper
_GenerateJsonSummaryOriginal = generate_json_summary
generate_json_summary = _generate_json_summary_compat


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
    
    return _GenerateMarkdownReportOriginal(settings, defects, test_logs, browser_launch_info, start_time, end_time)


# Save original function and replace with compat wrapper
_GenerateMarkdownReportOriginal = generate_markdown_report
generate_markdown_report = _generate_markdown_report_compat


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
QdrantMemoryStore = QdrantMemoryStoreCompat


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
    "decide_next_action",
    "generate_form_payload",
    "parse_action_plan_response",
    "compute_max_layout_shift",
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
