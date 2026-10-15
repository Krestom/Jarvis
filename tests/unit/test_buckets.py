from datetime import datetime
from zoneinfo import ZoneInfo

from jarvis.domain.buckets import Placement, local_date, place_task, week_bounds, week_window
from jarvis.domain.enums import TaskBucket, TaskStatus

TZ = ZoneInfo("Europe/Moscow")


def now_thursday() -> datetime:
    # 2026-10-15 — четверг, ISO-неделя 12–18 октября.
    return datetime(2026, 10, 15, 12, 0, tzinfo=TZ)


def due(year: int, month: int, day: int, hour: int = 18) -> datetime:
    return datetime(year, month, day, hour, 0, tzinfo=TZ)


def test_week_bounds() -> None:
    monday, sunday = week_bounds(now_thursday().date())
    assert monday.isoformat() == "2026-10-12"
    assert sunday.isoformat() == "2026-10-18"


def test_no_bucket_no_due_lives_outside_lists() -> None:
    placement = place_task(
        status=TaskStatus.TODO, bucket=None, due_at=None, now=now_thursday(), tz=TZ
    )
    assert placement == Placement(overdue=False, today=False, this_week=False)


def test_bucket_today_is_in_today_list() -> None:
    placement = place_task(
        status=TaskStatus.TODO, bucket=TaskBucket.TODAY, due_at=None, now=now_thursday(), tz=TZ
    )
    assert placement.today


def test_due_yesterday_is_overdue_and_today() -> None:
    placement = place_task(
        status=TaskStatus.TODO,
        bucket=None,
        due_at=due(2026, 10, 14),
        now=now_thursday(),
        tz=TZ,
    )
    assert placement.overdue
    assert placement.today


def test_due_today_is_today() -> None:
    placement = place_task(
        status=TaskStatus.TODO,
        bucket=None,
        due_at=due(2026, 10, 15, hour=23),
        now=now_thursday(),
        tz=TZ,
    )
    assert not placement.overdue
    assert placement.today


def test_due_within_week_is_week() -> None:
    for day in (16, 17, 18):
        placement = place_task(
            status=TaskStatus.TODO,
            bucket=None,
            due_at=due(2026, 10, day),
            now=now_thursday(),
            tz=TZ,
        )
        assert placement.this_week, day
        assert not placement.today


def test_due_next_monday_is_not_week() -> None:
    placement = place_task(
        status=TaskStatus.TODO,
        bucket=None,
        due_at=due(2026, 10, 19),
        now=now_thursday(),
        tz=TZ,
    )
    assert not placement.this_week
    assert not placement.today


def test_bucket_week_is_week() -> None:
    placement = place_task(
        status=TaskStatus.TODO, bucket=TaskBucket.WEEK, due_at=None, now=now_thursday(), tz=TZ
    )
    assert placement.this_week


def test_bucket_week_with_due_today_is_only_today() -> None:
    placement = place_task(
        status=TaskStatus.TODO,
        bucket=TaskBucket.WEEK,
        due_at=due(2026, 10, 15),
        now=now_thursday(),
        tz=TZ,
    )
    assert placement.today
    assert not placement.this_week


def test_bucket_long_term_not_in_dynamic_lists() -> None:
    placement = place_task(
        status=TaskStatus.TODO, bucket=TaskBucket.LONG_TERM, due_at=None, now=now_thursday(), tz=TZ
    )
    assert placement == Placement(overdue=False, today=False, this_week=False)


def test_done_task_leaves_all_lists() -> None:
    placement = place_task(
        status=TaskStatus.DONE,
        bucket=TaskBucket.TODAY,
        due_at=due(2026, 10, 10),
        now=now_thursday(),
        tz=TZ,
    )
    assert placement == Placement(overdue=False, today=False, this_week=False)


def test_sunday_late_due_still_this_week() -> None:
    placement = place_task(
        status=TaskStatus.TODO,
        bucket=None,
        due_at=datetime(2026, 10, 18, 23, 59, tzinfo=TZ),
        now=now_thursday(),
        tz=TZ,
    )
    assert placement.this_week


def test_next_week_monday_midnight_not_this_week() -> None:
    placement = place_task(
        status=TaskStatus.TODO,
        bucket=None,
        due_at=datetime(2026, 10, 19, 0, 0, tzinfo=TZ),
        now=now_thursday(),
        tz=TZ,
    )
    assert not placement.this_week


def test_week_window_boundaries() -> None:
    end_today, end_week = week_window(now_thursday(), TZ)
    assert end_today == datetime(2026, 10, 16, 0, 0, tzinfo=TZ)
    assert end_week == datetime(2026, 10, 19, 0, 0, tzinfo=TZ)


def test_local_date_converts_utc_to_user_tz() -> None:
    utc_moment = datetime(2026, 10, 15, 22, 30, tzinfo=ZoneInfo("UTC"))
    assert local_date(utc_moment, TZ).isoformat() == "2026-10-16"


def test_weekend_now() -> None:
    # В воскресенье «на этой неделе» список схлопывается: остаются только сегодня.
    sunday = datetime(2026, 10, 18, 10, 0, tzinfo=TZ)
    placement = place_task(
        status=TaskStatus.TODO,
        bucket=None,
        due_at=due(2026, 10, 18, hour=20),
        now=sunday,
        tz=TZ,
    )
    assert placement.today
    assert not placement.this_week
