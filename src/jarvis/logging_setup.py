"""Структурированные логи с correlation id и маскированием секретов."""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar

from jarvis.security.redaction import redact

correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="-")


def new_correlation_id() -> str:
    """Новый id для корреляции событий одного запроса/апдейта."""
    value = uuid.uuid4().hex[:12]
    correlation_id_var.set(value)
    return value


def get_correlation_id() -> str:
    return correlation_id_var.get()


class RedactingFilter(logging.Filter):
    """Подставляет correlation id и вычищает секреты из сообщения."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = correlation_id_var.get()
        record.msg = redact(record.getMessage())
        record.args = None
        return True


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s %(name)s [%(correlation_id)s] %(message)s")
    )
    handler.addFilter(RedactingFilter())

    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())

    # Не засоряем лог шумом сторонних библиотек.
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
