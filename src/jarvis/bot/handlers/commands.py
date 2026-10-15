"""Команды бота."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.agent.queue import UserSessionGate
from jarvis.ai import AIProvider
from jarvis.bot.keyboards import main_menu, tasks_menu_keyboard
from jarvis.bot.texts import CANCELLED_TEXT, HELP_TEXT, START_TEXT
from jarvis.config.settings import Settings
from jarvis.conversation.flows import Flow
from jarvis.conversation.state import ConversationStore
from jarvis.repositories.conversation import ConversationRepository
from jarvis.services.catalog import CatalogService
from jarvis.services.confirmation import ConfirmationService


async def cmd_start(message: Message, session: AsyncSession) -> None:
    created = await CatalogService(session).ensure_defaults()
    text = START_TEXT
    if created:
        text += f"\nСоздал {created} стартовых категорий — их можно переименовать в /settings."
    await message.answer(text, reply_markup=main_menu())


async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT, reply_markup=main_menu())


async def cmd_cancel(
    message: Message, session: AsyncSession, settings: Settings, agent_gate: UserSessionGate
) -> None:
    user_id = message.from_user.id
    # Сначала останавливаем бегущий агентский запрос (если он есть).
    cancelled_agent = agent_gate.request_cancel(user_id)
    store = ConversationStore(ConversationRepository(session), settings)
    state = await store.get_active(user_id)
    if state is not None and state.pending_operation_id is not None:
        await ConfirmationService(session, settings).cancel(user_id, state.pending_operation_id)
    await store.clear(user_id)
    text = CANCELLED_TEXT
    if cancelled_agent:
        text = "Останавливаю текущий запрос. " + text
    await message.answer(text, reply_markup=main_menu())


async def cmd_search(message: Message, session: AsyncSession, settings: Settings) -> None:
    store = ConversationStore(ConversationRepository(session), settings)
    await store.set(message.from_user.id, Flow.SEARCH_WAIT, step="asked")
    await message.answer("Напишите, что искать.")


async def cmd_tasks(message: Message) -> None:
    await message.answer("📋 <b>Задачи</b>\nВыберите список:", reply_markup=tasks_menu_keyboard())


async def cmd_today(message: Message, session: AsyncSession, settings: Settings) -> None:
    from jarvis.bot.handlers.tasks import send_task_list

    await send_task_list(message, session, settings, view="today", page=0)


async def cmd_inbox(message: Message, session: AsyncSession, settings: Settings) -> None:
    from jarvis.bot.handlers.tasks import send_task_list

    await send_task_list(message, session, settings, view="inbox", page=0)


async def cmd_documents(message: Message, session: AsyncSession) -> None:
    from jarvis.bot.handlers.documents import send_documents_list

    await send_documents_list(message, session, page=0)


async def cmd_secret(
    message: Message, session: AsyncSession, settings: Settings, ai: AIProvider
) -> None:
    from jarvis.bot.handlers.secrets import start_secret_flow

    await start_secret_flow(message, session, settings, ai)


async def cmd_settings(message: Message, session: AsyncSession, settings: Settings) -> None:
    from jarvis.bot.handlers.menu import send_settings

    await send_settings(message, session, settings)


def build_router() -> Router:
    router = Router(name="commands")
    router.message.register(cmd_start, CommandStart())
    router.message.register(cmd_help, Command("help"))
    router.message.register(cmd_cancel, Command("cancel"))
    router.message.register(cmd_search, Command("search"))
    router.message.register(cmd_tasks, Command("tasks"))
    router.message.register(cmd_today, Command("today"))
    router.message.register(cmd_inbox, Command("inbox"))
    router.message.register(cmd_documents, Command("documents"))
    router.message.register(cmd_secret, Command("secret"))
    router.message.register(cmd_settings, Command("settings"))
    return router
