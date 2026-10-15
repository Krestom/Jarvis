"""Сборка aiogram-бота и диспетчера."""

from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.types import BotCommand
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jarvis.ai import AIProvider
from jarvis.bot.handlers import build_routers
from jarvis.bot.middlewares import register_middlewares
from jarvis.config.settings import Settings
from jarvis.security.auth import Authorizer

BOT_COMMANDS = [
    BotCommand(command="start", description="Главное меню"),
    BotCommand(command="search", description="Поиск по базе"),
    BotCommand(command="tasks", description="Задачи"),
    BotCommand(command="today", description="Задачи на сегодня"),
    BotCommand(command="inbox", description="Неразобранное"),
    BotCommand(command="documents", description="Документы"),
    BotCommand(command="secret", description="Секреты"),
    BotCommand(command="settings", description="Настройки"),
    BotCommand(command="status", description="Диагностика"),
    BotCommand(command="help", description="Справка"),
    BotCommand(command="cancel", description="Отменить текущий сценарий"),
]


def create_bot(settings: Settings) -> Bot:
    session = AiohttpSession(proxy=settings.bot_proxy or None)
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
) -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher["settings"] = settings
    dispatcher["ai"] = ai
    dispatcher["session_factory"] = session_factory
    dispatcher["transcription"] = transcription

    authorizer = Authorizer(settings.telegram_allowed_user_ids)
    register_middlewares(dispatcher, session_factory, authorizer)

    for router in build_routers():
        dispatcher.include_router(router)
    return dispatcher


async def setup_bot_commands(bot: Bot) -> None:
    await bot.set_my_commands(BOT_COMMANDS)
