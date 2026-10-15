"""Интеграционные тесты журнала чата и дневного экспорта."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from jarvis.db.models import ChatMessage
from jarvis.repositories.chatlog import ChatLogRepository
from jarvis.services.chatlog import ChatLogService, export_chat_day

pytestmark = pytest.mark.integration

USER = 111


async def test_record_redacts_secrets_and_skips_empty(session, test_settings):
    service = ChatLogService(session, test_settings)
    await service.record(USER, direction="in", text="  ")
    await service.record(
        USER,
        direction="in",
        text="мой пароль password=hunter2secret",
        source="text",
        correlation_id="corr-1",
    )
    await service.record(USER, direction="out", text="Запомнил.", source="bot")

    rows = (
        await session.execute(
            select(ChatMessage).order_by(ChatMessage.created_at, ChatMessage.id)
        )
    ).scalars().all()
    assert len(rows) == 2
    assert "hunter2secret" not in rows[0].text
    assert "[REDACTED" in rows[0].text
    assert rows[0].correlation_id == "corr-1"
    assert rows[1].direction == "out"


async def test_record_disabled_by_setting(session, test_settings):
    test_settings.chat_log_enabled = False
    await ChatLogService(session, test_settings).record(
        USER, direction="in", text="ничего не записываем"
    )
    count = await session.scalar(select(func.count()).select_from(ChatMessage))
    assert count == 0


async def test_day_export_contains_both_directions_without_secrets(
    session, test_settings
):
    service = ChatLogService(session, test_settings)
    await service.record(USER, direction="in", text="/start", source="command")
    await service.record(
        USER, direction="in", text="сохрани пароль password=topsecret", source="text"
    )
    await service.record(USER, direction="out", text="Готово.", source="bot")
    # Сообщение за другой день не попадает в выгрузку.
    repo = ChatLogRepository(session)
    await repo.create(
        USER,
        direction="in",
        source="text",
        text="вчерашнее",
        created_at=datetime.now(UTC) - timedelta(days=2),
    )

    day = datetime.now(test_settings.tz).date()
    result = await export_chat_day(session, test_settings, day)
    assert result.message_count == 3
    assert result.truncated is False
    assert "Пользователь (команда):" in result.markdown
    assert "Jarvis:" in result.markdown
    assert "topsecret" not in result.markdown
    assert "[REDACTED" in result.markdown

    payload = json.loads(result.json_text)
    assert payload["count"] == 3
    assert {message["direction"] for message in payload["messages"]} == {"in", "out"}
    assert "topsecret" not in result.json_text
    assert payload["date"] == day.isoformat()
    assert payload["timezone"] == test_settings.app_timezone


async def test_day_export_counts_and_truncation_flag(session, test_settings):
    service = ChatLogService(session, test_settings)
    await service.record(USER, direction="in", text="одно")
    day = datetime.now(test_settings.tz).date()
    repo = ChatLogRepository(session)
    assert await repo.count_for_period(
        datetime.combine(day, datetime.min.time(), test_settings.tz),
        datetime.combine(day, datetime.min.time(), test_settings.tz) + timedelta(days=1),
    ) == 1
    result = await export_chat_day(session, test_settings, day)
    assert result.message_count == 1
    assert result.truncated is False


async def test_retention_deletes_old_messages(session, test_settings):
    repo = ChatLogRepository(session)
    old = await repo.create(
        USER,
        direction="in",
        source="text",
        text="старое",
        created_at=datetime.now(UTC) - timedelta(days=40),
    )
    fresh = await repo.create(USER, direction="in", source="text", text="свежее")

    removed = await repo.delete_older_than(datetime.now(UTC) - timedelta(days=30))
    assert removed == 1
    remaining = (await session.execute(select(ChatMessage))).scalars().all()
    assert [row.id for row in remaining] == [fresh.id]
    assert old.id not in [row.id for row in remaining]
