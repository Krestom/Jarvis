"""Интеграционные тесты репозиториев на реальном PostgreSQL."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from jarvis.domain.enums import KnowledgeType, Sensitivity
from jarvis.repositories.knowledge import KnowledgeRepository
from jarvis.repositories.processed_updates import ProcessedUpdateRepository
from jarvis.repositories.tasks import TaskRepository

pytestmark = pytest.mark.integration

TZ = ZoneInfo("Europe/Moscow")
NOW = datetime(2026, 10, 15, 12, 0, tzinfo=TZ)  # четверг


async def test_task_calendar_lists(session):
    repo = TaskRepository(session)
    await repo.create(title="Сегодня", due_at=NOW.replace(hour=18))
    await repo.create(title="Просрочено", due_at=NOW - timedelta(days=2))
    await repo.create(title="Неделя", due_at=(NOW + timedelta(days=2)).replace(hour=10))
    await repo.create(title="Следующая неделя", due_at=(NOW + timedelta(days=10)).replace(hour=9))
    await repo.create(title="Без срока")

    today = {task.title for task in await repo.list_today(NOW, TZ)}
    assert today == {"Сегодня", "Просрочено"}

    week = {task.title for task in await repo.list_week(NOW, TZ)}
    assert week == {"Неделя", "Сегодня"}
    assert "Просрочено" not in week

    assert [t.title for t in await repo.list_no_group()] == ["Без срока"]
    assert {t.title for t in await repo.list_all()} == {
        "Сегодня",
        "Просрочено",
        "Неделя",
        "Следующая неделя",
        "Без срока",
    }


async def test_task_status_filters(session):
    repo = TaskRepository(session)
    await repo.create(title="Готово", status="done")
    await repo.create(title="В работе")

    assert await repo.count_active() == 1
    assert [t.title for t in await repo.list_all()] == ["В работе"]


async def test_knowledge_fulltext_search_russian(session):
    repo = KnowledgeRepository(session)
    await repo.create(
        type=KnowledgeType.INSTRUCTION,
        title="Настройка резервного копирования",
        content="rsync бэкап сервера каждую ночь",
        summary="Как я настраивал бэкапы",
    )
    await repo.create(type=KnowledgeType.NOTE, title="Покупки", content="молоко и хлеб")

    hits = await repo.search("резервное копирование")
    assert len(hits) == 1
    assert hits[0].title == "Настройка резервного копирования"

    assert await repo.search("молоко") != []
    assert await repo.search("совершенно несуществующий текст") == []


async def test_knowledge_sensitivity_filter(session):
    repo = KnowledgeRepository(session)
    await repo.create(
        title="Обычное", content="описание сервера", sensitivity=Sensitivity.NORMAL
    )
    await repo.create(
        title="Чувствительное", content="сервер персональные данные",
        sensitivity=Sensitivity.SENSITIVE,
    )

    all_hits = await repo.search("сервер", allowed_sensitivities=None)
    assert len(all_hits) == 2

    limited = await repo.search("сервер", allowed_sensitivities=[Sensitivity.NORMAL])
    assert [item.title for item in limited] == ["Обычное"]


async def test_processed_updates_dedup(session):
    repo = ProcessedUpdateRepository(session)
    assert await repo.is_processed(777) is False
    assert await repo.mark(777) is True
    assert await repo.mark(777) is False
    assert await repo.is_processed(777) is True


async def test_conversation_state_roundtrip(session, test_settings):
    from jarvis.conversation.state import ConversationStore
    from jarvis.repositories.conversation import ConversationRepository

    store = ConversationStore(ConversationRepository(session), test_settings)
    await store.set(111, "search_wait", data={"query": "бэкап"})
    state = await store.get_active(111)
    assert state is not None
    assert state.data["query"] == "бэкап"
    await store.clear(111)
    assert await store.get_active(111) is None


async def test_expired_state_is_cleared(session, test_settings):
    from datetime import UTC

    from jarvis.conversation.state import ConversationStore
    from jarvis.repositories.conversation import ConversationRepository

    repo = ConversationRepository(session)
    store = ConversationStore(repo, test_settings)
    await repo.upsert(
        222,
        flow="search_wait",
        step=None,
        data={},
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    assert await store.get_active(222) is None
    assert await repo.get(222) is None
