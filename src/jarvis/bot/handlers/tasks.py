"""Задачи: списки, карточка, изменение срока/группы/статуса."""

from __future__ import annotations

from datetime import UTC, datetime

from aiogram import Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.ai import AIProvider
from jarvis.bot.callbacks import TaskCB, TaskListCB, uuid_hex
from jarvis.bot.formatting import esc, split_message, task_line
from jarvis.bot.handlers.open_entity import parse_hex_uuid, render_task_card
from jarvis.bot.handlers.render import propose_and_send, send_outcome
from jarvis.bot.keyboards import (
    PAGE_SIZE,
    bucket_keyboard,
    comments_keyboard,
    main_menu,
    task_categories_keyboard,
    task_list_keyboard,
)
from jarvis.config.settings import Settings
from jarvis.conversation.engine import ConversationEngine
from jarvis.conversation.flows import Flow
from jarvis.conversation.preview import fmt_dt_local
from jarvis.conversation.state import ConversationStore
from jarvis.domain.enums import PendingOpType, TaskBucket
from jarvis.repositories.categories import CategoryRepository
from jarvis.repositories.comments import TaskCommentRepository
from jarvis.repositories.conversation import ConversationRepository
from jarvis.repositories.tasks import TaskRepository
from jarvis.services.catalog import CatalogService
from jarvis.services.tasks import TaskService

VIEW_TITLES = {
    "today": "🔸 Задачи на сегодня",
    "week": "📆 На этой неделе",
    "long_term": "🗓 Долгосрочные",
    "someday": "💭 Когда-нибудь",
    "no_group": "📥 Без срока",
    "inbox": "🌀 Inbox",
    "all": "📋 Все задачи",
    "done": "✅ Выполненные",
}


async def build_task_list(
    session: AsyncSession, settings: Settings, view: str, page: int, value: str = ""
) -> tuple[str, object]:
    service = TaskService(session)
    now = datetime.now(UTC)
    if view == "category":
        category_id = parse_hex_uuid(value)
        category = (
            await CategoryRepository(session).get(category_id) if category_id else None
        )
        if category is None:
            categories = await CatalogService(session).list_categories()
            return "🗂 Категория не найдена — выберите заново.", task_categories_keyboard(
                categories
            )
        tasks = await service.list_by_category(
            category.id, limit=PAGE_SIZE, offset=page * PAGE_SIZE
        )
        title = f"🗂 {esc(category.name)}"
    else:
        tasks = await service.list_view(
            view, now=now, tz=settings.tz, limit=PAGE_SIZE, offset=page * PAGE_SIZE
        )
        title = VIEW_TITLES.get(view, "Задачи")
    if not tasks:
        text = f"{title}\n\nПусто."
    else:
        lines = [title, ""]
        for index, task in enumerate(tasks, start=1):
            lines.append(task_line(task, settings.tz, index=index, now=now))
        text = "\n".join(lines)
    markup = task_list_keyboard(
        tasks, view, page, has_next=len(tasks) >= PAGE_SIZE, value=value
    )
    return text, markup


async def send_task_list(
    message: Message, session: AsyncSession, settings: Settings, *, view: str, page: int
) -> None:
    text, markup = await build_task_list(session, settings, view, page)
    for chunk in split_message(text):
        await message.answer(chunk)
    await message.answer("Выберите задачу:", reply_markup=markup)


async def task_list(
    callback: CallbackQuery, callback_data: TaskListCB, session: AsyncSession, settings: Settings
) -> None:
    if callback_data.view == "category" and not callback_data.value:
        categories = await CatalogService(session).list_categories()
        await callback.answer()
        try:
            await callback.message.edit_text("🗂 <b>Задачи по категориям</b>")
        except Exception:  # noqa: BLE001 - сообщение могло быть недоступно для правки
            await callback.message.answer("🗂 <b>Задачи по категориям</b>")
        await callback.message.answer(
            "Выберите категорию:", reply_markup=task_categories_keyboard(categories)
        )
        return
    text, markup = await build_task_list(
        session, settings, callback_data.view, callback_data.page, callback_data.value
    )
    await callback.answer()
    chunks = split_message(text)
    try:
        await callback.message.edit_text(chunks[0])
        for chunk in chunks[1:]:
            await callback.message.answer(chunk)
        await callback.message.answer("Выберите задачу:", reply_markup=markup)
    except Exception:  # noqa: BLE001 - сообщение могло быть недоступно для правки
        await callback.message.answer(chunks[0])
        await callback.message.answer("Выберите задачу:", reply_markup=markup)


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
        text, markup = await render_task_card(session, settings, task_id)
        await callback.answer()
        if text is None:
            await callback.message.answer("Задача не найдена.", reply_markup=main_menu())
            return
        for chunk in split_message(text):
            await callback.message.answer(chunk)
        await callback.message.answer("Действия:", reply_markup=markup)
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

    if action == "bucket":
        await callback.answer()
        await callback.message.answer(
            f"Группа для «{esc(task.title)}»:", reply_markup=bucket_keyboard(uuid_hex(task.id))
        )
        return

    if action == "bucket_set":
        value = callback_data.value
        bucket = None
        if value != "none":
            try:
                bucket = TaskBucket(value).value
            except ValueError:
                await callback.answer("Неизвестная группа", show_alert=True)
                return
        await callback.answer()
        await propose_and_send(
            callback.message,
            session,
            settings,
            user_id,
            PendingOpType.UPDATE_TASK,
            {"task_id": str(task.id), "changes": {"bucket": bucket}},
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
        comments = await TaskCommentRepository(session).list_for_task(task.id)
        await callback.answer()
        if not comments:
            await callback.message.answer(
                f"💬 Комментариев к «{esc(task.title)}» пока нет.",
                reply_markup=comments_keyboard([], uuid_hex(task.id)),
            )
            return
        lines = [f"💬 <b>Комментарии</b> — {esc(task.title)}", ""]
        for index, comment in enumerate(comments, start=1):
            stamp = fmt_dt_local(comment.created_at, settings.tz)
            lines.append(f"<b>{index}.</b> {esc(comment.content)}\n<i>{stamp}</i>")
        for chunk in split_message("\n".join(lines)):
            await callback.message.answer(chunk)
        await callback.message.answer(
            "Действия:", reply_markup=comments_keyboard(comments, uuid_hex(task.id))
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
        await send_outcome(callback.message, outcome)
        return

    await callback.answer("Неизвестное действие", show_alert=True)


def build_router() -> Router:
    router = Router(name="tasks")
    router.callback_query.register(task_list, TaskListCB.filter())
    router.callback_query.register(task_action, TaskCB.filter())
    return router
