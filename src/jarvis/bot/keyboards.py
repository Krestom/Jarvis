"""Inline-клавиатуры, формируемые из контекста."""

from __future__ import annotations

import uuid

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from jarvis.bot.callbacks import (
    ConfirmCB,
    DocCB,
    DocListCB,
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
        InlineKeyboardButton(text="🏠 В меню", callback_data=MenuCB(action="main").pack())
    )
    builder.adjust(2, 2, 2, 1, 1)
    return builder.as_markup()


def task_list_keyboard(
    tasks, view: str, page: int, has_next: bool
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
                text="⬅️", callback_data=TaskListCB(view=view, page=page - 1).pack()
            )
        )
    if has_next:
        nav.append(
            InlineKeyboardButton(
                text="➡️", callback_data=TaskListCB(view=view, page=page + 1).pack()
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


def task_card_keyboard(task_id: str, status: str) -> InlineKeyboardMarkup:
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
    builder.add(
        InlineKeyboardButton(
            text="🔗 Связи", callback_data=TaskCB(action="related", task_id=task_id).pack()
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
            text="🔗 Связи", callback_data=KbCB(action="related", item_id=item_id).pack()
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
    builder.adjust(2, 2, 1)
    return builder.as_markup()


def documents_list_keyboard(documents, page: int, has_next: bool) -> InlineKeyboardMarkup:
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
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=DocListCB(page=page - 1).pack()))
    if has_next:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=DocListCB(page=page + 1).pack()))
    if nav:
        builder.row(*nav)
    builder.row(
        InlineKeyboardButton(text="🏠 В меню", callback_data=MenuCB(action="main").pack())
    )
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
            text="🔗 Связи", callback_data=DocCB(action="related", doc_id=doc_id).pack()
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
