"""Свободный текст — последний обработчик: всё, что не команда и не файл."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.agent.queue import UserSessionGate
from jarvis.ai import AIProvider
from jarvis.bot.handlers.render import send_outcome
from jarvis.bot.keyboards import main_menu
from jarvis.bot.texts import HELP_TEXT
from jarvis.config.settings import Settings
from jarvis.conversation.engine import ConversationEngine


async def free_text(
    message: Message,
    session: AsyncSession,
    settings: Settings,
    ai: AIProvider,
    agent_gate: UserSessionGate,
    search_provider=None,
) -> None:
    text = message.text or ""
    if text.startswith("/"):
        await message.answer(
            "Неизвестная команда. Вот что я умею:\n\n" + HELP_TEXT, reply_markup=main_menu()
        )
        return
    engine = ConversationEngine(
        session, settings, ai, gate=agent_gate, search_provider=search_provider
    )
    outcome = await engine.handle_text(message.from_user.id, text)
    await send_outcome(message, outcome)


def build_router() -> Router:
    router = Router(name="free-text")
    router.message.register(free_text, F.text)
    return router
