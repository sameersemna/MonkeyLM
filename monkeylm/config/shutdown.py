"""Graceful shutdown coordination."""

from __future__ import annotations

import asyncio
import signal
import sys
from typing import Any, Dict


# ── Graceful shutdown coordination ─────────────────────────────────────────────

SHUTDOWN_EVENT: asyncio.Event = asyncio.Event()
GRACEFUL_SHUTDOWN_REQUESTED: bool = False


def _config_namespace() -> Dict[str, Any]:
    package = sys.modules.get("monkeylm.config")
    return package.__dict__ if package is not None else globals()


def _request_graceful_shutdown(signum: int, frame: Any) -> None:
    namespace = _config_namespace()
    if namespace.get("GRACEFUL_SHUTDOWN_REQUESTED", False):
        signal.default_int_handler(signum, frame)
        return

    namespace["GRACEFUL_SHUTDOWN_REQUESTED"] = True
    print("\n\U0001f6d1 Graceful shutdown requested (signal {}). Finishing in-flight steps...".format(signum))
    shutdown_event = namespace.get("SHUTDOWN_EVENT", SHUTDOWN_EVENT)
    try:
        loop = asyncio.get_event_loop()
        loop.call_soon_threadsafe(shutdown_event.set)
    except Exception:
        try:
            shutdown_event.set()
        except Exception:
            pass


def _register_graceful_shutdown_signals() -> None:
    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGINT, lambda: _request_graceful_shutdown(signal.SIGINT, None))
        loop.add_signal_handler(signal.SIGTERM, lambda: _request_graceful_shutdown(signal.SIGTERM, None))
    except NotImplementedError:
        signal.signal(signal.SIGINT, _request_graceful_shutdown)
        try:
            signal.signal(signal.SIGTERM, _request_graceful_shutdown)
        except Exception:
            pass



__all__ = ["SHUTDOWN_EVENT", "GRACEFUL_SHUTDOWN_REQUESTED", "_request_graceful_shutdown", "_register_graceful_shutdown_signals"]
