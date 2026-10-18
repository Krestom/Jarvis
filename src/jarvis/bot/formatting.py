"""Форматирование сообщений: безопасный HTML, карточки, разбиение длинных текстов."""

from __future__ import annotations

import html
from zoneinfo import ZoneInfo

from jarvis.conversation.preview import (
    BUCKET_LABELS,
    DOCUMENT_TYPE_LABELS,
    KNOWLEDGE_TYPE_LABELS,
    PRIORITY_LABELS,
    SENSITIVITY_LABELS,
    STATUS_LABELS,
    fmt_dt_local,
)
from jarvis.domain.enums import Sensitivity, TaskBucket, TaskPriority

MESSAGE_LIMIT = 4000


def esc(value: object) -> str:
    return html.escape(str(value))


def split_message(text: str, limit: int = MESSAGE_LIMIT) -> list[str]:
    """Разбить длинный текст на части, по возможности по границам абзацев."""
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    remaining = text
    while len(remaining) > limit:
        split_at = remaining.rfind("\n\n", 0, limit)
        if split_at < limit // 2:
            split_at = remaining.rfind("\n", 0, limit)
        if split_at < limit // 2:
            split_at = limit
        parts.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()
    if remaining:
        parts.append(remaining)
    return parts


def task_card_text(task, tz: ZoneInfo, category_name: str | None = None) -> str:
    lines = [f"<b>Задача:</b> {esc(task.title)}"]
    if task.description:
        lines.append(f"\n{esc(task.description)}")
    lines.append(f"\n<b>Статус:</b> {esc(STATUS_LABELS.get(task.status, task.status))}")
    if task.bucket is not None:
        label = BUCKET_LABELS.get(TaskBucket(task.bucket), str(task.bucket))
        lines.append(f"<b>Группа:</b> {esc(label)}")
    if task.due_at is not None:
        lines.append(f"<b>Дедлайн:</b> {esc(fmt_dt_local(task.due_at, tz))}")
    if task.priority != TaskPriority.NORMAL:
        label = PRIORITY_LABELS.get(TaskPriority(task.priority), str(task.priority))
        lines.append(f"<b>Приоритет:</b> {esc(label)}")
    if category_name:
        lines.append(f"<b>Категория:</b> {esc(category_name)}")
    if task.original_text and task.original_text.strip() != task.title.strip():
        original = task.original_text.strip()
        if len(original) > 200:
            original = original[:199] + "…"
        lines.append(f"\n<i>Исходная формулировка: {esc(original)}</i>")
    return "\n".join(lines)


def knowledge_card_text(item, *, full: bool = False) -> str:
    lines = [
        f"<b>{esc(item.title)}</b>",
        f"<i>{esc(KNOWLEDGE_TYPE_LABELS.get(item.type, str(item.type)))}</i>",
    ]
    if item.summary:
        lines.append(f"\n{esc(item.summary)}")
    if full:
        lines.append(f"\n{esc(item.content)}")
    else:
        preview = item.content if len(item.content) <= 400 else item.content[:399] + "…"
        lines.append(f"\n{esc(preview)}")
    if item.sensitivity != Sensitivity.NORMAL:
        label = SENSITIVITY_LABELS.get(item.sensitivity, str(item.sensitivity))
        lines.append(f"\n<b>Чувствительность:</b> {esc(label)}")
    return "\n".join(lines)


def document_card_text(document, category_name: str | None = None) -> str:
    type_label = DOCUMENT_TYPE_LABELS.get(document.document_type, str(document.document_type))
    sensitivity_label = SENSITIVITY_LABELS.get(
        document.sensitivity, str(document.sensitivity)
    )
    lines = [
        f"<b>Документ:</b> {esc(document.title)}",
        f"<b>Тип:</b> {esc(type_label)}",
    ]
    if category_name:
        lines.append(f"<b>Категория:</b> {esc(category_name)}")
    lines.append(f"<b>Чувствительность:</b> {esc(sensitivity_label)}")
    size_kb = document.size_bytes / 1024
    lines.append(f"<b>Размер:</b> {size_kb:.1f} КБ")
    if document.mime_type:
        lines.append(f"<b>Тип файла:</b> {esc(document.mime_type)}")
    lines.append("\n<i>Файл хранится в зашифрованном виде.</i>")
    return "\n".join(lines)


def secret_card_text(secret, project_name: str | None = None) -> str:
    lines = [
        f"<b>Секрет:</b> {esc(secret.name)}",
        f"<b>Тип:</b> {esc(secret.secret_type)}",
    ]
    if secret.purpose:
        lines.append(f"<b>Назначение:</b> {esc(secret.purpose)}")
    if project_name:
        lines.append(f"<b>Проект:</b> {esc(project_name)}")
    lines.append("\n<i>Значение хранится в зашифрованном виде и не показывается в поиске.</i>")
    return "\n".join(lines)
