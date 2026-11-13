"""Отправка результатов движка в чат."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.bot.formatting import split_message
from jarvis.bot.keyboards import (
    agent_entity_keyboard,
    cancel_flow_keyboard,
    comment_target_keyboard,
    confirm_keyboard,
    force_save_keyboard,
    main_menu,
    quick_actions_keyboard,
    search_results_keyboard,
)
from jarvis.bot.rich import send_rich, text_screen
from jarvis.bot.ui_state import remember_task_list, stale_message
from jarvis.config.settings import Settings
from jarvis.conversation.engine import EDITABLE_PENDING_OPS, EngineOutcome

EMPTY_SEARCH_TEXT = "Ничего не нашёл. Уточните запрос или сохраните это как заметку."

log = logging.getLogger(__name__)

_PARSE_ERROR_MARKER = "can't parse entities"
_NOT_MODIFIED_MARKER = "message is not modified"
_TAG_RE = re.compile(r"</?[a-zA-Z][^>]{0,80}>")


async def answer_safe(message: Message, text: str, **kwargs) -> Message:
    """Отправить текст; при ошибке HTML-парсинга — повторить без разметки.

    Динамический текст (ответы агента, фрагменты данных) может содержать «<»,
    который Telegram отвергает как тег, — сообщение не должно теряться совсем.
    Возвращает отправленное сообщение (нужно сценарию показа секрета).
    """
    markup = kwargs.get("reply_markup")
    if not isinstance(markup, InlineKeyboardMarkup):
        markup = None
    try:
        sent = await send_rich(message, text_screen(text, markup))
        if sent is not None:
            return sent
    except TelegramBadRequest as error:
        if _PARSE_ERROR_MARKER not in str(error):
            raise
        log.warning("telegram rejected html in outgoing message, sending plain text")
        return await message.answer(_TAG_RE.sub("", text), parse_mode=None, **kwargs)
    return await message.answer(text, **kwargs)


async def send_or_edit(
    message: Message, text: str, *, markup=None, edit: bool = False
) -> None:
    """Страница списка: при листании правим текущее сообщение, а не шлём новое.

    Клавиатура крепится к последнему чанку; если правка недоступна (или текст
    не изменился) — тихий фолбэк на новое сообщение.
    """
    chunks = split_message(text)
    if len(chunks) == 1:
        await send_rich(message, text_screen(chunks[0], markup), edit=edit)
        return
    last_index = len(chunks) - 1
    if edit and not stale_message.get():
        try:
            await message.edit_text(
                chunks[0], reply_markup=markup if last_index == 0 else None
            )
        except TelegramBadRequest as error:
            if _NOT_MODIFIED_MARKER in str(error):
                return
            log.warning("telegram rejected list edit, sending new message")
        else:
            for index, extra in enumerate(chunks[1:], start=1):
                await answer_safe(
                    message, extra, reply_markup=markup if index == last_index else None
                )
            return
    for index, chunk in enumerate(chunks):
        await answer_safe(
            message, chunk, reply_markup=markup if index == last_index else None
        )


async def propose_and_send(
    message: Message,
    session,
    settings,
    user_id: int,
    operation_type,
    payload: dict,
) -> str:
    """Создать pending-операцию и отправить превью с кнопками подтверждения."""
    from jarvis.conversation.preview import render_preview
    from jarvis.services.confirmation import ConfirmationService

    operation_id = await ConfirmationService(session, settings).propose(
        user_id, operation_type, payload
    )
    preview = await render_preview(session, operation_type, payload, settings.tz)
    await send_rich(
        message,
        text_screen(
            preview,
            confirm_keyboard(
                operation_id, editable=operation_type in EDITABLE_PENDING_OPS
            ),
        ),
    )
    return operation_id


async def send_outcome(
    message: Message, outcome: EngineOutcome, session: AsyncSession, settings: Settings
) -> None:
    if outcome.widget is not None:
        from jarvis.bot.widgets.send import send_widget

        try:
            await send_widget(message.bot, message.chat.id, outcome.widget)
        except Exception:  # noqa: BLE001 - виджет не должен терять ответ
            log.exception("failed to send widget, falling back to caption")
            await answer_safe(
                message,
                outcome.widget.caption or outcome.text or "…",
                reply_markup=main_menu(),
            )
        return

    if outcome.search_query is not None:
        await send_search_results(message, outcome)
        return

    # Карточка сущности (агент открыл задачу/заметку/документ) — живые кнопки.
    if outcome.card is not None:
        from jarvis.bot.handlers.open_entity import parse_hex_uuid
        from jarvis.bot.screens.content import document_card_screen, knowledge_card_screen
        from jarvis.bot.screens.core import task_card_screen
        from jarvis.repositories.comments import TaskCommentRepository
        from jarvis.repositories.documents import DocumentRepository
        from jarvis.repositories.knowledge import KnowledgeRepository
        from jarvis.repositories.tasks import TaskRepository

        kind, entity_id = outcome.card
        entity_uuid = parse_hex_uuid(entity_id)
        if entity_uuid is not None:
            if kind == "task":
                task = await TaskRepository(session).get(entity_uuid)
                if task is not None:
                    comments = await TaskCommentRepository(session).list_for_task(entity_uuid)
                    await send_rich(message, task_card_screen(task, comments, settings.tz))
                    return
            elif kind == "knowledge":
                item = await KnowledgeRepository(session).get(entity_uuid)
                if item is not None:
                    await send_rich(message, knowledge_card_screen(item))
                    return
            elif kind == "document":
                document = await DocumentRepository(session).get(entity_uuid)
                if document is not None:
                    await send_rich(message, document_card_screen(document))
                    return
        await answer_safe(message, "Запись не найдена.", reply_markup=main_menu())
        return

    # Экраны-списки (задачи/документы/знания): одна страница — одно сообщение,
    # чтобы пагинация правила его на месте, а не слала новое при каждом шаге.
    list_screen = None
    if outcome.task_page is not None:
        from jarvis.bot.screens.core import task_list_screen

        view, page, value, has_next = outcome.task_page
        now = datetime.now(UTC)
        title = (outcome.text or "Задачи").split("\n", 1)[0]
        day = now.astimezone(settings.tz).date() if view == "today" else None
        list_screen = task_list_screen(
            title=title,
            tasks=outcome.task_choices,
            tz=settings.tz,
            now=now,
            view=view,
            page=page,
            value=value,
            has_next=has_next,
            day=day,
        )
        remember_task_list(message.chat.id, view, page, value)
    elif outcome.documents:
        from jarvis.bot.keyboards import PAGE_SIZE
        from jarvis.bot.screens.content import document_list_screen

        has_next = len(outcome.documents) > PAGE_SIZE
        list_screen = document_list_screen(
            outcome.documents[:PAGE_SIZE], 0, has_next=has_next
        )
    elif outcome.knowledge:
        from jarvis.bot.keyboards import PAGE_SIZE
        from jarvis.bot.screens.content import knowledge_list_screen

        has_next = len(outcome.knowledge) > PAGE_SIZE
        list_screen = knowledge_list_screen(
            outcome.knowledge[:PAGE_SIZE], 0, has_next=has_next
        )
    if list_screen is not None:
        await send_rich(message, list_screen)
        return

    markup = None
    if outcome.awaiting_document or outcome.awaiting_secret:
        markup = cancel_flow_keyboard()
    elif outcome.force_save:
        markup = force_save_keyboard()
    elif outcome.ui_actions:
        markup = quick_actions_keyboard(outcome.ui_actions)
    elif outcome.menu:
        markup = main_menu()

    if outcome.text or not outcome.pending_ops:
        chunks = split_message(outcome.text or "…")
        for index, chunk in enumerate(chunks):
            await answer_safe(
                message, chunk, reply_markup=markup if index == len(chunks) - 1 else None
            )

    # Файлы, которые агент подготовил инструментами (документ, экспорт чата).
    for file in outcome.files:
        await message.answer_document(
            BufferedInputFile(file.data, filename=file.filename),
            caption=file.caption or None,
        )

    # Каждая предложенная операция — отдельное превью с кнопками подтверждения.
    for op_id, preview, editable in outcome.pending_ops:
        await answer_safe(
            message, preview, reply_markup=confirm_keyboard(op_id, editable=editable)
        )

    if outcome.task_choices:
        from jarvis.bot.screens.search import task_choice_screen

        await send_rich(
            message,
            task_choice_screen(
                outcome.task_choices,
                action=outcome.task_choices_action,
                prompt=outcome.task_choices_prompt or "Выберите задачу:",
                markup=comment_target_keyboard(
                    outcome.task_choices, action=outcome.task_choices_action
                ),
            ),
        )

    if outcome.entity_choices:
        from jarvis.bot.screens.search import entity_choice_screen

        await send_rich(
            message,
            entity_choice_screen(
                outcome.entity_choices,
                prompt=outcome.entity_choice_prompt or "Уточните, что имелось в виду:",
                markup=agent_entity_keyboard(outcome.entity_choices),
            ),
        )


async def send_search_results(
    message: Message,
    outcome: EngineOutcome,
    *,
    edit: bool = False,
    back: tuple[str, str] | None = None,
) -> None:
    from jarvis.bot.screens.search import search_empty_screen, search_results_screen

    hits = outcome.search_hits
    query = outcome.search_query or ""
    if not hits:
        text = EMPTY_SEARCH_TEXT if outcome.search_page == 0 else "Больше результатов нет."
        await send_rich(
            message,
            search_empty_screen(
                query, page=outcome.search_page, text=text, markup=main_menu()
            ),
            edit=edit,
        )
        return

    from jarvis.bot.keyboards import PAGE_SIZE

    has_next = len(hits) > PAGE_SIZE
    hits = hits[:PAGE_SIZE]
    markup = search_results_keyboard(
        hits, query, outcome.search_page, has_next=has_next
    )
    if back is not None:
        from aiogram.types import InlineKeyboardButton

        markup.inline_keyboard.append(
            [InlineKeyboardButton(text=back[0], callback_data=back[1])]
        )
    await send_rich(
        message,
        search_results_screen(
            hits,
            query,
            outcome.search_page,
            has_next=has_next,
            markup=markup,
            back=back,
        ),
        edit=edit,
    )
