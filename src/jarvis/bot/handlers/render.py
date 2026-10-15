"""Отправка результатов движка в чат."""

from __future__ import annotations

from aiogram.types import Message

from jarvis.bot.formatting import esc, split_message
from jarvis.bot.keyboards import (
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
    if outcome.pending_op_id:
        markup = confirm_keyboard(outcome.pending_op_id)
    elif outcome.awaiting_document or outcome.awaiting_secret:
        markup = cancel_flow_keyboard()
    elif outcome.force_save:
        markup = force_save_keyboard()
    elif outcome.ui_actions:
        markup = quick_actions_keyboard(outcome.ui_actions)
    if markup is None and outcome.menu:
        markup = main_menu()

    chunks = split_message(outcome.text or "…")
    for index, chunk in enumerate(chunks):
        await message.answer(chunk, reply_markup=markup if index == len(chunks) - 1 else None)

    if outcome.task_choices:
        await message.answer(
            "Выберите задачу:",
            reply_markup=comment_target_keyboard(
                outcome.task_choices, action=outcome.task_choices_action
            ),
        )


async def send_search_results(message: Message, outcome: EngineOutcome) -> None:
    hits = outcome.search_hits
    query = outcome.search_query or ""
    if not hits:
        if outcome.search_page > 0:
            await message.answer("Больше результатов нет.", reply_markup=main_menu())
            return
        await message.answer(
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
        await message.answer(chunk)
    await message.answer("Выберите запись, чтобы открыть:", reply_markup=markup)
