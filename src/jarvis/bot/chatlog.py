"""Запись диалога в журнал: middleware (вход) и обёртка отправки (выход)."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import Message, TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jarvis.config.settings import Settings
from jarvis.db.session import session_scope
from jarvis.logging_setup import get_correlation_id
from jarvis.services.chatlog import ChatLogService

log = logging.getLogger(__name__)


class ChatLogMiddleware(BaseMiddleware):
    """Входящие текстовые сообщения и команды (внутри DBSessionMiddleware)."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        session = data.get("session")
        if session is not None and isinstance(event, Message) and event.text:
            source = "command" if event.text.startswith("/") else "text"
            await ChatLogService(session, data["settings"]).record(
                event.from_user.id,
                direction="in",
                text=event.text,
                source=source,
                correlation_id=get_correlation_id(),
            )
        return await handler(event, data)


class RecordingAiohttpSession(AiohttpSession):
    """AiohttpSession, которая после успешной отправки пишет текст в журнал."""

    recorder: Callable[[int, str], Awaitable[None]] | None = None

    async def make_request(self, bot, method, timeout: int | None = None):
        result = await super().make_request(bot, method, timeout)
        if (
            self.recorder is not None
            and type(method).__name__ == "SendMessage"
            and getattr(method, "text", None)
        ):
            await self.recorder(int(method.chat_id), method.text)
        return result


def build_out_recorder(session_factory: async_sessionmaker[AsyncSession], settings: Settings):
    """Колбэк для RecordingAiohttpSession: пишет исходящие в тот же журнал."""

    async def record(chat_id: int, text: str) -> None:
        try:
            async with session_scope(session_factory) as session:
                await ChatLogService(session, settings).record(
                    chat_id, direction="out", text=text, source="bot"
                )
        except Exception:  # noqa: BLE001 - журнал не должен ломать отправку сообщений
            log.exception("failed to record outgoing chat message")

    return record


__all__ = ["ChatLogMiddleware", "RecordingAiohttpSession", "build_out_recorder"]
