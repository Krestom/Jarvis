"""Общие функции открытия карточек (используются поиском и командами)."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.bot.formatting import document_card_text, esc, knowledge_card_text, task_card_text
from jarvis.bot.keyboards import (
    comments_keyboard,
    document_card_keyboard,
    knowledge_card_keyboard,
    task_card_keyboard,
    uuid_hex,
)
from jarvis.config.settings import Settings
from jarvis.repositories.categories import CategoryRepository
from jarvis.repositories.documents import DocumentRepository
from jarvis.repositories.knowledge import KnowledgeRepository
from jarvis.repositories.tasks import TaskRepository


def parse_hex_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError):
        return None


async def render_task_card(session: AsyncSession, settings: Settings, task_id: uuid.UUID):
    from jarvis.repositories.comments import TaskCommentRepository

    task = await TaskRepository(session).get(task_id)
    if task is None:
        return None, None
    category_name = None
    if task.category_id:
        category = await CategoryRepository(session).get(task.category_id)
        category_name = category.name if category else None
    project_name = None
    if task.project_id:
        from jarvis.repositories.projects import ProjectRepository

        project = await ProjectRepository(session).get(task.project_id)
        project_name = project.name if project else None
    text = task_card_text(task, settings.tz, category_name, project_name)

    comments = await TaskCommentRepository(session).list_for_task(task_id)
    if comments:
        text += "\n\n💬 <b>Комментарии</b>"
        for index, comment in enumerate(comments[-3:], start=1):
            raw = comment.content
            preview = raw if len(raw) <= 200 else raw[:199] + "…"
            text += f"\n{index}. {esc(preview)}"
        if len(comments) > 3:
            text += f"\n<i>…и ещё {len(comments) - 3}</i>"

    markup = task_card_keyboard(uuid_hex(task.id), task.status.value, len(comments))
    return text, markup


async def render_knowledge_card(session: AsyncSession, item_id: uuid.UUID, *, full: bool = False):
    item = await KnowledgeRepository(session).get(item_id)
    if item is None:
        return None, None
    text = knowledge_card_text(item, full=full)
    markup = knowledge_card_keyboard(uuid_hex(item.id), full=full)
    return text, markup


async def render_comments_view(session: AsyncSession, settings: Settings, task_id: uuid.UUID):
    """Комментарии задачи с клавиатурой (карточка ↔ список комментариев)."""
    from jarvis.bot.formatting import esc
    from jarvis.conversation.preview import fmt_dt_local
    from jarvis.repositories.comments import TaskCommentRepository

    task = await TaskRepository(session).get(task_id)
    if task is None:
        return None, None
    comments = await TaskCommentRepository(session).list_for_task(task_id)
    keyboard = comments_keyboard(comments, uuid_hex(task.id))
    if not comments:
        return f"💬 Комментариев к «{esc(task.title)}» пока нет.", keyboard
    lines = [f"💬 <b>Комментарии</b> — {esc(task.title)}", ""]
    for index, comment in enumerate(comments, start=1):
        stamp = fmt_dt_local(comment.created_at, settings.tz)
        lines.append(f"<b>{index}.</b> {esc(comment.content)}\n<i>{stamp}</i>")
    return "\n".join(lines), keyboard


async def render_document_card(session: AsyncSession, document_id: uuid.UUID):
    document = await DocumentRepository(session).get(document_id)
    if document is None:
        return None, None
    category_name = None
    if document.category_id:
        category = await CategoryRepository(session).get(document.category_id)
        category_name = category.name if category else None
    text = document_card_text(document, category_name)
    markup = document_card_keyboard(uuid_hex(document.id), can_download=True)
    return text, markup
