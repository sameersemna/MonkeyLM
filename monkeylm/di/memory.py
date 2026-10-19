"""Memory store DI adapter."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from monkeylm.config import Settings, load_settings
from monkeylm.interfaces import IMemoryStore
from monkeylm.types import PageSnapshot


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
