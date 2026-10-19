"""Состояние текущего update, которое читают UI-хендлеры."""

from __future__ import annotations

from contextvars import ContextVar

# Клик по кнопке сообщения старше ~10 минут: результат шлём новым сообщением.
stale_message: ContextVar[bool] = ContextVar("stale_message", default=False)

__all__ = ["stale_message"]
