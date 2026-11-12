"""Telegram-сессия с учётом id отправленных сообщений для проверки устаревших кнопок."""

from __future__ import annotations

from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import Message

from jarvis.bot.ui_state import MessageTracker


class TrackingAiohttpSession(AiohttpSession):
    """AiohttpSession, которая запоминает id отправленных сообщений."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.tracker = MessageTracker()

    async def make_request(self, bot, method, timeout: int | None = None):
        result = await super().make_request(bot, method, timeout)
        if isinstance(result, Message):
            self.tracker.note(result.chat.id, result.message_id)
        return result


__all__ = ["TrackingAiohttpSession"]
