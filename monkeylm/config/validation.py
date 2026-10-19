"""Runtime dependency inspection and configuration validation."""

from __future__ import annotations

import shutil
import sys
from typing import Any, Dict

from monkeylm.types import Settings

from .defaults import MAX_ALLOWED_RETRIES, MAX_ALLOWED_RETRY_BASE_DELAY_SECONDS


def inspect_optional_runtime_dependencies() -> Dict[str, Dict[str, Any]]:
    """Check for optional runtime tools and Python packages that support the harness."""
    report: Dict[str, Dict[str, Any]] = {}

    python_bin = shutil.which("python") or shutil.which("python3")
    python_version = ".".join(str(part) for part in sys.version_info[:3])
    python_vulnerable = sys.version_info < (3, 13, 3)
    python_detail = "Python runtime is available for the harness entrypoint."
    if python_vulnerable:
        python_detail += (
            " Python 3.12.x and early 3.13.x can raise '_add_writer' from "
            "asyncio StreamWriter.writelines after a lost Redis connection "
            "(CPython gh-136234); Redis reconnect/fail-open mitigation is active. "
            "Upgrade to Python 3.13.3+ when possible."
        )
    if python_bin:
        report["python"] = {
            "status": "warning" if python_vulnerable else "ok",
            "path": python_bin,
            "version": python_version,
            "detail": python_detail,
        }
    else:
        report["python"] = {
            "status": "missing",
            "path": None,
            "version": python_version,
            "detail": "Python interpreter was not found on PATH.",
        }

    node_bin = shutil.which("node")
    if node_bin:
        report["node"] = {
            "status": "ok",
            "path": node_bin,
            "detail": "Node.js is available for optional pixelmatch visual diff fallback.",
        }
    else:
        report["node"] = {
            "status": "missing",
            "path": None,
            "detail": "Node.js is not installed; visual diff fallback will rely on Python-only paths.",
        }

    for module_name in ("playwright", "dotenv"):
        try:
            import importlib

            importlib.import_module(module_name)
            report[module_name] = {
                "status": "ok",
                "path": None,
                "detail": f"Python package '{module_name}' is importable.",
            }
        except Exception as exc:  # pragma: no cover - import availability varies by environment
            report[module_name] = {
                "status": "missing",
                "path": None,
                "detail": f"Python package '{module_name}' is not importable: {exc}",
            }

    return report


def validate_runtime_configuration(settings: Settings) -> None:
    dependency_report = inspect_optional_runtime_dependencies()
    if dependency_report.get("node", {}).get("status") != "ok":
        print(
            "⚠️ Optional runtime dependency check: Node.js not found on PATH; visual diff fallback may be limited."
        )

    if settings.max_steps_per_worker > settings.max_steps:
        raise ValueError(
            "MAX_STEPS_PER_WORKER must be less than or equal to MAX_STEPS "
            f"(got MAX_STEPS_PER_WORKER={settings.max_steps_per_worker}, MAX_STEPS={settings.max_steps})."
        )
    if settings.retry_base_delay_seconds <= 0.0:
        raise ValueError("RETRY_BASE_DELAY_SECONDS must be greater than 0.")
    retry_settings = {
        "WORKER_NAVIGATION_RETRIES": settings.worker_navigation_retries,
        "WORKER_QDRANT_INIT_RETRIES": settings.worker_qdrant_init_retries,
        "WORKER_BOUNDARY_RECOVERY_RETRIES": settings.worker_boundary_recovery_retries,
    }
    for setting_name, setting_value in retry_settings.items():
        if setting_value > MAX_ALLOWED_RETRIES:
            raise ValueError(
                f"{setting_name} must be less than or equal to {MAX_ALLOWED_RETRIES} " f"(got {setting_value})."
            )
    if settings.retry_base_delay_seconds > MAX_ALLOWED_RETRY_BASE_DELAY_SECONDS:
        raise ValueError(
            "RETRY_BASE_DELAY_SECONDS is too high for safe runtime defaults; "
            f"must be <= {MAX_ALLOWED_RETRY_BASE_DELAY_SECONDS} "
            f"(got {settings.retry_base_delay_seconds})."
        )
    if settings.redis_path_lock_ttl_seconds < 1 or settings.redis_path_lock_ttl_seconds > 300:
        raise ValueError(
            "REDIS_PATH_LOCK_TTL_SECONDS must be between 1 and 300 seconds "
            f"(got {settings.redis_path_lock_ttl_seconds})."
        )


__all__ = ["inspect_optional_runtime_dependencies", "validate_runtime_configuration"]
