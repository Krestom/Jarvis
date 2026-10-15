"""Знания: список, карточка, полный текст, похожие, удаление."""

from __future__ import annotations

from aiogram import Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.bot.callbacks import KbCB, KbListCB, uuid_hex
from jarvis.bot.formatting import split_message
from jarvis.bot.handlers.open_entity import parse_hex_uuid, render_knowledge_card
from jarvis.bot.handlers.render import send_search_results
from jarvis.bot.keyboards import PAGE_SIZE, knowledge_list_keyboard, main_menu
from jarvis.config.settings import Settings
from jarvis.conversation.engine import EngineOutcome
from jarvis.domain.enums import EntityType, PendingOpType
from jarvis.repositories.knowledge import KnowledgeRepository
from jarvis.repositories.links import LinkRepository
from jarvis.services.search import SearchService


async def send_knowledge_list(message: Message, session: AsyncSession, *, page: int) -> None:
    repo = KnowledgeRepository(session)
    items = await repo.list_recent(limit=PAGE_SIZE, offset=page * PAGE_SIZE)
    if not items and page == 0:
        await message.answer(
            "📚 Пока пусто. Напишите заметку — я предложу её сохранить.",
            reply_markup=main_menu(),
        )
        return
    lines = ["📚 <b>Знания</b> (последние записи)", ""]
    for index, item in enumerate(items, start=1):
        lines.append(f"{index}. {item.title}")
    markup = knowledge_list_keyboard(items, page, has_next=len(items) >= PAGE_SIZE)
    for chunk in split_message("\n".join(lines)):
        await message.answer(chunk)
    await message.answer("Выберите запись:", reply_markup=markup)


async def knowledge_page(
    callback: CallbackQuery, callback_data: KbListCB, session: AsyncSession
) -> None:
    await callback.answer()
    await send_knowledge_list(callback.message, session, page=callback_data.page)


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
        text, markup = await render_knowledge_card(
            session, item_id, full=callback_data.action == "full"
        )
        await callback.answer()
        if text is None:
            await callback.message.answer("Запись не найдена.", reply_markup=main_menu())
            return
        for chunk in split_message(text):
            await callback.message.answer(chunk)
        if callback_data.action == "view":
            await callback.message.answer("Действия:", reply_markup=markup)
        return

    if callback_data.action == "similar":
        item = await KnowledgeRepository(session).get(item_id)
        if item is None:
            await callback.answer("Запись не найдена", show_alert=True)
            return
        hits = await SearchService(session).search(item.title, limit=PAGE_SIZE)
        hits = [hit for hit in hits if hit.entity_id != item_id]
        await callback.answer()
        await send_search_results(
            callback.message,
            EngineOutcome("", search_hits=hits, search_query=item.title, search_page=0),
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

    if callback_data.action == "related":
        await callback.answer()
        links = await LinkRepository(session).list_for(EntityType.KNOWLEDGE, item_id)
        if not links:
            await callback.message.answer("Связанных записей нет.")
            return
        lines = ["🔗 <b>Связанные записи</b>"]
        for link in links:
            lines.append(
                f"• {link.entity_type.value}: <code>{link.entity_id}</code> ({link.relation.value})"
            )
        await callback.message.answer("\n".join(lines))
        return

    await callback.answer("Неизвестное действие", show_alert=True)


_ = uuid_hex


def build_router() -> Router:
    router = Router(name="knowledge")
    router.callback_query.register(knowledge_page, KbListCB.filter())
    router.callback_query.register(knowledge_action, KbCB.filter())
    return router
