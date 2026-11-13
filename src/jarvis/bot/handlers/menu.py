"""Навигация по меню (inline-кнопки)."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.ai import AIProvider
from jarvis.bot.callbacks import MenuCB
from jarvis.bot.ui_state import stale_message
from jarvis.config.settings import Settings
from jarvis.conversation.flows import Flow
from jarvis.conversation.state import ConversationStore
from jarvis.repositories.conversation import ConversationRepository


def MenuCBFilter(action: str):  # noqa: N802 - читаемо на месте использования
    return MenuCB.filter(F.action == action)


async def _edit_or_send(callback: CallbackQuery, text: str, markup, *, with_menu: bool = False):
    from jarvis.bot.keyboards import main_menu

    reply_markup = main_menu() if with_menu else markup
    message = callback.message
    if stale_message.get():
        await message.answer(text, reply_markup=reply_markup)
        return
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except Exception:  # noqa: BLE001 - старое сообщение могло быть недоступно для правки
        await message.answer(text, reply_markup=reply_markup)


async def _leave_search(
    callback: CallbackQuery, session: AsyncSession, settings: Settings
) -> None:
    await ConversationStore(
        ConversationRepository(session), settings
    ).clear_search_wait(callback.from_user.id)


async def menu_main(
    callback: CallbackQuery, session: AsyncSession, settings: Settings
) -> None:
    await _leave_search(callback, session, settings)
    await _edit_or_send(callback, "Главное меню:", markup=None, with_menu=True)


async def menu_search(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    store = ConversationStore(ConversationRepository(session), settings)
    await store.set(callback.from_user.id, Flow.SEARCH_WAIT, step="asked")
    await callback.answer()
    await callback.message.answer("🔎 Напишите, что искать.")


async def menu_tasks(
    callback: CallbackQuery, session: AsyncSession, settings: Settings
) -> None:
    from jarvis.bot.handlers.render import send_or_edit
    from jarvis.bot.keyboards import tasks_menu_keyboard

    await _leave_search(callback, session, settings)
    await callback.answer()
    await send_or_edit(
        callback.message,
        "📋 <b>Задачи</b>\nВыберите список:",
        markup=tasks_menu_keyboard(),
        edit=True,
    )


async def menu_knowledge(
    callback: CallbackQuery, session: AsyncSession, settings: Settings
) -> None:
    from jarvis.bot.handlers.knowledge import send_knowledge_list

    await _leave_search(callback, session, settings)
    await callback.answer()
    await send_knowledge_list(callback.message, session, page=0, edit=True)


async def menu_documents(
    callback: CallbackQuery, session: AsyncSession, settings: Settings
) -> None:
    from jarvis.bot.handlers.documents import send_documents_list

    await _leave_search(callback, session, settings)
    await callback.answer()
    await send_documents_list(callback.message, session, page=0, edit=True)


async def menu_secrets(
    callback: CallbackQuery, session: AsyncSession, settings: Settings
) -> None:
    from jarvis.bot.handlers.secrets import send_secrets_list

    await _leave_search(callback, session, settings)
    await callback.answer()
    await send_secrets_list(callback.message, session, settings, page=0, edit=True)


async def menu_secret_new(
    callback: CallbackQuery, session: AsyncSession, settings: Settings, ai: AIProvider
) -> None:
    from jarvis.bot.handlers.secrets import start_secret_flow

    await callback.answer()
    await start_secret_flow(callback.message, session, settings, ai)


async def menu_cancel_flow(
    callback: CallbackQuery, session: AsyncSession, settings: Settings
) -> None:
    store = ConversationStore(ConversationRepository(session), settings)
    await store.clear(callback.from_user.id)
    await callback.answer("Сценарий отменён")
    await _edit_or_send(callback, "Сценарий отменён.", markup=None, with_menu=True)


async def menu_force_save(
    callback: CallbackQuery, session: AsyncSession, settings: Settings
) -> None:
    """Сознательное сохранение сообщения с секретом как заметки."""
    from jarvis.bot.handlers.render import propose_and_send
    from jarvis.conversation.heuristics import first_line
    from jarvis.domain.enums import (
        KnowledgeSource,
        KnowledgeType,
        PendingOpType,
        Sensitivity,
    )
    from jarvis.services.drafts import KnowledgeDraft

    store = ConversationStore(ConversationRepository(session), settings)
    state = await store.get_active(callback.from_user.id)
    if (
        state is None
        or state.flow != Flow.FORCE_SAVE_PENDING
        or not state.data.get("text")
    ):
        await callback.answer("Нечего сохранять — напишите сообщение заново.", show_alert=True)
        return
    text = str(state.data["text"])
    await store.clear(callback.from_user.id)
    await callback.answer()

    draft = KnowledgeDraft(
        type=KnowledgeType.NOTE,
        title=first_line(text) or "Заметка",
        content=text,
        source_type=KnowledgeSource.TEXT,
        original_text=text,
        sensitivity=Sensitivity.SENSITIVE,
    )
    await propose_and_send(
        callback.message,
        session,
        settings,
        callback.from_user.id,
        PendingOpType.CREATE_KNOWLEDGE,
        draft.model_dump(mode="json"),
    )
    mcp_note = (
        "но MCP её увидит — пароли и токены храните через /secret."
        if settings.mcp_include_sensitive
        else "MCP её не увидит."
    )
    await callback.message.answer(
        f"<i>Сохраню с уровнем «чувствительная»: вы сможете её искать сами, {mcp_note}</i>"
    )


def build_router() -> Router:
    router = Router(name="menu")
    router.callback_query.register(menu_main, MenuCBFilter("main"))
    router.callback_query.register(menu_search, MenuCBFilter("search"))
    router.callback_query.register(menu_tasks, MenuCBFilter("tasks"))
    router.callback_query.register(menu_knowledge, MenuCBFilter("knowledge"))
    router.callback_query.register(menu_documents, MenuCBFilter("documents"))
    router.callback_query.register(menu_secrets, MenuCBFilter("secrets"))
    router.callback_query.register(menu_secret_new, MenuCBFilter("secret_new"))
    router.callback_query.register(menu_cancel_flow, MenuCBFilter("cancel_flow"))
    router.callback_query.register(menu_force_save, MenuCBFilter("force_save"))
    return router
