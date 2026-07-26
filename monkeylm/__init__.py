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

from typing import Any, Dict, List, Optional

from monkeylm.interfaces import IBrowserProvider, IMemoryStore, IModelClient, IReportGenerator

from monkeylm.config import (
    Settings,
    load_settings,
    parse_cli_args,
    validate_runtime_configuration,
    apply_runtime_overrides,
    ACTIVE_SEED,
    TARGET_URL,
    OLLAMA_MODEL,
    OLLAMA_TIMEOUT_SECONDS,
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
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OLLAMA_TIMEOUT_SECONDS,
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
)
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
from monkeylm.browser import (
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
from monkeylm.models import (
    build_decision_prompt,
    parse_action_plan_response,
    generate_form_payload,
)
from monkeylm.memory import PersistenceEngine, QdrantMemoryStore
from monkeylm.reporting import (
    generate_markdown_report,
    generate_json_summary,
    generate_pdf_report,
    summarize_semantic_memory_telemetry,
    summarize_vibe_coding_accountability,
)

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
    "capture_dom_and_layout",
    "compare_screenshots_pixelmatch",
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


# ── Dependency Injection Factory Functions ───────────────────────────────────
# Note: The current codebase uses function-based modules rather than classes.
# These factory functions provide a DI-compatible interface and will be updated
# in Phase 2 when modules are refactored into proper class-based implementations.


def create_browser_provider(settings: Optional[Settings] = None) -> IBrowserProvider:
    """Factory function to create browser provider instance.

    Args:
        settings: Runtime configuration. Defaults to load_settings() if None.

    Returns:
        IBrowserProvider implementation (currently BrowserProviderAdapter).

    Example:
        >>> from monkeylm import create_browser_provider
        >>> browser = create_browser_provider()
        >>> await browser.launch()

    Note:
        Currently returns BrowserProviderAdapter which wraps browser.py functions.
        Will be replaced with proper Browser class in Phase 2 refactoring.
    """
    from monkeylm.browser import (
        execute_action,
        get_page_state,
        launch_context_with_fallback,
    )
    from playwright.async_api import async_playwright

    if settings is None:
        settings = load_settings()

    class BrowserProviderAdapter:
        """Adapter that wraps browser.py functions to implement IBrowserProvider."""

        def __init__(self, settings: Settings):
            self._settings = settings
            self._playwright = None
            self._context = None
            self._page = None

        async def launch(self) -> None:
            from monkeylm import build_worker_user_data_dir
            self._playwright = await async_playwright().start()
            user_data_dir = build_worker_user_data_dir(settings, 0)
            self._context, _ = await launch_context_with_fallback(
                self._playwright,
                settings=settings,
                user_data_dir=user_data_dir,
                worker_label="DI",
            )
            self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()

        async def navigate(self, url: str) -> PageSnapshot:
            if self._page is None:
                raise RuntimeError("Browser not launched. Call launch() first.")
            await self._page.goto(url)
            return await get_page_state(self._page, settings=self._settings)

        async def snapshot(self) -> PageSnapshot:
            if self._page is None:
                raise RuntimeError("Browser not launched. Call launch() first.")
            return await get_page_state(self._page, settings=self._settings)

        async def click(self, selector: str) -> None:
            if self._page is None:
                raise RuntimeError("Browser not launched. Call launch() first.")
            await execute_action(self._page, "click", selector, "", self._settings)

        async def type_text(self, selector: str, text: str) -> None:
            if self._page is None:
                raise RuntimeError("Browser not launched. Call launch() first.")
            await execute_action(self._page, "type", selector, text, self._settings)

        async def submit_form(self, selector: str) -> None:
            if self._page is None:
                raise RuntimeError("Browser not launched. Call launch() first.")
            await execute_action(self._page, "submit", selector, "", self._settings)

        async def close(self) -> None:
            if self._context:
                await self._context.close()
            if self._playwright:
                await self._playwright.stop()
            self._page = None
            self._context = None
            self._playwright = None

        @property
        def current_page(self) -> Optional[Any]:
            return self._page

    return BrowserProviderAdapter(settings)


def create_memory_store(settings: Optional[Settings] = None) -> IMemoryStore:
    """Factory function to create memory store instance.

    Args:
        settings: Runtime configuration. Defaults to load_settings() if None.

    Returns:
        IMemoryStore implementation (currently MemoryStoreAdapter).

    Example:
        >>> from monkeylm import create_memory_store
        >>> memory = create_memory_store()
        >>> await memory.initialize()

    Note:
        Currently returns MemoryStoreAdapter which wraps memory.py PersistenceEngine.
        Will be replaced with proper MemoryManager class in Phase 2 refactoring.
    """
    from monkeylm.memory import PersistenceEngine

    if settings is None:
        settings = load_settings()

    class MemoryStoreAdapter:
        """Adapter that wraps PersistenceEngine to implement IMemoryStore."""

        def __init__(self, settings: Settings):
            from monkeylm.core import DefectTracker
            self._settings = settings
            self._defects = DefectTracker()
            self._engine = PersistenceEngine(settings, self._defects, max_workers=1)

        async def initialize(self) -> None:
            await self._engine.initialize()

        async def save_state(
            self,
            domain: str,
            route: str,
            snapshot: PageSnapshot,
            metadata: Dict[str, Any],
        ) -> str:
            return await self._engine.save_baseline(snapshot, domain, route)

        async def load_state(
            self,
            domain: str,
            route: str,
            state_id: Optional[str] = None,
        ) -> Optional[PageSnapshot]:
            return await self._engine.load_baseline(domain, route)

        async def search_memory(
            self,
            query: str,
            domain: Optional[str] = None,
            limit: int = 20,
        ) -> List[Dict[str, Any]]:
            return await self._engine.search_similar(query, limit)

        async def acquire_lock(
            self,
            resource_key: str,
            ttl_seconds: int,
        ) -> bool:
            return await self._engine.acquire_lock(resource_key, ttl_seconds)

        async def release_lock(self, resource_key: str) -> None:
            await self._engine.release_lock(resource_key)

        async def close(self) -> None:
            await self._engine.close()

    return MemoryStoreAdapter(settings)


def create_model_client(settings: Optional[Settings] = None) -> IModelClient:
    """Factory function to create model client instance.

    Args:
        settings: Runtime configuration. Defaults to load_settings() if None.

    Returns:
        IModelClient implementation (currently ModelClientAdapter).

    Example:
        >>> from monkeylm import create_model_client
        >>> model = create_model_client()
        >>> result = await model.infer("Hello!", model="minimax-m3:cloud")

    Note:
        Currently returns ModelClientAdapter which wraps models.py functions.
        Will be replaced with proper ModelClient class in Phase 2 refactoring.
    """
    import ollama
    from monkeylm.models import (
        build_decision_prompt,
        decide_next_action as _decide_next_action,
        run_application_discovery,
    )

    if settings is None:
        settings = load_settings()

    class ModelClientAdapter:
        """Adapter that wraps models.py functions to implement IModelClient."""

        def __init__(self, settings: Settings):
            self._settings = settings

        async def infer(
            self,
            prompt: str,
            model: str,
            temperature: float = 0.2,
            top_p: float = 0.9,
            max_tokens: Optional[int] = None,
        ) -> Dict[str, Any]:
            response = ollama.chat(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": temperature, "top_p": top_p},
            )
            import json
            content = response["message"]["content"]
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                return {"raw_response": content}

        async def vision_infer(
            self,
            prompt: str,
            image_path: str,
            model: str,
        ) -> Dict[str, Any]:
            import base64
            from pathlib import Path
            with open(Path(image_path), "rb") as f:
                image_data = base64.b64encode(f.read()).decode()
            response = ollama.chat(
                model=model,
                messages=[{
                    "role": "user",
                    "content": prompt,
                    "images": [image_data],
                }],
            )
            import json
            content = response["message"]["content"]
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                return {"raw_response": content}

        async def stream(
            self,
            prompt: str,
            model: str,
            temperature: float = 0.2,
        ) -> Any:
            stream = ollama.chat(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": temperature},
                stream=True,
            )
            for chunk in stream:
                yield chunk

        def analyze_testing_strategy(
            self,
            page_snapshot: PageSnapshot,
        ) -> Dict[str, Any]:
            return run_application_discovery(page_snapshot, self._settings)

        def decide_next_action(
            self,
            page_snapshot: PageSnapshot,
            goal: str,
            history: List[Dict[str, Any]],
        ) -> Dict[str, Any]:
            prompt = build_decision_prompt(page_snapshot, goal, history, self._settings)
            return _decide_next_action(prompt, page_snapshot, self._settings)

    return ModelClientAdapter(settings)


def create_report_generator(
    format: str = "markdown",
    settings: Optional[Settings] = None,
) -> IReportGenerator:
    """Factory function to create report generator instance.

    Args:
        format: Report format ("markdown", "pdf", or "json").
        settings: Runtime configuration. Defaults to load_settings() if None.

    Returns:
        IReportGenerator implementation for specified format.

    Raises:
        ValueError: If format is not supported.

    Example:
        >>> from monkeylm import create_report_generator
        >>> generator = create_report_generator("markdown")
        >>> report_path = await generator.generate(results, "./reports", settings)

    Note:
        Currently returns adapter wrapping reporting.py generators.
        Will be replaced with proper generator classes in Phase 2 refactoring.
    """
    from monkeylm.reporting import (
        generate_markdown_report,
        generate_pdf_report,
        generate_json_summary,
    )

    if settings is None:
        settings = load_settings()

    class ReportGeneratorAdapter:
        """Adapter that wraps reporting functions to implement IReportGenerator."""

        def __init__(self, format: str, settings: Settings):
            self._format = format.lower()
            self._settings = settings

        async def generate(
            self,
            results: List[Dict[str, Any]],
            output_dir: str,
            settings: Any,
        ) -> str:
            if self._format == "markdown":
                return await generate_markdown_report(results, output_dir, self._settings)
            elif self._format == "pdf":
                return await generate_pdf_report(results, output_dir, self._settings)
            elif self._format == "json":
                return await generate_json_summary(results, output_dir, self._settings)
            else:
                raise ValueError(f"Unsupported format: {self._format}")

    return ReportGeneratorAdapter(format, settings)
