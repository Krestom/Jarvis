"""Единый вид списков задач: заголовки всех вью и строки (preview.task_list_text)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from jarvis.conversation.preview import TASK_VIEW_TITLES, task_list_text
from jarvis.domain.enums import TaskBucket, TaskPriority, TaskStatus
from jarvis.services.tasks import TASK_VIEWS

TZ = ZoneInfo("Europe/Moscow")
NOW = datetime(2026, 10, 18, 12, 0, tzinfo=TZ)


@dataclass
class FakeTask:
    title: str
    status: TaskStatus = TaskStatus.TODO
    bucket: TaskBucket | None = None
    priority: TaskPriority = TaskPriority.NORMAL
    due_at: datetime | None = None


def test_view_titles_cover_all_views() -> None:
    """Новый вью в TASK_VIEWS не должен забывать заголовок."""
    assert set(TASK_VIEWS) <= set(TASK_VIEW_TITLES)


def test_task_list_text_empty() -> None:
    assert task_list_text("🔸 Задачи", [], TZ, NOW) == "🔸 Задачи\n\nПусто."


def test_task_list_day_header_shows_time_only() -> None:
    task = FakeTask(title="Позвонить в банк", due_at=datetime(2026, 10, 19, 18, 0, tzinfo=TZ))
    line = task_list_text(
        "🔸 Задачи на 19.10.2026", [task], TZ, NOW, day=datetime(2026, 10, 19).date()
    ).splitlines()[2]
    assert "до 18:00" in line
    assert "октября" not in line


def test_task_list_day_header_date_only_is_all_day() -> None:
    task = FakeTask(title="Оплатить", due_at=datetime(2026, 10, 19, 0, 0, tzinfo=TZ))
    line = task_list_text(
        "🔸 Задачи на 19.10.2026", [task], TZ, NOW, day=datetime(2026, 10, 19).date()
    ).splitlines()[2]
    assert "весь день" in line


def test_task_list_day_header_keeps_date_for_overdue() -> None:
    task = FakeTask(title="Прошлое", due_at=datetime(2026, 10, 17, 10, 0, tzinfo=TZ))
    line = task_list_text(
        "🔸 Задачи на 19.10.2026", [task], TZ, NOW, day=datetime(2026, 10, 19).date()
    ).splitlines()[2]
    assert "⚠️" in line and "17 октября 2026, 10:00" in line


def test_task_list_text_rich_line() -> None:
    task = FakeTask(
        title="Позвонить в банк",
        bucket=TaskBucket.TODAY,
        priority=TaskPriority.HIGH,
        due_at=datetime(2026, 10, 19, 18, 0, tzinfo=TZ),
    )
    lines = task_list_text("🔸 Задачи на сегодня", [task], TZ, NOW).splitlines()
    assert lines[0] == "🔸 Задачи на сегодня"
    assert lines[1] == ""
    assert lines[2].startswith("1. ▪️ Позвонить в банк <i>(до 19 октября 2026, 18:00")
    assert "Сегодня" in lines[2] and "высокий" in lines[2]


def test_task_line_escapes_html() -> None:
    text = task_list_text("Список", [FakeTask(title="Отчёт <b>x</b>")], TZ, NOW)
    assert "&lt;b&gt;" in text
    assert "<b>x</b>" not in text


def test_task_line_marks_overdue() -> None:
    task = FakeTask(title="Прошлое", due_at=datetime(2026, 10, 17, 10, 0, tzinfo=TZ))
    line = task_list_text("Список", [task], TZ, NOW).splitlines()[2]
    assert "⚠️" in line
