"""Unit-тесты парсера дат."""

from datetime import datetime
from zoneinfo import ZoneInfo

from jarvis.conversation.dates import parse_human_date, parse_human_range

TZ = ZoneInfo("Europe/Moscow")
# 2026-10-15 — четверг.
NOW = datetime(2026, 10, 15, 12, 0, tzinfo=TZ)


def parse(text: str):
    value, matched = parse_human_date(text, NOW, TZ)
    assert matched
    return value


def test_today() -> None:
    assert parse("сегодня").date().isoformat() == "2026-10-15"


def test_tomorrow() -> None:
    assert parse("завтра").date().isoformat() == "2026-10-16"


def test_day_after_tomorrow() -> None:
    assert parse("послезавтра").date().isoformat() == "2026-10-17"


def test_weekday_friday() -> None:
    assert parse("в пятницу").date().isoformat() == "2026-10-16"


def test_weekday_same_day_is_today() -> None:
    assert parse("в четверг").date().isoformat() == "2026-10-15"


def test_weekday_monday_is_next_week() -> None:
    assert parse("в понедельник").date().isoformat() == "2026-10-19"


def test_short_weekday() -> None:
    assert parse("в ср").date().isoformat() == "2026-10-21"


def test_next_week() -> None:
    assert parse("на следующей неделе").date().isoformat() == "2026-10-19"


def test_end_of_month() -> None:
    assert parse("до конца месяца").date().isoformat() == "2026-10-31"


def test_end_of_next_month() -> None:
    assert parse("срок до конца следующего месяца").date().isoformat() == "2026-11-30"


def test_end_of_week() -> None:
    # 2026-10-15 — четверг, конец недели — воскресенье 18.10.
    assert parse("до конца недели").date().isoformat() == "2026-10-18"


def test_numeric_date_with_year() -> None:
    assert parse("22.10.2026").date().isoformat() == "2026-10-22"


def test_numeric_date_without_year() -> None:
    assert parse("22.10").date().isoformat() == "2026-10-22"


def test_numeric_date_without_year_rolls_to_next_year() -> None:
    assert parse("05.02").date().isoformat() == "2027-02-05"


def test_slash_format() -> None:
    assert parse("01/11/2026").date().isoformat() == "2026-11-01"


def test_month_name() -> None:
    assert parse("22 октября").date().isoformat() == "2026-10-22"


def test_month_name_with_year() -> None:
    assert parse("1 ноября 2027").date().isoformat() == "2027-11-01"


def test_time_with_date() -> None:
    moment = parse("завтра в 18:30")
    assert moment.isoformat().startswith("2026-10-16T18:30")


def test_hour_only() -> None:
    moment = parse("завтра в 9")
    assert moment.isoformat().startswith("2026-10-16T09:00")


def test_time_only_defaults_to_today() -> None:
    moment = parse("в 15:00")
    assert moment.isoformat().startswith("2026-10-15T15:00")


def test_no_match_returns_none() -> None:
    value, matched = parse_human_date("потом когда-нибудь", NOW, TZ)
    assert value is None
    assert not matched


def test_invalid_date_returns_none() -> None:
    value, matched = parse_human_date("31.02.2026", NOW, TZ)
    assert value is None or not matched


def range_dates(text: str):
    value = parse_human_range(text, NOW, TZ)
    assert value is not None
    return value.start.isoformat(), value.end.isoformat()


def test_next_week_range() -> None:
    assert range_dates("задачи на следующую неделю") == ("2026-10-19", "2026-10-25")


def test_current_week_range() -> None:
    assert range_dates("задачи на этой неделе") == ("2026-10-12", "2026-10-18")


def test_previous_week_range() -> None:
    assert range_dates("задачи на прошлой неделе") == ("2026-10-05", "2026-10-11")


def test_day_range_same_month_name() -> None:
    assert range_dates("задачи с 20 по 27 ноября") == ("2026-11-20", "2026-11-27")
    assert range_dates("задачи 20–27 ноября") == ("2026-11-20", "2026-11-27")


def test_day_range_numeric() -> None:
    assert range_dates("задачи с 20.11 по 27.11") == ("2026-11-20", "2026-11-27")
    assert range_dates("задачи 20.11-27.11") == ("2026-11-20", "2026-11-27")


def test_range_across_months() -> None:
    assert range_dates("задачи с 20 ноября по 5 декабря") == (
        "2026-11-20",
        "2026-12-05",
    )


def test_iso_range() -> None:
    assert range_dates("задачи с 2026-11-20 по 2026-11-27") == (
        "2026-11-20",
        "2026-11-27",
    )


def test_range_with_bare_end_day() -> None:
    assert range_dates("задачи с 20 ноября по 27") == ("2026-11-20", "2026-11-27")


def test_month_range() -> None:
    assert range_dates("задачи на следующем месяце") == ("2026-11-01", "2026-11-30")


def test_no_range_returns_none() -> None:
    assert parse_human_range("задачи на 22.10.2026", NOW, TZ) is None
