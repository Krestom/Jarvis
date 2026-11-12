"""Календарные границы списков задач.

«Сегодня» и «На этой неделе» вычисляются по сроку и таймзоне пользователя;
отдельной группы-намерения у задачи больше нет.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from jarvis.domain.enums import TaskStatus

DONE_STATUSES = frozenset({TaskStatus.DONE, TaskStatus.CANCELLED})


def local_date(moment: datetime, tz: ZoneInfo) -> date:
    return moment.astimezone(tz).date()


def week_bounds(day: date) -> tuple[date, date]:
    """ISO-неделя (понедельник..воскресенье), содержащая день."""
    monday = day - timedelta(days=day.weekday())
    return monday, monday + timedelta(days=6)


def local_day_start(day: date, tz: ZoneInfo) -> datetime:
    """Полночь указанного дня в таймзоне пользователя (tz-aware)."""
    return datetime.combine(day, time.min, tz)


def today_window(now: datetime, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """Границы списка «Сегодня» в UTC-совместимом виде для SQL.

    Возвращает (start, end): всё, что due_at < end, считается просроченным
    или сегодняшним. start не используется в фильтрах, но полезен в тестах.
    """
    today = local_date(now, tz)
    return local_day_start(today, tz), local_day_start(today + timedelta(days=1), tz)


def week_window(now: datetime, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """Границы списка «На этой неделе»: (начало сегодняшнего дня, конец недели).

    Прошедшие дни не входят: просроченное остаётся в «Сегодня».
    """
    today = local_date(now, tz)
    _, sunday = week_bounds(today)
    start_today = local_day_start(today, tz)
    end_week = local_day_start(sunday + timedelta(days=1), tz)
    return start_today, end_week
