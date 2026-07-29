"""PostgreSQL persistence - baseline tables, regression drift logging, and PersistenceEngine."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from playwright.async_api import Page

from monkeylm.config import (
    Settings,
    _local_service_log,
    asyncpg,
    build_redis_key,
    redis_asyncio,
    split_domain_and_route,
)

_baseline_logger = logging.getLogger("monkeylm.baseline")
if not _baseline_logger.handlers:
    _baseline_logger.setLevel(logging.WARNING)
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(message)s"))
    _baseline_logger.addHandler(_handler)


def _normalize_url_for_baseline_lookup(url: str, preserve_routes: Optional[List[str]] = None) -> str:
    from urllib.parse import parse_qs, urlparse, urlunparse, urlencode

    if not url or url == "about:blank":
        return "/"
    parsed = urlparse(url)
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    query_params = parse_qs(parsed.query, keep_blank_values=True)
    preserved_routes = preserve_routes or []
    strip_patterns = [
        r"^session_", r"^csrf_", r"^_token$", r"^nonce$", r"^timestamp$",
        r"^__r$", r"^utm_", r"^gclid$", r"^fbclid$", r"^srchid$",
        r"^gs_lcp$", r"^source$", r"^ref$",
    ]
    stripped_count = 0
    filtered_params: Dict[str, List[str]] = {}
    for key, values in query_params.items():
        if key.lower() in [p.lower() for p in preserved_routes]:
            filtered_params[key] = values
            continue
        should_strip = False
        for pattern in strip_patterns:
            if re.search(pattern, key, re.IGNORECASE):
                should_strip = True
                stripped_count += 1
                break
        if not should_strip:
            filtered_params[key] = values
    if filtered_params:
        new_query = urlencode(filtered_params, doseq=True)
    else:
        new_query = ""
    normalized = urlunparse(("", "", path, parsed.params, new_query, parsed.fragment))
    return normalized if normalized else "/"


def _sanitize_path_component(value: Any, fallback: str = "unknown") -> str:
    if not isinstance(value, str):
        return fallback
    cleaned = re.sub(r"[^a-zA-Z0-9._-]", "_", value)
    cleaned = re.sub(r"\.{2,}", "_", cleaned)
    cleaned = cleaned.lstrip(".")
    if not cleaned or cleaned in (".", "..", "-", "_"):
        return fallback
    return cleaned[:128]


def _baseline_lookup_path(domain: str, route: str) -> str:
    safe_domain = _sanitize_path_component(domain, "unknown")
    safe_route = _sanitize_path_component(route or "", "root")
    base_dir = Path(os.environ.get("MONKEYLM_REPORTS_DIR", "reports")).resolve()
    candidate = (base_dir / safe_domain / "baseline" / safe_route).resolve()
    try:
        candidate.relative_to(base_dir)
    except ValueError:
        _baseline_logger.warning("Refused baseline lookup path escaping base dir for domain=%r route=%r", domain, route)
        return str(base_dir)
    return str(candidate)


def _secure_atomic_write(path: str | os.PathLike, data: str | bytes, *, mode: int = 0o600) -> None:
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=".tmp_")
    try:
        with os.fdopen(fd, "wb") as fh:
            if isinstance(data, str):
                fh.write(data.encode("utf-8"))
            else:
                fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, str(target))
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _secure_atomic_write_json(path: str | os.PathLike, payload: Any, *, mode: int = 0o600) -> None:
    _secure_atomic_write(path, json.dumps(payload, ensure_ascii=False), mode=mode)


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
                raise RuntimeError("Persistence strict mode requires services to be reachable. Missing: " + ", ".join(missing))

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
            assert self.pg_pool is not None
            async with self.pg_pool.acquire() as conn:
                await conn.execute("""
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
                """)
                await conn.execute("""
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_app_baselines_one_golden_per_route
                    ON app_baselines(domain, page_route)
                    WHERE is_golden_standard = TRUE
                """)
                await conn.execute("""
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
                """)
                await conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_regression_drift_log_route_time
                    ON regression_drift_log(domain, page_route, created_at DESC)
                """)
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
            assert self.redis_client is not None
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
        if self.redis_client is None:
            return True
        redis_key = f"monkeylm:active_path:{path_hash}"
        try:
            async with self.redis_write_semaphore:
                prefixed_key = build_redis_key(self.settings.redis_prefix, redis_key)
                acquired = await self.redis_client.set(prefixed_key, worker_label, nx=True, ex=self.settings.redis_path_lock_ttl_seconds)
                if acquired:
                    return True
                current_owner = await self.redis_client.get(prefixed_key)
                if isinstance(current_owner, bytes):
                    current_owner = current_owner.decode("utf-8")
                elif not isinstance(current_owner, str):
                    current_owner = str(current_owner) if current_owner is not None else ""
                if current_owner == worker_label:
                    return True
            return False
        except Exception as exc:
            _local_service_log(f"Redis action-path lock claim failed for '{path_hash}': {exc}", self.settings.output_dir)
            return False

    async def _fetch_golden_baseline(self, domain: str, page_route: str) -> Optional[Dict[str, Any]]:
        if self.pg_pool is None:
            return None
        try:
            async with self.pg_pool.acquire() as conn:
                row = await conn.fetchrow(
                    """SELECT dom_structure_hash, component_manifest FROM app_baselines
                       WHERE domain = $1 AND page_route = $2 AND is_golden_standard = TRUE
                       ORDER BY updated_at DESC LIMIT 1""",
                    domain, page_route,
                )
            if row is None:
                return None
            manifest_value = row["component_manifest"]
            if isinstance(manifest_value, str):
                try:
                    manifest_value = json.loads(manifest_value)
                except Exception:
                    manifest_value = []
            return {"dom_structure_hash": row["dom_structure_hash"], "component_manifest": manifest_value if isinstance(manifest_value, list) else []}
        except Exception as exc:
            _local_service_log(f"Failed to fetch golden baseline: {exc}", self.settings.output_dir)
            return None

    async def _upsert_baseline(self, domain: str, page_route: str, dom_structure_hash: str, component_manifest: List[Dict[str, Any]], is_golden_standard: bool) -> None:
        if self.pg_pool is None:
            return
        try:
            manifest_json = json.dumps(component_manifest)
            async with self.pg_write_semaphore:
                async with self.pg_pool.acquire() as conn:
                    if is_golden_standard:
                        async with conn.transaction():
                            await conn.execute("""DELETE FROM app_baselines WHERE domain = $1 AND page_route = $2 AND is_golden_standard = TRUE""", domain, page_route)
                            await conn.execute("""INSERT INTO app_baselines (domain, page_route, dom_structure_hash, component_manifest, is_golden_standard, updated_at) VALUES ($1, $2, $3, $4::jsonb, TRUE, NOW())""", domain, page_route, dom_structure_hash, manifest_json)
                    else:
                        await conn.execute("""INSERT INTO app_baselines (domain, page_route, dom_structure_hash, component_manifest, is_golden_standard, updated_at) VALUES ($1, $2, $3, $4::jsonb, FALSE, NOW()) ON CONFLICT (domain, page_route, dom_structure_hash, is_golden_standard) DO UPDATE SET component_manifest = EXCLUDED.component_manifest, updated_at = NOW()""", domain, page_route, dom_structure_hash, manifest_json)
        except Exception as exc:
            _local_service_log(f"Failed to upsert baseline data: {exc}", self.settings.output_dir)

    async def _insert_regression_drift_log(self, *, domain: str, page_route: str, step_number: int, defect_tag: str, severity: str, missing_components: List[Dict[str, Any]], broken_selectors: List[str], drift_alert: Dict[str, Any]) -> None:
        if self.pg_pool is None:
            return
        try:
            async with self.pg_write_semaphore:
                async with self.pg_pool.acquire() as conn:
                    await conn.execute("""INSERT INTO regression_drift_log (domain, page_route, defect_tag, severity, missing_components, broken_selectors, drift_alert, step_number) VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7::jsonb, $8)""", domain, page_route, defect_tag, severity, json.dumps(missing_components), json.dumps(broken_selectors), json.dumps(drift_alert), step_number)
        except Exception as exc:
            _local_service_log(f"Failed to insert regression drift log row: {exc}", self.settings.output_dir)

    async def analyze_route_regression(self, page: Page, snapshot: Any, step_num: int) -> None:
        from monkeylm.browser import diff_component_manifests, extract_component_manifest
        domain, page_route = split_domain_and_route(snapshot.url)
        if not domain:
            return
        normalized_route = _normalize_url_for_baseline_lookup(page_route, preserve_routes=["lang", "locale", "language", "currency"])
        lookup_dir = _baseline_lookup_path(domain, normalized_route)
        if page_route != normalized_route:
            _baseline_logger.info("Normalization: %s → %s (stripped variable query params)", page_route, normalized_route)
        _baseline_logger.info("Baseline lookup: domain=%s, original_route=%s, normalized_route=%s, search_dir=%s", domain, page_route, normalized_route, lookup_dir)
        component_manifest = await extract_component_manifest(page)
        await self._upsert_baseline(domain=domain, page_route=normalized_route, dom_structure_hash=snapshot.structure_hash, component_manifest=component_manifest, is_golden_standard=False)
        golden = await self._fetch_golden_baseline(domain, normalized_route)
        if golden is None:
            if self.settings.golden_baseline_mode == "auto_upsert":
                await self._upsert_baseline(domain=domain, page_route=normalized_route, dom_structure_hash=snapshot.structure_hash, component_manifest=component_manifest, is_golden_standard=True)
                _local_service_log(f"Auto-seeded golden baseline for {domain}{normalized_route}.", self.settings.output_dir)
            else:
                _local_service_log(f"Golden baseline missing for {domain}{page_route}; comparison skipped.", self.settings.output_dir)
            return
        missing_components, broken_selectors = diff_component_manifests(golden_manifest=golden.get("component_manifest", []), current_manifest=component_manifest)
        if not missing_components:
            return
        expected_baseline_components = len(golden.get("component_manifest", []) or [])
        expected_baseline_components = max(expected_baseline_components, len(missing_components))
        defect_tag = "Vibe-Code-Regression-Missing-Component"
        drift_alert = {"current_dom_structure_hash": snapshot.structure_hash, "golden_dom_structure_hash": golden.get("dom_structure_hash", ""), "missing_count": len(missing_components), "expected_baseline_components": expected_baseline_components, "missing_preview": missing_components[:10]}
        self.defects.add("regression_findings", {"step": step_num, "type": defect_tag, "severity": "high", "domain": domain, "page_route": page_route, "missing_components": missing_components, "broken_selectors": broken_selectors, "expected_baseline_components": expected_baseline_components, "current_component_count": len(component_manifest), "url": snapshot.url})
        await self._insert_regression_drift_log(domain=domain, page_route=page_route, step_number=step_num, defect_tag=defect_tag, severity="high", missing_components=missing_components, broken_selectors=broken_selectors, drift_alert=drift_alert)
