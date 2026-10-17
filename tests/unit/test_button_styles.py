"""Цвета inline-кнопок: style только у перечисленных действий.

Bot API 9.4+: style = primary | success | danger; остальные — нейтральные.
Тексты кнопок не менять — e2e-тесты кликают по подстрокам.
"""

from __future__ import annotations

from jarvis.bot.keyboards import (
    backlog_card_keyboard,
    backlog_delete_keyboard,
    cancel_flow_keyboard,
    comments_keyboard,
    confirm_keyboard,
    document_card_keyboard,
    knowledge_card_keyboard,
    secret_card_keyboard,
    secrets_list_keyboard,
    settings_keyboard,
    task_card_keyboard,
)


def styles_by_text(markup) -> dict[str, str | None]:
    return {
        button.text: button.style
        for row in markup.inline_keyboard
        for button in row
    }


def test_confirm_keyboard_colors():
    styles = styles_by_text(confirm_keyboard("op-1"))
    assert styles["✅ Подтвердить"] == "success"
    assert styles["❌ Отменить"] == "danger"
    assert styles["✏️ Изменить"] is None  # нейтральная


def test_cancel_flow_is_danger():
    markup = cancel_flow_keyboard()
    (button,) = [b for row in markup.inline_keyboard for b in row]
    assert button.text == "❌ Отменить сценарий"
    assert button.style == "danger"


def test_task_card_colors():
    styles = styles_by_text(task_card_keyboard("ab" * 16, "todo", comments_count=2))
    assert styles["🏁 Завершить"] == "success"
    assert styles["🗑 Удалить"] == "danger"
    assert styles["📅 Изменить срок"] is None
    assert styles["💬 Комментарии (2)"] is None
    assert styles["📋 К спискам"] is None


def test_knowledge_card_delete_is_danger():
    styles = styles_by_text(knowledge_card_keyboard("ab" * 16))
    assert styles["🗑 Удалить"] == "danger"
    assert styles["🔎 Похожие"] is None


def test_document_card_colors():
    styles = styles_by_text(document_card_keyboard("ab" * 16, can_download=True))
    assert styles["📤 Получить файл"] == "primary"
    assert styles["🗑 Удалить"] == "danger"
    assert styles["🗂 Категория"] is None


def test_secret_card_and_list_colors():
    card = styles_by_text(secret_card_keyboard("ab" * 16))
    assert card["👁 Показать значение"] == "primary"
    assert card["🗑 Удалить"] == "danger"
    lst = styles_by_text(secrets_list_keyboard([], page=0, has_next=False))
    assert lst["➕ Новый секрет"] == "primary"


def test_settings_and_comments_primary():
    settings = styles_by_text(settings_keyboard([]))
    assert settings["➕ Новая категория"] == "primary"
    comments = styles_by_text(comments_keyboard([], "ab" * 16))
    assert comments["➕ Добавить комментарий"] == "primary"


class _Item:
    """Мини-заглушка записи backlog для клавиатур."""

    def __init__(self, status: str) -> None:
        import uuid

        self.id = uuid.uuid4()
        self.status = type("Status", (), {"value": status})()


def test_backlog_card_and_delete_colors():
    card = styles_by_text(backlog_card_keyboard(_Item("new"), "open", 0))
    assert card["🗄 В архив"] == "danger"
    assert card["🗑 Удалить"] == "danger"
    confirm = styles_by_text(backlog_delete_keyboard("ab" * 16, "open", 0))
    assert confirm["🗑 Да, удалить"] == "danger"
    assert confirm["⬅️ Назад"] is None
