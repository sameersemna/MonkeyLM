"""Utility functions for MonkeyLM reporting."""

from __future__ import annotations
import re


def redact_sensitive_content(text: str) -> str:
    """Redact sensitive patterns from text before writing to files."""
    text = re.sub(r'sk-\w+', '[REDACTED]', text, flags=re.IGNORECASE)
    text = re.sub(r'gsk_\w+', '[REDACTED]', text, flags=re.IGNORECASE)
    text = re.sub(r'ollama-\w+', '[REDACTED]', text, flags=re.IGNORECASE)
    
    password_patterns = [
        r'(password|passwd|secret|token|key|credential|api_key|access_key|auth_token|session_token|api_secret|private_key|public_key|auth_secret|jwt_token|access_secret)',
    ]
    
    for pattern in password_patterns:
        text = re.sub(pattern, '[REDACTED]', text, flags=re.IGNORECASE)
    
    return text
