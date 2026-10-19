"""Состояние текущего update, которое читают UI-хендлеры."""

from __future__ import annotations

from contextvars import ContextVar


class MessageTracker:
    """Последний message_id в чате: всё, что ниже, уже не последнее сообщение.

    message_id в Telegram растут монотонно в пределах чата; трекер живёт в
    процессе бота и после рестарта считает неизвестное сообщение старым.
    """

    def __init__(self) -> None:
        self._last: dict[int, int] = {}

    def note(self, chat_id: int, message_id: int) -> None:
        if message_id > self._last.get(chat_id, 0):
            self._last[chat_id] = message_id

    def is_stale(self, chat_id: int, message_id: int) -> bool:
        last = self._last.get(chat_id)
        return last is None or message_id < last


# Клик по кнопке не последнего сообщения: результат шлём новым сообщением.
stale_message: ContextVar[bool] = ContextVar("stale_message", default=False)

__all__ = ["MessageTracker", "stale_message"]
