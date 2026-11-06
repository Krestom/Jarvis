"""Личный дневник: экран дня, создание, правка и удаление записей."""

from __future__ import annotations

import contextlib
from datetime import UTC, date, datetime, timedelta

from aiogram import Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.bot.callbacks import DiaryCB
from jarvis.bot.formatting import diary_day_text, esc
from jarvis.bot.handlers.open_entity import parse_hex_uuid
from jarvis.bot.handlers.render import send_or_edit
from jarvis.bot.keyboards import diary_day_keyboard, diary_delete_keyboard
from jarvis.config.settings import Settings
from jarvis.conversation.flows import Flow
from jarvis.conversation.state import ConversationStore
from jarvis.repositories.conversation import ConversationRepository
from jarvis.repositories.diary import DiaryRepository, DiarySnoozeRepository

DAY_LIMIT = 4000


async def send_diary_day(
    message: Message,
    session: AsyncSession,
    settings: Settings,
    *,
    day: date | None = None,
    edit: bool = False,
) -> None:
    today = datetime.now(UTC).astimezone(settings.tz).date()
    day = day or today
    if day > today:
        day = today
    entries = await DiaryRepository(session).list_day(message.chat.id, day)
    text = diary_day_text(day, entries, settings.tz, is_today=day == today)
    markup = diary_day_keyboard(entries, day.isoformat(), day == today, today.isoformat())
    await send_or_edit(message, text, markup=markup, edit=edit)


async def diary_action(
    callback: CallbackQuery,
    callback_data: DiaryCB,
    session: AsyncSession,
    settings: Settings,
) -> None:
    user_id = callback.from_user.id
    action = callback_data.action
    repo = DiaryRepository(session)

    if action == "day":
        try:
            day = date.fromisoformat(callback_data.day)
        except ValueError:
            await callback.answer("Дата не распознана", show_alert=True)
            return
        await callback.answer()
        await send_diary_day(callback.message, session, settings, day=day, edit=True)
        return

    if action == "write":
        store = ConversationStore(ConversationRepository(session), settings)
        await store.set(user_id, Flow.DIARY_WAIT)
        if not callback_data.day:
            # Кнопка из напоминания/сводки: убираем её, чтобы не нажали дважды.
            with contextlib.suppress(Exception):
                await callback.message.edit_reply_markup(reply_markup=None)
        await callback.answer()
        await callback.message.answer(
            "📔 Напишите или надиктуйте запись за сегодня."
        )
        return

    if action == "snooze":
        today = datetime.now(UTC).astimezone(settings.tz).date()
        if await repo.has_entry(user_id, today):
            await callback.answer("Запись уже есть")
            with contextlib.suppress(Exception):
                await callback.message.edit_reply_markup(reply_markup=None)
            return
        next_at = datetime.now(UTC) + timedelta(hours=1)
        if next_at.astimezone(settings.tz).date() != today:
            await callback.answer("Уже поздно, сегодня напоминать не буду")
            with contextlib.suppress(Exception):
                await callback.message.edit_reply_markup(reply_markup=None)
            return
        await DiarySnoozeRepository(session).schedule(user_id, today, next_at)
        await callback.answer("Напомню через час")
        with contextlib.suppress(Exception):
            await callback.message.edit_reply_markup(reply_markup=None)
        return

    entry_id = parse_hex_uuid(callback_data.entry_id)
    entry = await repo.get(user_id, entry_id) if entry_id else None
    if entry is None:
        await callback.answer("Запись не найдена", show_alert=True)
        return

    if action == "edit":
        store = ConversationStore(ConversationRepository(session), settings)
        await store.set(
            user_id, Flow.DIARY_EDIT_WAIT, data={"entry_id": str(entry.id)}
        )
        await callback.answer()
        current = entry.content[:300]
        await callback.message.answer(
            f"Пришлите новый текст записи от "
            f"{entry.created_at.astimezone(settings.tz):%H:%M}.\n\n"
            f"<i>Сейчас: {esc(current)}</i>"
        )
        return

    if action == "delete":
        await callback.answer()
        stamp = entry.created_at.astimezone(settings.tz).strftime("%H:%M")
        await send_or_edit(
            callback.message,
            f"Удалить запись от {stamp}?\n\n<i>{esc(entry.content[:300])}</i>",
            markup=diary_delete_keyboard(str(entry.id), entry.entry_date.isoformat()),
            edit=True,
        )
        return

    if action == "delete_yes":
        day = entry.entry_date
        await repo.delete(entry)
        await callback.answer("Удалено")
        await send_diary_day(callback.message, session, settings, day=day, edit=True)
        return

    if action == "cancel":
        await callback.answer()
        await send_diary_day(
            callback.message, session, settings, day=entry.entry_date, edit=True
        )
        return

    await callback.answer("Неизвестное действие", show_alert=True)


def build_router() -> Router:
    router = Router(name="diary")
    router.callback_query.register(diary_action, DiaryCB.filter())
    return router


__all__ = ["build_router", "send_diary_day"]
