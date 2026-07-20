"""Centralized exception hierarchy for MonkeyLM.

All custom exceptions inherit from :class:`MonkeyLMError` so callers can
catch a single base type. Subclasses carry structured context for logging
and reporting.
"""

from __future__ import annotations


class MonkeyLMError(Exception):
    """Base exception for all MonkeyLM-specific errors."""


class ConfigurationError(MonkeyLMError):
    """Invalid or inconsistent runtime configuration."""


class BrowserError(MonkeyLMError):
    """Browser launch, navigation, or interaction failure."""

    def __init__(self, message: str, *, url: str = "", selector: str = "") -> None:
        super().__init__(message)
        self.url = url
        self.selector = selector


class ModelError(MonkeyLMError):
    """LLM inference failure (timeout, overload, parse error)."""

    def __init__(self, message: str, *, model: str = "", attempt: int = 0) -> None:
        super().__init__(message)
        self.model = model
        self.attempt = attempt


class PersistenceError(MonkeyLMError):
    """Database or cache operation failure."""

    def __init__(self, message: str, *, service: str = "") -> None:
        super().__init__(message)
        self.service = service


class NavigationError(BrowserError):
    """Page navigation failure (timeout, abort, invalid URL)."""


class ValidationError(MonkeyLMError):
    """Input validation or sanitization failure."""


class ShutdownRequested(MonkeyLMError):
    """Graceful shutdown signal received."""


__all__ = [
    "MonkeyLMError",
    "ConfigurationError",
    "BrowserError",
    "ModelError",
    "PersistenceError",
    "NavigationError",
    "ValidationError",
    "ShutdownRequested",
]
