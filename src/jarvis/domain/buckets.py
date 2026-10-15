"""Политика временных групп задач.

Группа («сегодня», «на этой неделе») — это намерение, которое хранится в БД,
а членство в списках «Сегодня»/«На этой неделе» вычисляется динамически
по календарю и таймзоне пользователя. «Сегодня» никогда не сохраняется
как конкретная дата.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from jarvis.domain.enums import TaskBucket, TaskStatus

DONE_STATUSES = frozenset({TaskStatus.DONE, TaskStatus.CANCELLED})


@dataclass(frozen=True)
class Placement:
    """Куда попадает задача в текущем календаре."""

    overdue: bool
    today: bool
    this_week: bool


def local_date(moment: datetime, tz: ZoneInfo) -> date:
    return moment.astimezone(tz).date()


def week_bounds(day: date) -> tuple[date, date]:
    """ISO-неделя (понедельник..воскресенье), содержащая день."""
    monday = day - timedelta(days=day.weekday())
    return monday, monday + timedelta(days=6)


def local_day_start(day: date, tz: ZoneInfo) -> datetime:
    """Полночь указанного дня в таймзоне пользователя (tz-aware)."""
    return datetime.combine(day, time.min, tz)


def place_task(
    *,
    status: TaskStatus,
    bucket: TaskBucket | None,
    due_at: datetime | None,
    now: datetime,
    tz: ZoneInfo,
) -> Placement:
    """Вычислить членство задачи в списках «Сегодня»/«На этой неделе»."""
    if status in DONE_STATUSES:
        return Placement(overdue=False, today=False, this_week=False)
    today = local_date(now, tz)
    due_day = local_date(due_at, tz) if due_at is not None else None
    overdue = due_day is not None and due_day < today
    in_today = overdue or due_day == today or bucket is TaskBucket.TODAY
    _, sunday = week_bounds(today)
    in_week = (
        due_day is not None and today < due_day <= sunday
    ) or (bucket is TaskBucket.WEEK and not in_today)
    return Placement(overdue=overdue, today=in_today, this_week=in_week)


def today_window(now: datetime, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """Границы списка «Сегодня» в UTC-совместимом виде для SQL.

    Возвращает (start, end): всё, что due_at < end, считается просроченным
    или сегодняшним. start не используется в фильтрах, но полезен в тестах.
    """
    today = local_date(now, tz)
    return local_day_start(today, tz), local_day_start(today + timedelta(days=1), tz)


def week_window(now: datetime, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """Границы списка «На этой неделе»: (конец сегодняшнего дня, конец недели)."""
    today = local_date(now, tz)
    _, sunday = week_bounds(today)
    end_today = local_day_start(today + timedelta(days=1), tz)
    end_week = local_day_start(sunday + timedelta(days=1), tz)
    return end_today, end_week
