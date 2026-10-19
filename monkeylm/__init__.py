"""MonkeyLM – Advanced Monkey Testing Agent.

Package structure:
    monkeylm/config.py     – Settings, constants, CLI parsing, env loading
    monkeylm/types.py      – All dataclasses and type definitions
    monkeylm/errors.py     – Centralized exception hierarchy
    monkeylm/interfaces.py – Protocol definitions for dependency injection
    monkeylm/memory.py     – PostgreSQL, Redis, Qdrant persistence layers
    monkeylm/models.py     – Ollama client, vision router, decision prompts
    monkeylm/browser.py    – Playwright browser, DOM snapshots, action execution
    monkeylm/reporting.py  – Markdown, JSON, PDF report generators
    monkeylm/core.py       – Main loop, workers, monitor classes, entry point
    monkeylm/resources/    – Bundled third-party assets (axe-core)
"""

from typing import Any, Dict, Tuple

try:
    from monkeylm.interfaces import IBrowserProvider, IMemoryStore, IModelClient, IReportGenerator
except Exception:
    IBrowserProvider = IMemoryStore = IModelClient = IReportGenerator = Any  # type: ignore[misc]

from monkeylm.di import create_browser_provider, create_memory_store, create_model_client, create_report_generator

try:
    from monkeylm.config import (
        Settings,
        load_settings,
        parse_cli_args,
        validate_runtime_configuration,
        apply_runtime_overrides,
        ACTIVE_SEED,
        TARGET_URL,
        TARGET_USERNAME,
        TARGET_PASSWORD,
        OLLAMA_MODEL,
        OLLAMA_TIMEOUT_SECONDS,
        OPENAI_BASE_URL,
        OPENAI_API_KEY,
        VISION_MODEL,
        MAX_STEPS,
        MAX_STEPS_PER_WORKER,
        WORKERS,
        WORKER_NAVIGATION_RETRIES,
        WORKER_QDRANT_INIT_RETRIES,
        WORKER_BOUNDARY_RECOVERY_RETRIES,
        RETRY_BASE_DELAY_SECONDS,
        HEADLESS,
        BROWSER_WINDOW_SIZE,
        NO_VIEWPORT,
        POSTGRES_DSN,
        REDIS_URL,
        REDIS_PREFIX,
        REDIS_PATH_LOCK_TTL_SECONDS,
        GOLDEN_BASELINE_MODE,
        STRICT_PERSISTENCE,
        QDRANT_URL,
        QDRANT_COLLECTION,
        QDRANT_ENABLE_READS,
        QDRANT_ENABLE_WRITES,
        QDRANT_EMBEDDING_PROVIDER,
        QDRANT_EMBEDDING_MODEL,
        QDRANT_RERANK_ENABLED,
        QDRANT_RERANK_MODEL,
        QDRANT_CANDIDATE_LIMIT,
        QDRANT_ADMIN_ACTION,
        OUTPUT_DIR,
        RUN_USER_DATA_DIR,
        DEFAULT_TARGET_URL,
        DEFAULT_TARGET_USERNAME,
        DEFAULT_TARGET_PASSWORD,
        DEFAULT_OLLAMA_MODEL,
        DEFAULT_OLLAMA_TIMEOUT_SECONDS,
        DEFAULT_OPENAI_BASE_URL,
        DEFAULT_OPENAI_API_KEY,
        DEFAULT_MAX_STEPS,
        DEFAULT_WORKERS,
        DEFAULT_MAX_STEPS_PER_WORKER,
        DEFAULT_WORKER_NAVIGATION_RETRIES,
        DEFAULT_WORKER_QDRANT_INIT_RETRIES,
        DEFAULT_WORKER_BOUNDARY_RECOVERY_RETRIES,
        DEFAULT_RETRY_BASE_DELAY_SECONDS,
        DEFAULT_HEADLESS,
        DEFAULT_WINDOW_SIZE,
        DEFAULT_NO_VIEWPORT,
        DEFAULT_POSTGRES_DSN,
        DEFAULT_REDIS_URL,
        DEFAULT_REDIS_PREFIX,
        DEFAULT_REDIS_PATH_LOCK_TTL_SECONDS,
        DEFAULT_GOLDEN_BASELINE_MODE,
        DEFAULT_STRICT_PERSISTENCE,
        DEFAULT_QDRANT_URL,
        DEFAULT_QDRANT_COLLECTION,
        DEFAULT_QDRANT_ENABLE_READS,
        DEFAULT_QDRANT_ENABLE_WRITES,
        DEFAULT_QDRANT_EMBEDDING_PROVIDER,
        DEFAULT_QDRANT_EMBEDDING_MODEL,
        DEFAULT_QDRANT_RERANK_ENABLED,
        DEFAULT_QDRANT_RERANK_MODEL,
        DEFAULT_QDRANT_CANDIDATE_LIMIT,
        DEFAULT_VISION_MODEL,
        AXE_CDN_URL,
        ACTION_COOLDOWN_SECONDS,
        ALLOWED_ACTIONS,
        MAX_ALLOWED_RETRIES,
        MAX_ALLOWED_RETRY_BASE_DELAY_SECONDS,
        SHUTDOWN_EVENT,
        GRACEFUL_SHUTDOWN_REQUESTED,
        is_in_scope,
        normalize_action_plan,
        split_domain_and_route,
        build_redis_key,
        inspect_optional_runtime_dependencies,
    )
except Exception:
    Settings = Any  # type: ignore[misc]
    load_settings = parse_cli_args = validate_runtime_configuration = apply_runtime_overrides = lambda *args, **kwargs: None
    ACTIVE_SEED = None
    TARGET_URL = DEFAULT_TARGET_URL = "https://noblequran-85hu2yge.manus.space/"
    TARGET_USERNAME = DEFAULT_TARGET_USERNAME = ""
    TARGET_PASSWORD = DEFAULT_TARGET_PASSWORD = ""
    OLLAMA_MODEL = DEFAULT_OLLAMA_MODEL = "minimax-m3:cloud"
    OLLAMA_TIMEOUT_SECONDS = DEFAULT_OLLAMA_TIMEOUT_SECONDS = 15.0
    OPENAI_BASE_URL = DEFAULT_OPENAI_BASE_URL = "http://localhost:11434"
    OPENAI_API_KEY = DEFAULT_OPENAI_API_KEY = ""
    VISION_MODEL = DEFAULT_VISION_MODEL = "gemini-3-flash-preview"
    MAX_STEPS = DEFAULT_MAX_STEPS = 10
    MAX_STEPS_PER_WORKER = DEFAULT_MAX_STEPS_PER_WORKER = 10
    WORKERS = DEFAULT_WORKERS = 1
    WORKER_NAVIGATION_RETRIES = DEFAULT_WORKER_NAVIGATION_RETRIES = 2
    WORKER_QDRANT_INIT_RETRIES = DEFAULT_WORKER_QDRANT_INIT_RETRIES = 1
    WORKER_BOUNDARY_RECOVERY_RETRIES = DEFAULT_WORKER_BOUNDARY_RECOVERY_RETRIES = 1
    RETRY_BASE_DELAY_SECONDS = DEFAULT_RETRY_BASE_DELAY_SECONDS = 0.75
    HEADLESS = DEFAULT_HEADLESS = True
    BROWSER_WINDOW_SIZE = DEFAULT_WINDOW_SIZE = "1920,1080"
    NO_VIEWPORT = DEFAULT_NO_VIEWPORT = True
    POSTGRES_DSN = DEFAULT_POSTGRES_DSN = "postgresql://postgres:postgres@latitude:5432/monkeylm"
    REDIS_URL = DEFAULT_REDIS_URL = "redis://:LatitudeRedis1407@latitude:6379/0"
    REDIS_PREFIX = DEFAULT_REDIS_PREFIX = "monkey:"
    REDIS_PATH_LOCK_TTL_SECONDS = DEFAULT_REDIS_PATH_LOCK_TTL_SECONDS = 45
    GOLDEN_BASELINE_MODE = DEFAULT_GOLDEN_BASELINE_MODE = "preexisting"
    STRICT_PERSISTENCE = DEFAULT_STRICT_PERSISTENCE = False
    QDRANT_URL = DEFAULT_QDRANT_URL = "http://127.0.0.1:6333"
    QDRANT_COLLECTION = DEFAULT_QDRANT_COLLECTION = "monkeylm_semantic_memory"
    QDRANT_ENABLE_READS = DEFAULT_QDRANT_ENABLE_READS = True
    QDRANT_ENABLE_WRITES = DEFAULT_QDRANT_ENABLE_WRITES = True
    QDRANT_EMBEDDING_PROVIDER = DEFAULT_QDRANT_EMBEDDING_PROVIDER = "hash"
    QDRANT_EMBEDDING_MODEL = DEFAULT_QDRANT_EMBEDDING_MODEL = "nomic-embed-text"
    QDRANT_RERANK_ENABLED = DEFAULT_QDRANT_RERANK_ENABLED = False
    QDRANT_RERANK_MODEL = DEFAULT_QDRANT_RERANK_MODEL = "qwen2.5:3b"
    QDRANT_CANDIDATE_LIMIT = DEFAULT_QDRANT_CANDIDATE_LIMIT = 20
    QDRANT_ADMIN_ACTION = ""
    OUTPUT_DIR = f"reports/testrun_{__import__('datetime').datetime.now().strftime('%Y%m%d_%H%M%S')}"
    RUN_USER_DATA_DIR = f"playwright_user_data/session_{__import__('datetime').datetime.now().strftime('%Y%m%d_%H%M%S')}"
    AXE_CDN_URL = "https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.9.1/axe.min.js"
    ACTION_COOLDOWN_SECONDS = 1.0
    ALLOWED_ACTIONS = {"click", "type", "submit_form", "handle_modal", "press_key", "scroll", "back", "random_jump", "restart_target"}
    MAX_ALLOWED_RETRIES = 10
    MAX_ALLOWED_RETRY_BASE_DELAY_SECONDS = 10.0
    SHUTDOWN_EVENT = None
    GRACEFUL_SHUTDOWN_REQUESTED = False

    def is_in_scope(*args: Any, **kwargs: Any) -> bool:
        return False

    def normalize_action_plan(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        return {}

    def split_domain_and_route(*args: Any, **kwargs: Any) -> Tuple[str, str]:
        return ("", "/")

    def build_redis_key(*args: Any, **kwargs: Any) -> str:
        return ""

    def inspect_optional_runtime_dependencies(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        return {}

try:
    from monkeylm.core import (
        main,
        DefectTracker,
        Fuzzer,
        A11yChecker,
        NetworkMonitor,
        PerformanceMonitor,
        allocate_worker_steps,
        build_worker_user_data_dir,
        with_retry_backoff,
        test_logs,
    )
except Exception:
    main = DefectTracker = Fuzzer = A11yChecker = NetworkMonitor = PerformanceMonitor = allocate_worker_steps = build_worker_user_data_dir = with_retry_backoff = test_logs = None

try:
    from monkeylm.types import (
        PersonaGoal,
        CriticalFlow,
        TestingStrategy,
        FormControlRecord,
        FormRecord,
        PageSnapshot,
        WorkerRunResult,
        DefectTicket,
    )
except Exception:
    PersonaGoal = CriticalFlow = TestingStrategy = FormControlRecord = FormRecord = PageSnapshot = WorkerRunResult = DefectTicket = Any  # type: ignore[misc]

try:
    from monkeylm.browser import (
        capture_dom_and_layout,
        compare_screenshots_pixelmatch,
        analyze_screenshot_health,
        compare_screenshots_cv,
        diff_component_manifests,
        execute_action,
        extract_component_manifest,
        get_page_state,
        handle_dialog,
        launch_context_with_fallback,
        state_to_prompt,
        wait_for_page_ready,
    )
except Exception:
    capture_dom_and_layout = compare_screenshots_pixelmatch = analyze_screenshot_health = compare_screenshots_cv = diff_component_manifests = execute_action = extract_component_manifest = get_page_state = handle_dialog = launch_context_with_fallback = state_to_prompt = wait_for_page_ready = None

try:
    from monkeylm.models import (
        build_decision_prompt,
        parse_action_plan_response,
        generate_form_payload,
    )
except Exception:
    build_decision_prompt = parse_action_plan_response = generate_form_payload = None

try:
    from monkeylm.memory import PersistenceEngine, QdrantMemoryStore
except Exception:
    PersistenceEngine = QdrantMemoryStore = None

try:
    from monkeylm.reporting import (
        generate_markdown_report,
        generate_json_summary,
        generate_pdf_report,
        summarize_semantic_memory_telemetry,
        summarize_vibe_coding_accountability,
    )
except Exception:
    generate_markdown_report = generate_json_summary = generate_pdf_report = summarize_semantic_memory_telemetry = summarize_vibe_coding_accountability = None

__all__ = [
    "Settings",
    "load_settings",
    "parse_cli_args",
    "validate_runtime_configuration",
    "apply_runtime_overrides",
    "main",
    "DefectTracker",
    "Fuzzer",
    "A11yChecker",
    "NetworkMonitor",
    "PerformanceMonitor",
    "PersistenceEngine",
    "QdrantMemoryStore",
    "PersonaGoal",
    "CriticalFlow",
    "TestingStrategy",
    "FormControlRecord",
    "FormRecord",
    "PageSnapshot",
    "WorkerRunResult",
    "DefectTicket",
    "ACTIVE_SEED",
    "TARGET_URL",
    "OLLAMA_MODEL",
    "OLLAMA_TIMEOUT_SECONDS",
    "OPENAI_BASE_URL",
    "OPENAI_API_KEY",
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
    "DEFAULT_TARGET_URL",
    "DEFAULT_OLLAMA_MODEL",
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
    "SHUTDOWN_EVENT",
    "GRACEFUL_SHUTDOWN_REQUESTED",
    "is_in_scope",
    "normalize_action_plan",
    "split_domain_and_route",
    "build_redis_key",
    "inspect_optional_runtime_dependencies",
    "capture_dom_and_layout",
    "compare_screenshots_pixelmatch",
    "analyze_screenshot_health",
    "compare_screenshots_cv",
    "diff_component_manifests",
    "execute_action",
    "extract_component_manifest",
    "get_page_state",
    "handle_dialog",
    "launch_context_with_fallback",
    "state_to_prompt",
    "wait_for_page_ready",
    "build_decision_prompt",
    "parse_action_plan_response",
    "generate_form_payload",
    "generate_markdown_report",
    "generate_json_summary",
    "generate_pdf_report",
    "summarize_semantic_memory_telemetry",
    "summarize_vibe_coding_accountability",
    "allocate_worker_steps",
    "build_worker_user_data_dir",
    "with_retry_backoff",
    "test_logs",
    "create_browser_provider",
    "create_memory_store",
    "create_model_client",
    "create_report_generator",
]
