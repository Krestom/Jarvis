from datetime import datetime
from zoneinfo import ZoneInfo

from jarvis.domain.windows import local_date, today_window, week_bounds, week_window

TZ = ZoneInfo("Europe/Moscow")


def now_thursday() -> datetime:
    # 2026-10-15 — четверг, ISO-неделя 12–18 октября.
    return datetime(2026, 10, 15, 12, 0, tzinfo=TZ)


def test_week_bounds() -> None:
    monday, sunday = week_bounds(now_thursday().date())
    assert monday.isoformat() == "2026-10-12"
    assert sunday.isoformat() == "2026-10-18"


def test_today_window_covers_overdue_and_today() -> None:
    start, end = today_window(now_thursday(), TZ)
    assert start == datetime(2026, 10, 15, 0, 0, tzinfo=TZ)
    assert end == datetime(2026, 10, 16, 0, 0, tzinfo=TZ)


def test_week_window_boundaries() -> None:
    start_today, end_week = week_window(now_thursday(), TZ)
    assert start_today == datetime(2026, 10, 15, 0, 0, tzinfo=TZ)
    assert end_week == datetime(2026, 10, 19, 0, 0, tzinfo=TZ)


def test_local_date_converts_utc_to_user_tz() -> None:
    utc_moment = datetime(2026, 10, 15, 22, 30, tzinfo=ZoneInfo("UTC"))
    assert local_date(utc_moment, TZ).isoformat() == "2026-10-16"


def test_weekend_window_ends_monday() -> None:
    sunday = datetime(2026, 10, 18, 10, 0, tzinfo=TZ)
    start_today, end_week = week_window(sunday, TZ)
    assert start_today == datetime(2026, 10, 18, 0, 0, tzinfo=TZ)
    assert end_week == datetime(2026, 10, 19, 0, 0, tzinfo=TZ)
