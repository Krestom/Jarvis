"""Разбор простых формулировок дат без AI («завтра», «в пятницу», «22.10.2026»)."""

from __future__ import annotations

import re
from dataclasses import dataclass
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

_MONTHS_PATTERN = "|".join(MONTHS_GENITIVE)
_ISO_TOKEN = r"\d{4}-\d{2}-\d{2}"
_NUMERIC_TOKEN = r"\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?"
_DAY_MONTH_TOKEN = rf"\d{{1,2}}\s+(?:{_MONTHS_PATTERN})(?:\s+\d{{4}})?"
_DATE_TOKEN = rf"(?:{_ISO_TOKEN}|{_NUMERIC_TOKEN}|{_DAY_MONTH_TOKEN})"
_RANGE_CONNECTOR = r"(?:-|по|до)"

_DAY_DAY_MONTH_RE = re.compile(
    rf"(?:с\s+)?(\d{{1,2}})\s*{_RANGE_CONNECTOR}\s*(\d{{1,2}})\s+"
    rf"({_MONTHS_PATTERN})(?:\s+(\d{{4}}))?"
)
_FULL_RANGE_RE = re.compile(
    rf"(?:с\s+)?({_DATE_TOKEN})\s*{_RANGE_CONNECTOR}\s*({_DATE_TOKEN})"
)
_START_BARE_END_RE = re.compile(
    rf"(?:с\s+)?({_DATE_TOKEN})\s*{_RANGE_CONNECTOR}\s*(\d{{1,2}})(?![\d./])"
)


@dataclass(frozen=True)
class DateRange:
    """Включительный диапазон дат: start и end в локальном календаре."""

    start: date
    end: date


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


def parse_human_range(
    text: str, now: datetime, tz: ZoneInfo
) -> DateRange | None:
    """Период из фразы: «на следующую неделю», «с 20 по 27 ноября»."""
    lowered = _normalize_range_text(text)
    today = now.astimezone(tz).date()

    if "недел" in lowered:
        if "через" in lowered:
            start = today + timedelta(days=7)
            return DateRange(start, start + timedelta(days=6))
        markers = ("следующ", "будущ", "прошл", "текущ", "этой", "эту", "нынешн")
        if not any(marker in lowered for marker in markers):
            return None
        monday = today - timedelta(days=today.weekday())
        if "следующ" in lowered or "будущ" in lowered:
            monday += timedelta(days=7)
        elif "прошл" in lowered:
            monday -= timedelta(days=7)
        return DateRange(monday, monday + timedelta(days=6))

    if "месяц" in lowered:
        first = today.replace(day=1)
        if "следующ" in lowered or "будущ" in lowered:
            first = _last_day_of_month(first) + timedelta(days=1)
        elif "прошл" in lowered:
            first = (first - timedelta(days=1)).replace(day=1)
        return DateRange(first, _last_day_of_month(first))

    match = _DAY_DAY_MONTH_RE.search(lowered)
    if match:
        start_day = int(match.group(1))
        end_day = int(match.group(2))
        month = _month_number(match.group(3))
        year = _year_or_current(match.group(4), today)
        if month is None:
            return None
        start = _safe_date(year, month, start_day, today)
        end = _safe_date(year, month, end_day, today)
        return _ordered_range(start, end)

    match = _FULL_RANGE_RE.search(lowered)
    if match:
        start = _parse_date_token(match.group(1), today)
        end = _parse_date_token(match.group(2), today)
        return _ordered_range(start, end)

    match = _START_BARE_END_RE.search(lowered)
    if match:
        start = _parse_date_token(match.group(1), today)
        if start is None:
            return None
        end = _safe_date(start.year, start.month, int(match.group(2)), today)
        return _ordered_range(start, end)

    return None


def range_title(start: date, end: date, *, today: date | None = None) -> str:
    """Заголовок списка за период; год добавляется только при необходимости."""
    if today is not None and start.year == end.year == today.year:
        return f"📋 Задачи с {start:%d.%m} по {end:%d.%m}"
    return f"📋 Задачи с {start:%d.%m.%Y} по {end:%d.%m.%Y}"


def _normalize_range_text(text: str) -> str:
    return re.sub(
        r"\s+",
        " ",
        str(text).lower().replace("–", "-").replace("—", "-").replace("−", "-").replace("ё", "е"),
    )


def _month_number(name: str) -> int | None:
    name = name.strip().lower().replace("ё", "е")
    try:
        return MONTHS_GENITIVE.index(name) + 1
    except ValueError:
        for index, month in enumerate(MONTHS_GENITIVE, start=1):
            if month.startswith(name) or name.startswith(month[:3]):
                return index
        return None


def _year_or_current(raw: str | None, today: date) -> int:
    return int(raw) if raw is not None else today.year


def _safe_date(year: int, month: int, day: int, today: date) -> date | None:
    try:
        result = date(year, month, day)
    except ValueError:
        return None
    if year == today.year and result < today - timedelta(days=1):
        try:
            result = result.replace(year=year + 1)
        except ValueError:
            return None
    return result


def _parse_date_token(token: str, today: date) -> date | None:
    token = token.strip()
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", token)
    if match:
        return _safe_date(int(match.group(1)), int(match.group(2)), int(match.group(3)), today)
    match = re.fullmatch(r"(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?", token)
    if match:
        year = int(match.group(3)) if match.group(3) else today.year
        if year < 100:
            year += 2000
        return _safe_date(year, int(match.group(2)), int(match.group(1)), today)
    match = re.fullmatch(r"(\d{1,2})\s+([а-я]+)(?:\s+(\d{4}))?", token)
    if match:
        month = _month_number(match.group(2))
        if month is None:
            return None
        return _safe_date(
            _year_or_current(match.group(3), today),
            month,
            int(match.group(1)),
            today,
        )
    return None


def _ordered_range(start: date | None, end: date | None) -> DateRange | None:
    if start is None or end is None or end < start:
        return None
    return DateRange(start, end)


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
