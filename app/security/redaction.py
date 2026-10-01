from __future__ import annotations

import re
from collections.abc import Mapping

SECRET_PATTERNS = [
    re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{20,}\b"),  # Telegram bot token
    re.compile(r"\bAQ\.[A-Za-z0-9_-]{20,}\b"),      # Gemini auth key
    re.compile(r"\bAIza[A-Za-z0-9_-]{20,}\b"),      # Legacy Google API key
]

SENSITIVE_KEYS = {
    "gemini_api_key",
    "telegram_bot_token",
    "telegram_api_hash",
    "telegram_session",
    "api_hash",
    "session",
    "password",
    "otp",
    "code",
}


def redact_text(text: str) -> str:
    out = text
    for pattern in SECRET_PATTERNS:
        out = pattern.sub("[REDACTED]", out)
    return out


def redact_mapping(data: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, value in data.items():
        if key.lower() in SENSITIVE_KEYS:
            safe[key] = "[REDACTED]"
        elif isinstance(value, str):
            safe[key] = redact_text(value)
        else:
            safe[key] = value
    return safe
