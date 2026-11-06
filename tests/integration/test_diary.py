"""Личный дневник: несколько записей в день, правка, удаление и переносы."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from jarvis.repositories.diary import DiaryRepository, DiarySnoozeRepository

pytestmark = pytest.mark.integration

DAY = date(2026, 11, 6)


async def test_diary_multiple_entries_edit_and_delete(session):
    repo = DiaryRepository(session)
    first = await repo.create(
        111,
        "Первая мысль",
        entry_date=DAY,
        now=datetime(2026, 11, 6, 10, 0, tzinfo=UTC),
    )
    second = await repo.create(
        111,
        "Вторая мысль",
        entry_date=DAY,
        now=datetime(2026, 11, 6, 12, 0, tzinfo=UTC),
    )

    assert [entry.content for entry in await repo.list_day(111, DAY)] == [
        "Первая мысль",
        "Вторая мысль",
    ]
    assert await repo.has_entry(111, DAY)
    assert not await repo.has_entry(111, DAY + timedelta(days=1))

    await repo.update_content(first, "Исправленная мысль")
    fresh = await repo.get(111, first.id)
    assert fresh is not None
    assert fresh.content == "Исправленная мысль"
    assert fresh.created_at == first.created_at

    await repo.delete(second)
    assert [entry.content for entry in await repo.list_day(111, DAY)] == [
        "Исправленная мысль"
    ]


async def test_diary_snooze_schedule_due_reset_and_delete(session):
    repo = DiarySnoozeRepository(session)
    now = datetime(2026, 11, 6, 16, 31, tzinfo=UTC)
    snooze = await repo.schedule(111, DAY, now + timedelta(hours=1))

    assert await repo.due(111, now) == []
    due = await repo.due(111, now + timedelta(hours=1))
    assert [row.id for row in due] == [snooze.id]

    await repo.mark_sent(snooze, now)
    assert await repo.due(111, now + timedelta(hours=2)) == []

    await repo.reset(snooze.id)
    assert [row.id for row in await repo.due(111, now + timedelta(hours=2))] == [
        snooze.id
    ]

    await repo.delete_for_date(111, DAY)
    assert await repo.due(111, now + timedelta(hours=3)) == []
