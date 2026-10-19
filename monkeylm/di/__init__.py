"""Dependency-injection adapters for the function-based subsystems.

The adapters are retained for API compatibility. Methods that cannot be
implemented honestly against the real subsystems fail loudly with
``NotImplementedError`` instead of raising a misleading AttributeError.
"""

from .browser import create_browser_provider
from .memory import create_memory_store
from .model import create_model_client
from .reporting import create_report_generator

__all__ = [
    "create_browser_provider",
    "create_memory_store",
    "create_model_client",
    "create_report_generator",
]
