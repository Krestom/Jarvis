"""Отправка результатов движка в чат."""

from __future__ import annotations

import logging
import re

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile, Message
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.bot.formatting import esc, split_message
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
from jarvis.config.settings import Settings
from jarvis.conversation.engine import EngineOutcome

EMPTY_SEARCH_TEXT = "Ничего не нашёл. Уточните запрос или сохраните это как заметку."

log = logging.getLogger(__name__)

_PARSE_ERROR_MARKER = "can't parse entities"
_NOT_MODIFIED_MARKER = "message is not modified"
_TAG_RE = re.compile(r"</?[a-zA-Z][^>]{0,80}>")


async def answer_safe(message: Message, text: str, **kwargs) -> None:
    """Отправить текст; при ошибке HTML-парсинга — повторить без разметки.

    Динамический текст (ответы агента, фрагменты данных) может содержать «<»,
    который Telegram отвергает как тег, — сообщение не должно теряться совсем.
    """
    try:
        await message.answer(text, **kwargs)
    except TelegramBadRequest as error:
        if _PARSE_ERROR_MARKER not in str(error):
            raise
        log.warning("telegram rejected html in outgoing message, sending plain text")
        await message.answer(_TAG_RE.sub("", text), parse_mode=None, **kwargs)


async def send_or_edit(
    message: Message, text: str, *, markup=None, edit: bool = False
) -> None:
    """Страница списка: при листании правим текущее сообщение, а не шлём новое.

    Клавиатура крепится к последнему чанку; если правка недоступна (или текст
    не изменился) — тихий фолбэк на новое сообщение.
    """
    chunks = split_message(text)
    last_index = len(chunks) - 1
    if edit:
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
    await message.answer(preview, reply_markup=confirm_keyboard(operation_id))
    return operation_id


async def send_outcome(
    message: Message, outcome: EngineOutcome, session: AsyncSession, settings: Settings
) -> None:
    if outcome.search_query is not None:
        await send_search_results(message, outcome)
        return

    # Карточка сущности (агент открыл задачу/заметку/документ) — живые кнопки.
    if outcome.card is not None:
        from jarvis.bot.handlers.open_entity import (
            parse_hex_uuid,
            render_document_card,
            render_knowledge_card,
            render_task_card,
        )

        kind, entity_id = outcome.card
        entity_uuid = parse_hex_uuid(entity_id)
        text, card_markup = None, None
        if entity_uuid is not None:
            if kind == "task":
                text, card_markup = await render_task_card(session, settings, entity_uuid)
            elif kind == "knowledge":
                text, card_markup = await render_knowledge_card(session, entity_uuid)
            elif kind == "document":
                text, card_markup = await render_document_card(session, entity_uuid)
        if text is None:
            await message.answer("Запись не найдена.", reply_markup=main_menu())
            return
        for chunk in split_message(text):
            await answer_safe(message, chunk)
        await message.answer("Действия:", reply_markup=card_markup)
        return

    # Экраны-списки (задачи/документы/знания): одна страница — одно сообщение,
    # чтобы пагинация правила его на месте, а не слала новое при каждом шаге.
    list_markup = None
    list_prompt = None
    if outcome.task_page is not None:
        from jarvis.bot.keyboards import task_list_keyboard

        view, page, value, has_next = outcome.task_page
        list_markup = task_list_keyboard(outcome.task_choices, view, page, has_next, value)
        list_prompt = outcome.task_choices_prompt or "Выберите задачу:"
    elif outcome.documents:
        from jarvis.bot.keyboards import PAGE_SIZE, documents_list_keyboard

        list_markup = documents_list_keyboard(
            outcome.documents, 0, has_next=len(outcome.documents) >= PAGE_SIZE
        )
        list_prompt = "Выберите документ:"
    elif outcome.knowledge:
        from jarvis.bot.keyboards import PAGE_SIZE, knowledge_list_keyboard

        list_markup = knowledge_list_keyboard(
            outcome.knowledge, 0, has_next=len(outcome.knowledge) >= PAGE_SIZE
        )
        list_prompt = "Выберите запись:"
    if list_markup is not None:
        text = f"{outcome.text}\n\n{list_prompt}" if outcome.text else list_prompt
        await send_or_edit(message, text, markup=list_markup)
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
    for op_id, preview in outcome.pending_ops:
        await answer_safe(message, preview, reply_markup=confirm_keyboard(op_id))

    if outcome.task_choices:
        await message.answer(
            outcome.task_choices_prompt or "Выберите задачу:",
            reply_markup=comment_target_keyboard(
                outcome.task_choices, action=outcome.task_choices_action
            ),
        )

    if outcome.entity_choices:
        await message.answer(
            outcome.entity_choice_prompt or "Уточните, что имелось в виду:",
            reply_markup=agent_entity_keyboard(outcome.entity_choices),
        )


async def send_search_results(
    message: Message, outcome: EngineOutcome, *, edit: bool = False
) -> None:
    hits = outcome.search_hits
    query = outcome.search_query or ""
    if not hits:
        if outcome.search_page > 0:
            await send_or_edit(
                message, "Больше результатов нет.", markup=main_menu(), edit=edit
            )
            return
        await send_or_edit(
            message,
            f"{EMPTY_SEARCH_TEXT}\n\n<i>Запрос: {esc(query)}</i>",
            markup=main_menu(),
            edit=edit,
        )
        return

    header = f"🔎 <b>Результаты по запросу</b> «{esc(query)}» — стр. {outcome.search_page + 1}\n"
    from jarvis.bot.keyboards import PAGE_SIZE

    lines = [header]
    for index, hit in enumerate(hits, start=1):
        kind_emoji = {"knowledge": "📚", "task": "✅", "document": "📄"}.get(hit.kind, "•")
        snippet = f" — <i>{esc(hit.snippet)}</i>" if hit.snippet else ""
        lines.append(f"{index}. {kind_emoji} {esc(hit.title)}{snippet}")
    text = "\n".join(lines) + "\n\nВыберите запись, чтобы открыть:"
    markup = search_results_keyboard(
        hits, query, outcome.search_page, has_next=len(hits) >= PAGE_SIZE
    )
    await send_or_edit(message, text, markup=markup, edit=edit)
