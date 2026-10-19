"""Whitelisted CLI overrides for module-level runtime globals."""

from __future__ import annotations

import argparse
import logging
import random
import sys
from typing import Any, Dict, List


_logger = logging.getLogger("monkeylm.config")

_RUNTIME_GLOBAL_SCHEMA: Dict[str, Any] = {
    "TARGET_URL": str,
    "TARGET_USERNAME": str,
    "TARGET_PASSWORD": str,
    "OLLAMA_MODEL": str,
    "OLLAMA_TIMEOUT_SECONDS": (int, float),
    "OPENAI_BASE_URL": str,
    "OPENAI_API_KEY": str,
    "VISION_MODEL": str,
    "MAX_STEPS": int,
    "MAX_STEPS_PER_WORKER": int,
    "WORKERS": int,
    "WORKER_NAVIGATION_RETRIES": int,
    "WORKER_QDRANT_INIT_RETRIES": int,
    "WORKER_BOUNDARY_RECOVERY_RETRIES": int,
    "RETRY_BASE_DELAY_SECONDS": (int, float),
    "HEADLESS": bool,
    "BROWSER_WINDOW_SIZE": str,
    "NO_VIEWPORT": bool,
    "POSTGRES_DSN": str,
    "REDIS_URL": str,
    "REDIS_PREFIX": str,
    "REDIS_PATH_LOCK_TTL_SECONDS": int,
    "GOLDEN_BASELINE_MODE": str,
    "STRICT_PERSISTENCE": bool,
    "QDRANT_URL": str,
    "QDRANT_COLLECTION": str,
    "QDRANT_EMBEDDING_PROVIDER": str,
    "QDRANT_EMBEDDING_MODEL": str,
    "QDRANT_EMBEDDING_LITELLM_BASE_URL": str,
    "QDRANT_EMBEDDING_LITELLM_API_KEY": str,
    "QDRANT_RERANK_ENABLED": bool,
    "QDRANT_RERANK_MODEL": str,
    "QDRANT_CANDIDATE_LIMIT": int,
    "QDRANT_ADMIN_ACTION": str,
    "QDRANT_ENABLE_READS": bool,
    "QDRANT_ENABLE_WRITES": bool,
}

_SENSITIVE_CONFIG_KEYS: frozenset = frozenset(["POSTGRES_DSN", "REDIS_URL", "OPENAI_API_KEY"])
_POS_INT_KEYS: frozenset = frozenset([
    "MAX_STEPS", "MAX_STEPS_PER_WORKER", "WORKERS",
    "WORKER_NAVIGATION_RETRIES", "WORKER_QDRANT_INIT_RETRIES",
    "WORKER_BOUNDARY_RECOVERY_RETRIES", "REDIS_PATH_LOCK_TTL_SECONDS",
    "QDRANT_CANDIDATE_LIMIT",
])
_POS_FLOAT_KEYS: frozenset = frozenset(["OLLAMA_TIMEOUT_SECONDS", "RETRY_BASE_DELAY_SECONDS"])
_QDRANT_ADMIN_ACTIONS_ALLOWED: frozenset = frozenset(["", "inspect", "clear"])


def _config_namespace() -> Dict[str, Any]:
    package = sys.modules.get("monkeylm.config")
    if package is None:
        raise RuntimeError("monkeylm.config is not initialized")
    return package.__dict__


def _safe_set_global(key: str, value: Any) -> None:
    if key not in _RUNTIME_GLOBAL_SCHEMA:
        _logger.warning("Runtime override rejected: key '%s' not in whitelist", key)
        return

    expected = _RUNTIME_GLOBAL_SCHEMA[key]
    if not isinstance(value, expected):
        try:
            coercer = expected[0] if isinstance(expected, tuple) else expected
            value = coercer(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Invalid type for {key}: expected {expected}, got {type(value).__name__}"
            ) from exc

    if key in _POS_INT_KEYS and value <= 0:
        raise ValueError(f"{key} must be a positive integer, got {value}")

    if key in _POS_FLOAT_KEYS and value <= 0:
        raise ValueError(f"{key} must be positive, got {value}")

    if key == "QDRANT_ADMIN_ACTION" and value not in _QDRANT_ADMIN_ACTIONS_ALLOWED:
        raise ValueError(
            f"QDRANT_ADMIN_ACTION must be one of {_QDRANT_ADMIN_ACTIONS_ALLOWED - {''}}, got {value!r}"
        )

    _config_namespace()[key] = value

    if key in _SENSITIVE_CONFIG_KEYS:
        _logger.warning("Sensitive config override applied: %s", key)


def apply_runtime_overrides(args: argparse.Namespace) -> None:
    namespace = _config_namespace()

    if getattr(args, "seed", None) is not None:
        try:
            seed_val = int(args.seed)
        except (TypeError, ValueError):
            raise ValueError(f"seed must be an integer, got {args.seed!r}")
        namespace["ACTIVE_SEED"] = str(seed_val)
        random.seed(seed_val)

    _SIMPLE_OVERRIDES: List[tuple] = [
        ("target_url", "TARGET_URL"),
        ("target_username", "TARGET_USERNAME"),
        ("target_password", "TARGET_PASSWORD"),
        ("ollama_model", "OLLAMA_MODEL"),
        ("ollama_timeout_seconds", "OLLAMA_TIMEOUT_SECONDS"),
        ("vision_model", "VISION_MODEL"),
        ("max_steps", "MAX_STEPS"),
        ("max_steps_per_worker", "MAX_STEPS_PER_WORKER"),
        ("workers", "WORKERS"),
        ("worker_navigation_retries", "WORKER_NAVIGATION_RETRIES"),
        ("worker_qdrant_init_retries", "WORKER_QDRANT_INIT_RETRIES"),
        ("worker_boundary_recovery_retries", "WORKER_BOUNDARY_RECOVERY_RETRIES"),
        ("retry_base_delay_seconds", "RETRY_BASE_DELAY_SECONDS"),
        ("headless", "HEADLESS"),
        ("window_size", "BROWSER_WINDOW_SIZE"),
        ("no_viewport", "NO_VIEWPORT"),
        ("postgres_dsn", "POSTGRES_DSN"),
        ("redis_url", "REDIS_URL"),
        ("redis_prefix", "REDIS_PREFIX"),
        ("redis_path_lock_ttl_seconds", "REDIS_PATH_LOCK_TTL_SECONDS"),
        ("golden_baseline_mode", "GOLDEN_BASELINE_MODE"),
        ("strict_persistence", "STRICT_PERSISTENCE"),
    ]

    for attr, gkey in _SIMPLE_OVERRIDES:
        val = getattr(args, attr, None)
        if val is not None:
            _safe_set_global(gkey, val)

    _QDRANT_OVERRIDES: List[tuple] = [
        ("qdrant_url", "QDRANT_URL"),
        ("qdrant_collection", "QDRANT_COLLECTION"),
        ("qdrant_embedding_provider", "QDRANT_EMBEDDING_PROVIDER"),
        ("qdrant_embedding_model", "QDRANT_EMBEDDING_MODEL"),
        ("qdrant_embedding_litellm_base_url", "QDRANT_EMBEDDING_LITELLM_BASE_URL"),
        ("qdrant_enable_rerank", "QDRANT_RERANK_ENABLED"),
        ("qdrant_rerank_model", "QDRANT_RERANK_MODEL"),
        ("qdrant_candidate_limit", "QDRANT_CANDIDATE_LIMIT"),
    ]

    for attr, gkey in _QDRANT_OVERRIDES:
        val = getattr(args, attr, None)
        if val is not None:
            _safe_set_global(gkey, val)

    admin_action = getattr(args, "qdrant_admin_action", None)
    if admin_action is not None:
        _safe_set_global("QDRANT_ADMIN_ACTION", admin_action)
    elif getattr(args, "qdrant_inspect", False):
        namespace["QDRANT_ADMIN_ACTION"] = "inspect"
    elif getattr(args, "qdrant_clear", False):
        namespace["QDRANT_ADMIN_ACTION"] = "clear"

    if getattr(args, "qdrant_disable_reads", False):
        namespace["QDRANT_ENABLE_READS"] = False
    if getattr(args, "qdrant_disable_writes", False) or getattr(args, "qdrant_read_only", False):
        namespace["QDRANT_ENABLE_WRITES"] = False
    if getattr(args, "qdrant_disable_rerank", False):
        namespace["QDRANT_RERANK_ENABLED"] = False


__all__ = ["_safe_set_global", "apply_runtime_overrides"]
