"""Inline-клавиатуры, формируемые из контекста."""

from __future__ import annotations

import uuid

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from jarvis.bot.callbacks import (
    ConfirmCB,
    DocCB,
    DocListCB,
    ImpCB,
    ImpListCB,
    KbCB,
    KbListCB,
    MenuCB,
    SearchCB,
    SecretCB,
    SecretListCB,
    TaskCB,
    TaskListCB,
    uuid_hex,
)

PAGE_SIZE = 7


def main_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text="🔎 Поиск", callback_data=MenuCB(action="search").pack()))
    builder.add(InlineKeyboardButton(text="✅ Задачи", callback_data=MenuCB(action="tasks").pack()))
    builder.add(
        InlineKeyboardButton(text="📚 Знания", callback_data=MenuCB(action="knowledge").pack())
    )
    builder.add(
        InlineKeyboardButton(text="📄 Документы", callback_data=MenuCB(action="documents").pack())
    )
    builder.add(
        InlineKeyboardButton(text="🗂 Проекты", callback_data=MenuCB(action="projects").pack())
    )
    builder.add(
        InlineKeyboardButton(text="⚙️ Настройки", callback_data=MenuCB(action="settings").pack())
    )
    builder.adjust(2, 2, 2)
    return builder.as_markup()


# Каталог быстрых действий: allow-list «действие → кнопка». Модель может лишь
# выбрать действия из списка; callback'и всегда строит сервер.
UI_ACTION_BUTTONS: dict[str, tuple[str, str]] = {
    "search": ("🔎 Поиск", MenuCB(action="search").pack()),
    "tasks_today": ("🔸 Задачи на сегодня", TaskListCB(view="today").pack()),
    "tasks_week": ("📆 Задачи на неделе", TaskListCB(view="week").pack()),
    "tasks_inbox": ("🌀 Inbox", TaskListCB(view="inbox").pack()),
    "documents": ("📄 Документы", MenuCB(action="documents").pack()),
    "projects": ("🗂 Проекты", MenuCB(action="projects").pack()),
    "settings": ("⚙️ Настройки", MenuCB(action="settings").pack()),
    "diagnostics": ("🩺 Диагностика", MenuCB(action="diagnostics").pack()),
}


def quick_actions_keyboard(actions) -> InlineKeyboardMarkup | None:
    """Клавиатура из allow-list действий; неизвестные молча отбрасываются."""
    buttons = []
    for action in dict.fromkeys(actions):
        button = UI_ACTION_BUTTONS.get(action)
        if button is not None and button not in buttons:
            buttons.append(button)
    if not buttons:
        return None
    builder = InlineKeyboardBuilder()
    for label, callback_data in buttons:
        builder.add(InlineKeyboardButton(text=label, callback_data=callback_data))
    builder.row(
        InlineKeyboardButton(text="🏠 В меню", callback_data=MenuCB(action="main").pack())
    )
    builder.adjust(2)
    return builder.as_markup()


def agent_entity_keyboard(candidates) -> InlineKeyboardMarkup:
    """Кнопки выбора реальных сущностей: только серверный индекс, id — в контексте."""
    from jarvis.bot.callbacks import AgentPickCB

    builder = InlineKeyboardBuilder()
    for index, candidate in enumerate(candidates):
        title = str(candidate.get("title") or "—")[:40]
        kind = {"task": "✅", "project": "🗂", "knowledge": "📚"}.get(
            str(candidate.get("kind")), "•"
        )
        builder.add(
            InlineKeyboardButton(
                text=f"{index + 1}. {kind} {title}",
                callback_data=AgentPickCB(index=index).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(text="🏠 В меню", callback_data=MenuCB(action="main").pack())
    )
    builder.adjust(1)
    return builder.as_markup()


def confirm_keyboard(operation_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.add(
        InlineKeyboardButton(
            text="✅ Подтвердить",
            callback_data=ConfirmCB(action="apply", op_id=operation_id).pack(),
        )
    )
    builder.add(
        InlineKeyboardButton(
            text="✏️ Изменить", callback_data=ConfirmCB(action="edit", op_id=operation_id).pack()
        )
    )
    builder.add(
        InlineKeyboardButton(
            text="❌ Отменить", callback_data=ConfirmCB(action="cancel", op_id=operation_id).pack()
        )
    )
    builder.adjust(1)
    return builder.as_markup()


def cancel_flow_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.add(
        InlineKeyboardButton(
            text="❌ Отменить сценарий", callback_data=MenuCB(action="cancel_flow").pack()
        )
    )
    return builder.as_markup()


def tasks_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    labels = [
        ("today", "🔸 Сегодня"),
        ("week", "📆 На этой неделе"),
        ("long_term", "🗓 Долгосрочные"),
        ("someday", "💭 Когда-нибудь"),
        ("no_group", "📥 Без срока"),
        ("inbox", "🌀 Inbox"),
        ("done", "✅ Выполненные"),
    ]
    for view, label in labels:
        builder.add(InlineKeyboardButton(text=label, callback_data=TaskListCB(view=view).pack()))
    builder.add(
        InlineKeyboardButton(
            text="🗂 По категориям", callback_data=TaskListCB(view="category").pack()
        )
    )
    builder.add(
        InlineKeyboardButton(text="🏠 В меню", callback_data=MenuCB(action="main").pack())
    )
    builder.adjust(2, 2, 2, 1, 1, 1)
    return builder.as_markup()


def task_list_keyboard(
    tasks, view: str, page: int, has_next: bool, value: str = ""
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for index, task in enumerate(tasks, start=1):
        builder.add(
            InlineKeyboardButton(
                text=f"{index}. {task.title[:24]}",
                callback_data=TaskCB(action="view", task_id=uuid_hex(task.id)).pack(),
            )
        )
    nav = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text="⬅️", callback_data=TaskListCB(view=view, page=page - 1, value=value).pack()
            )
        )
    if has_next:
        nav.append(
            InlineKeyboardButton(
                text="➡️", callback_data=TaskListCB(view=view, page=page + 1, value=value).pack()
            )
        )
    builder.adjust(1)
    if nav:
        builder.row(*nav)
    builder.row(
        InlineKeyboardButton(
            text="📋 К спискам", callback_data=MenuCB(action="tasks").pack()
        ),
        InlineKeyboardButton(text="🏠 В меню", callback_data=MenuCB(action="main").pack()),
    )
    return builder.as_markup()


def task_card_keyboard(task_id: str, status: str, comments_count: int = 0) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if status == "done":
        builder.add(
            InlineKeyboardButton(
                text="↩️ Вернуть в работу",
                callback_data=TaskCB(action="reopen", task_id=task_id).pack(),
            )
        )
    else:
        builder.add(
            InlineKeyboardButton(
                text="📅 Изменить срок", callback_data=TaskCB(action="due", task_id=task_id).pack()
            )
        )
        builder.add(
            InlineKeyboardButton(
                text="🗂 Группа",
                callback_data=TaskCB(action="bucket", task_id=task_id).pack(),
            )
        )
        builder.add(
            InlineKeyboardButton(
                text="🏁 Завершить", callback_data=TaskCB(action="done", task_id=task_id).pack()
            )
        )
    comments_label = f"💬 Комментарии ({comments_count})" if comments_count else "💬 Комментарии"
    builder.add(
        InlineKeyboardButton(
            text=comments_label,
            callback_data=TaskCB(action="comments", task_id=task_id).pack(),
        )
    )
    builder.add(
        InlineKeyboardButton(
            text="🗑 Удалить", callback_data=TaskCB(action="delete", task_id=task_id).pack()
        )
    )
    builder.row(
        InlineKeyboardButton(text="📋 К спискам", callback_data=MenuCB(action="tasks").pack())
    )
    builder.adjust(2, 2, 1, 1)
    return builder.as_markup()


def comments_keyboard(comments, task_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.add(
        InlineKeyboardButton(
            text="➕ Добавить комментарий",
            callback_data=TaskCB(action="comment_add", task_id=task_id).pack(),
        )
    )
    for index, comment in enumerate(comments, start=1):
        # Telegram: callback_data ≤ 64 байт, поэтому id задачи и комментария
        # вместе не помещаются — задачу восстанавливаем из строки комментария.
        builder.add(
            InlineKeyboardButton(
                text=f"✏️ Изменить {index}",
                callback_data=TaskCB(action="cedit", value=uuid_hex(comment.id)).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="⬅️ К задаче", callback_data=TaskCB(action="view", task_id=task_id).pack()
        )
    )
    builder.adjust(1)
    return builder.as_markup()


def comment_target_keyboard(tasks, action: str = "comment_choose") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for task in tasks:
        builder.add(
            InlineKeyboardButton(
                text=task.title[:40],
                callback_data=TaskCB(action=action, task_id=uuid_hex(task.id)).pack(),
            )
        )
    builder.adjust(1)
    return builder.as_markup()


def force_save_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.add(
        InlineKeyboardButton(
            text="⚠️ Всё равно сохранить как заметку",
            callback_data=MenuCB(action="force_save").pack(),
        )
    )
    builder.add(
        InlineKeyboardButton(
            text="🏠 В меню", callback_data=MenuCB(action="main").pack()
        )
    )
    builder.adjust(1)
    return builder.as_markup()


def task_categories_keyboard(categories) -> InlineKeyboardMarkup:
    """Категории-папки задач: выбор для просмотра."""
    builder = InlineKeyboardBuilder()
    for category in categories:
        builder.add(
            InlineKeyboardButton(
                text=f"🗂 {category.name[:40]}",
                callback_data=TaskListCB(view="category", value=uuid_hex(category.id)).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(text="📋 К спискам", callback_data=MenuCB(action="tasks").pack())
    )
    builder.adjust(1)
    return builder.as_markup()


def bucket_keyboard(task_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    options = [
        ("today", "Сегодня"),
        ("week", "На этой неделе"),
        ("long_term", "Долгосрочные"),
        ("someday", "Когда-нибудь"),
        ("none", "Без группы"),
    ]
    for value, label in options:
        builder.add(
            InlineKeyboardButton(
                text=label,
                callback_data=TaskCB(action="bucket_set", task_id=task_id, value=value).pack(),
            )
        )
    builder.add(
        InlineKeyboardButton(
            text="⬅️ Назад", callback_data=TaskCB(action="view", task_id=task_id).pack()
        )
    )
    builder.adjust(1)
    return builder.as_markup()


def knowledge_list_keyboard(items, page: int, has_next: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for index, item in enumerate(items, start=1):
        builder.add(
            InlineKeyboardButton(
                text=f"{index}. {item.title[:24]}",
                callback_data=KbCB(action="view", item_id=uuid_hex(item.id)).pack(),
            )
        )
    builder.adjust(1)
    nav = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(text="⬅️", callback_data=KbListCB(page=page - 1).pack())
        )
    if has_next:
        nav.append(
            InlineKeyboardButton(text="➡️", callback_data=KbListCB(page=page + 1).pack())
        )
    if nav:
        builder.row(*nav)
    builder.row(
        InlineKeyboardButton(text="🏠 В меню", callback_data=MenuCB(action="main").pack())
    )
    return builder.as_markup()


def knowledge_card_keyboard(item_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.add(
        InlineKeyboardButton(
            text="📖 Полный текст", callback_data=KbCB(action="full", item_id=item_id).pack()
        )
    )
    builder.add(
        InlineKeyboardButton(
            text="🔎 Похожие", callback_data=KbCB(action="similar", item_id=item_id).pack()
        )
    )
    builder.add(
        InlineKeyboardButton(
            text="🗑 Удалить", callback_data=KbCB(action="delete", item_id=item_id).pack()
        )
    )
    builder.row(
        InlineKeyboardButton(text="📚 К списку", callback_data=MenuCB(action="knowledge").pack())
    )
    builder.adjust(2, 2)
    return builder.as_markup()


def documents_list_keyboard(
    documents, page: int, has_next: bool, category: str = ""
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for index, document in enumerate(documents, start=1):
        builder.add(
            InlineKeyboardButton(
                text=f"{index}. {document.title[:24]}",
                callback_data=DocCB(action="view", doc_id=uuid_hex(document.id)).pack(),
            )
        )
    builder.adjust(1)
    nav = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text="⬅️", callback_data=DocListCB(page=page - 1, category=category).pack()
            )
        )
    if has_next:
        nav.append(
            InlineKeyboardButton(
                text="➡️", callback_data=DocListCB(page=page + 1, category=category).pack()
            )
        )
    if nav:
        builder.row(*nav)
    builder.row(
        InlineKeyboardButton(
            text="🗂 По категориям", callback_data=DocListCB(category="*").pack()
        ),
        InlineKeyboardButton(text="🏠 В меню", callback_data=MenuCB(action="main").pack()),
    )
    return builder.as_markup()


def document_categories_keyboard(categories) -> InlineKeyboardMarkup:
    """Категории-папки документов: выбор для просмотра."""
    builder = InlineKeyboardBuilder()
    for category in categories:
        builder.add(
            InlineKeyboardButton(
                text=f"🗂 {category.name[:40]}",
                callback_data=DocListCB(category=uuid_hex(category.id)).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(text="📄 Все документы", callback_data=DocListCB().pack()),
        InlineKeyboardButton(text="🏠 В меню", callback_data=MenuCB(action="main").pack()),
    )
    builder.adjust(1)
    return builder.as_markup()


def document_category_set_keyboard(categories, doc_id: str) -> InlineKeyboardMarkup:
    """Выбор категории для документа (id документа берётся из состояния диалога)."""
    builder = InlineKeyboardBuilder()
    for category in categories:
        builder.add(
            InlineKeyboardButton(
                text=f"🗂 {category.name[:40]}",
                callback_data=DocCB(action="category_set", value=uuid_hex(category.id)).pack(),
            )
        )
    builder.add(
        InlineKeyboardButton(
            text="🚫 Без категории",
            callback_data=DocCB(action="category_set", value="none").pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="⬅️ Назад", callback_data=DocCB(action="view", doc_id=doc_id).pack()
        )
    )
    builder.adjust(1)
    return builder.as_markup()


def document_card_keyboard(doc_id: str, *, can_download: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if can_download:
        builder.add(
            InlineKeyboardButton(
                text="📤 Получить файл",
                callback_data=DocCB(action="download", doc_id=doc_id).pack(),
            )
        )
    builder.add(
        InlineKeyboardButton(
            text="🗂 Категория", callback_data=DocCB(action="category", doc_id=doc_id).pack()
        )
    )
    builder.add(
        InlineKeyboardButton(
            text="🗑 Удалить", callback_data=DocCB(action="delete", doc_id=doc_id).pack()
        )
    )
    builder.row(
        InlineKeyboardButton(text="📄 К списку", callback_data=MenuCB(action="documents").pack())
    )
    builder.adjust(2, 1, 1)
    return builder.as_markup()


def search_results_keyboard(
    hits, query: str, page: int, has_next: bool
) -> InlineKeyboardMarkup:
    kind_emoji = {"knowledge": "📚", "task": "✅", "document": "📄"}
    builder = InlineKeyboardBuilder()
    for index, hit in enumerate(hits, start=1):
        builder.add(
            InlineKeyboardButton(
                text=f"{index}. {kind_emoji.get(hit.kind, '•')} {hit.title[:24]}",
                callback_data=SearchCB(
                    action="open", kind=hit.kind, entity=uuid_hex(hit.entity_id)
                ).pack(),
            )
        )
    builder.adjust(1)
    nav = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text="⬅️",
                callback_data=SearchCB(action="page_prev", page=page - 1).pack(),
            )
        )
    if has_next:
        nav.append(
            InlineKeyboardButton(
                text="➡️",
                callback_data=SearchCB(action="page_next", page=page + 1).pack(),
            )
        )
    if nav:
        builder.row(*nav)
    builder.row(
        InlineKeyboardButton(text="🏠 В меню", callback_data=MenuCB(action="main").pack())
    )
    return builder.as_markup()


def settings_keyboard(categories) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.add(
        InlineKeyboardButton(
            text="➕ Новая категория", callback_data=MenuCB(action="category_new").pack()
        )
    )
    for category in categories:
        builder.add(
            InlineKeyboardButton(
                text=f"✏️ {category.name[:30]}",
                callback_data=MenuCB(action="category_rename", value=category.id.hex).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(text="🏠 В меню", callback_data=MenuCB(action="main").pack())
    )
    builder.adjust(1)
    return builder.as_markup()


def secrets_list_keyboard(secrets, page: int, has_next: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.add(
        InlineKeyboardButton(
            text="➕ Новый секрет", callback_data=MenuCB(action="secret_new").pack()
        )
    )
    for index, secret in enumerate(secrets, start=1):
        builder.add(
            InlineKeyboardButton(
                text=f"{index}. {secret.name[:30]}",
                callback_data=SecretCB(action="view", secret_id=uuid_hex(secret.id)).pack(),
            )
        )
    builder.adjust(1)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=SecretListCB(page=page - 1).pack()))
    if has_next:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=SecretListCB(page=page + 1).pack()))
    if nav:
        builder.row(*nav)
    builder.row(
        InlineKeyboardButton(text="🏠 В меню", callback_data=MenuCB(action="main").pack())
    )
    return builder.as_markup()


def secret_card_keyboard(secret_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.add(
        InlineKeyboardButton(
            text="👁 Показать значение",
            callback_data=SecretCB(action="reveal", secret_id=secret_id).pack(),
        )
    )
    builder.add(
        InlineKeyboardButton(
            text="🗑 Удалить", callback_data=SecretCB(action="delete", secret_id=secret_id).pack()
        )
    )
    builder.row(
        InlineKeyboardButton(text="🔐 К списку", callback_data=MenuCB(action="secrets").pack())
    )
    builder.adjust(2, 1)
    return builder.as_markup()


def open_entity_keyboard(kind: str, entity_id: uuid.UUID) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    hex_id = uuid_hex(entity_id)
    open_button = InlineKeyboardButton(text="Открыть", callback_data="")
    if kind == "task":
        open_button.callback_data = TaskCB(action="view", task_id=hex_id).pack()
    elif kind == "knowledge":
        open_button.callback_data = KbCB(action="view", item_id=hex_id).pack()
    elif kind == "document":
        open_button.callback_data = DocCB(action="view", doc_id=hex_id).pack()
    builder.add(open_button)
    builder.row(
        InlineKeyboardButton(text="🏠 В меню", callback_data=MenuCB(action="main").pack())
    )
    return builder.as_markup()


# --- Improvement backlog (Stage 2) -------------------------------------------

IMPROVEMENT_STATUS_LABELS = {
    "new": "🆕 Новые",
    "reviewed": "👀 Разобранные",
    "planned": "📅 В плане",
    "implemented": "🛠 Реализованные",
    "wont_fix": "🙅 Не чинить",
    "archived": "🗄 Архив",
}

IMPROVEMENT_CATEGORY_LABELS = {
    "failure": "🐞 Сбои",
    "missing_capability": "🧩 Не хватает",
    "ux_friction": "😤 Трение UX",
    "improvement_idea": "💡 Идеи",
}


def diagnostics_keyboard(status_counts, category_counts) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    open_count = sum(
        status_counts.get(key, 0) for key in ("new", "reviewed", "planned")
    )
    builder.add(
        InlineKeyboardButton(
            text=f"📥 Открытые ({open_count})",
            callback_data=ImpListCB(kind="open").pack(),
        ),
        InlineKeyboardButton(text="📚 Все", callback_data=ImpListCB(kind="all").pack()),
    )
    for key, label in IMPROVEMENT_STATUS_LABELS.items():
        builder.add(
            InlineKeyboardButton(
                text=f"{label} ({status_counts.get(key, 0)})",
                callback_data=ImpListCB(kind="status", value=key).pack(),
            )
        )
    for key, label in IMPROVEMENT_CATEGORY_LABELS.items():
        builder.add(
            InlineKeyboardButton(
                text=f"{label} ({category_counts.get(key, 0)})",
                callback_data=ImpListCB(kind="category", value=key).pack(),
            )
        )
    builder.add(
        InlineKeyboardButton(text="📤 Экспорт", callback_data=ImpCB(action="export").pack())
    )
    builder.row(
        InlineKeyboardButton(text="🏠 В меню", callback_data=MenuCB(action="main").pack())
    )
    builder.adjust(2, 2, 2, 2, 2, 1, 1)
    return builder.as_markup()


def backlog_list_keyboard(items, kind: str, value: str, page: int, has_next: bool):
    builder = InlineKeyboardBuilder()
    # origin = «откуда пришли»; разделитель не ":", он занят aiogram'ом.
    origin = pack_origin(kind, value)
    for index, item in enumerate(items, start=1):
        builder.add(
            InlineKeyboardButton(
                text=f"{index}. {item.title[:30]}",
                callback_data=ImpCB(
                    action="view", item_id=uuid_hex(item.id), value=origin, page=page
                ).pack(),
            )
        )
    builder.adjust(1)
    nav = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text="⬅️",
                callback_data=ImpListCB(kind=kind, value=value, page=page - 1).pack(),
            )
        )
    if has_next:
        nav.append(
            InlineKeyboardButton(
                text="➡️",
                callback_data=ImpListCB(kind=kind, value=value, page=page + 1).pack(),
            )
        )
    if nav:
        builder.row(*nav)
    builder.row(
        InlineKeyboardButton(
            text="⬆️ К сводке", callback_data=MenuCB(action="diagnostics").pack()
        ),
        InlineKeyboardButton(text="🏠 В меню", callback_data=MenuCB(action="main").pack()),
    )
    return builder.as_markup()


# Компактные коды origin: полные названия не помещаются в callback_data
# (лимит Telegram — 64 байта; регрессия imp:delete_no из backlog).
_ORIGIN_KINDS = {"open": "o", "all": "a", "status": "s", "category": "c"}
_ORIGIN_VALUES = {
    "new": "n",
    "reviewed": "r",
    "planned": "p",
    "implemented": "i",
    "wont_fix": "w",
    "archived": "d",
    "failure": "f",
    "missing_capability": "m",
    "ux_friction": "u",
    "improvement_idea": "e",
}
_KIND_BY_CODE = {code: kind for kind, code in _ORIGIN_KINDS.items()}
_VALUE_BY_CODE = {code: value for value, code in _ORIGIN_VALUES.items()}


def pack_origin(kind: str, value: str) -> str:
    """Упаковать «откуда пришли» для callback_data (коротко, ≤64 байт)."""
    return f"{_ORIGIN_KINDS.get(kind, kind)}|{_ORIGIN_VALUES.get(value, value)}"


def _parse_origin(origin: str) -> tuple[str, str]:
    """Разобрать origin: новые коды (o/a/s/c) и legacy-формат «kind|value»."""
    kind, _, value = origin.partition("|")
    if kind in _KIND_BY_CODE:
        return _KIND_BY_CODE[kind], _VALUE_BY_CODE.get(value, value)
    return kind or "open", value


def backlog_card_keyboard(item, origin: str, page: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    hex_id = uuid_hex(item.id)
    status_actions = [
        ("reviewed", "👀 Разобран"),
        ("planned", "📅 В план"),
        ("implemented", "🛠 Реализовано"),
        ("wont_fix", "🙅 Не чинить"),
    ]
    for value, label in status_actions:
        if value == item.status.value:
            continue
        builder.add(
            InlineKeyboardButton(
                text=label,
                callback_data=ImpCB(action="status", item_id=hex_id, value=value).pack(),
            )
        )
    if item.status.value != "archived":
        builder.add(
            InlineKeyboardButton(
                text="🗄 В архив",
                callback_data=ImpCB(action="status", item_id=hex_id, value="archived").pack(),
            )
        )
    builder.add(
        InlineKeyboardButton(
            text="🗑 Удалить",
            callback_data=ImpCB(action="delete", item_id=hex_id, value=origin, page=page).pack(),
        )
    )
    kind, value = _parse_origin(origin)
    builder.row(
        InlineKeyboardButton(
            text="⬅️ К списку",
            callback_data=ImpListCB(kind=kind, value=value, page=page).pack(),
        )
    )
    builder.adjust(2, 2, 1, 1, 1)
    return builder.as_markup()


def backlog_delete_keyboard(item_id: str, origin: str, page: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.add(
        InlineKeyboardButton(
            text="🗑 Да, удалить",
            callback_data=ImpCB(action="delete_yes", item_id=item_id).pack(),
        )
    )
    builder.add(
        InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=ImpCB(
                action="delete_no", item_id=item_id, value=origin, page=page
            ).pack(),
        )
    )
    builder.adjust(1)
    return builder.as_markup()
