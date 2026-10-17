"""Разбор простых формулировок дат без AI («завтра», «в пятницу», «22.10.2026»)."""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

MONTHS_GENITIVE = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]

_WEEKDAY_ROOTS = [
    ("понедельник", "пн", 0),
    ("вторник", "вт", 1),
    ("сред", "ср", 2),
    ("четверг", "чт", 3),
    ("пятниц", "пт", 4),
    ("суббот", "сб", 5),
    ("воскресен", "вс", 6),
]

_TIME_RE = re.compile(r"\b(\d{1,2}):(\d{2})\b")
_HOUR_RE = re.compile(r"\bв\s+(\d{1,2})(?:\s*(?:час|ч)\b|$)")
_NUMERIC_DATE_RE = re.compile(r"\b(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?\b")
_MONTH_NAME_RE = re.compile(
    r"\b(\d{1,2})\s+(" + "|".join(MONTHS_GENITIVE) + r")(?:\s+(\d{4}))?\b"
)


def parse_human_date(text: str, now: datetime, tz: ZoneInfo) -> tuple[datetime | None, bool]:
    """Вернуть (дата-время, была ли распознана дата/время)."""
    lowered = text.lower()
    today = now.astimezone(tz).date()
    target: date | None = None

    if "послезавтра" in lowered:
        target = today + timedelta(days=2)
    elif "завтра" in lowered:
        target = today + timedelta(days=1)
    elif "сегодня" in lowered:
        target = today
    elif "следующ" in lowered and "недел" in lowered:
        target = today + timedelta(days=(7 - today.weekday()))
    elif "конц" in lowered and "месяц" in lowered:
        target = _last_day_of_month(today)
        if "следующ" in lowered:
            target = _last_day_of_month(target + timedelta(days=1))
    elif "конц" in lowered and "недел" in lowered:
        target = today + timedelta(days=(6 - today.weekday()))
    else:
        target = _match_weekday(lowered, today)
        if target is None:
            target = _match_numeric_date(lowered, today)
        if target is None:
            target = _match_month_name(lowered, today)

    moment = _match_time(lowered)
    if target is None and moment is None:
        return None, False
    if target is None:
        target = today
    return datetime.combine(target, moment or time.min, tz), True


def _last_day_of_month(day: date) -> date:
    if day.month == 12:
        return date(day.year, 12, 31)
    return date(day.year, day.month + 1, 1) - timedelta(days=1)


def _match_weekday(lowered: str, today: date) -> date | None:
    for root, short, weekday in _WEEKDAY_ROOTS:
        # Корень + любые окончания («в пятницу», «во вторник»), либо короткая форма.
        pattern = re.compile(rf"\b(?:в\s+)?(?:{root}\w*|{short})(?:\s|$|,)")
        if pattern.search(lowered):
            delta = (weekday - today.weekday()) % 7
            return today + timedelta(days=delta)
    return None


def _match_numeric_date(lowered: str, today: date) -> date | None:
    match = _NUMERIC_DATE_RE.search(lowered)
    if not match:
        return None
    day = int(match.group(1))
    month = int(match.group(2))
    year_text = match.group(3)
    try:
        if year_text is not None:
            year = int(year_text)
            if year < 100:
                year += 2000
            return date(year, month, day)
        candidate = date(today.year, month, day)
        if candidate < today - timedelta(days=1):
            candidate = date(today.year + 1, month, day)
        return candidate
    except ValueError:
        return None


def _match_month_name(lowered: str, today: date) -> date | None:
    match = _MONTH_NAME_RE.search(lowered)
    if not match:
        return None
    day = int(match.group(1))
    month = MONTHS_GENITIVE.index(match.group(2)) + 1
    year_text = match.group(3)
    try:
        if year_text is not None:
            return date(int(year_text), month, day)
        candidate = date(today.year, month, day)
        if candidate < today - timedelta(days=1):
            candidate = date(today.year + 1, month, day)
        return candidate
    except ValueError:
        return None


def _match_time(lowered: str) -> time | None:
    match = _TIME_RE.search(lowered)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        if hour <= 23 and minute <= 59:
            return time(hour, minute)
        return None
    match = _HOUR_RE.search(lowered)
    if match:
        hour = int(match.group(1))
        if hour <= 23:
            return time(hour, 0)
    return None
