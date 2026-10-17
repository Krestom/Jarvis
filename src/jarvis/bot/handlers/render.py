"""Отправка результатов движка в чат."""

from __future__ import annotations

import logging
import re

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message

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
from jarvis.conversation.engine import EngineOutcome

EMPTY_SEARCH_TEXT = "Ничего не нашёл. Уточните запрос или сохраните это как заметку."

log = logging.getLogger(__name__)

_PARSE_ERROR_MARKER = "can't parse entities"
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


async def send_outcome(message: Message, outcome: EngineOutcome) -> None:
    if outcome.search_query is not None:
        await send_search_results(message, outcome)
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


async def send_search_results(message: Message, outcome: EngineOutcome) -> None:
    hits = outcome.search_hits
    query = outcome.search_query or ""
    if not hits:
        if outcome.search_page > 0:
            await message.answer("Больше результатов нет.", reply_markup=main_menu())
            return
        await answer_safe(
            message,
            f"{EMPTY_SEARCH_TEXT}\n\n<i>Запрос: {esc(query)}</i>",
            reply_markup=main_menu(),
        )
        return

    header = f"🔎 <b>Результаты по запросу</b> «{esc(query)}» — стр. {outcome.search_page + 1}\n"
    from jarvis.bot.keyboards import PAGE_SIZE

    lines = [header]
    for index, hit in enumerate(hits, start=1):
        kind_emoji = {"knowledge": "📚", "task": "✅", "document": "📄"}.get(hit.kind, "•")
        snippet = f" — <i>{esc(hit.snippet)}</i>" if hit.snippet else ""
        lines.append(f"{index}. {kind_emoji} {esc(hit.title)}{snippet}")
    text = "\n".join(lines)
    markup = search_results_keyboard(
        hits, query, outcome.search_page, has_next=len(hits) >= PAGE_SIZE
    )
    for chunk in split_message(text):
        await answer_safe(message, chunk)
    await message.answer("Выберите запись, чтобы открыть:", reply_markup=markup)
