"""Protocol definitions for dependency injection and interface segregation.

This module defines abstract interfaces (using typing.Protocol) that decouple
core modules from their concrete implementations. By depending on these interfaces
instead of concrete classes, modules can be tested in isolation and swapped
without modifying dependent code.

Protocols enable structural subtyping (duck typing) with static type checking:
any class that implements the required methods automatically satisfies the protocol
without explicit inheritance.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Protocol

from playwright.async_api import Page

from monkeylm.types import PageSnapshot


# ── Browser Provider Interface ────────────────────────────────────────────────


class IBrowserProvider(Protocol):
    """Interface for browser lifecycle and page interaction.

    Implementations manage Playwright browser instances, handle navigation,
    capture page snapshots, and execute user actions (click, type, submit).

    Example implementation: monkeylm.browser.Browser
    """

    async def launch(self) -> None:
        """Launch the browser with configured settings.

        Raises:
            BrowserError: If browser fails to launch after retries.
        """
        ...

    async def navigate(self, url: str) -> PageSnapshot:
        """Navigate to URL and capture page snapshot.

        Args:
            url: Target URL to navigate to.

        Returns:
            PageSnapshot with DOM structure, layout anchors, and metadata.

        Raises:
            NavigationError: If navigation times out or fails.
        """
        ...

    async def snapshot(self) -> PageSnapshot:
        """Capture current page state without navigation.

        Returns:
            PageSnapshot representing current DOM state.
        """
        ...

    async def click(self, selector: str) -> None:
        """Click element matching selector.

        Args:
            selector: CSS selector for target element.

        Raises:
            BrowserError: If element not found or click fails.
        """
        ...

    async def type_text(self, selector: str, text: str) -> None:
        """Type text into input element.

        Args:
            selector: CSS selector for input element.
            text: Text to type.

        Raises:
            BrowserError: If element not found or not an input.
        """
        ...

    async def submit_form(self, selector: str) -> None:
        """Submit form matching selector.

        Args:
            selector: CSS selector for form element.

        Raises:
            BrowserError: If form not found or submit fails.
        """
        ...

    async def close(self) -> None:
        """Close browser and release resources."""
        ...

    @property
    def current_page(self) -> Optional[Page]:
        """Current active Playwright page, or None if not launched."""
        ...


# ── Memory Store Interface ────────────────────────────────────────────────────


class IMemoryStore(Protocol):
    """Interface for persistent state storage and retrieval.

    Implementations manage PostgreSQL state persistence, Redis session caching,
    and Qdrant vector embeddings for semantic memory.

    Example implementation: monkeylm.memory.MemoryManager
    """

    async def initialize(self) -> None:
        """Initialize all storage backends (PostgreSQL, Redis, Qdrant).

        Raises:
            PersistenceError: If any backend fails to initialize.
        """
        ...

    async def save_state(
        self,
        domain: str,
        route: str,
        snapshot: PageSnapshot,
        metadata: Dict[str, Any],
    ) -> str:
        """Save page state to persistent storage.

        Args:
            domain: Domain name (e.g., 'example.com').
            route: Normalized route path (e.g., '/login').
            snapshot: PageSnapshot to persist.
            metadata: Additional context (timestamp, worker_id, etc.).

        Returns:
            Unique state identifier for retrieval.

        Raises:
            PersistenceError: If save operation fails.
        """
        ...

    async def load_state(
        self,
        domain: str,
        route: str,
        state_id: Optional[str] = None,
    ) -> Optional[PageSnapshot]:
        """Load page state from persistent storage.

        Args:
            domain: Domain name.
            route: Normalized route path.
            state_id: Optional specific state ID (defaults to latest).

        Returns:
            PageSnapshot if found, None otherwise.

        Raises:
            PersistenceError: If load operation fails.
        """
        ...

    async def search_memory(
        self,
        query: str,
        domain: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Search semantic memory for similar states.

        Args:
            query: Search query (text or structured).
            domain: Optional domain filter.
            limit: Maximum results to return.

        Returns:
            List of matching state records with similarity scores.

        Raises:
            PersistenceError: If search operation fails.
        """
        ...

    async def acquire_lock(
        self,
        resource_key: str,
        ttl_seconds: int,
    ) -> bool:
        """Acquire distributed lock on resource.

        Args:
            resource_key: Unique resource identifier.
            ttl_seconds: Lock time-to-live.

        Returns:
            True if lock acquired, False if already held.
        """
        ...

    async def release_lock(self, resource_key: str) -> None:
        """Release distributed lock.

        Args:
            resource_key: Resource identifier to unlock.
        """
        ...

    async def close(self) -> None:
        """Close all storage connections."""
        ...


# ── Model Client Interface ────────────────────────────────────────────────────


class IModelClient(Protocol):
    """Interface for LLM inference and vision model routing.

    Implementations handle Ollama model communication, vision model selection,
    prompt engineering, and response parsing.

    Example implementation: monkeylm.models.ModelClient
    """

    async def infer(
        self,
        prompt: str,
        model: str,
        temperature: float = 0.2,
        top_p: float = 0.9,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Run text inference with specified model.

        Args:
            prompt: Input prompt for model.
            model: Model name (e.g., 'minimax-m3:cloud').
            temperature: Sampling temperature (0.0-1.0).
            top_p: Nucleus sampling parameter.
            max_tokens: Optional token limit.

        Returns:
            Parsed model response as dictionary.

        Raises:
            ModelError: If inference fails or times out.
        """
        ...

    async def vision_infer(
        self,
        prompt: str,
        image_path: str,
        model: str,
    ) -> Dict[str, Any]:
        """Run vision inference on image.

        Args:
            prompt: Instruction for vision model.
            image_path: Path to image file.
            model: Vision model name (e.g., 'gemini-3-flash-preview').

        Returns:
            Parsed vision model response.

        Raises:
            ModelError: If inference fails or times out.
        """
        ...

    async def stream(
        self,
        prompt: str,
        model: str,
        temperature: float = 0.2,
    ) -> Any:
        """Stream model response tokens.

        Args:
            prompt: Input prompt.
            model: Model name.
            temperature: Sampling temperature.

        Yields:
            Response chunks as received.

        Raises:
            ModelError: If streaming fails.
        """
        ...

    def analyze_testing_strategy(
        self,
        page_snapshot: PageSnapshot,
    ) -> Dict[str, Any]:
        """Analyze page and generate testing strategy.

        Args:
            page_snapshot: Current page state.

        Returns:
            TestingStrategy with personas, flows, and edge cases.
        """
        ...

    def decide_next_action(
        self,
        page_snapshot: PageSnapshot,
        goal: str,
        history: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Decide next action based on page state and goal.

        Args:
            page_snapshot: Current page state.
            goal: User-defined testing goal.
            history: Previous action history.

        Returns:
            Action decision with selector, action type, and value.
        """
        ...


# ── Reporting Interface ──────────────────────────────────────────────────────


class IReportGenerator(Protocol):
    """Interface for report generation.

    Implementations produce test run reports in various formats (Markdown, PDF, JSON).

    Example implementations: monkeylm.reporting.MarkdownReportGenerator, etc.
    """

    async def generate(
        self,
        results: List[Dict[str, Any]],
        output_dir: str,
        settings: Any,
    ) -> str:
        """Generate report from test results.

        Args:
            results: List of worker run results.
            output_dir: Directory for report output.
            settings: Runtime configuration.

        Returns:
            Path to generated report file.

        Raises:
            RuntimeError: If report generation fails.
        """
        ...


# ── Abstract Base Classes for Concrete Implementations ───────────────────────
# Note: These are optional convenience bases that implementations can inherit
# from, but protocols above are preferred for type hints to allow duck typing.


class BrowserProviderBase(ABC):
    """Abstract base class for browser provider implementations.

    Convenience base class that implements IBrowserProvider.
    Subclasses must implement all abstract methods.
    """

    @abstractmethod
    async def launch(self) -> None:
        pass

    @abstractmethod
    async def navigate(self, url: str) -> PageSnapshot:
        pass

    @abstractmethod
    async def snapshot(self) -> PageSnapshot:
        pass

    @abstractmethod
    async def click(self, selector: str) -> None:
        pass

    @abstractmethod
    async def type_text(self, selector: str, text: str) -> None:
        pass

    @abstractmethod
    async def submit_form(self, selector: str) -> None:
        pass

    @abstractmethod
    async def close(self) -> None:
        pass

    @property
    @abstractmethod
    def current_page(self) -> Optional[Page]:
        pass


class MemoryStoreBase(ABC):
    """Abstract base class for memory store implementations.

    Convenience base class that implements IMemoryStore.
    """

    @abstractmethod
    async def initialize(self) -> None:
        pass

    @abstractmethod
    async def save_state(
        self,
        domain: str,
        route: str,
        snapshot: PageSnapshot,
        metadata: Dict[str, Any],
    ) -> str:
        pass

    @abstractmethod
    async def load_state(
        self,
        domain: str,
        route: str,
        state_id: Optional[str] = None,
    ) -> Optional[PageSnapshot]:
        pass

    @abstractmethod
    async def search_memory(
        self,
        query: str,
        domain: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    async def acquire_lock(
        self,
        resource_key: str,
        ttl_seconds: int,
    ) -> bool:
        pass

    @abstractmethod
    async def release_lock(self, resource_key: str) -> None:
        pass

    @abstractmethod
    async def close(self) -> None:
        pass


class ModelClientBase(ABC):
    """Abstract base class for model client implementations.

    Convenience base class that implements IModelClient.
    """

    @abstractmethod
    async def infer(
        self,
        prompt: str,
        model: str,
        temperature: float = 0.2,
        top_p: float = 0.9,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def vision_infer(
        self,
        prompt: str,
        image_path: str,
        model: str,
    ) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def stream(
        self,
        prompt: str,
        model: str,
        temperature: float = 0.2,
    ) -> Any:
        pass

    @abstractmethod
    def analyze_testing_strategy(
        self,
        page_snapshot: PageSnapshot,
    ) -> Dict[str, Any]:
        pass

    @abstractmethod
    def decide_next_action(
        self,
        page_snapshot: PageSnapshot,
        goal: str,
        history: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        pass


__all__ = [
    "IBrowserProvider",
    "IMemoryStore",
    "IModelClient",
    "IReportGenerator",
    "BrowserProviderBase",
    "MemoryStoreBase",
    "ModelClientBase",
]
