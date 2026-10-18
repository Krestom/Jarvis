"""Человекочитаемые превью предложенных операций (до подтверждения)."""

from __future__ import annotations

import html
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.domain.enums import (
    DocumentType,
    KnowledgeType,
    PendingOpType,
    Sensitivity,
    TaskBucket,
    TaskPriority,
    TaskStatus,
)
from jarvis.repositories.categories import CategoryRepository
from jarvis.repositories.knowledge import KnowledgeRepository
from jarvis.repositories.tasks import TaskRepository

MONTHS_RU = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]

BUCKET_LABELS = {
    TaskBucket.TODAY: "Сегодня",
    TaskBucket.WEEK: "На этой неделе",
    TaskBucket.LONG_TERM: "Долгосрочные",
    TaskBucket.SOMEDAY: "Когда-нибудь",
}

PRIORITY_LABELS = {
    TaskPriority.LOW: "низкий",
    TaskPriority.NORMAL: "обычный",
    TaskPriority.HIGH: "высокий",
    TaskPriority.URGENT: "срочный",
}

KNOWLEDGE_TYPE_LABELS = {
    KnowledgeType.NOTE: "заметка",
    KnowledgeType.REFERENCE: "справочная информация",
    KnowledgeType.INSTRUCTION: "инструкция",
    KnowledgeType.DECISION: "решение",
    KnowledgeType.PROJECT_CONTEXT: "контекст проекта",
}

SENSITIVITY_LABELS = {
    Sensitivity.NORMAL: "обычная",
    Sensitivity.SENSITIVE: "чувствительная",
    Sensitivity.SECRET: "секретная",
}

DOCUMENT_TYPE_LABELS = {
    DocumentType.PASSPORT: "паспорт",
    DocumentType.ID_CARD: "удостоверение",
    DocumentType.DRIVERS_LICENSE: "водительские права",
    DocumentType.CERTIFICATE: "справка/сертификат",
    DocumentType.CONTRACT: "договор",
    DocumentType.FINANCE: "финансовый документ",
    DocumentType.MANUAL: "инструкция",
    DocumentType.OTHER: "другое",
}

STATUS_LABELS = {
    TaskStatus.INBOX: "входящие",
    TaskStatus.TODO: "к выполнению",
    TaskStatus.IN_PROGRESS: "в работе",
    TaskStatus.WAITING: "ожидание",
    TaskStatus.DONE: "выполнена",
    TaskStatus.CANCELLED: "отменена",
}

OPERATION_TITLES = {
    PendingOpType.CREATE_TASK: "Создать задачу?",
    PendingOpType.UPDATE_TASK: "Изменить задачу?",
    PendingOpType.DELETE_TASK: "Удалить задачу?",
    PendingOpType.CREATE_COMMENT: "Добавить комментарий?",
    PendingOpType.UPDATE_COMMENT: "Изменить комментарий?",
    PendingOpType.CREATE_KNOWLEDGE: "Сохранить запись?",
    PendingOpType.UPDATE_KNOWLEDGE: "Изменить запись?",
    PendingOpType.DELETE_KNOWLEDGE: "Удалить запись?",
    PendingOpType.CREATE_CATEGORY: "Создать категорию?",
    PendingOpType.CREATE_PROJECT: "Создать проект?",
    PendingOpType.RENAME_CATEGORY: "Переименовать категорию?",
    PendingOpType.SAVE_DOCUMENT: "Сохранить документ?",
    PendingOpType.UPDATE_DOCUMENT: "Обновить документ?",
    PendingOpType.DELETE_DOCUMENT: "Удалить документ?",
    PendingOpType.CREATE_SECRET: "Сохранить секрет?",
    PendingOpType.DELETE_SECRET: "Удалить секрет?",
}


def fmt_dt_local(dt: datetime, tz: ZoneInfo) -> str:
    local = dt.astimezone(tz)
    date_part = f"{local.day} {MONTHS_RU[local.month - 1]} {local.year}"
    if local.hour or local.minute:
        return f"{date_part}, {local.hour:02d}:{local.minute:02d}"
    return date_part


# Единый вид списков задач: заголовки и строки общие для меню/команд,
# естественного языка и экранов агента.
TASK_VIEW_TITLES = {
    "today": "🔸 Задачи на сегодня",
    "week": "📆 На этой неделе",
    "month": "📅 В этом месяце",
    "long_term": "🗓 Долгосрочные",
    "someday": "💭 Когда-нибудь",
    "no_group": "📥 Без срока",
    "inbox": "🌀 Inbox",
    "all": "📋 Все задачи",
    "done": "✅ Выполненные",
}


def task_icon(status: TaskStatus, overdue: bool) -> str:
    if status == TaskStatus.DONE:
        return "✅"
    if status == TaskStatus.CANCELLED:
        return "🚫"
    if overdue:
        return "⚠️"
    if status == TaskStatus.IN_PROGRESS:
        return "🔵"
    if status == TaskStatus.WAITING:
        return "⏳"
    return "▪️"


def task_line(
    task, tz: ZoneInfo, *, index: int | None = None, now: datetime | None = None
) -> str:
    """Строка списка: статус-иконка, срок, группа, приоритет."""
    overdue = False
    if now is not None and task.due_at is not None:
        overdue = task.due_at.astimezone(tz).date() < now.astimezone(tz).date()
    prefix = f"{index}. " if index is not None else ""
    line = f"{prefix}{task_icon(task.status, overdue)} {html.escape(str(task.title))}"
    details: list[str] = []
    if task.due_at is not None:
        details.append("до " + fmt_dt_local(task.due_at, tz))
    if task.bucket is not None:
        details.append(BUCKET_LABELS.get(TaskBucket(task.bucket), str(task.bucket)))
    if task.priority in (TaskPriority.HIGH, TaskPriority.URGENT):
        details.append(PRIORITY_LABELS.get(TaskPriority(task.priority), str(task.priority)))
    if details:
        line += f" <i>({' · '.join(html.escape(part) for part in details)})</i>"
    return line


def task_list_text(title: str, tasks, tz: ZoneInfo, now: datetime) -> str:
    """Текст списка задач (пустой список — «Пусто.»)."""
    if not tasks:
        return f"{title}\n\nПусто."
    lines = [title, ""]
    for index, task in enumerate(tasks, start=1):
        lines.append(task_line(task, tz, index=index, now=now))
    return "\n".join(lines)


def _line(label: str, value: str | None) -> str:
    if not value:
        return ""
    return f"<b>{html.escape(label)}:</b> {html.escape(value)}\n"


async def _category_name(session: AsyncSession, category_id) -> str | None:
    if not category_id:
        return None
    try:
        category = await CategoryRepository(session).get(uuid.UUID(str(category_id)))
    except (ValueError, AttributeError):
        return None
    return category.name if category else None


async def _project_label(session: AsyncSession, payload: dict) -> str | None:
    """Название проекта для превью; помечаем проект, который будет создан."""
    from jarvis.repositories.projects import ProjectRepository

    repo = ProjectRepository(session)
    project_id = payload.get("project_id")
    if project_id:
        try:
            project = await repo.get(uuid.UUID(str(project_id)))
        except (ValueError, AttributeError):
            project = None
        return project.name if project else None
    name = str(payload.get("project_name") or "").strip()
    if not name:
        return None
    existing = await repo.find_by_name(name)
    return name if existing else f"{name} (будет создан)"


async def render_preview(
    session: AsyncSession,
    operation_type: PendingOpType,
    payload: dict,
    tz: ZoneInfo,
) -> str:
    """Собрать текст предпросмотра операции по её payload."""
    title = OPERATION_TITLES.get(operation_type, "Подтвердите операцию:")
    lines = [f"<b>{title}</b>\n"]

    if operation_type == PendingOpType.CREATE_TASK:
        lines.append(_line("Название", payload.get("title")))
        lines.append(_line("Описание", payload.get("description")))
        bucket = payload.get("bucket")
        if bucket:
            label = BUCKET_LABELS.get(TaskBucket(bucket))
            lines.append(_line("Группа", label))
        due_raw = payload.get("due_at")
        due = None
        if due_raw:
            try:
                due = datetime.fromisoformat(str(due_raw))
            except ValueError:
                due = None
        if due is not None:
            lines.append(_line("Дедлайн", fmt_dt_local(due, tz)))
        priority = payload.get("priority")
        if priority and priority != TaskPriority.NORMAL.value:
            lines.append(_line("Приоритет", PRIORITY_LABELS.get(TaskPriority(priority), priority)))
        category = await _category_name(session, payload.get("category_id")) or (
            payload.get("category_name")
        )
        lines.append(_line("Категория", category))
        lines.append(_line("Проект", await _project_label(session, payload)))

    elif operation_type == PendingOpType.UPDATE_TASK:
        changes = payload.get("changes", {})
        task_id = payload.get("task_id")
        if task_id:
            task = await TaskRepository(session).get(uuid.UUID(str(task_id)))
            if task is not None:
                lines.append(_line("Задача", task.title))
                lines.append(_render_task_changes(task, changes, tz))
            else:
                lines.append("Задача не найдена.\n")

    elif operation_type == PendingOpType.CREATE_COMMENT:
        task_id = payload.get("task_id")
        if task_id:
            task = await TaskRepository(session).get(uuid.UUID(str(task_id)))
            lines.append(_line("Задача", task.title if task else "не найдена"))
        content = str(payload.get("content") or "")
        preview = content if len(content) <= 300 else content[:299] + "…"
        lines.append(f"\n<i>{html.escape(preview)}</i>")

    elif operation_type == PendingOpType.UPDATE_COMMENT:
        from jarvis.repositories.comments import TaskCommentRepository

        comment_id = payload.get("comment_id")
        comment = None
        try:
            comment = await TaskCommentRepository(session).get(uuid.UUID(str(comment_id)))
        except (ValueError, TypeError):
            comment = None
        if comment is not None:
            old = comment.content if len(comment.content) <= 120 else comment.content[:119] + "…"
            new_raw = str(payload.get("content") or "")
            new = new_raw if len(new_raw) <= 120 else new_raw[:119] + "…"
            lines.append(f"<i>{html.escape(old)}</i> → {html.escape(new)}")
        else:
            lines.append("Комментарий не найден.\n")

    elif operation_type == PendingOpType.CREATE_KNOWLEDGE:
        ktype = payload.get("type", KnowledgeType.NOTE.value)
        lines.append(_line("Тип", KNOWLEDGE_TYPE_LABELS.get(KnowledgeType(ktype), ktype)))
        lines.append(_line("Заголовок", payload.get("title")))
        summary = payload.get("summary")
        if summary:
            lines.append(_line("Сводка", summary))
        category = await _category_name(session, payload.get("category_id"))
        lines.append(_line("Категория", category))
        lines.append(_line("Проект", await _project_label(session, payload)))
        sensitivity = payload.get("sensitivity", Sensitivity.NORMAL.value)
        if sensitivity != Sensitivity.NORMAL.value:
            lines.append(
                _line("Чувствительность", SENSITIVITY_LABELS.get(Sensitivity(sensitivity)))
            )
        content = payload.get("content") or ""
        preview = content if len(content) <= 300 else content[:299] + "…"
        lines.append(f"\n<i>{html.escape(preview)}</i>")

    elif operation_type == PendingOpType.UPDATE_KNOWLEDGE:
        item = None
        item_id = payload.get("item_id")
        try:
            item = await KnowledgeRepository(session).get(uuid.UUID(str(item_id)))
        except (ValueError, TypeError):
            item = None
        if item is None:
            lines.append("Запись не найдена.\n")
        else:
            lines.append(_line("Запись", item.title))
            lines.append(_render_knowledge_changes(item, payload.get("changes", {})))

    elif operation_type == PendingOpType.SAVE_DOCUMENT:
        lines.append(_line("Название", payload.get("title")))
        dtype = payload.get("document_type", DocumentType.OTHER.value)
        lines.append(_line("Тип", DOCUMENT_TYPE_LABELS.get(DocumentType(dtype), dtype)))
        category = await _category_name(session, payload.get("category_id"))
        lines.append(_line("Категория", category))
        sensitivity = payload.get("sensitivity", Sensitivity.SENSITIVE.value)
        lines.append(
            _line("Чувствительность", SENSITIVITY_LABELS.get(Sensitivity(sensitivity)))
        )

    elif operation_type == PendingOpType.UPDATE_DOCUMENT:
        from jarvis.repositories.documents import DocumentRepository

        document_id = payload.get("document_id")
        document = None
        try:
            document = await DocumentRepository(session).get(uuid.UUID(str(document_id)))
        except (ValueError, TypeError):
            document = None
        if document is None:
            lines.append("Документ не найден.\n")
        else:
            if payload.get("title"):
                new_title = html.escape(str(payload["title"]))
                lines.append(_line("Название", f"{html.escape(document.title)} → {new_title}"))
            else:
                lines.append(_line("Документ", document.title))
            if "category_id" in payload:
                old = await _category_name(session, document.category_id)
                new = await _category_name(session, payload.get("category_id"))
                lines.append(_line("Категория", f"{old or 'нет'} → {new or 'нет'}"))
            if payload.get("sensitivity"):
                old = SENSITIVITY_LABELS.get(document.sensitivity, document.sensitivity.value)
                new = SENSITIVITY_LABELS.get(
                    Sensitivity(payload["sensitivity"]), payload["sensitivity"]
                )
                lines.append(_line("Чувствительность", f"{old} → {new}"))

    elif operation_type in (
        PendingOpType.DELETE_TASK,
        PendingOpType.DELETE_KNOWLEDGE,
        PendingOpType.DELETE_DOCUMENT,
        PendingOpType.DELETE_SECRET,
    ):
        entity_id = payload.get("entity_id")
        lines.append(await _render_delete_target(session, operation_type, entity_id))

    elif operation_type == PendingOpType.CREATE_CATEGORY:
        lines.append(_line("Название", payload.get("name")))

    elif operation_type == PendingOpType.CREATE_PROJECT:
        lines.append(_line("Название", payload.get("name")))
        lines.append(_line("Описание", payload.get("description")))

    elif operation_type == PendingOpType.RENAME_CATEGORY:
        category = await _category_name(session, payload.get("category_id"))
        lines.append(_line("Категория", category))
        lines.append(_line("Новое название", payload.get("new_name")))

    elif operation_type == PendingOpType.CREATE_SECRET:
        lines.append(_line("Название", payload.get("name")))
        lines.append(_line("Назначение", payload.get("purpose")))
        secret_type = payload.get("secret_type")
        if secret_type:
            lines.append(_line("Тип", secret_type))
        lines.append("<i>Значение хранится в зашифрованном виде и не показывается.</i>")

    lines.append("\nПодтвердите или отмените операцию.")
    return "".join(line for line in lines if line)


def _render_knowledge_changes(item, changes: dict) -> str:
    """Изменения записи знаний: старое → новое (превью UPDATE_KNOWLEDGE)."""
    parts = []
    if "title" in changes:
        old_title = item.title
        new_title = str(changes["title"])
        parts.append(f"заголовок: <i>{html.escape(old_title)}</i> → {html.escape(new_title)}")
    if "summary" in changes:
        old = (item.summary or "нет")[:120]
        new = str(changes["summary"] or "нет")[:120]
        parts.append(f"сводка: <i>{html.escape(old)}</i> → {html.escape(new)}")
    if "content" in changes:
        old = item.content[:120] if item.content else "нет"
        new = str(changes["content"] or "нет")[:120]
        parts.append(f"текст: <i>{html.escape(old)}</i> → {html.escape(new)}")
    if "sensitivity" in changes:
        old = SENSITIVITY_LABELS.get(item.sensitivity, item.sensitivity)
        new = SENSITIVITY_LABELS.get(Sensitivity(changes["sensitivity"]), changes["sensitivity"])
        parts.append(f"чувствительность: <i>{old}</i> → {new}")
    if "category_id" in changes:
        parts.append("категория будет изменена")
    if "project_id" in changes:
        parts.append("проект будет изменён")
    if not parts:
        return "<i>Нет изменений.</i>"
    return "\n".join(f"• {part}" for part in parts)


def _render_task_changes(task, changes: dict, tz: ZoneInfo) -> str:
    from jarvis.domain.enums import TaskBucket as Bucket

    parts = []
    if "title" in changes:
        new_title = html.escape(str(changes["title"]))
        parts.append(f"название: <i>{html.escape(task.title)}</i> → {new_title}")
    if "due_at" in changes:
        old = fmt_dt_local(task.due_at, tz) if task.due_at else "нет"
        raw = changes["due_at"]
        if raw:
            try:
                new = fmt_dt_local(datetime.fromisoformat(str(raw)), tz)
            except ValueError:
                new = html.escape(str(raw))
        else:
            new = "нет"
        parts.append(f"дедлайн: <i>{old}</i> → {new}")
    if "bucket" in changes:
        old = BUCKET_LABELS.get(task.bucket, "без группы") if task.bucket else "без группы"
        raw = changes["bucket"]
        new = BUCKET_LABELS.get(Bucket(raw), "без группы") if raw else "без группы"
        parts.append(f"группа: <i>{old}</i> → {new}")
    if "status" in changes:
        old = STATUS_LABELS.get(task.status, task.status)
        new = STATUS_LABELS.get(TaskStatus(changes["status"]), changes["status"])
        parts.append(f"статус: <i>{old}</i> → {new}")
    if "priority" in changes:
        old = PRIORITY_LABELS.get(task.priority, task.priority)
        new = PRIORITY_LABELS.get(TaskPriority(changes["priority"]), changes["priority"])
        parts.append(f"приоритет: <i>{old}</i> → {new}")
    if not parts:
        return "<i>Нет изменений.</i>"
    return "\n".join(f"• {part}" for part in parts)


async def _render_delete_target(
    session: AsyncSession, operation_type: PendingOpType, entity_id
) -> str:
    if not entity_id:
        return "<i>Объект не указан.</i>"
    try:
        uuid.UUID(str(entity_id))
    except ValueError:
        return "<i>Некорректный идентификатор.</i>"

    if operation_type == PendingOpType.DELETE_TASK:
        task = await TaskRepository(session).get(uuid.UUID(str(entity_id)))
        if task is not None:
            return _line("Задача", task.title)
    elif operation_type == PendingOpType.DELETE_KNOWLEDGE:
        item = await KnowledgeRepository(session).get(uuid.UUID(str(entity_id)))
        if item is not None:
            return _line("Запись", item.title)
    elif operation_type == PendingOpType.DELETE_DOCUMENT:
        from jarvis.repositories.documents import DocumentRepository

        document = await DocumentRepository(session).get(uuid.UUID(str(entity_id)))
        if document is not None:
            return (
                _line("Документ", document.title)
                + "<i>Файл будет удалён из хранилища без возможности восстановления.</i>"
            )
    elif operation_type == PendingOpType.DELETE_SECRET:
        from jarvis.repositories.secrets import SecretRepository

        secret = await SecretRepository(session).get(uuid.UUID(str(entity_id)))
        if secret is not None:
            return (
                _line("Секрет", secret.name)
                + "<i>Строка секрета будет удалена из базы без возможности восстановления.</i>"
            )
    return "<i>Объект не найден.</i>"
