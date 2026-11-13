"""Свободный текст — последний обработчик: всё, что не команда и не файл."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.agent.queue import UserSessionGate
from jarvis.ai import AIProvider
from jarvis.bot.handlers.render import send_outcome
from jarvis.bot.rich import send_rich
from jarvis.bot.screens.core import help_screen
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
        await send_rich(
            message,
            help_screen(intro="Неизвестная команда. Вот что я умею:"),
        )
        return
    engine = ConversationEngine(
        session, settings, ai, gate=agent_gate, search_provider=search_provider
    )
    outcome = await engine.handle_text(message.from_user.id, text)
    await send_outcome(message, outcome, session, settings)


def build_router() -> Router:
    router = Router(name="free-text")
    router.message.register(free_text, F.text)
    return router
