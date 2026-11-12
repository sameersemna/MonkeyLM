"""Redis is only engaged when there are workers to coordinate.

The cache exists for cross-worker coordination only:
``increment_visited_state`` shares revisit counts between workers and
``claim_action_path_lock`` stops two workers performing the same action. With
``WORKERS=1`` both short-circuit, so the previous behaviour — connect, PING,
print "ready", write nothing — looked like a broken integration. Single-worker
runs now skip the connection entirely.

These tests stub the Redis module, so no live Redis is required.
"""

from __future__ import annotations

from typing import Any

import pytest


class _FakeRedis:
    def __init__(self) -> None:
        self.pinged = False

    async def ping(self) -> bool:
        self.pinged = True
        return True

    async def aclose(self) -> None:
        return None


class _FakeRedisAsyncio:
    """Stand-in for ``redis.asyncio`` that records whether it was ever used."""

    def __init__(self, client: _FakeRedis) -> None:
        self._client = client
        self.connections_opened = 0

    def from_url(self, *_args: Any, **_kwargs: Any) -> _FakeRedis:
        self.connections_opened += 1
        return self._client


@pytest.fixture()
def engine_factory(monkeypatch: pytest.MonkeyPatch):
    from monkeylm.config import load_settings
    from monkeylm.memory import postgres as pg

    def _build(max_workers: int):
        # A fresh Settings per engine: some tests mutate it (strict_persistence).
        settings = load_settings()
        client = _FakeRedis()
        fake_asyncio = _FakeRedisAsyncio(client)
        monkeypatch.setattr(pg, "redis_asyncio", fake_asyncio)
        engine = pg.PersistenceEngine(settings, defects=None, max_workers=max_workers)
        return engine, client, fake_asyncio

    return _build


# ── Single worker: no connection at all ─────────────────────────────────────


@pytest.mark.asyncio
async def test_single_worker_does_not_open_a_redis_connection(engine_factory, capsys) -> None:
    engine, client, fake_asyncio = engine_factory(max_workers=1)

    await engine._initialize_redis()

    assert fake_asyncio.connections_opened == 0, "single worker must not connect"
    assert client.pinged is False
    assert engine.redis_client is None
    out = capsys.readouterr().out
    assert "Redis not used" in out
    assert "WORKERS=1" in out
    # The old, misleading claim must be gone.
    assert "Redis state cache is ready" not in out


@pytest.mark.asyncio
async def test_single_worker_without_redis_url_is_silent(engine_factory, capsys) -> None:
    """Nothing configured and nothing needed ⇒ no Redis noise."""
    engine, *_ = engine_factory(max_workers=1)
    engine.settings.redis_url = ""
    # Discard anything emitted while building settings (e.g. a precedence
    # warning when the shell exports a REDIS_URL that differs from .env); this
    # test is about Redis *initialisation* output only.
    capsys.readouterr()

    await engine._initialize_redis()

    assert engine.redis_client is None
    assert capsys.readouterr().out.strip() == ""


# ── Multi worker: connected and ready ───────────────────────────────────────


@pytest.mark.asyncio
async def test_multi_worker_connects_and_reports_ready(engine_factory, capsys) -> None:
    engine, client, fake_asyncio = engine_factory(max_workers=2)

    await engine._initialize_redis()

    assert fake_asyncio.connections_opened == 1
    assert client.pinged is True
    assert engine.redis_client is not None
    out = capsys.readouterr().out
    assert "Redis state cache is ready" in out
    assert "WORKERS=2" in out


def test_coordination_enabled_follows_worker_count(engine_factory) -> None:
    one, *_ = engine_factory(max_workers=1)
    two, *_ = engine_factory(max_workers=2)
    assert one.coordination_enabled is False
    assert two.coordination_enabled is True


# ── Strict persistence must not demand an unused service ───────────────────


@pytest.mark.asyncio
async def test_strict_mode_does_not_require_redis_for_a_single_worker(
    engine_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Strict mode means "the services you use must be reachable"."""
    engine, *_ = engine_factory(max_workers=1)
    engine.settings.strict_persistence = True

    async def _pg_ok() -> None:
        engine.pg_pool = object()  # pretend PostgreSQL is fine

    async def _no_load() -> None:
        engine.baseline_summary = {}

    monkeypatch.setattr(engine, "_initialize_postgres", _pg_ok)
    monkeypatch.setattr(engine, "_load_baseline_summary", _no_load)

    # Must not raise: Redis was deliberately skipped, not unavailable.
    await engine.initialize()
    assert engine.redis_client is None


@pytest.mark.asyncio
async def test_strict_mode_still_requires_redis_for_multiple_workers(
    engine_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, *_ = engine_factory(max_workers=2)
    engine.settings.strict_persistence = True

    async def _pg_ok() -> None:
        engine.pg_pool = object()

    async def _redis_down() -> None:
        engine.redis_client = None

    async def _no_load() -> None:
        engine.baseline_summary = {}

    monkeypatch.setattr(engine, "_initialize_postgres", _pg_ok)
    monkeypatch.setattr(engine, "_initialize_redis", _redis_down)
    monkeypatch.setattr(engine, "_load_baseline_summary", _no_load)

    with pytest.raises(RuntimeError, match="Redis"):
        await engine.initialize()


# ── The gates themselves ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_write_paths_remain_no_ops_for_a_single_worker(engine_factory) -> None:
    engine, *_ = engine_factory(max_workers=1)
    assert await engine.increment_visited_state("state-a") is None
    assert await engine.claim_action_path_lock("hash", "worker-01") is True


def test_write_gates_key_off_worker_count() -> None:
    import inspect

    from monkeylm.memory import postgres as pg

    for method in (
        pg.PersistenceEngine.increment_visited_state,
        pg.PersistenceEngine.claim_action_path_lock,
    ):
        assert "max_workers <= 1" in inspect.getsource(method), method.__name__
