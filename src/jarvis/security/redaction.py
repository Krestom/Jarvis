"""Маскирование секретов в логах и текстах, которые уходят наружу."""

from __future__ import annotations

import re

# (pattern, replacement). Порядок важен: сначала многострочные блоки.
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
        "[REDACTED_PRIVATE_KEY]",
    ),
    (
        re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b"),
        "[REDACTED_TELEGRAM_TOKEN]",
    ),
    (
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
        "[REDACTED_JWT]",
    ),
    (
        re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
        "[REDACTED_API_KEY]",
    ),
    (
        re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"),
        "Bearer [REDACTED]",
    ),
    (
        re.compile(
            r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key)\b\s*[:=]\s*\S+"
        ),
        r"\1=[REDACTED]",
    ),
]


def redact(text: str) -> str:
    """Заменить известные типы секретов на маркеры."""
    if not text:
        return text
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


# Слова-маркеры, по которым текст похож на креды/секреты.
_SECRET_HINTS = (
    "пароль",
    "password",
    "passwd",
    "токен",
    "token",
    "api key",
    "api-key",
    "секрет",
    "private key",
    "креды",
    "creds",
    "credential",
)


def looks_like_secret(text: str) -> bool:
    """Похож ли текст на секрет/креды (для безопасного уровня чувствительности)."""
    if not text:
        return False
    lowered = text.lower()
    if any(hint in lowered for hint in _SECRET_HINTS):
        return True
    return any(pattern.search(text) for pattern, _ in _PATTERNS)
