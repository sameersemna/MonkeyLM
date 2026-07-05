"""PostgreSQL, Redis, and Qdrant persistence layers for MonkeyLM."""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import ollama
from playwright.async_api import Page

from monkeylm.config import (
    Settings,
    _local_service_log,
    asyncpg,
    build_redis_key,
    httpx,
    redis_asyncio,
    split_domain_and_route,
)


# ── Hash-based embedding helpers ───────────────────────────────────────────────


def _stable_text_vector(text: str, vector_size: int) -> List[float]:
    """Deterministic hash-based sparse vector fallback for Qdrant embeddings."""
    vector = [0.0] * vector_size
    tokens = re.findall(r"[a-zA-Z0-9_#.-]+", text.lower())
    if not tokens:
        return vector

    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        idx = int.from_bytes(digest[:4], "big") % vector_size
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        weight = 1.0 + (digest[5] / 255.0)
        vector[idx] += sign * weight

    norm = sum(value * value for value in vector) ** 0.5
    if norm > 0.0:
        vector = [value / norm for value in vector]
    return vector


def simplify_layout_query(page_state: str) -> str:
    """Condense a page state string into a short, Qdrant-friendly query."""
    lines = [line.strip() for line in page_state.splitlines() if line.strip()]
    selected: List[str] = []
    for line in lines:
        if line.startswith("URL:") or line.startswith("Title:"):
            selected.append(line)
            continue
        if line.startswith("[id="):
            selected.append(line)
    if not selected:
        selected = lines[:80]
    return "\n".join(selected[:120])


# ── PersistenceEngine (PostgreSQL + Redis) ─────────────────────────────────────


class PersistenceEngine:
    """Manages PostgreSQL baseline tables and Redis worker state locks."""

    def __init__(self, settings: Settings, defects: Any, max_workers: int = 1) -> None:
        self.settings = settings
        self.defects = defects
        self.max_workers = max(1, max_workers)
        self.pg_pool = None
        self.redis_client = None
        self.pg_write_semaphore = asyncio.Semaphore(max(2, self.max_workers * 2))
        self.redis_write_semaphore = asyncio.Semaphore(max(4, self.max_workers * 4))

    async def initialize(self) -> None:
        await self._initialize_postgres()
        await self._initialize_redis()

        if self.settings.strict_persistence:
            missing: List[str] = []
            if self.pg_pool is None:
                missing.append("PostgreSQL")
            if self.redis_client is None:
                missing.append("Redis")
            if missing:
                raise RuntimeError(
                    "Persistence strict mode requires services to be reachable. Missing: " + ", ".join(missing)
                )

    async def _initialize_postgres(self) -> None:
        if asyncpg is None:
            _local_service_log("asyncpg not installed; PostgreSQL persistence disabled.", self.settings.output_dir)
            return

        try:
            min_size = max(1, min(4, self.max_workers))
            max_size = max(4, self.max_workers * 4)
            self.pg_pool = await asyncpg.create_pool(
                self.settings.postgres_dsn,
                min_size=min_size,
                max_size=max_size,
                command_timeout=30,
            )
            async with self.pg_pool.acquire() as conn:
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS app_baselines (
                        id BIGSERIAL PRIMARY KEY,
                        domain TEXT NOT NULL,
                        page_route TEXT NOT NULL,
                        dom_structure_hash TEXT NOT NULL,
                        component_manifest JSONB NOT NULL,
                        is_golden_standard BOOLEAN NOT NULL DEFAULT FALSE,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        UNIQUE(domain, page_route, dom_structure_hash, is_golden_standard)
                    )
                    """
                )
                await conn.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_app_baselines_one_golden_per_route
                    ON app_baselines(domain, page_route)
                    WHERE is_golden_standard = TRUE
                    """
                )
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS regression_drift_log (
                        id BIGSERIAL PRIMARY KEY,
                        domain TEXT NOT NULL,
                        page_route TEXT NOT NULL,
                        defect_tag TEXT NOT NULL,
                        severity TEXT NOT NULL,
                        missing_components JSONB NOT NULL DEFAULT '[]'::jsonb,
                        broken_selectors JSONB NOT NULL DEFAULT '[]'::jsonb,
                        drift_alert JSONB NOT NULL DEFAULT '{}'::jsonb,
                        step_number INTEGER NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                await conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_regression_drift_log_route_time
                    ON regression_drift_log(domain, page_route, created_at DESC)
                    """
                )
            print("✅ PostgreSQL baseline tables are ready.")
        except Exception as exc:
            _local_service_log(f"PostgreSQL initialization failed: {exc}", self.settings.output_dir)
            if self.pg_pool is not None:
                try:
                    await self.pg_pool.close()
                except Exception:
                    pass
            self.pg_pool = None

    async def _initialize_redis(self) -> None:
        if redis_asyncio is None:
            _local_service_log("redis package not installed; Redis state cache disabled.", self.settings.output_dir)
            return

        try:
            self.redis_client = redis_asyncio.from_url(
                self.settings.redis_url,
                decode_responses=True,
                max_connections=max(16, self.max_workers * 8),
                socket_connect_timeout=5,
                socket_timeout=5,
                health_check_interval=30,
                retry_on_timeout=True,
            )
            await self.redis_client.ping()
            print("✅ Redis state cache is ready.")
        except Exception as exc:
            _local_service_log(f"Redis initialization failed: {exc}", self.settings.output_dir)
            self.redis_client = None

    async def close(self) -> None:
        if self.pg_pool is not None:
            try:
                await self.pg_pool.close()
            except Exception as exc:
                _local_service_log(f"Failed to close PostgreSQL pool cleanly: {exc}", self.settings.output_dir)
            self.pg_pool = None

        if self.redis_client is not None:
            try:
                close_method = getattr(self.redis_client, "aclose", None)
                if close_method is None:
                    close_method = self.redis_client.close
                await close_method()
            except Exception as exc:
                _local_service_log(f"Failed to close Redis client cleanly: {exc}", self.settings.output_dir)
            self.redis_client = None

    async def increment_visited_state(self, state_key: str) -> Optional[int]:
        if self.redis_client is None:
            return None

        redis_key = f"monkeylm:visited_states:{self.settings.timestamp}"
        try:
            async with self.redis_write_semaphore:
                prefixed_key = build_redis_key(self.settings.redis_prefix, redis_key)
                count = await self.redis_client.hincrby(prefixed_key, state_key, 1)
                await self.redis_client.expire(prefixed_key, self.settings.redis_state_ttl_seconds)
            return int(count)
        except Exception as exc:
            _local_service_log(f"Redis visited-state update failed: {exc}", self.settings.output_dir)
            return None

    async def claim_action_path_lock(self, path_hash: str, worker_label: str) -> bool:
        """Try to claim a cross-worker action-path lock in Redis."""
        if self.redis_client is None:
            return True

        redis_key = f"monkeylm:active_path:{path_hash}"
        try:
            async with self.redis_write_semaphore:
                prefixed_key = build_redis_key(self.settings.redis_prefix, redis_key)
                acquired = await self.redis_client.set(
                    prefixed_key,
                    worker_label,
                    nx=True,
                    ex=self.settings.redis_path_lock_ttl_seconds,
                )
                if acquired:
                    return True

                current_owner = await self.redis_client.get(prefixed_key)
                if current_owner and current_owner.decode("utf-8") == worker_label:
                    return True
            return False
        except Exception as exc:
            _local_service_log(f"Redis action-path lock claim failed: {exc}", self.settings.output_dir)
            return True

    async def _fetch_golden_baseline(self, domain: str, page_route: str) -> Optional[Dict[str, Any]]:
        if self.pg_pool is None:
            return None

        try:
            async with self.pg_pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT dom_structure_hash, component_manifest
                    FROM app_baselines
                    WHERE domain = $1 AND page_route = $2 AND is_golden_standard = TRUE
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    domain,
                    page_route,
                )
            if row is None:
                return None

            manifest_value = row["component_manifest"]
            if isinstance(manifest_value, str):
                try:
                    manifest_value = json.loads(manifest_value)
                except Exception:
                    manifest_value = []

            return {
                "dom_structure_hash": row["dom_structure_hash"],
                "component_manifest": manifest_value if isinstance(manifest_value, list) else [],
            }
        except Exception as exc:
            _local_service_log(f"Failed to fetch golden baseline: {exc}", self.settings.output_dir)
            return None

    async def _upsert_baseline(
        self,
        domain: str,
        page_route: str,
        dom_structure_hash: str,
        component_manifest: List[Dict[str, Any]],
        is_golden_standard: bool,
    ) -> None:
        if self.pg_pool is None:
            return

        try:
            manifest_json = json.dumps(component_manifest)
            async with self.pg_write_semaphore:
                async with self.pg_pool.acquire() as conn:
                    if is_golden_standard:
                        async with conn.transaction():
                            await conn.execute(
                                """
                                DELETE FROM app_baselines
                                WHERE domain = $1 AND page_route = $2 AND is_golden_standard = TRUE
                                """,
                                domain,
                                page_route,
                            )
                            await conn.execute(
                                """
                                INSERT INTO app_baselines (
                                    domain, page_route, dom_structure_hash,
                                    component_manifest, is_golden_standard, updated_at
                                ) VALUES ($1, $2, $3, $4::jsonb, TRUE, NOW())
                                """,
                                domain,
                                page_route,
                                dom_structure_hash,
                                manifest_json,
                            )
                    else:
                        await conn.execute(
                            """
                            INSERT INTO app_baselines (
                                domain, page_route, dom_structure_hash,
                                component_manifest, is_golden_standard, updated_at
                            ) VALUES ($1, $2, $3, $4::jsonb, FALSE, NOW())
                            ON CONFLICT (domain, page_route, dom_structure_hash, is_golden_standard)
                            DO UPDATE SET
                                component_manifest = EXCLUDED.component_manifest,
                                updated_at = NOW()
                            """,
                            domain,
                            page_route,
                            dom_structure_hash,
                            manifest_json,
                        )
        except Exception as exc:
            _local_service_log(f"Failed to upsert baseline data: {exc}", self.settings.output_dir)

    async def _insert_regression_drift_log(
        self,
        *,
        domain: str,
        page_route: str,
        step_number: int,
        defect_tag: str,
        severity: str,
        missing_components: List[Dict[str, Any]],
        broken_selectors: List[str],
        drift_alert: Dict[str, Any],
    ) -> None:
        if self.pg_pool is None:
            return

        try:
            async with self.pg_write_semaphore:
                async with self.pg_pool.acquire() as conn:
                    await conn.execute(
                        """
                        INSERT INTO regression_drift_log (
                            domain, page_route, defect_tag, severity,
                            missing_components, broken_selectors, drift_alert, step_number
                        ) VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7::jsonb, $8)
                        """,
                        domain,
                        page_route,
                        defect_tag,
                        severity,
                        json.dumps(missing_components),
                        json.dumps(broken_selectors),
                        json.dumps(drift_alert),
                        step_number,
                    )
        except Exception as exc:
            _local_service_log(f"Failed to insert regression drift log row: {exc}", self.settings.output_dir)

    async def analyze_route_regression(self, page: Page, snapshot: Any, step_num: int) -> None:
        """Analyze current route against golden baseline for component regressions.

        Import extract_component_manifest and diff_component_manifests lazily to avoid
        circular imports with browser.py.
        """
        from monkeylm.browser import diff_component_manifests, extract_component_manifest

        domain, page_route = split_domain_and_route(snapshot.url)
        if not domain:
            return

        component_manifest = await extract_component_manifest(page)
        await self._upsert_baseline(
            domain=domain,
            page_route=page_route,
            dom_structure_hash=snapshot.structure_hash,
            component_manifest=component_manifest,
            is_golden_standard=False,
        )

        golden = await self._fetch_golden_baseline(domain, page_route)
        if golden is None:
            if self.settings.golden_baseline_mode == "auto_upsert":
                await self._upsert_baseline(
                    domain=domain,
                    page_route=page_route,
                    dom_structure_hash=snapshot.structure_hash,
                    component_manifest=component_manifest,
                    is_golden_standard=True,
                )
                _local_service_log(
                    f"Auto-seeded golden baseline for {domain}{page_route}.", self.settings.output_dir
                )
            else:
                _local_service_log(
                    f"Golden baseline missing for {domain}{page_route}; comparison skipped.",
                    self.settings.output_dir,
                )
            return

        missing_components, broken_selectors = diff_component_manifests(
            golden_manifest=golden.get("component_manifest", []),
            current_manifest=component_manifest,
        )
        if not missing_components:
            return

        expected_baseline_components = len(golden.get("component_manifest", []) or [])
        expected_baseline_components = max(expected_baseline_components, len(missing_components))

        defect_tag = "Vibe-Code-Regression-Missing-Component"
        drift_alert = {
            "current_dom_structure_hash": snapshot.structure_hash,
            "golden_dom_structure_hash": golden.get("dom_structure_hash", ""),
            "missing_count": len(missing_components),
            "expected_baseline_components": expected_baseline_components,
            "missing_preview": missing_components[:10],
        }

        self.defects.add(
            "regression_findings",
            {
                "step": step_num,
                "type": defect_tag,
                "severity": "high",
                "domain": domain,
                "page_route": page_route,
                "missing_components": missing_components,
                "broken_selectors": broken_selectors,
                "expected_baseline_components": expected_baseline_components,
                "current_component_count": len(component_manifest),
                "url": snapshot.url,
            },
        )

        await self._insert_regression_drift_log(
            domain=domain,
            page_route=page_route,
            step_number=step_num,
            defect_tag=defect_tag,
            severity="high",
            missing_components=missing_components,
            broken_selectors=broken_selectors,
            drift_alert=drift_alert,
        )


# ── QdrantMemoryStore ─────────────────────────────────────────────────────────


class QdrantMemoryStore:
    """Semantic memory store backed by Qdrant with optional Ollama embeddings and reranking."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = None
        self.enabled = False
        self.reads_enabled = settings.qdrant_enable_reads
        self.writes_enabled = settings.qdrant_enable_writes
        self.embedding_provider = settings.qdrant_embedding_provider
        self.embedding_model = settings.qdrant_embedding_model
        self.vector_size = settings.qdrant_vector_size
        self.rerank_enabled = settings.qdrant_rerank_enabled
        self.rerank_model = settings.qdrant_rerank_model
        self.candidate_limit = settings.qdrant_candidate_limit
        self._ollama_embedding_warned = False
        self._ollama_rerank_warned = False
        self._last_search_telemetry: Dict[str, Any] = {}
        self._last_write_telemetry: Dict[str, Any] = {}

    def consume_last_search_telemetry(self) -> Dict[str, Any]:
        telemetry = dict(self._last_search_telemetry)
        self._last_search_telemetry = {}
        return telemetry

    def consume_last_write_telemetry(self) -> Dict[str, Any]:
        telemetry = dict(self._last_write_telemetry)
        self._last_write_telemetry = {}
        return telemetry

    # ── Embedding helpers ────────────────────────────────────────────────

    def _extract_embedding_from_response(self, result: Any) -> Optional[List[float]]:
        if isinstance(result, dict):
            embedding = result.get("embedding")
            if isinstance(embedding, list) and embedding:
                return [float(x) for x in embedding]
            embeddings = result.get("embeddings")
            if isinstance(embeddings, list) and embeddings:
                first = embeddings[0]
                if isinstance(first, list) and first:
                    return [float(x) for x in first]
        return None

    def _ollama_embed_sync(self, text: str) -> Optional[List[float]]:
        try:
            response = ollama.embed(model=self.embedding_model, input=text)
            vector = self._extract_embedding_from_response(response)
            if vector:
                return vector
        except Exception:
            pass

        try:
            response = ollama.embeddings(model=self.embedding_model, prompt=text)
            vector = self._extract_embedding_from_response(response)
            if vector:
                return vector
        except Exception:
            pass
        return None

    async def _vectorize(self, text: str) -> List[float]:
        if self.embedding_provider == "ollama":
            try:
                vector = await asyncio.to_thread(self._ollama_embed_sync, text)
                if vector:
                    return vector
            except Exception as exc:
                if not self._ollama_embedding_warned:
                    _local_service_log(
                        f"Ollama embedding failed, falling back to hash vectors: {exc}",
                        self.settings.output_dir,
                    )
                    self._ollama_embedding_warned = True

        return _stable_text_vector(text, self.vector_size)

    async def _vectorize_with_telemetry(self, text: str) -> Tuple[List[float], Dict[str, Any]]:
        started = time.perf_counter()
        provider_used = self.embedding_provider
        fallback_used = False

        if self.embedding_provider == "ollama":
            try:
                vector = await asyncio.to_thread(self._ollama_embed_sync, text)
                if vector:
                    elapsed = (time.perf_counter() - started) * 1000.0
                    return vector, {
                        "provider_used": "ollama",
                        "fallback_used": False,
                        "vector_size": len(vector),
                        "vectorize_ms": round(elapsed, 3),
                    }
            except Exception:
                fallback_used = True

            provider_used = "hash"

        vector = _stable_text_vector(text, self.vector_size)
        elapsed = (time.perf_counter() - started) * 1000.0
        return vector, {
            "provider_used": provider_used,
            "fallback_used": fallback_used,
            "vector_size": len(vector),
            "vectorize_ms": round(elapsed, 3),
        }

    # ── Collection management ────────────────────────────────────────────

    async def _ensure_collection(self) -> None:
        if self.client is None:
            return
        payload = {"vectors": {"size": self.vector_size, "distance": "Cosine"}}
        response = await self.client.put(
            f"{self.settings.qdrant_url}/collections/{self.settings.qdrant_collection}",
            json=payload,
        )
        if response.status_code == 409:
            return
        if response.status_code >= 400:
            raise RuntimeError(
                f"collection create returned {response.status_code}: {response.text[:200]}"
            )

    async def initialize(self, for_admin: bool = False) -> None:
        self.reads_enabled = self.settings.qdrant_enable_reads
        self.writes_enabled = self.settings.qdrant_enable_writes
        self.embedding_provider = self.settings.qdrant_embedding_provider
        self.embedding_model = self.settings.qdrant_embedding_model
        self.vector_size = self.settings.qdrant_vector_size
        self.rerank_enabled = self.settings.qdrant_rerank_enabled
        self.rerank_model = self.settings.qdrant_rerank_model
        self.candidate_limit = self.settings.qdrant_candidate_limit

        if not for_admin and not (self.reads_enabled or self.writes_enabled):
            _local_service_log(
                "Qdrant reads and writes are disabled by configuration.", self.settings.output_dir
            )
            self.enabled = False
            return

        if httpx is None:
            _local_service_log(
                "httpx is unavailable; Qdrant semantic memory is disabled.", self.settings.output_dir
            )
            self.enabled = False
            return

        try:
            self.client = httpx.AsyncClient(timeout=6.0)
            health = await self.client.get(f"{self.settings.qdrant_url}/collections")
            if health.status_code >= 400:
                raise RuntimeError(f"collections endpoint returned {health.status_code}")

            if self.embedding_provider == "ollama":
                probe_vector = await asyncio.to_thread(
                    self._ollama_embed_sync, "monkeylm semantic memory bootstrap"
                )
                if probe_vector:
                    self.vector_size = len(probe_vector)
                else:
                    _local_service_log(
                        "Unable to resolve Ollama embedding vector size during startup; falling back to hash vectors.",
                        self.settings.output_dir,
                    )
                    self.embedding_provider = "hash"

            await self._ensure_collection()

            self.enabled = True
            print("✅ Qdrant semantic memory is ready.")
        except Exception as exc:
            _local_service_log(f"Qdrant initialization failed: {exc}", self.settings.output_dir)
            self.enabled = False

    async def close(self) -> None:
        if self.client is not None:
            try:
                await self.client.aclose()
            except Exception as exc:
                _local_service_log(
                    f"Failed to close Qdrant client cleanly: {exc}", self.settings.output_dir
                )
            self.client = None
        self.enabled = False

    async def inspect_collection(self) -> Dict[str, Any]:
        if self.client is None:
            return {"collection": self.settings.qdrant_collection, "exists": False, "error": "client_not_initialized"}

        try:
            response = await self.client.get(
                f"{self.settings.qdrant_url}/collections/{self.settings.qdrant_collection}"
            )
            if response.status_code == 404:
                return {"collection": self.settings.qdrant_collection, "exists": False}
            if response.status_code >= 400:
                return {
                    "collection": self.settings.qdrant_collection,
                    "exists": False,
                    "error": f"status={response.status_code}",
                    "raw": response.text[:200],
                }

            data = response.json().get("result", {})
            config = data.get("config", {}).get("params", {}).get("vectors", {})
            return {
                "collection": self.settings.qdrant_collection,
                "exists": True,
                "points_count": data.get("points_count", 0),
                "indexed_vectors_count": data.get("indexed_vectors_count", 0),
                "vector_size": config.get("size", self.vector_size),
                "distance": config.get("distance", "Cosine"),
                "status": data.get("status", "unknown"),
            }
        except Exception as exc:
            return {"collection": self.settings.qdrant_collection, "exists": False, "error": str(exc)}

    async def clear_collection(self) -> Dict[str, Any]:
        if self.client is None:
            return {"ok": False, "error": "client_not_initialized"}

        try:
            delete_response = await self.client.delete(
                f"{self.settings.qdrant_url}/collections/{self.settings.qdrant_collection}"
            )
            if delete_response.status_code not in {200, 202, 404}:
                return {
                    "ok": False,
                    "error": f"delete_failed_status={delete_response.status_code}",
                    "raw": delete_response.text[:200],
                }

            await self._ensure_collection()
            info = await self.inspect_collection()
            info["ok"] = True
            info["action"] = "cleared_and_recreated"
            return info
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # ── Reranking ────────────────────────────────────────────────────────

    def _parse_rerank_response(self, content: Any) -> List[int]:
        if not isinstance(content, str):
            return []
        cleaned = content.replace("```json", "").replace("```", "").strip()
        if not cleaned:
            return []

        parsed: Optional[Dict[str, Any]] = None
        try:
            value = json.loads(cleaned)
            if isinstance(value, dict):
                parsed = value
        except Exception:
            match = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if match:
                try:
                    value = json.loads(match.group(0))
                    if isinstance(value, dict):
                        parsed = value
                except Exception:
                    parsed = None

        if not parsed:
            return []

        ranked_indices = parsed.get("ranked_indices", [])
        normalized: List[int] = []
        if isinstance(ranked_indices, list):
            for item in ranked_indices:
                if isinstance(item, int) and item >= 0:
                    normalized.append(item)
        return normalized

    def _build_rerank_prompt(self, query_text: str, candidates: List[Dict[str, Any]], final_limit: int) -> str:
        candidate_rows: List[str] = []
        for idx, memory in enumerate(candidates):
            summary = str(memory.get("layout_summary", ""))[:500]
            action = str(memory.get("action", ""))[:120]
            outcome = str(memory.get("outcome", ""))[:220]
            score = float(memory.get("score", 0.0))
            candidate_rows.append(
                f"[{idx}] score={score:.4f} action={action} outcome={outcome} layout={summary}"
            )

        return (
            "You are ranking historical web-testing memories for relevance to a current page layout query.\n"
            "Return strictly JSON with key ranked_indices containing unique candidate indices in best-first order.\n"
            f"Select exactly {final_limit} indices when possible.\n\n"
            f"Query:\n{query_text[:1200]}\n\n"
            "Candidates:\n" + "\n".join(candidate_rows) + "\n\nOutput format:\n{\"ranked_indices\": [0, 2, 1]}"
        )

    async def _rerank_memories(
        self, query_text: str, candidates: List[Dict[str, Any]], final_limit: int
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        started = time.perf_counter()
        if not candidates:
            return [], {
                "rerank_enabled": self.rerank_enabled,
                "rerank_applied": False,
                "rerank_model": self.rerank_model,
                "rerank_ms": round((time.perf_counter() - started) * 1000.0, 3),
            }

        if not self.rerank_enabled:
            return candidates[:final_limit], {
                "rerank_enabled": False,
                "rerank_applied": False,
                "rerank_model": self.rerank_model,
                "rerank_ms": round((time.perf_counter() - started) * 1000.0, 3),
            }

        try:
            # Log which model is being used for reranking
            print(f"   └─ 🧠 Reranking memories with {self.rerank_model} (limit={final_limit})")
            
            prompt = self._build_rerank_prompt(query_text, candidates, final_limit)
            response = await asyncio.to_thread(
                ollama.chat,
                model=self.rerank_model,
                messages=[{"role": "user", "content": prompt}],
                format="json",
                options={"temperature": 0.0, "top_p": 0.9},
            )
            ranked_indices = self._parse_rerank_response(response.get("message", {}).get("content", ""))
            if not ranked_indices:
                return candidates[:final_limit], {
                    "rerank_enabled": True,
                    "rerank_applied": False,
                    "rerank_model": self.rerank_model,
                    "rerank_ms": round((time.perf_counter() - started) * 1000.0, 3),
                    "reason": "empty_ranking",
                }

            picked: List[Dict[str, Any]] = []
            seen: set = set()
            for idx in ranked_indices:
                if idx in seen or idx < 0 or idx >= len(candidates):
                    continue
                seen.add(idx)
                item = dict(candidates[idx])
                item["rerank_model"] = self.rerank_model
                picked.append(item)
                if len(picked) >= final_limit:
                    break

            if len(picked) < final_limit:
                for item in candidates:
                    if item in picked:
                        continue
                    picked.append(item)
                    if len(picked) >= final_limit:
                        break
            return picked, {
                "rerank_enabled": True,
                "rerank_applied": True,
                "rerank_model": self.rerank_model,
                "rerank_ms": round((time.perf_counter() - started) * 1000.0, 3),
            }
        except Exception as exc:
            if not self._ollama_rerank_warned:
                _local_service_log(
                    f"Ollama reranker failed, falling back to vector ranking: {exc}",
                    self.settings.output_dir,
                )
                self._ollama_rerank_warned = True
            return candidates[:final_limit], {
                "rerank_enabled": True,
                "rerank_applied": False,
                "rerank_model": self.rerank_model,
                "rerank_ms": round((time.perf_counter() - started) * 1000.0, 3),
                "error": str(exc),
            }

    # ── Search & write ───────────────────────────────────────────────────

    async def search_similar_layouts(self, page_state: str, limit: int = 3) -> List[Dict[str, Any]]:
        started_total = time.perf_counter()
        if not self.enabled or self.client is None or not self.reads_enabled:
            self._last_search_telemetry = {
                "enabled": False,
                "reads_enabled": self.reads_enabled,
                "returned_count": 0,
                "total_ms": round((time.perf_counter() - started_total) * 1000.0, 3),
            }
            return []

        query_text = simplify_layout_query(page_state)
        query_vector, vector_meta = await self._vectorize_with_telemetry(query_text)
        candidate_limit = max(limit, self.candidate_limit)
        body = {
            "vector": query_vector,
            "limit": max(1, min(candidate_limit, 50)),
            "with_payload": True,
        }

        try:
            search_started = time.perf_counter()
            response = await self.client.post(
                f"{self.settings.qdrant_url}/collections/{self.settings.qdrant_collection}/points/search",
                json=body,
            )
            search_ms = (time.perf_counter() - search_started) * 1000.0
            if response.status_code >= 400:
                _local_service_log(
                    f"Qdrant search failed ({response.status_code}): {response.text[:200]}",
                    self.settings.output_dir,
                )
                self._last_search_telemetry = {
                    "enabled": True,
                    "reads_enabled": self.reads_enabled,
                    "returned_count": 0,
                    "status": "search_failed",
                    "status_code": response.status_code,
                    "vectorize_ms": vector_meta.get("vectorize_ms", 0.0),
                    "qdrant_search_ms": round(search_ms, 3),
                    "total_ms": round((time.perf_counter() - started_total) * 1000.0, 3),
                }
                return []

            data = response.json()
            points = data.get("result", [])
            memories: List[Dict[str, Any]] = []
            for point in points:
                payload = point.get("payload", {})
                memories.append(
                    {
                        "layout_summary": payload.get("layout_summary", ""),
                        "action": payload.get("action", ""),
                        "outcome": payload.get("outcome", ""),
                        "url": payload.get("url", ""),
                        "score": float(point.get("score", 0.0)),
                        "vector_rank": len(memories) + 1,
                    }
                )
            reranked, rerank_meta = await self._rerank_memories(
                query_text, memories, final_limit=max(1, min(limit, 10))
            )
            scores = [float(item.get("score", 0.0)) for item in reranked]
            self._last_search_telemetry = {
                "enabled": True,
                "reads_enabled": self.reads_enabled,
                "status": "ok",
                "provider_used": vector_meta.get("provider_used", self.embedding_provider),
                "fallback_used": bool(vector_meta.get("fallback_used", False)),
                "vector_size": int(vector_meta.get("vector_size", self.vector_size)),
                "vectorize_ms": float(vector_meta.get("vectorize_ms", 0.0)),
                "qdrant_search_ms": round(search_ms, 3),
                "rerank_ms": float(rerank_meta.get("rerank_ms", 0.0)),
                "rerank_enabled": bool(rerank_meta.get("rerank_enabled", False)),
                "rerank_applied": bool(rerank_meta.get("rerank_applied", False)),
                "candidate_count": len(memories),
                "returned_count": len(reranked),
                "top_score": round(scores[0], 6) if scores else 0.0,
                "avg_score": round(sum(scores) / len(scores), 6) if scores else 0.0,
                "total_ms": round((time.perf_counter() - started_total) * 1000.0, 3),
            }
            return reranked
        except Exception as exc:
            _local_service_log(f"Qdrant search error: {exc}", self.settings.output_dir)
            self._last_search_telemetry = {
                "enabled": True,
                "reads_enabled": self.reads_enabled,
                "status": "search_error",
                "error": str(exc),
                "vectorize_ms": float(vector_meta.get("vectorize_ms", 0.0)),
                "total_ms": round((time.perf_counter() - started_total) * 1000.0, 3),
                "returned_count": 0,
            }
            return []

    async def add_step_memory(
        self, *, page_state: str, action: str, outcome: str, url: str, step: int
    ) -> None:
        started_total = time.perf_counter()
        if not self.enabled or self.client is None or not self.writes_enabled:
            self._last_write_telemetry = {
                "enabled": False,
                "writes_enabled": self.writes_enabled,
                "status": "skipped",
                "total_ms": round((time.perf_counter() - started_total) * 1000.0, 3),
            }
            return

        layout_summary = simplify_layout_query(page_state)
        vector, vector_meta = await self._vectorize_with_telemetry(layout_summary)
        point_id = int(time.time() * 1_000_000) + random.randint(0, 999)
        body = {
            "points": [
                {
                    "id": point_id,
                    "vector": vector,
                    "payload": {
                        "layout_summary": layout_summary,
                        "action": action,
                        "outcome": outcome,
                        "url": url,
                        "step": step,
                        "timestamp": __import__("datetime").datetime.now().isoformat(),
                    },
                }
            ]
        }

        try:
            upsert_started = time.perf_counter()
            response = await self.client.put(
                f"{self.settings.qdrant_url}/collections/{self.settings.qdrant_collection}/points",
                json=body,
            )
            upsert_ms = (time.perf_counter() - upsert_started) * 1000.0
            if response.status_code >= 400:
                _local_service_log(
                    f"Qdrant upsert failed ({response.status_code}): {response.text[:200]}",
                    self.settings.output_dir,
                )
                self._last_write_telemetry = {
                    "enabled": True,
                    "writes_enabled": self.writes_enabled,
                    "status": "upsert_failed",
                    "status_code": response.status_code,
                    "provider_used": vector_meta.get("provider_used", self.embedding_provider),
                    "vectorize_ms": float(vector_meta.get("vectorize_ms", 0.0)),
                    "qdrant_upsert_ms": round(upsert_ms, 3),
                    "total_ms": round((time.perf_counter() - started_total) * 1000.0, 3),
                }
                return

            self._last_write_telemetry = {
                "enabled": True,
                "writes_enabled": self.writes_enabled,
                "status": "ok",
                "provider_used": vector_meta.get("provider_used", self.embedding_provider),
                "vectorize_ms": float(vector_meta.get("vectorize_ms", 0.0)),
                "qdrant_upsert_ms": round(upsert_ms, 3),
                "total_ms": round((time.perf_counter() - started_total) * 1000.0, 3),
            }
        except Exception as exc:
            _local_service_log(f"Qdrant upsert error: {exc}", self.settings.output_dir)
            self._last_write_telemetry = {
                "enabled": True,
                "writes_enabled": self.writes_enabled,
                "status": "upsert_error",
                "error": str(exc),
                "provider_used": vector_meta.get("provider_used", self.embedding_provider),
                "vectorize_ms": float(vector_meta.get("vectorize_ms", 0.0)),
                "total_ms": round((time.perf_counter() - started_total) * 1000.0, 3),
            }
