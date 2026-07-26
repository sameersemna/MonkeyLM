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
#
# KNOWN BROKEN (tracked, not a false positive): mypy adoption (see
# IMPROVEMENT_LOG.md Cycle 4) found that every adapter below calls its
# wrapped module with the wrong argument order/shape, or a method that does
# not exist (e.g. MemoryStoreAdapter calls PersistenceEngine.save_baseline,
# which was never implemented). This section has zero test coverage and is
# never called from the real CLI entrypoint, so the breakage is latent, but
# it IS exported in __all__ with docstring usage examples, so calling it as
# documented will raise. Deliberately not fixed here - correcting it needs
# real design work (e.g. IMemoryStore's generic acquire_lock/release_lock
# contract has no equivalent in the actual Redis-backed
# claim_action_path_lock mechanism) rather than a mypy-adoption-cycle patch.
# The `# type: ignore` comments below are a deliberate, tracked deferral per
# mypy's own guidance for incrementally adopting type checking on an
# existing codebase - not a silent workaround.


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
        launch(), navigate(), snapshot(), close(), and current_page work.
        click(), type_text(), and submit_form() raise NotImplementedError -
        the real execute_action() needs live fuzzer/defect-tracker/monitor
        session objects this thin adapter has no way to construct honestly.
        See IMPROVEMENT_LOG.md Cycle 5.
    """
    from monkeylm.browser import (
        get_page_state,
        launch_context_with_fallback,
    )
    from playwright.async_api import async_playwright, BrowserContext, Page, Playwright

    if settings is None:
        settings = load_settings()

    class BrowserProviderAdapter:
        """Adapter that wraps browser.py functions to implement IBrowserProvider."""

        def __init__(self, settings: Settings):
            self._settings = settings
            self._playwright: Optional[Playwright] = None
            self._context: Optional[BrowserContext] = None
            self._page: Optional[Page] = None
            self._step = 0

        async def launch(self) -> None:
            from monkeylm import build_worker_user_data_dir
            self._playwright = await async_playwright().start()
            user_data_dir = build_worker_user_data_dir(self._settings, 0)
            self._context, _ = await launch_context_with_fallback(
                self._playwright,
                settings=self._settings,
                user_data_dir=user_data_dir,
                worker_label="DI",
            )
            self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()

        async def navigate(self, url: str) -> PageSnapshot:
            if self._page is None:
                raise RuntimeError("Browser not launched. Call launch() first.")
            await self._page.goto(url)
            self._step += 1
            return await get_page_state(self._page, self._step, phase="di-navigate", output_dir=self._settings.output_dir)

        async def snapshot(self) -> PageSnapshot:
            if self._page is None:
                raise RuntimeError("Browser not launched. Call launch() first.")
            self._step += 1
            return await get_page_state(self._page, self._step, phase="di-snapshot", output_dir=self._settings.output_dir)

        # click/type_text/submit_form are intentionally not implemented: the real
        # execute_action(page, settings, action_plan, step_num, fuzzer, defects,
        # network_monitor, perf_monitor, ...) takes a structured action_plan dict
        # plus live fuzzer/defect-tracker/network-monitor/perf-monitor session
        # objects that carry accumulated state across a whole run. A thin
        # (selector) / (selector, text) adapter method can't honestly construct
        # those - see IMPROVEMENT_LOG.md Cycle 5.

        async def click(self, selector: str) -> None:
            raise NotImplementedError(
                "BrowserProviderAdapter.click: the real execute_action() needs a "
                "structured action_plan dict plus live fuzzer/defects/network_monitor/"
                "perf_monitor session objects that this thin adapter has no way to "
                "honestly construct. Use monkeylm.browser.execute_action directly "
                "within a real worker run instead."
            )

        async def type_text(self, selector: str, text: str) -> None:
            raise NotImplementedError(
                "BrowserProviderAdapter.type_text: same limitation as click() - see its error."
            )

        async def submit_form(self, selector: str) -> None:
            raise NotImplementedError(
                "BrowserProviderAdapter.submit_form: same limitation as click() - see its error."
            )

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
        initialize() and close() work. save_state(), load_state(),
        search_memory(), acquire_lock(), and release_lock() all raise
        NotImplementedError - IMemoryStore's generic contract has no
        corresponding capability in the real subsystem (baseline persistence
        is private/workflow-specific, semantic search lives on the separate
        QdrantMemoryStore class, and there is no generic resource lock).
        See IMPROVEMENT_LOG.md Cycle 5.
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

        # IMemoryStore's generic save/load/search/lock contract has no
        # corresponding capability in the real memory subsystem (see
        # IMPROVEMENT_LOG.md Cycle 5). PersistenceEngine's baseline logic is
        # private and workflow-specific (_upsert_baseline takes
        # domain/page_route/dom_structure_hash/component_manifest/
        # is_golden_standard - not "a PageSnapshot under a domain/route id"),
        # semantic search lives on the separate QdrantMemoryStore class with a
        # different signature (search_similar_layouts(page_state, limit)), and
        # there is no generic resource lock - only claim_action_path_lock(
        # path_hash, worker_label), which is TTL-expiring with no explicit
        # release. Building real implementations here means inventing new
        # subsystem capability, not fixing a wiring bug. Failing loud instead
        # of the previous silent AttributeError.

        async def save_state(
            self,
            domain: str,
            route: str,
            snapshot: PageSnapshot,
            metadata: Dict[str, Any],
        ) -> str:
            raise NotImplementedError(
                "MemoryStoreAdapter.save_state: PersistenceEngine has no generic "
                "save primitive - baseline persistence is private and workflow-"
                "specific (see PersistenceEngine.analyze_route_regression)."
            )

        async def load_state(
            self,
            domain: str,
            route: str,
            state_id: Optional[str] = None,
        ) -> Optional[PageSnapshot]:
            raise NotImplementedError(
                "MemoryStoreAdapter.load_state: PersistenceEngine has no generic "
                "load primitive matching this contract."
            )

        async def search_memory(
            self,
            query: str,
            domain: Optional[str] = None,
            limit: int = 20,
        ) -> List[Dict[str, Any]]:
            raise NotImplementedError(
                "MemoryStoreAdapter.search_memory: semantic search lives on "
                "monkeylm.memory.QdrantMemoryStore.search_similar_layouts(page_state, limit), "
                "a different class with a different signature than IMemoryStore "
                "expects. Use QdrantMemoryStore directly instead."
            )

        async def acquire_lock(
            self,
            resource_key: str,
            ttl_seconds: int,
        ) -> bool:
            raise NotImplementedError(
                "MemoryStoreAdapter.acquire_lock: no generic resource lock exists. "
                "The real primitive is PersistenceEngine.claim_action_path_lock(path_hash, worker_label), "
                "which is TTL-expiring with no explicit release and a different key shape."
            )

        async def release_lock(self, resource_key: str) -> None:
            raise NotImplementedError(
                "MemoryStoreAdapter.release_lock: no generic resource lock exists "
                "to release - see acquire_lock's error for detail."
            )

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
        infer(), vision_infer(), and stream() work. analyze_testing_strategy()
        and decide_next_action() raise NotImplementedError - the real
        run_application_discovery()/decide_next_action() are async, need a
        memory_store this Protocol never provides, and don't take a "goal"
        parameter. See IMPROVEMENT_LOG.md Cycle 5.
    """
    import asyncio
    import ollama

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
            response = await asyncio.to_thread(
                ollama.chat,
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
            response = await asyncio.to_thread(
                ollama.chat,
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
            # IModelClient declares this as a sync method returning Dict[str, Any],
            # but the real run_application_discovery(settings, page_state: str) is
            # async and returns Optional[TestingStrategy] (a dataclass, not a
            # dict), and needs page_state text from state_to_prompt(), not the raw
            # PageSnapshot. The Protocol shape doesn't match the real capability -
            # see IMPROVEMENT_LOG.md Cycle 5. Failing loud rather than silently
            # calling the wrong thing.
            raise NotImplementedError(
                "ModelClientAdapter.analyze_testing_strategy: IModelClient's sync "
                "Dict[str, Any] contract does not match run_application_discovery's "
                "real async signature (settings, page_state: str) -> "
                "Optional[TestingStrategy]. Call "
                "monkeylm.models.run_application_discovery(settings, state_to_prompt(page_snapshot)) "
                "directly instead."
            )

        def decide_next_action(
            self,
            page_snapshot: PageSnapshot,
            goal: str,
            history: List[Dict[str, Any]],
        ) -> Dict[str, Any]:
            # Same category of Protocol/reality mismatch: the real
            # decide_next_action(settings, page_state, memory_store, ...) is async,
            # requires a memory_store (raises ValueError without one), and has no
            # "goal" parameter at all - it isn't goal-directed, it drives from
            # page state + memory + testing strategy. There is no honest way to
            # implement this signature without inventing behavior. See
            # IMPROVEMENT_LOG.md Cycle 5.
            raise NotImplementedError(
                "ModelClientAdapter.decide_next_action: IModelClient's "
                "(page_snapshot, goal, history) contract has no correspondence to "
                "the real decide_next_action(settings, page_state, memory_store, ...), "
                "which is async and requires a memory_store. Call "
                "monkeylm.models.decide_next_action(settings, page_state, memory_store=...) "
                "directly instead."
            )

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
        NotImplementedError: generate() always raises this for a supported
            format - see the Note below.

    Example:
        >>> from monkeylm import create_report_generator
        >>> generator = create_report_generator("markdown")

    Note:
        Currently returns adapter wrapping reporting.py generators.
        Will be replaced with proper generator classes in Phase 2 refactoring.
        generate() raises NotImplementedError for every supported format:
        IReportGenerator's (results, output_dir, settings) -> str contract
        has no correspondence to the real generate_*_report(settings,
        defects, test_logs, browser_launch_info, start_time, end_time) ->
        None functions, which need data this method is never given and
        write files directly rather than returning a path. Call
        monkeylm.reporting functions directly instead. See
        IMPROVEMENT_LOG.md Cycle 5.
    """
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
            # IReportGenerator.generate(results, output_dir, settings) -> str has
            # no correspondence to the real generate_*_report(settings, defects,
            # test_logs, browser_launch_info, start_time, end_time) -> None
            # functions: they take a `defects` object and `test_logs` (not a
            # `results` list), also need browser_launch_info/start_time/end_time
            # this method is never given, write files directly using
            # settings.output_dir (ignoring the `output_dir` param entirely), and
            # return None, not a path string. See IMPROVEMENT_LOG.md Cycle 5.
            if self._format not in {"markdown", "pdf", "json"}:
                raise ValueError(f"Unsupported format: {self._format}")
            raise NotImplementedError(
                f"ReportGeneratorAdapter.generate({self._format}): IReportGenerator's "
                "(results, output_dir, settings) -> str contract does not match the real "
                f"generate_{self._format}_report(settings, defects, test_logs, "
                "browser_launch_info, start_time, end_time) -> None signature. Call "
                "monkeylm.reporting functions directly with the run's actual defects/"
                "test_logs/timing data instead."
            )

    return ReportGeneratorAdapter(format, settings)
