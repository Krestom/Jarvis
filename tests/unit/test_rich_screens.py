"""Rich-экраны: HTML-карточки с кнопками внутри пузыря."""

from __future__ import annotations

import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from jarvis.bot.callbacks import MenuCB, TaskCB, TaskListCB, uuid_hex
from jarvis.bot.rich import RichScreen, send_rich, send_rich_chat, text_screen
from jarvis.bot.screens.core import (
    comments_screen,
    main_menu_screen,
    task_card_screen,
    task_list_screen,
    tasks_menu_screen,
)
from jarvis.domain.enums import TaskStatus

TZ = ZoneInfo("Europe/Moscow")
NOW = datetime(2026, 11, 13, 8, 0, tzinfo=TZ)


class _Task:
    def __init__(
        self,
        title: str,
        status=TaskStatus.TODO,
        due_at=None,
        description: str | None = None,
        original_text: str | None = None,
    ) -> None:
        self.id = uuid.uuid4()
        self.title = title
        self.status = status
        self.due_at = due_at
        self.description = description
        self.original_text = original_text


class _Comment:
    def __init__(self, content: str) -> None:
        self.id = uuid.uuid4()
        self.content = content
        self.created_at = NOW


def _screen(tasks, *, page: int = 0, has_next: bool = False) -> RichScreen:
    return task_list_screen(
        title="📋 Все задачи",
        tasks=tasks,
        tz=TZ,
        now=NOW,
        view="all",
        page=page,
        has_next=has_next,
    )


def test_main_menu_has_inline_buttons():
    screen = main_menu_screen()
    assert "<tg-button-row" in screen.html
    assert MenuCB(action="search").pack() in screen.html
    assert MenuCB(action="tasks").pack() in screen.html
    assert screen.markup is not None


def test_tasks_menu_has_all_views():
    screen = tasks_menu_screen()
    for view in ("today", "week", "next_week", "all", "no_group"):
        assert TaskListCB(view=view).pack() in screen.html
    assert MenuCB(action="main").pack() in screen.html


def test_task_list_has_task_buttons_and_nav():
    tasks = [_Task("Первая"), _Task("Вторая", due_at=NOW)]
    screen = _screen(tasks, has_next=True)
    assert TaskCB(action="view", task_id=uuid_hex(tasks[0].id), value="l").pack() in screen.html
    assert TaskListCB(view="all", page=1).pack() in screen.html
    assert "Выберите задачу:" in screen.text
    assert "Первая" in screen.html
    assert "Вторая" in screen.html
    assert screen.markup is not None


def test_task_list_escapes_task_title():
    screen = _screen([_Task("<b>опасно</b>")])
    assert "<b>опасно</b>" not in screen.html
    assert "&lt;b&gt;опасно&lt;/b&gt;" in screen.html


def test_task_list_empty_has_no_task_buttons():
    screen = _screen([])
    assert "Пусто." in screen.html
    assert "tsk:view" not in screen.html
    assert "Выберите задачу:" not in screen.text


def test_task_list_page_two_has_back_button():
    screen = _screen([_Task("Первая")], page=1)
    assert "Страница 2" in screen.html
    assert TaskListCB(view="all", page=0).pack() in screen.html


class _FakeMessage:
    def __init__(self, *, fail_rich: bool = True) -> None:
        self.calls: list[tuple[str, object]] = []
        self._fail_rich = fail_rich

    async def answer_rich(self, rich):
        self.calls.append(("rich", rich))
        if self._fail_rich:
            raise TelegramBadRequest(method=None, message="Bad Request: rich is unavailable")

    async def answer(self, text, **kwargs):
        self.calls.append(("text", text))
        return None


async def test_send_rich_falls_back_to_text():
    message = _FakeMessage()
    screen = main_menu_screen()
    await send_rich(message, screen)
    assert [call[0] for call in message.calls] == ["rich", "text"]
    assert message.calls[1][1] == screen.text


async def test_send_rich_keeps_rich_when_accepted():
    message = _FakeMessage(fail_rich=False)
    await send_rich(message, main_menu_screen())
    assert [call[0] for call in message.calls] == ["rich"]


def test_task_card_has_actions_and_comments():
    task = _Task("Починить сервер", description="Длинное описание " * 30)
    comments = [_Comment("Первый"), _Comment("Второй")]
    screen = task_card_screen(task, comments, TZ)
    assert "<h3>" in screen.html
    assert "Починить сервер" in screen.html
    assert "Завершить" in screen.html
    assert "Изменить срок" in screen.html
    assert "Комментарии (2)" in screen.html
    assert "Удалить" in screen.html
    assert "<details>" in screen.html
    assert screen.markup is not None


def test_done_task_card_can_reopen():
    screen = task_card_screen(_Task("Готово", status=TaskStatus.DONE), [], TZ)
    assert "Вернуть в работу" in screen.html
    assert "Завершить" not in screen.html


def test_comments_screen_has_add_and_edit_buttons():
    task = _Task("Починить сервер")
    comments = [_Comment("Первый"), _Comment("Второй")]
    screen = comments_screen(task, comments, TZ)
    assert "Добавить комментарий" in screen.html
    assert "Изменить 1" in screen.html
    assert "Изменить 2" in screen.html
    assert screen.markup is not None


def test_text_screen_wraps_paragraphs_and_moves_buttons_inside():
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Открыть", callback_data=MenuCB(action="tasks").pack(), style="primary"
                )
            ]
        ]
    )
    screen = text_screen("Первая строка\nвторая строка\n\nНовый абзац", markup)
    assert "<p>Первая строка<br>вторая строка</p>" in screen.html
    assert "<p>Новый абзац</p>" in screen.html
    assert "<tg-button-row" in screen.html
    assert MenuCB(action="tasks").pack() in screen.html
    assert screen.text == "Первая строка\nвторая строка\n\nНовый абзац"
    assert screen.markup is markup


def test_text_screen_keeps_block_html():
    screen = text_screen("<h3>Заголовок</h3><p>Текст</p>")
    assert screen.html == "<h3>Заголовок</h3><p>Текст</p>"


class _FakeBot:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def send_rich_message(self, chat_id, rich):
        self.calls.append(("rich", chat_id))
        raise TelegramBadRequest(method=None, message="Bad Request: rich is unavailable")

    async def send_message(self, chat_id, text, **kwargs):
        self.calls.append(("text", chat_id, text, kwargs))
        return None


async def test_send_rich_chat_falls_back_to_text():
    bot = _FakeBot()
    screen = main_menu_screen()
    await send_rich_chat(bot, 111, screen)
    assert [call[0] for call in bot.calls] == ["rich", "text"]
    assert bot.calls[1][2] == screen.text
