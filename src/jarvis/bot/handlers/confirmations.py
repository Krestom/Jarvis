"""Подтверждение, отмена и редактирование pending-операций."""

from __future__ import annotations

import uuid

from aiogram import Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.ai import AIProvider
from jarvis.bot.callbacks import ConfirmCB, MenuCB
from jarvis.bot.rich import RichScreen, send_rich, send_rich_text
from jarvis.bot.texts import CALLBACK_STALE, CONFIRM_ALREADY, CONFIRM_APPLIED, CONFIRM_FAILED
from jarvis.config.settings import Settings
from jarvis.conversation.engine import ConversationEngine
from jarvis.domain.enums import PendingOpType
from jarvis.services.confirmation import ConfirmationService, ConfirmResult

# Для удалений и «невизуальных» операций: кнопка возврата к нужному списку.
_NAV_AFTER: dict[PendingOpType, tuple[str, str]] = {
    PendingOpType.DELETE_TASK: ("📋 К спискам", "tasks"),
    PendingOpType.DELETE_KNOWLEDGE: ("📚 К списку", "knowledge"),
    PendingOpType.DELETE_DOCUMENT: ("📄 К списку", "documents"),
    PendingOpType.CREATE_SECRET: ("🔐 Секреты", "secrets"),
    PendingOpType.DELETE_SECRET: ("🔐 Секреты", "secrets"),
}


async def _update_message(
    callback: CallbackQuery, suffix: str, *, nav: tuple[str, str] | None = None
) -> None:
    """Показать итог операции отдельной карточкой, сняв кнопки решения."""
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
    await send_rich_text(callback.message, suffix.strip() or "Готово.", markup, edit=True)


async def _result_view(
    result: ConfirmResult, session: AsyncSession, settings: Settings
) -> RichScreen | None:
    """Свежий rich-вид сущности после применённой операции."""
    from jarvis.bot.screens.content import document_card_screen, knowledge_card_screen
    from jarvis.bot.screens.core import comments_screen, task_card_screen
    from jarvis.repositories.comments import TaskCommentRepository
    from jarvis.repositories.documents import DocumentRepository
    from jarvis.repositories.knowledge import KnowledgeRepository
    from jarvis.repositories.tasks import TaskRepository

    if result.entity_id is None:
        return None
    try:
        entity_id = uuid.UUID(result.entity_id)
    except ValueError:
        return None
    op = result.operation_type
    screen: RichScreen | None = None
    if op in (PendingOpType.CREATE_TASK, PendingOpType.UPDATE_TASK):
        task = await TaskRepository(session).get(entity_id)
        if task is not None:
            comments = await TaskCommentRepository(session).list_for_task(task.id)
            screen = task_card_screen(task, comments, settings.tz)
    elif op in (PendingOpType.CREATE_COMMENT, PendingOpType.UPDATE_COMMENT):
        task = await TaskRepository(session).get(entity_id)
        if task is not None:
            comments = await TaskCommentRepository(session).list_for_task(task.id)
            screen = comments_screen(task, comments, settings.tz)
    elif op in (PendingOpType.CREATE_KNOWLEDGE, PendingOpType.UPDATE_KNOWLEDGE):
        item = await KnowledgeRepository(session).get(entity_id)
        if item is not None:
            screen = knowledge_card_screen(item)
    elif op in (PendingOpType.SAVE_DOCUMENT, PendingOpType.UPDATE_DOCUMENT):
        document = await DocumentRepository(session).get(entity_id)
        if document is not None:
            screen = document_card_screen(document)
    if screen is None:
        return None
    return RichScreen(
        html="<p><b>✅ Готово</b></p>" + screen.html,
        text="✅ Готово\n\n" + screen.text,
        markup=screen.markup,
    )


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
                await send_rich(callback.message, view, edit=True)
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
