"""Задачи: списки, карточка, изменение срока/статуса."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from aiogram import Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.ai import AIProvider
from jarvis.bot.callbacks import TaskCB, TaskListCB
from jarvis.bot.formatting import esc
from jarvis.bot.handlers.open_entity import parse_hex_uuid
from jarvis.bot.handlers.render import (
    answer_safe,
    propose_and_send,
    send_or_edit,
    send_outcome,
)
from jarvis.bot.keyboards import PAGE_SIZE, main_menu, tasks_menu_keyboard
from jarvis.bot.rich import RichScreen, plain_screen, send_rich
from jarvis.bot.screens.core import (
    comments_screen,
    task_card_screen,
    task_list_screen,
    tasks_menu_screen,
)
from jarvis.bot.ui_state import last_task_list, remember_task_list
from jarvis.config.settings import Settings
from jarvis.conversation.dates import range_title
from jarvis.conversation.engine import ConversationEngine
from jarvis.conversation.flows import Flow
from jarvis.conversation.preview import task_view_title
from jarvis.conversation.state import ConversationStore
from jarvis.domain.enums import PendingOpType
from jarvis.domain.windows import DONE_STATUSES
from jarvis.repositories.audit import AuditRepository
from jarvis.repositories.comments import TaskCommentRepository
from jarvis.repositories.conversation import ConversationRepository
from jarvis.repositories.tasks import TaskRepository
from jarvis.services.tasks import TASK_VIEWS, TaskService


async def build_task_list(
    session: AsyncSession, settings: Settings, view: str, page: int, value: str = ""
) -> RichScreen:
    service = TaskService(session)
    now = datetime.now(UTC)
    fetch_limit = PAGE_SIZE + 1
    day = None
    if view not in ("date", "range", "next_week") and view not in TASK_VIEWS:
        return plain_screen(
            "📋 <b>Задачи</b>\nКнопка устарела — выберите список заново.",
            tasks_menu_keyboard(),
        )
    if view == "next_week":
        today = now.astimezone(settings.tz).date()
        start = today + timedelta(days=7 - today.weekday())
        end = start + timedelta(days=6)
        tasks = await service.list_period(
            start, end, tz=settings.tz, limit=fetch_limit, offset=page * PAGE_SIZE
        )
        title = range_title(start, end, today=today)
    elif view == "range":
        try:
            start_text, end_text = value.split("|", 1)
            start = date.fromisoformat(start_text)
            end = date.fromisoformat(end_text)
        except (ValueError, AttributeError):
            return plain_screen(
                "📋 Период не распознан — откройте список заново.", main_menu()
            )
        if end < start:
            return plain_screen(
                "📋 Период не распознан — откройте список заново.", main_menu()
            )
        tasks = await service.list_period(
            start, end, tz=settings.tz, limit=fetch_limit, offset=page * PAGE_SIZE
        )
        title = range_title(
            start, end, today=now.astimezone(settings.tz).date()
        )
    elif view == "date":
        try:
            day = date.fromisoformat(value)
        except ValueError:
            return plain_screen("📋 Дата не распознана — откройте список заново.", main_menu())
        tasks = await service.list_on_date(
            day, tz=settings.tz, limit=fetch_limit, offset=page * PAGE_SIZE
        )
        title = f"📋 Задачи на {day.strftime('%d.%m.%Y')}"
    else:
        tasks = await service.list_view(
            view, now=now, tz=settings.tz, limit=fetch_limit, offset=page * PAGE_SIZE
        )
        title = task_view_title(view, now, settings.tz)
        if view == "today":
            day = now.astimezone(settings.tz).date()
    has_next = len(tasks) > PAGE_SIZE
    return task_list_screen(
        title=title,
        tasks=tasks[:PAGE_SIZE],
        tz=settings.tz,
        now=now,
        view=view,
        page=page,
        value=value,
        has_next=has_next,
        day=day,
    )


async def send_task_list(
    message: Message, session: AsyncSession, settings: Settings, *, view: str, page: int
) -> None:
    screen = await build_task_list(session, settings, view, page)
    remember_task_list(message.chat.id, view, page)
    await send_rich(message, screen)


async def task_list(
    callback: CallbackQuery, callback_data: TaskListCB, session: AsyncSession, settings: Settings
) -> None:
    screen = await build_task_list(
        session, settings, callback_data.view, callback_data.page, callback_data.value
    )
    remember_task_list(
        callback.message.chat.id,
        callback_data.view,
        callback_data.page,
        callback_data.value,
    )
    await callback.answer()
    await send_rich(callback.message, screen, edit=True)


async def task_action(
    callback: CallbackQuery,
    callback_data: TaskCB,
    session: AsyncSession,
    settings: Settings,
    ai: AIProvider,
) -> None:
    user_id = callback.from_user.id
    action = callback_data.action
    task_id = parse_hex_uuid(callback_data.task_id)

    if action == "back":
        await callback.answer()
        target = last_task_list(callback.message.chat.id)
        if target is None:
            await send_rich(callback.message, tasks_menu_screen(), edit=True)
            return
        screen = await build_task_list(
            session, settings, target.view, target.page, target.value
        )
        await send_rich(callback.message, screen, edit=True)
        return

    if action == "cedit":
        # Изменение комментария: id задачи восстанавливаем из самого комментария
        # (в callback_data оба id не помещаются).
        comment_id = parse_hex_uuid(callback_data.value)
        comment = await TaskCommentRepository(session).get(comment_id) if comment_id else None
        if comment is None:
            await callback.answer("Комментарий не найден", show_alert=True)
            return
        store = ConversationStore(ConversationRepository(session), settings)
        await store.set(
            user_id,
            Flow.EDIT_COMMENT,
            data={"comment_id": str(comment.id), "task_id": str(comment.task_id)},
        )
        await callback.answer()
        preview = comment.content if len(comment.content) <= 300 else comment.content[:299] + "…"
        await callback.message.answer(
            f"Пришлите новую версию комментария.\n\n<i>{esc(preview)}</i>"
        )
        return

    if action == "view":
        if task_id is None:
            await callback.answer("Некорректная задача", show_alert=True)
            return
        task = await TaskRepository(session).get(task_id)
        await callback.answer()
        if task is None:
            await answer_safe(callback.message, "Задача не найдена.", reply_markup=main_menu())
            return
        comments = await TaskCommentRepository(session).list_for_task(task.id)
        # Карточка правит сообщение, из которого её открыли (список/пикер/поиск).
        await send_rich(
            callback.message,
            task_card_screen(
                task,
                comments,
                settings.tz,
                from_list=callback_data.value == "l",
            ),
            edit=True,
        )
        return

    if task_id is None:
        await callback.answer("Некорректная задача", show_alert=True)
        return

    task = await TaskRepository(session).get(task_id)
    if task is None:
        await callback.answer("Задача не найдена", show_alert=True)
        return

    if action == "due":
        store = ConversationStore(ConversationRepository(session), settings)
        await store.set(user_id, Flow.EDIT_TASK_DUE, data={"task_id": str(task.id)})
        await callback.answer()
        await callback.message.answer(
            f"На когда перенести «{esc(task.title)}»?\n"
            "Напишите: <i>завтра</i>, <i>в пятницу</i>, <i>22.10.2026</i>, <i>22.10 в 18:00</i>."
        )
        return

    if action == "complete":
        if task.status in DONE_STATUSES:
            await callback.answer("Задача уже закрыта")
            await send_or_edit(
                callback.message,
                f"✅ Задача «{esc(task.title)}» уже закрыта.",
                edit=True,
            )
            return
        await TaskService(session).apply_update(task.id, {"status": "done"})
        await AuditRepository(session).log(
            actor=f"telegram:{user_id}",
            action="reminder:complete",
            entity_type="task",
            entity_id=task.id,
        )
        await callback.answer("Готово")
        await send_or_edit(
            callback.message, f"✅ Задача «{esc(task.title)}» завершена.", edit=True
        )
        return

    if action == "done":
        await callback.answer()
        await propose_and_send(
            callback.message,
            session,
            settings,
            user_id,
            PendingOpType.UPDATE_TASK,
            {"task_id": str(task.id), "changes": {"status": "done"}},
        )
        return

    if action == "reopen":
        await callback.answer()
        await propose_and_send(
            callback.message,
            session,
            settings,
            user_id,
            PendingOpType.UPDATE_TASK,
            {"task_id": str(task.id), "changes": {"status": "todo"}},
        )
        return

    if action == "delete":
        await callback.answer()
        await propose_and_send(
            callback.message,
            session,
            settings,
            user_id,
            PendingOpType.DELETE_TASK,
            {"entity_id": str(task.id)},
        )
        return

    if action == "comments":
        await callback.answer()
        comments = await TaskCommentRepository(session).list_for_task(task.id)
        await send_rich(
            callback.message,
            comments_screen(task, comments, settings.tz),
            edit=True,
        )
        return

    if action == "comment_add":
        store = ConversationStore(ConversationRepository(session), settings)
        await store.set(user_id, Flow.AWAIT_COMMENT, data={"task_id": str(task.id)})
        await callback.answer()
        await callback.message.answer(
            f"Напишите комментарий к «{esc(task.title)}» (текстом или голосом)."
        )
        return

    if action == "comment_choose":
        engine = ConversationEngine(session, settings, ai)
        outcome = await engine.choose_task_for_comment(user_id, str(task.id))
        await callback.answer()
        await send_outcome(callback.message, outcome, session, settings)
        return

    await callback.answer("Неизвестное действие", show_alert=True)


def build_router() -> Router:
    router = Router(name="tasks")
    router.callback_query.register(task_list, TaskListCB.filter())
    router.callback_query.register(task_action, TaskCB.filter())
    return router
