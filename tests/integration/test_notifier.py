"""Интеграционные проверки планировщика уведомлений."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from jarvis.domain.enums import TaskBucket, TaskStatus
from jarvis.repositories.tasks import TaskRepository
from jarvis.services.drafts import TaskDraft
from jarvis.services.tasks import TaskService
from jarvis.workers.notifier import run_notifier_once

pytestmark = pytest.mark.integration


def _digest_outside_window(settings) -> str:
    now_local = datetime.now(UTC).astimezone(settings.tz)
    return (now_local + timedelta(hours=1)).strftime("%H:%M")


async def test_due_reminder_sent_once(test_settings, session_factory, session):
    test_settings.notify_enabled = True
    test_settings.notify_due_reminders = True
    test_settings.notify_digest_times = _digest_outside_window(test_settings)

    await TaskService(session).create_from_draft(
        TaskDraft(title="Просроченная задача", due_at=datetime.now(UTC) - timedelta(hours=1))
    )
    await session.commit()

    sent: list[tuple[int, str]] = []

    async def _send(chat_id: int, text: str) -> None:
        sent.append((chat_id, text))

    stats = await run_notifier_once(session_factory, test_settings, _send)
    assert stats["due"] == 1
    assert "Просроченная задача" in sent[0][1]

    sent.clear()
    stats = await run_notifier_once(session_factory, test_settings, _send)
    assert stats["due"] == 0
    assert sent == []


async def test_due_soon_reminder_fires_once_and_rearms_on_move(
    test_settings, session_factory, session
):
    """Напоминание за час: раз на срок; перенос срока перевооружает."""
    test_settings.notify_enabled = True
    test_settings.notify_due_reminders = True
    test_settings.notify_before_due_minutes = 60
    test_settings.notify_digest_times = _digest_outside_window(test_settings)

    service = TaskService(session)
    task_id = await service.create_from_draft(
        TaskDraft(title="Скоро дедлайн", due_at=datetime.now(UTC) + timedelta(minutes=30))
    )
    await session.commit()

    sent: list[str] = []

    async def _send(chat_id: int, text: str) -> None:
        sent.append(text)

    stats = await run_notifier_once(session_factory, test_settings, _send)
    assert stats["due_soon"] == 1
    assert "Срок близко" in sent[0] and "Скоро дедлайн" in sent[0]

    sent.clear()
    stats = await run_notifier_once(session_factory, test_settings, _send)
    assert stats["due_soon"] == 0 and sent == []

    # Перенос срока внутри окна — новое напоминание (ref включает время срока).
    await service.apply_update(task_id, {"due_at": datetime.now(UTC) + timedelta(minutes=45)})
    await session.commit()
    stats = await run_notifier_once(session_factory, test_settings, _send)
    assert stats["due_soon"] == 1 and sent


async def test_task_due_reminder_rearms_on_move(test_settings, session_factory, session):
    """Просроченное напоминание тоже перевооружается при переносе срока."""
    test_settings.notify_enabled = True
    test_settings.notify_due_reminders = True
    test_settings.notify_before_due_minutes = 0  # только пост-срочные
    test_settings.notify_digest_times = _digest_outside_window(test_settings)

    service = TaskService(session)
    task_id = await service.create_from_draft(
        TaskDraft(title="Просроченная", due_at=datetime.now(UTC) - timedelta(minutes=10))
    )
    await session.commit()

    sent: list[str] = []

    async def _send(chat_id: int, text: str) -> None:
        sent.append(text)

    stats = await run_notifier_once(session_factory, test_settings, _send)
    assert stats["due"] == 1

    sent.clear()
    await service.apply_update(task_id, {"due_at": datetime.now(UTC) - timedelta(minutes=5)})
    await session.commit()
    stats = await run_notifier_once(session_factory, test_settings, _send)
    assert stats["due"] == 1  # новый срок — новое напоминание


async def test_reminders_do_not_starve_beyond_per_pass_limit(
    test_settings, session_factory, session
):
    """7 просроченных: за проход не больше 5, остальные — в следующем проходе."""
    test_settings.notify_enabled = True
    test_settings.notify_due_reminders = True
    test_settings.notify_before_due_minutes = 0
    test_settings.notify_digest_times = _digest_outside_window(test_settings)

    service = TaskService(session)
    for index in range(7):
        await service.create_from_draft(
            TaskDraft(
                title=f"Просроченная {index}",
                due_at=datetime.now(UTC) - timedelta(hours=index + 1),
            )
        )
    await session.commit()

    sent: list[str] = []

    async def _send(chat_id: int, text: str) -> None:
        sent.append(text)

    first = await run_notifier_once(session_factory, test_settings, _send)
    assert first["due"] == 5
    second = await run_notifier_once(session_factory, test_settings, _send)
    assert second["due"] == 2  # хвост не голодает
    third = await run_notifier_once(session_factory, test_settings, _send)
    assert third["due"] == 0


async def test_digest_sent_once_per_day(test_settings, session_factory, session):
    test_settings.notify_enabled = True
    test_settings.notify_due_reminders = False
    now_local = datetime.now(UTC).astimezone(test_settings.tz)
    test_settings.notify_digest_times = (now_local - timedelta(minutes=1)).strftime("%H:%M")

    await TaskService(session).create_from_draft(
        TaskDraft(title="Задача на сегодня", bucket=TaskBucket.TODAY)
    )
    await session.commit()

    sent: list[str] = []

    async def _send(chat_id: int, text: str) -> None:
        sent.append(text)

    stats = await run_notifier_once(session_factory, test_settings, _send)
    assert stats["digest"] == 1
    assert any("Задача на сегодня" in text for text in sent)

    sent.clear()
    stats = await run_notifier_once(session_factory, test_settings, _send)
    assert stats["digest"] == 0
    assert sent == []


async def test_multiple_digests_per_day_are_independent(test_settings, session_factory, session):
    """Два времени в окне — две сводки, каждая дедупится отдельно."""
    test_settings.notify_enabled = True
    test_settings.notify_due_reminders = False
    now_local = datetime.now(UTC).astimezone(test_settings.tz)
    first = (now_local - timedelta(minutes=2)).strftime("%H:%M")
    second = (now_local - timedelta(minutes=1)).strftime("%H:%M")
    test_settings.notify_digest_times = f"{first},{second}"

    sent: list[str] = []

    async def _send(chat_id: int, text: str) -> None:
        sent.append(text)

    stats = await run_notifier_once(session_factory, test_settings, _send)
    assert stats["digest"] == 2
    assert len(sent) == 2

    stats = await run_notifier_once(session_factory, test_settings, _send)
    assert stats["digest"] == 0


async def test_evening_digest_shows_closed_remaining_and_tomorrow(
    test_settings, session_factory, session, monkeypatch
):
    """Вечерняя сводка: закрыто сегодня + осталось + завтра."""
    from jarvis.workers import notifier as notifier_module

    monkeypatch.setattr(notifier_module, "EVENING_HOUR", 0)  # вечерний формат в любое время
    test_settings.notify_enabled = True
    test_settings.notify_due_reminders = False
    now_local = datetime.now(UTC).astimezone(test_settings.tz)
    test_settings.notify_digest_times = (now_local - timedelta(minutes=1)).strftime("%H:%M")

    service = TaskService(session)
    done_id = await service.create_from_draft(TaskDraft(title="Закрытая сегодня"))
    await service.apply_update(done_id, {"status": TaskStatus.DONE})
    await service.create_from_draft(TaskDraft(title="Осталась на сегодня", bucket=TaskBucket.TODAY))
    await service.create_from_draft(
        TaskDraft(title="Задача на завтра", due_at=datetime.now(UTC) + timedelta(days=1))
    )
    await session.commit()

    sent: list[str] = []

    async def _send(chat_id: int, text: str) -> None:
        sent.append(text)

    stats = await run_notifier_once(session_factory, test_settings, _send)
    assert stats["digest"] == 1
    text = sent[0]
    assert "Вечерняя сводка" in text
    assert "Закрыто сегодня: 1" in text
    assert "Осталась на сегодня" in text
    assert "Завтра:" in text and "Задача на завтра" in text
    # Закрытая задача в списках не показывается.
    assert "Закрытая сегодня" not in text


async def test_evening_digest_empty_day(test_settings, session_factory, session, monkeypatch):
    from jarvis.workers import notifier as notifier_module

    monkeypatch.setattr(notifier_module, "EVENING_HOUR", 0)
    test_settings.notify_enabled = True
    test_settings.notify_due_reminders = False
    now_local = datetime.now(UTC).astimezone(test_settings.tz)
    test_settings.notify_digest_times = (now_local - timedelta(minutes=1)).strftime("%H:%M")

    sent: list[str] = []

    async def _send(chat_id: int, text: str) -> None:
        sent.append(text)

    stats = await run_notifier_once(session_factory, test_settings, _send)
    assert stats["digest"] == 1
    assert "На сегодня всё закрыто, на завтра задач нет." in sent[0]


async def test_count_completed_between(session):
    service = TaskService(session)
    done_id = await service.create_from_draft(TaskDraft(title="Сделано"))
    await service.apply_update(done_id, {"status": TaskStatus.DONE})
    await session.commit()

    now = datetime.now(UTC)
    count = await TaskRepository(session).count_completed_between(
        now - timedelta(hours=1), now + timedelta(hours=1)
    )
    assert count == 1
    assert await TaskRepository(session).count_completed_between(
        now + timedelta(hours=1), now + timedelta(hours=2)
    ) == 0
