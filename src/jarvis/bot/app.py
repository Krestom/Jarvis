"""Сборка aiogram-бота и диспетчера."""

from __future__ import annotations

import logging
import time
import traceback

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, ErrorEvent
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jarvis.agent.queue import UserSessionGate
from jarvis.ai import AIProvider
from jarvis.bot.chatlog import RecordingAiohttpSession
from jarvis.bot.handlers import build_routers
from jarvis.bot.middlewares import register_middlewares
from jarvis.config.settings import Settings
from jarvis.db.session import session_scope
from jarvis.logging_setup import get_correlation_id
from jarvis.research import create_search_provider
from jarvis.security.auth import Authorizer
from jarvis.services.improvements import ImprovementService

log = logging.getLogger(__name__)

BOT_COMMANDS = [
    BotCommand(command="start", description="Главное меню"),
    BotCommand(command="search", description="Поиск по базе"),
    BotCommand(command="tasks", description="Задачи"),
    BotCommand(command="today", description="Задачи на сегодня"),
    BotCommand(command="inbox", description="Неразобранное"),
    BotCommand(command="documents", description="Документы"),
    BotCommand(command="secret", description="Секреты"),
    BotCommand(command="settings", description="Настройки"),
    BotCommand(command="diagnostics", description="Диагностика и backlog"),
    BotCommand(command="export_issues", description="Экспорт заявок (MD/JSON)"),
    BotCommand(command="export_chat", description="Экспорт чата за день (MD/JSON)"),
    BotCommand(command="help", description="Справка"),
    BotCommand(command="cancel", description="Отменить текущий сценарий"),
]


def create_bot(settings: Settings) -> Bot:
    session = RecordingAiohttpSession(proxy=settings.bot_proxy or None)
    return Bot(
        token=settings.bot_token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def create_dispatcher(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    ai: AIProvider,
    transcription=None,
    bot: Bot | None = None,
) -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher["settings"] = settings
    dispatcher["ai"] = ai
    dispatcher["session_factory"] = session_factory
    dispatcher["transcription"] = transcription
    dispatcher["agent_gate"] = UserSessionGate(settings)
    try:
        dispatcher["search_provider"] = create_search_provider(settings)
    except ValueError as error:
        log.error("web search provider is misconfigured, search disabled: %s", error)
        from jarvis.research.none import NoneSearchProvider

        dispatcher["search_provider"] = NoneSearchProvider()

    authorizer = Authorizer(settings.telegram_allowed_user_ids)
    register_middlewares(dispatcher, session_factory, authorizer)
    _register_error_handler(dispatcher, settings, session_factory, bot)

    for router in build_routers():
        dispatcher.include_router(router)
    return dispatcher


# Уведомления о сбоях: не чаще одного на заголовок в этом окне (защита от флуда
# при циклической ошибке); словарь не должен расти бесконечно.
_notified_titles: dict[str, float] = {}
_NOTIFY_THROTTLE_SECONDS = 30.0


def notification_allowed(title: str) -> bool:
    now = time.monotonic()
    last = _notified_titles.get(title)
    if last is not None and now - last < _NOTIFY_THROTTLE_SECONDS:
        return False
    _notified_titles[title] = now
    if len(_notified_titles) > 200:
        for key in sorted(_notified_titles, key=_notified_titles.get)[:100]:
            _notified_titles.pop(key, None)
    return True


async def send_failure_notification(
    bot: Bot, owner_id: int, title: str, occurrences: int
) -> None:
    """Уведомить владельца о сбое, записанном в диагностику."""
    await bot.send_message(
        owner_id,
        f"⚠️ Зафиксирован сбой: {title[:200]}\n"
        f"Повторов: {occurrences}. Подробнее: /diagnostics",
        parse_mode=None,
    )


def _register_error_handler(
    dispatcher: Dispatcher,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    bot: Bot | None = None,
) -> None:
    """Технические сбои бота автоматически попадают в improvement backlog."""

    async def on_error(event: ErrorEvent) -> bool:
        try:
            update = event.update
            error = event.exception
            error_type = type(error).__name__
            message = str(error).strip() or error_type
            async with session_scope(session_factory) as session:
                item = await ImprovementService(session, settings).record_failure(
                    title=f"{error_type}: {message}",
                    summary=message,
                    component="telegram_bot",
                    correlation_id=get_correlation_id(),
                    error_type=error_type,
                    traceback_text="".join(
                        traceback.format_exception(type(error), error, error.__traceback__)
                    ),
                    context={
                        "update_type": getattr(update, "event_type", None),
                        "update_id": getattr(update, "update_id", None),
                    },
                )
            if (
                bot is not None
                and settings.telegram_allowed_user_ids
                and notification_allowed(item.title)
            ):
                try:
                    await send_failure_notification(
                        bot,
                        settings.telegram_allowed_user_ids[0],
                        item.title,
                        item.occurrence_count,
                    )
                except Exception:  # noqa: BLE001 - уведомление не важнее обработки
                    log.exception("failed to send failure notification")
        except Exception:  # noqa: BLE001 - обработчик ошибок не должен падать
            log.exception("failed to record error to improvement backlog")
        return True

    dispatcher.errors.register(on_error)


async def setup_bot_commands(bot: Bot) -> None:
    await bot.set_my_commands(BOT_COMMANDS)
