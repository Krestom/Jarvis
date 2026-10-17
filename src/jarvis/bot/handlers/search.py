"""Поиск: пагинация и открытие результатов."""

from __future__ import annotations

from aiogram import Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.ai import AIProvider
from jarvis.bot.callbacks import SearchCB
from jarvis.bot.formatting import split_message
from jarvis.bot.handlers.open_entity import (
    parse_hex_uuid,
    render_document_card,
    render_knowledge_card,
    render_task_card,
)
from jarvis.bot.handlers.render import send_outcome
from jarvis.bot.keyboards import main_menu
from jarvis.config.settings import Settings
from jarvis.conversation.engine import ConversationEngine


async def search_action(
    callback: CallbackQuery,
    callback_data: SearchCB,
    session: AsyncSession,
    settings: Settings,
    ai: AIProvider,
) -> None:
    if callback_data.action in ("page_next", "page_prev"):
        engine = ConversationEngine(session, settings, ai)
        outcome = await engine.search_page(callback.from_user.id, callback_data.page)
        await callback.answer()
        await send_outcome(callback.message, outcome, session, settings)
        return

    if callback_data.action == "open":
        entity_id = parse_hex_uuid(callback_data.entity)
        if entity_id is None:
            await callback.answer("Некорректная ссылка", show_alert=True)
            return
        text, markup = None, None
        if callback_data.kind == "task":
            text, markup = await render_task_card(session, settings, entity_id)
        elif callback_data.kind == "knowledge":
            text, markup = await render_knowledge_card(session, entity_id)
        elif callback_data.kind == "document":
            text, markup = await render_document_card(session, entity_id)
        await callback.answer()
        if text is None:
            await callback.message.answer("Запись не найдена.", reply_markup=main_menu())
            return
        for chunk in split_message(text):
            await callback.message.answer(chunk)
        await callback.message.answer("Действия:", reply_markup=markup)
        return

    await callback.answer("Неизвестное действие", show_alert=True)


def build_router() -> Router:
    router = Router(name="search")
    router.callback_query.register(search_action, SearchCB.filter())
    return router
