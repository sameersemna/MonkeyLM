"""Convenience abstract base classes for concrete interface implementations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from playwright.async_api import Page

from monkeylm.types import PageSnapshot


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
