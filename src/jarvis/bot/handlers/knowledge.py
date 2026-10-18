"""Знания: список, карточка, полный текст, похожие, удаление."""

from __future__ import annotations

from aiogram import Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.bot.callbacks import KbCB, KbListCB, uuid_hex
from jarvis.bot.handlers.open_entity import parse_hex_uuid, render_knowledge_card
from jarvis.bot.handlers.render import send_or_edit, send_search_results
from jarvis.bot.keyboards import (
    PAGE_SIZE,
    knowledge_card_keyboard,
    knowledge_list_keyboard,
    main_menu,
)
from jarvis.config.settings import Settings
from jarvis.conversation.engine import EngineOutcome
from jarvis.domain.enums import PendingOpType
from jarvis.repositories.knowledge import KnowledgeRepository
from jarvis.services.search import SearchService


async def send_knowledge_list(
    message: Message, session: AsyncSession, *, page: int, edit: bool = False
) -> None:
    from jarvis.bot.handlers.render import send_or_edit

    repo = KnowledgeRepository(session)
    items = await repo.list_recent(limit=PAGE_SIZE, offset=page * PAGE_SIZE)
    if not items and page == 0:
        await send_or_edit(
            message,
            "📚 Пока пусто. Напишите заметку — я предложу её сохранить.",
            markup=main_menu(),
            edit=edit,
        )
        return
    lines = ["📚 <b>Знания</b> (последние записи)", ""]
    for index, item in enumerate(items, start=1):
        lines.append(f"{index}. {item.title}")
    markup = knowledge_list_keyboard(items, page, has_next=len(items) >= PAGE_SIZE)
    await send_or_edit(
        message, "\n".join(lines) + "\n\nВыберите запись:", markup=markup, edit=edit
    )


async def knowledge_page(
    callback: CallbackQuery, callback_data: KbListCB, session: AsyncSession
) -> None:
    await callback.answer()
    await send_knowledge_list(
        callback.message, session, page=callback_data.page, edit=True
    )


async def knowledge_action(
    callback: CallbackQuery,
    callback_data: KbCB,
    session: AsyncSession,
    settings: Settings,
) -> None:
    user_id = callback.from_user.id
    item_id = parse_hex_uuid(callback_data.item_id)
    if item_id is None:
        await callback.answer("Некорректная запись", show_alert=True)
        return

    if callback_data.action in ("view", "full"):
        full = callback_data.action == "full"
        text, markup = await render_knowledge_card(session, item_id, full=full)
        await callback.answer()
        if text is None:
            await callback.message.answer("Запись не найдена.", reply_markup=main_menu())
            return
        # Карточка и полный текст правят сообщение, из которого открыты.
        await send_or_edit(
            callback.message, f"{text}\n\nДействия:", markup=markup, edit=True
        )
        return

    if callback_data.action == "similar":
        item = await KnowledgeRepository(session).get(item_id)
        if item is None:
            await callback.answer("Запись не найдена", show_alert=True)
            return
        hits = await SearchService(session).search(item.title, limit=PAGE_SIZE)
        hits = [hit for hit in hits if hit.entity_id != item_id]
        await callback.answer()
        if not hits:
            await send_or_edit(
                callback.message,
                "Похожих записей не нашлось.",
                markup=knowledge_card_keyboard(uuid_hex(item.id)),
                edit=True,
            )
            return
        await send_search_results(
            callback.message,
            EngineOutcome("", search_hits=hits, search_query=item.title, search_page=0),
            edit=True,
            back=("⬅️ К записи", KbCB(action="view", item_id=uuid_hex(item.id)).pack()),
        )
        return

    if callback_data.action == "delete":
        from jarvis.bot.handlers.render import propose_and_send

        await callback.answer()
        await propose_and_send(
            callback.message,
            session,
            settings,
            user_id,
            PendingOpType.DELETE_KNOWLEDGE,
            {"entity_id": str(item_id)},
        )
        return

    await callback.answer("Неизвестное действие", show_alert=True)


def build_router() -> Router:
    router = Router(name="knowledge")
    router.callback_query.register(knowledge_page, KbListCB.filter())
    router.callback_query.register(knowledge_action, KbCB.filter())
    return router
