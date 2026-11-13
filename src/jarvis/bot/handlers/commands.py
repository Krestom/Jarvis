"""Команды бота."""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.agent.queue import UserSessionGate
from jarvis.ai import AIProvider
from jarvis.bot.keyboards import main_menu
from jarvis.bot.rich import send_rich, send_rich_text
from jarvis.bot.screens.core import help_screen, main_menu_screen, tasks_menu_screen
from jarvis.bot.texts import CANCELLED_TEXT, START_TEXT
from jarvis.bot.widgets.send import send_widget
from jarvis.bot.widgets.weather import WidgetTooLarge, render_weather_widget
from jarvis.config.settings import Settings
from jarvis.conversation.flows import Flow
from jarvis.conversation.state import ConversationStore
from jarvis.repositories.conversation import ConversationRepository
from jarvis.services.confirmation import ConfirmationService
from jarvis.weather import create_weather_provider
from jarvis.weather.provider import WeatherUnavailable

log = logging.getLogger(__name__)


async def _leave_search(user_id: int, session: AsyncSession, settings: Settings) -> None:
    await ConversationStore(
        ConversationRepository(session), settings
    ).clear_search_wait(user_id)


async def cmd_start(message: Message, session: AsyncSession, settings: Settings) -> None:
    await _leave_search(message.from_user.id, session, settings)
    await send_rich(
        message,
        main_menu_screen(intro="На связи. Выберите действие:", fallback=START_TEXT),
    )


async def cmd_help(message: Message, session: AsyncSession, settings: Settings) -> None:
    await _leave_search(message.from_user.id, session, settings)
    await send_rich(message, help_screen())


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
    await send_rich_text(message, text, main_menu())


async def cmd_search(message: Message, session: AsyncSession, settings: Settings) -> None:
    store = ConversationStore(ConversationRepository(session), settings)
    await store.set(message.from_user.id, Flow.SEARCH_WAIT, step="asked")
    await message.answer("Напишите, что искать.")


async def cmd_tasks(message: Message, session: AsyncSession, settings: Settings) -> None:
    await _leave_search(message.from_user.id, session, settings)
    await send_rich(message, tasks_menu_screen())


async def cmd_today(message: Message, session: AsyncSession, settings: Settings) -> None:
    from jarvis.bot.handlers.tasks import send_task_list

    await _leave_search(message.from_user.id, session, settings)
    await send_task_list(message, session, settings, view="today", page=0)


async def cmd_weather(message: Message, session: AsyncSession, settings: Settings) -> None:
    await _leave_search(message.from_user.id, session, settings)
    try:
        snapshot = await create_weather_provider(settings).fetch()
        payload = render_weather_widget(snapshot)
    except (WeatherUnavailable, WidgetTooLarge, ValueError) as error:
        log.warning("weather is unavailable: %s", error)
        await send_rich_text(
            message, "Погода сейчас недоступна. Попробуйте позже.", main_menu()
        )
        return
    try:
        await send_widget(message.bot, message.chat.id, payload)
    except Exception:  # noqa: BLE001 - виджет не должен терять ответ
        log.exception("failed to send weather widget")
        await send_rich_text(message, payload.caption, main_menu())


async def cmd_documents(message: Message, session: AsyncSession, settings: Settings) -> None:
    from jarvis.bot.handlers.documents import send_documents_list

    await _leave_search(message.from_user.id, session, settings)
    await send_documents_list(message, session, page=0)


async def cmd_secret(
    message: Message, session: AsyncSession, settings: Settings, ai: AIProvider
) -> None:
    from jarvis.bot.handlers.secrets import start_secret_flow

    await start_secret_flow(message, session, settings, ai)


def build_router() -> Router:
    router = Router(name="commands")
    router.message.register(cmd_start, CommandStart())
    router.message.register(cmd_help, Command("help"))
    router.message.register(cmd_cancel, Command("cancel"))
    router.message.register(cmd_search, Command("search"))
    router.message.register(cmd_tasks, Command("tasks"))
    router.message.register(cmd_today, Command("today"))
    router.message.register(cmd_weather, Command("weather"))
    router.message.register(cmd_documents, Command("documents"))
    router.message.register(cmd_secret, Command("secret"))
    return router
