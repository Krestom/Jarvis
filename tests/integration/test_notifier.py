"""Интеграционные проверки планировщика уведомлений."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from jarvis.domain.enums import TaskBucket
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
    test_settings.notify_digest_time = _digest_outside_window(test_settings)

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


async def test_digest_sent_once_per_day(test_settings, session_factory, session):
    test_settings.notify_enabled = True
    test_settings.notify_due_reminders = False
    now_local = datetime.now(UTC).astimezone(test_settings.tz)
    test_settings.notify_digest_time = (now_local - timedelta(minutes=1)).strftime("%H:%M")

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
