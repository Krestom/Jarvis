"""Middlewares: корреляция, авторизация, сессия БД, дедупликация updates."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, Update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jarvis.bot.ui_state import stale_message
from jarvis.conversation.flows import Flow
from jarvis.conversation.state import ConversationStore
from jarvis.db.session import session_scope
from jarvis.logging_setup import new_correlation_id
from jarvis.repositories.conversation import ConversationRepository
from jarvis.repositories.processed_updates import ProcessedUpdateRepository
from jarvis.security.auth import Authorizer

log = logging.getLogger(__name__)


class CorrelationMiddleware(BaseMiddleware):
    """Новый correlation id на каждый update."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        new_correlation_id()
        return await handler(event, data)


class AccessMiddleware(BaseMiddleware):
    """Проверка allow-list для сообщений и callback-кнопок.

    Неавторизованные события молча игнорируются: бот не подтверждает,
    что за ним вообще есть база.
    """

    def __init__(self, authorizer: Authorizer) -> None:
        self._authorizer = authorizer

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        user_id = getattr(user, "id", None)
        if not self._authorizer.is_allowed(user_id):
            log.warning("access denied for user_id=%s", user_id)
            return None
        return await handler(event, data)


class UpdateDedupMiddleware(BaseMiddleware):
    """Идемпотентность: один и тот же update не обрабатывается дважды.

    Отметка ставится после успешной обработки; повторная доставка после
    сбоя процесса безопасна (операции подтверждаются идемпотентно).
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Update) or event.update_id <= 0:
            return await handler(event, data)
        async with session_scope(self._session_factory) as session:
            if await ProcessedUpdateRepository(session).is_processed(event.update_id):
                log.info("duplicate update_id=%s skipped", event.update_id)
                return None
        result = await handler(event, data)
        async with session_scope(self._session_factory) as session:
            await ProcessedUpdateRepository(session).mark(event.update_id)
        return result


class DBSessionMiddleware(BaseMiddleware):
    """Открывает сессию БД на время обработки события.

    commit выполняется после успешного хендлера, rollback — при ошибке.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with session_scope(self._session_factory) as session:
            data["session"] = session
            return await handler(event, data)


class MessageFreshnessMiddleware(BaseMiddleware):
    """Кнопка не последнего сообщения: новый ответ внизу и сброс сценария.

    Живёт внутри DBSessionMiddleware (нужен data["session"]). Для обычных
    сообщений трекер запоминает message_id. Поиск не сбрасываем: по нему
    листают старые результаты.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        bot = data.get("bot")
        tracker = getattr(getattr(bot, "session", None), "tracker", None)
        if isinstance(event, Message):
            if tracker is not None:
                tracker.note(event.chat.id, event.message_id)
            return await handler(event, data)
        if not isinstance(event, CallbackQuery) or event.message is None:
            return await handler(event, data)
        if (
            tracker is not None
            and not tracker.is_stale(event.message.chat.id, event.message.message_id)
        ):
            return await handler(event, data)
        session = data.get("session")
        settings = data.get("settings")
        if session is not None and settings is not None and event.from_user is not None:
            store = ConversationStore(ConversationRepository(session), settings)
            state = await store.get_active(event.from_user.id)
            if state is not None and state.flow != Flow.SEARCH_WAIT:
                await store.clear(event.from_user.id)
        token = stale_message.set(True)
        try:
            return await handler(event, data)
        finally:
            stale_message.reset(token)


def register_middlewares(dispatcher, session_factory, authorizer) -> None:
    """Порядок: корреляция → дедупликация → авторизация → сессия → журнал → хендлеры."""
    dispatcher.update.outer_middleware(CorrelationMiddleware())
    dispatcher.update.outer_middleware(UpdateDedupMiddleware(session_factory))
    dispatcher.message.outer_middleware(AccessMiddleware(authorizer))
    dispatcher.callback_query.outer_middleware(AccessMiddleware(authorizer))
    dispatcher.message.middleware(DBSessionMiddleware(session_factory))
    dispatcher.callback_query.middleware(DBSessionMiddleware(session_factory))
    dispatcher.message.middleware(MessageFreshnessMiddleware())
    dispatcher.callback_query.middleware(MessageFreshnessMiddleware())
    from jarvis.bot.chatlog import ChatLogMiddleware

    dispatcher.message.middleware(ChatLogMiddleware())
