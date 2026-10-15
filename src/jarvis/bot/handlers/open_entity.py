"""Общие функции открытия карточек (используются поиском и командами)."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.bot.formatting import document_card_text, knowledge_card_text, task_card_text
from jarvis.bot.keyboards import (
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
    task = await TaskRepository(session).get(task_id)
    if task is None:
        return None, None
    category_name = None
    if task.category_id:
        category = await CategoryRepository(session).get(task.category_id)
        category_name = category.name if category else None
    text = task_card_text(task, settings.tz, category_name)
    markup = task_card_keyboard(uuid_hex(task.id), task.status.value)
    return text, markup


async def render_knowledge_card(session: AsyncSession, item_id: uuid.UUID, *, full: bool = False):
    item = await KnowledgeRepository(session).get(item_id)
    if item is None:
        return None, None
    text = knowledge_card_text(item, full=full)
    markup = knowledge_card_keyboard(uuid_hex(item.id))
    return text, markup


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
