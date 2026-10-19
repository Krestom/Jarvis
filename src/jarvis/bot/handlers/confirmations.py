"""Подтверждение, отмена и редактирование pending-операций."""

from __future__ import annotations

import uuid

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.ai import AIProvider
from jarvis.bot.callbacks import ConfirmCB, MenuCB
from jarvis.bot.texts import CALLBACK_STALE, CONFIRM_ALREADY, CONFIRM_APPLIED, CONFIRM_FAILED
from jarvis.bot.ui_state import stale_message
from jarvis.config.settings import Settings
from jarvis.conversation.engine import ConversationEngine
from jarvis.domain.enums import PendingOpType
from jarvis.services.confirmation import ConfirmationService, ConfirmResult

MESSAGE_LIMIT = 3900

# Для удалений и «невизуальных» операций: кнопка возврата к нужному списку.
_NAV_AFTER: dict[PendingOpType, tuple[str, str]] = {
    PendingOpType.DELETE_TASK: ("📋 К спискам", "tasks"),
    PendingOpType.DELETE_KNOWLEDGE: ("📚 К списку", "knowledge"),
    PendingOpType.DELETE_DOCUMENT: ("📄 К списку", "documents"),
    PendingOpType.CREATE_CATEGORY: ("⚙️ Настройки", "settings"),
    PendingOpType.RENAME_CATEGORY: ("⚙️ Настройки", "settings"),
    PendingOpType.CREATE_PROJECT: ("🗂 Проекты", "projects"),
    PendingOpType.CREATE_SECRET: ("🔐 Секреты", "secrets"),
    PendingOpType.DELETE_SECRET: ("🔐 Секреты", "secrets"),
}


async def _update_message(
    callback: CallbackQuery, suffix: str, *, nav: tuple[str, str] | None = None
) -> None:
    """Дописать итог в сообщение операции (кнопки снимаются/заменяются)."""
    message = callback.message
    updated = ((message.text or "") + suffix).strip()
    markup = (
        InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=nav[0], callback_data=MenuCB(action=nav[1]).pack()
                    )
                ]
            ]
        )
        if nav is not None
        else None
    )
    if len(updated) > MESSAGE_LIMIT or stale_message.get():
        await message.answer(suffix.strip(), reply_markup=markup)
        return
    try:
        await message.edit_text(updated, reply_markup=markup)
    except TelegramBadRequest as error:
        if "not modified" in str(error):
            return
        await message.answer(suffix.strip(), reply_markup=markup)
    except Exception:  # noqa: BLE001 - сообщение могло устареть
        await message.answer(suffix.strip(), reply_markup=markup)


async def _result_view(
    result: ConfirmResult, session: AsyncSession, settings: Settings
) -> tuple[str, InlineKeyboardMarkup] | None:
    """Свежий вид сущности после применённой операции (иначе — обычный текст)."""
    from jarvis.bot.handlers.open_entity import (
        render_comments_view,
        render_document_card,
        render_knowledge_card,
        render_task_card,
    )

    if result.entity_id is None:
        return None
    try:
        entity_id = uuid.UUID(result.entity_id)
    except ValueError:
        return None
    op = result.operation_type
    if op in (PendingOpType.CREATE_TASK, PendingOpType.UPDATE_TASK):
        text, markup = await render_task_card(session, settings, entity_id)
    elif op in (PendingOpType.CREATE_COMMENT, PendingOpType.UPDATE_COMMENT):
        text, markup = await render_comments_view(session, settings, entity_id)
    elif op in (PendingOpType.CREATE_KNOWLEDGE, PendingOpType.UPDATE_KNOWLEDGE):
        text, markup = await render_knowledge_card(session, entity_id)
    elif op in (PendingOpType.SAVE_DOCUMENT, PendingOpType.UPDATE_DOCUMENT):
        text, markup = await render_document_card(session, entity_id)
    else:
        return None
    if text is None:
        return None
    return f"{text}\n\nДействия:", markup


async def confirm_action(
    callback: CallbackQuery,
    callback_data: ConfirmCB,
    session: AsyncSession,
    settings: Settings,
    ai: AIProvider,
) -> None:
    user_id = callback.from_user.id
    service = ConfirmationService(session, settings)

    if callback_data.action == "apply":
        result = await service.confirm(user_id, callback_data.op_id)
        await callback.answer()
        if result.ok:
            if result.already:
                await _update_message(
                    callback, f"\n\n{CONFIRM_ALREADY.format(summary=result.summary)}"
                )
                return
            # Превью превращается в свежую карточку (задача/комментарии/заметка/документ).
            view = await _result_view(result, session, settings)
            if view is not None:
                from jarvis.bot.handlers.render import send_or_edit

                text, markup = view
                await send_or_edit(callback.message, text, markup=markup, edit=True)
                return
            nav = _NAV_AFTER.get(result.operation_type)
            await _update_message(
                callback,
                f"\n\n{CONFIRM_APPLIED.format(summary=result.summary)}",
                nav=nav,
            )
        else:
            await _update_message(callback, f"\n\n{CONFIRM_FAILED.format(summary=result.summary)}")
        return

    if callback_data.action == "cancel":
        result = await service.cancel(user_id, callback_data.op_id)
        await callback.answer("Отменено" if result.ok else result.summary)
        await _update_message(callback, f"\n\n❌ {result.summary}")
        return

    if callback_data.action == "edit":
        engine = ConversationEngine(session, settings, ai)
        await engine.start_edit_pending(user_id, callback_data.op_id)
        await callback.answer()
        await _update_message(
            callback,
            "\n\n✏️ Напишите исправленную формулировку одним сообщением — "
            "я предложу обновлённую операцию.",
        )
        return

    await callback.answer(CALLBACK_STALE, show_alert=True)


def build_router() -> Router:
    router = Router(name="confirmations")
    router.callback_query.register(confirm_action, ConfirmCB.filter())
    return router
