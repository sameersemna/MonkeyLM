"""Qdrant semantic memory store - vector embeddings, search, and step memory."""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import ollama

from monkeylm.config import Settings, _local_service_log, httpx


def _stable_text_vector(text: str, vector_size: int) -> List[float]:
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
        self._embedding_fallback_warned = False
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
            embed_response = ollama.embeddings(model=self.embedding_model, prompt=text)
            vector = self._extract_embedding_from_response(embed_response)
            if vector:
                return vector
        except Exception:
            pass
        return None

    async def _litellm_embed(self, text: str) -> List[float]:
        """Call a LiteLLM proxy's OpenAI-compatible /v1/embeddings endpoint.

        Unlike `_ollama_embed_sync`, this raises on failure instead of
        swallowing the error -- the caller needs the real reason (connection
        refused, wrong model string, non-200 response) to log a loud warning
        instead of silently degrading to hash vectors with no trace of why.
        """
        if httpx is None:
            raise RuntimeError("httpx is unavailable; cannot reach LiteLLM")
        base_url = self.settings.qdrant_embedding_litellm_base_url.rstrip("/")
        headers = {"Content-Type": "application/json"}
        if self.settings.qdrant_embedding_litellm_api_key:
            headers["Authorization"] = f"Bearer {self.settings.qdrant_embedding_litellm_api_key}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{base_url}/v1/embeddings",
                json={"model": self.embedding_model, "input": text},
                headers=headers,
            )
            if response.status_code >= 400:
                raise RuntimeError(
                    f"LiteLLM embeddings endpoint {base_url}/v1/embeddings returned "
                    f"{response.status_code} for model {self.embedding_model!r}: {response.text[:200]}"
                )
            data = response.json()
        items = data.get("data") or []
        if items and isinstance(items[0], dict):
            embedding = items[0].get("embedding")
            if isinstance(embedding, list) and embedding:
                return [float(x) for x in embedding]
        raise RuntimeError(f"LiteLLM embeddings response had no usable embedding: {str(data)[:200]}")

    async def _vectorize(self, text: str) -> List[float]:
        if self.embedding_provider == "ollama":
            try:
                vector = await asyncio.to_thread(self._ollama_embed_sync, text)
                if vector:
                    return vector
                raise RuntimeError("Ollama embedding call returned no vector")
            except Exception as exc:
                if not self._embedding_fallback_warned:
                    _local_service_log(f"Ollama embedding failed, falling back to hash vectors: {exc}", self.settings.output_dir)
                    self._embedding_fallback_warned = True
        elif self.embedding_provider == "litellm":
            try:
                return await self._litellm_embed(text)
            except Exception as exc:
                if not self._embedding_fallback_warned:
                    _local_service_log(f"LiteLLM embedding failed, falling back to hash vectors: {exc}", self.settings.output_dir)
                    self._embedding_fallback_warned = True
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
                    return vector, {"provider_used": "ollama", "fallback_used": False, "vector_size": len(vector), "vectorize_ms": round(elapsed, 3)}
                raise RuntimeError("Ollama embedding call returned no vector")
            except Exception as exc:
                fallback_used = True
                if not self._embedding_fallback_warned:
                    _local_service_log(f"Ollama embedding failed, falling back to hash vectors: {exc}", self.settings.output_dir)
                    self._embedding_fallback_warned = True
            provider_used = "hash"
        elif self.embedding_provider == "litellm":
            try:
                vector = await self._litellm_embed(text)
                elapsed = (time.perf_counter() - started) * 1000.0
                return vector, {"provider_used": "litellm", "fallback_used": False, "vector_size": len(vector), "vectorize_ms": round(elapsed, 3)}
            except Exception as exc:
                fallback_used = True
                if not self._embedding_fallback_warned:
                    _local_service_log(f"LiteLLM embedding failed, falling back to hash vectors: {exc}", self.settings.output_dir)
                    self._embedding_fallback_warned = True
            provider_used = "hash"
        vector = _stable_text_vector(text, self.vector_size)
        elapsed = (time.perf_counter() - started) * 1000.0
        return vector, {"provider_used": provider_used, "fallback_used": fallback_used, "vector_size": len(vector), "vectorize_ms": round(elapsed, 3)}

    async def _ensure_collection(self) -> None:
        if self.client is None:
            return
        payload = {"vectors": {"size": self.vector_size, "distance": "Cosine"}}
        response = await self.client.put(f"{self.settings.qdrant_url}/collections/{self.settings.qdrant_collection}", json=payload)
        if response.status_code == 409:
            return
        if response.status_code >= 400:
            raise RuntimeError(f"collection create returned {response.status_code}: {response.text[:200]}")

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
            _local_service_log("Qdrant reads and writes are disabled by configuration.", self.settings.output_dir)
            self.enabled = False
            return
        if httpx is None:
            _local_service_log("httpx is unavailable; Qdrant semantic memory is disabled.", self.settings.output_dir)
            self.enabled = False
            return
        try:
            self.client = httpx.AsyncClient(timeout=6.0)
            assert self.client is not None
            health = await self.client.get(f"{self.settings.qdrant_url}/collections")
            if health.status_code >= 400:
                raise RuntimeError(f"collections endpoint returned {health.status_code}")
            if self.embedding_provider == "ollama":
                probe_vector = await asyncio.to_thread(self._ollama_embed_sync, "monkeylm semantic memory bootstrap")
                if probe_vector:
                    self.vector_size = len(probe_vector)
                else:
                    _local_service_log("Unable to resolve Ollama embedding vector size during startup; falling back to hash vectors.", self.settings.output_dir)
                    self.embedding_provider = "hash"
            elif self.embedding_provider == "litellm":
                try:
                    probe_vector = await self._litellm_embed("monkeylm semantic memory bootstrap")
                    self.vector_size = len(probe_vector)
                except Exception as exc:
                    _local_service_log(f"Unable to resolve LiteLLM embedding vector size during startup ({exc}); falling back to hash vectors.", self.settings.output_dir)
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
                _local_service_log(f"Failed to close Qdrant client cleanly: {exc}", self.settings.output_dir)
            self.client = None
        self.enabled = False

    async def inspect_collection(self) -> Dict[str, Any]:
        if self.client is None:
            return {"collection": self.settings.qdrant_collection, "exists": False, "error": "client_not_initialized"}
        try:
            response = await self.client.get(f"{self.settings.qdrant_url}/collections/{self.settings.qdrant_collection}")
            if response.status_code == 404:
                return {"collection": self.settings.qdrant_collection, "exists": False}
            if response.status_code >= 400:
                return {"collection": self.settings.qdrant_collection, "exists": False, "error": f"status={response.status_code}", "raw": response.text[:200]}
            data = response.json().get("result", {})
            config = data.get("config", {}).get("params", {}).get("vectors", {})
            return {"collection": self.settings.qdrant_collection, "exists": True, "points_count": data.get("points_count", 0), "indexed_vectors_count": data.get("indexed_vectors_count", 0), "vector_size": config.get("size", self.vector_size), "distance": config.get("distance", "Cosine"), "status": data.get("status", "unknown")}
        except Exception as exc:
            return {"collection": self.settings.qdrant_collection, "exists": False, "error": str(exc)}

    async def clear_collection(self) -> Dict[str, Any]:
        if self.client is None:
            return {"ok": False, "error": "client_not_initialized"}
        try:
            delete_response = await self.client.delete(f"{self.settings.qdrant_url}/collections/{self.settings.qdrant_collection}")
            if delete_response.status_code not in {200, 202, 404}:
                return {"ok": False, "error": f"delete_failed_status={delete_response.status_code}", "raw": delete_response.text[:200]}
            await self._ensure_collection()
            info = await self.inspect_collection()
            info["ok"] = True
            info["action"] = "cleared_and_recreated"
            return info
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

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
            candidate_rows.append(f"[{idx}] score={score:.4f} action={action} outcome={outcome} layout={summary}")
        return (
            "You are ranking historical web-testing memories for relevance to a current page layout query.\n"
            "Return strictly JSON with key ranked_indices containing unique candidate indices in best-first order.\n"
            f"Select exactly {final_limit} indices when possible.\n\n"
            f"Query:\n{query_text[:1200]}\n\n"
            "Candidates:\n" + "\n".join(candidate_rows) + "\n\nOutput format:\n{\"ranked_indices\": [0, 2, 1]}"
        )

    async def _rerank_memories(self, query_text: str, candidates: List[Dict[str, Any]], final_limit: int) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        started = time.perf_counter()
        if not candidates:
            return [], {"rerank_enabled": self.rerank_enabled, "rerank_applied": False, "rerank_model": self.rerank_model, "rerank_ms": round((time.perf_counter() - started) * 1000.0, 3)}
        if not self.rerank_enabled:
            return candidates[:final_limit], {"rerank_enabled": False, "rerank_applied": False, "rerank_model": self.rerank_model, "rerank_ms": round((time.perf_counter() - started) * 1000.0, 3)}
        try:
            print(f"   └─ 🧠 Reranking memories with {self.rerank_model} (limit={final_limit})")
            prompt = self._build_rerank_prompt(query_text, candidates, final_limit)
            response = await asyncio.to_thread(ollama.chat, model=self.rerank_model, messages=[{"role": "user", "content": prompt}], format="json", options={"temperature": 0.0, "top_p": 0.9})
            ranked_indices = self._parse_rerank_response(response.get("message", {}).get("content", ""))
            if not ranked_indices:
                return candidates[:final_limit], {"rerank_enabled": True, "rerank_applied": False, "rerank_model": self.rerank_model, "rerank_ms": round((time.perf_counter() - started) * 1000.0, 3), "reason": "empty_ranking"}
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
            return picked, {"rerank_enabled": True, "rerank_applied": True, "rerank_model": self.rerank_model, "rerank_ms": round((time.perf_counter() - started) * 1000.0, 3)}
        except Exception as exc:
            if not self._ollama_rerank_warned:
                _local_service_log(f"Ollama reranker failed, falling back to vector ranking: {exc}", self.settings.output_dir)
                self._ollama_rerank_warned = True
            return candidates[:final_limit], {"rerank_enabled": True, "rerank_applied": False, "rerank_model": self.rerank_model, "rerank_ms": round((time.perf_counter() - started) * 1000.0, 3), "error": str(exc)}

    async def search_similar_layouts(self, page_state: str, limit: int = 3) -> List[Dict[str, Any]]:
        started_total = time.perf_counter()
        if not self.enabled or self.client is None or not self.reads_enabled:
            self._last_search_telemetry = {"enabled": False, "reads_enabled": self.reads_enabled, "returned_count": 0, "total_ms": round((time.perf_counter() - started_total) * 1000.0, 3)}
            return []
        query_text = simplify_layout_query(page_state)
        query_vector, vector_meta = await self._vectorize_with_telemetry(query_text)
        candidate_limit = max(limit, self.candidate_limit)
        body = {"vector": query_vector, "limit": max(1, min(candidate_limit, 50)), "with_payload": True}
        try:
            search_started = time.perf_counter()
            response = await self.client.post(f"{self.settings.qdrant_url}/collections/{self.settings.qdrant_collection}/points/search", json=body)
            search_ms = (time.perf_counter() - search_started) * 1000.0
            if response.status_code >= 400:
                _local_service_log(f"Qdrant search failed ({response.status_code}): {response.text[:200]}", self.settings.output_dir)
                self._last_search_telemetry = {"enabled": True, "reads_enabled": self.reads_enabled, "returned_count": 0, "status": "search_failed", "status_code": response.status_code, "vectorize_ms": vector_meta.get("vectorize_ms", 0.0), "qdrant_search_ms": round(search_ms, 3), "total_ms": round((time.perf_counter() - started_total) * 1000.0, 3)}
                return []
            data = response.json()
            points = data.get("result", [])
            memories: List[Dict[str, Any]] = []
            for point in points:
                payload = point.get("payload", {})
                memories.append({"layout_summary": payload.get("layout_summary", ""), "action": payload.get("action", ""), "outcome": payload.get("outcome", ""), "url": payload.get("url", ""), "score": float(point.get("score", 0.0)), "vector_rank": len(memories) + 1})
            reranked, rerank_meta = await self._rerank_memories(query_text, memories, final_limit=max(1, min(limit, 10)))
            scores = [float(item.get("score", 0.0)) for item in reranked]
            self._last_search_telemetry = {"enabled": True, "reads_enabled": self.reads_enabled, "status": "ok", "provider_used": vector_meta.get("provider_used", self.embedding_provider), "fallback_used": bool(vector_meta.get("fallback_used", False)), "vector_size": int(vector_meta.get("vector_size", self.vector_size)), "vectorize_ms": float(vector_meta.get("vectorize_ms", 0.0)), "qdrant_search_ms": round(search_ms, 3), "rerank_ms": float(rerank_meta.get("rerank_ms", 0.0)), "rerank_enabled": bool(rerank_meta.get("rerank_enabled", False)), "rerank_applied": bool(rerank_meta.get("rerank_applied", False)), "candidate_count": len(memories), "returned_count": len(reranked), "top_score": round(scores[0], 6) if scores else 0.0, "avg_score": round(sum(scores) / len(scores), 6) if scores else 0.0, "total_ms": round((time.perf_counter() - started_total) * 1000.0, 3)}
            return reranked
        except Exception as exc:
            _local_service_log(f"Qdrant search error: {exc}", self.settings.output_dir)
            self._last_search_telemetry = {"enabled": True, "reads_enabled": self.reads_enabled, "status": "search_error", "error": str(exc), "vectorize_ms": float(vector_meta.get("vectorize_ms", 0.0)), "total_ms": round((time.perf_counter() - started_total) * 1000.0, 3), "returned_count": 0}
            return []

    async def add_step_memory(self, *, page_state: str, action: str, outcome: str, url: str, step: int) -> None:
        started_total = time.perf_counter()
        if not self.enabled or self.client is None or not self.writes_enabled:
            self._last_write_telemetry = {"enabled": False, "writes_enabled": self.writes_enabled, "status": "skipped", "total_ms": round((time.perf_counter() - started_total) * 1000.0, 3)}
            return
        layout_summary = simplify_layout_query(page_state)
        vector, vector_meta = await self._vectorize_with_telemetry(layout_summary)
        point_id = int(time.time() * 1_000_000) + random.randint(0, 999)
        body = {"points": [{"id": point_id, "vector": vector, "payload": {"layout_summary": layout_summary, "action": action, "outcome": outcome, "url": url, "step": step, "timestamp": __import__("datetime").datetime.now().isoformat()}}]}
        try:
            upsert_started = time.perf_counter()
            response = await self.client.put(f"{self.settings.qdrant_url}/collections/{self.settings.qdrant_collection}/points", json=body)
            upsert_ms = (time.perf_counter() - upsert_started) * 1000.0
            if response.status_code >= 400:
                _local_service_log(f"Qdrant upsert failed ({response.status_code}): {response.text[:200]}", self.settings.output_dir)
                self._last_write_telemetry = {"enabled": True, "writes_enabled": self.writes_enabled, "status": "upsert_failed", "status_code": response.status_code, "provider_used": vector_meta.get("provider_used", self.embedding_provider), "vectorize_ms": float(vector_meta.get("vectorize_ms", 0.0)), "qdrant_upsert_ms": round(upsert_ms, 3), "total_ms": round((time.perf_counter() - started_total) * 1000.0, 3)}
                return
            self._last_write_telemetry = {"enabled": True, "writes_enabled": self.writes_enabled, "status": "ok", "provider_used": vector_meta.get("provider_used", self.embedding_provider), "vectorize_ms": float(vector_meta.get("vectorize_ms", 0.0)), "qdrant_upsert_ms": round(upsert_ms, 3), "total_ms": round((time.perf_counter() - started_total) * 1000.0, 3)}
        except Exception as exc:
            _local_service_log(f"Qdrant upsert error: {exc}", self.settings.output_dir)
            self._last_write_telemetry = {"enabled": True, "writes_enabled": self.writes_enabled, "status": "upsert_error", "error": str(exc), "provider_used": vector_meta.get("provider_used", self.embedding_provider), "vectorize_ms": float(vector_meta.get("vectorize_ms", 0.0)), "total_ms": round((time.perf_counter() - started_total) * 1000.0, 3)}
