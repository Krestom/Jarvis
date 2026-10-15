"""Интеграционные тесты реестра инструментов агента."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from jarvis.agent.tools import ToolRegistry
from jarvis.db.models import PendingOperation
from jarvis.domain.enums import (
    ImprovementCategory,
    ImprovementSource,
    ImprovementStatus,
    PendingOpType,
    TaskStatus,
)
from jarvis.repositories.comments import TaskCommentRepository
from jarvis.repositories.improvements import ImprovementRepository
from jarvis.repositories.knowledge import KnowledgeRepository
from jarvis.repositories.projects import ProjectRepository
from jarvis.repositories.tasks import TaskRepository
from jarvis.research.fake import FakeSearchProvider
from jarvis.research.none import NoneSearchProvider
from jarvis.research.provider import WebSearchResult
from jarvis.services.drafts import TaskDraft
from jarvis.services.tasks import TaskService

pytestmark = pytest.mark.integration

USER = 111


def make_registry(session, test_settings, provider=None) -> ToolRegistry:
    return ToolRegistry(
        session,
        test_settings,
        telegram_user_id=USER,
        search_provider=provider or NoneSearchProvider(),
    )


async def test_list_tasks_returns_real_items_and_candidates(session, test_settings):
    await TaskService(session).create_from_draft(TaskDraft(title="Починить сервер"))
    await TaskService(session).create_from_draft(
        TaskDraft(title="Обновить документацию", bucket=None)
    )
    registry = make_registry(session, test_settings)

    result = await registry.call("list_tasks", {"view": "no_group"})
    assert result.ok is True
    titles = {item["title"] for item in result.data["items"]}
    assert titles == {"Починить сервер", "Обновить документацию"}
    # Кандидаты для контекста/кнопок собираются только из реальных данных.
    assert {candidate.title for candidate in registry.surfaced} == titles


async def test_validation_not_found_and_unknown_tool_errors(session, test_settings):
    registry = make_registry(session, test_settings)

    bad_view = await registry.call("list_tasks", {"view": "nope"})
    assert bad_view.ok is False
    assert bad_view.error_kind == "validation"

    missing = await registry.call("get_task", {"task_id": str(uuid.uuid4())})
    assert missing.ok is False
    assert missing.error_kind == "not_found"

    unknown = await registry.call("drop_database", {})
    assert unknown.ok is False
    assert unknown.error_kind == "validation"
    assert "неизвестный инструмент" in (unknown.error_message or "")


async def test_mutations_go_through_domain_services(session, test_settings):
    registry = make_registry(session, test_settings)
    repo = TaskRepository(session)

    created = await registry.call(
        "create_task",
        {"title": "Обновить инфраструктуру", "project": "SuperSport", "priority": "high"},
    )
    assert created.ok is True
    task_id = uuid.UUID(created.data["task"]["id"])
    task = await repo.get(task_id)
    assert task is not None and task.title == "Обновить инфраструктуру"
    assert await ProjectRepository(session).find_by_name("SuperSport") is not None

    updated = await registry.call(
        "update_task", {"task_id": str(task_id), "status": "done"}
    )
    assert updated.ok is True
    assert updated.data["task"]["status"] == TaskStatus.DONE.value

    commented = await registry.call(
        "add_comment", {"task_id": str(task_id), "content": "Проверил конфигурацию"}
    )
    assert commented.ok is True
    comments = await TaskCommentRepository(session).list_for_task(task_id)
    assert [comment.content for comment in comments] == ["Проверил конфигурацию"]


async def test_delete_task_only_sends_confirmation(session, test_settings):
    task_id = await TaskService(session).create_from_draft(TaskDraft(title="Удалить меня"))
    registry = make_registry(session, test_settings)

    result = await registry.call("delete_task", {"task_id": str(task_id)})
    assert result.ok is True
    assert result.data["status"] == "confirmation_required"
    assert registry.pending_operation_id is not None

    # Задача не удалена: ждём подтверждения пользователя.
    assert await TaskRepository(session).get(task_id) is not None
    operations = (
        await session.execute(
            select(PendingOperation).where(
                PendingOperation.operation_type == PendingOpType.DELETE_TASK
            )
        )
    ).scalars().all()
    assert len(operations) == 1
    assert operations[0].payload == {"entity_id": str(task_id)}


async def test_web_search_returns_results_or_honest_unavailable(session, test_settings):
    provider = FakeSearchProvider()
    provider.queue(
        [WebSearchResult(title="Дока", url="https://example.com/docs", snippet="short")]
    )
    registry = make_registry(session, test_settings, provider=provider)
    found = await registry.call("web_search", {"query": "настройка nginx"})
    assert found.ok is True
    assert found.data["results"][0]["url"] == "https://example.com/docs"
    assert provider.calls == ["настройка nginx"]

    disabled = make_registry(session, test_settings)
    unavailable = await disabled.call("web_search", {"query": "настройка nginx"})
    assert unavailable.ok is False
    assert unavailable.error_kind == "unsupported"


async def test_report_capability_gap_creates_backlog_entry_and_dedups(
    session, test_settings
):
    registry = make_registry(session, test_settings)
    first = await registry.call(
        "report_capability_gap",
        {
            "title": "Нет экспорта задач в календарь",
            "summary": "Пользователь просил выгрузить задачи в календарь; такой возможности нет",
            "evidence": "«выгрузи задачи в Google Calendar»",
        },
    )
    assert first.ok is True
    item_id = first.data["item_id"]
    item = await ImprovementRepository(session).get(uuid.UUID(item_id))
    assert item.source == ImprovementSource.USER_OBSERVATION
    assert item.category == ImprovementCategory.MISSING_CAPABILITY
    assert "Google Calendar" in item.diagnostic_context["evidence"]

    # Повтор не создаёт дубликат.
    second = await registry.call(
        "report_capability_gap",
        {"title": "Нет экспорта задач в календарь", "summary": "Снова просили календарь"},
    )
    assert second.data["item_id"] == item_id
    assert second.data["occurrence_count"] == 2
    assert await ImprovementRepository(session).count_items() == 1


async def test_list_improvements_shows_backlog_with_filters(session, test_settings):
    from jarvis.services.improvements import ImprovementService

    service = ImprovementService(session, test_settings)
    gap = await service.record_gap(
        title="Нет экспорта задач в календарь",
        summary="Просили выгрузить задачи в календарь",
    )
    failure = await service.record_failure(title="Сбой отправки", summary="Кнопка упала")
    registry = make_registry(session, test_settings)

    open_items = await registry.call("list_improvements", {})
    assert open_items.ok is True
    titles = {item["title"] for item in open_items.data["items"]}
    assert titles == {"Нет экспорта задач в календарь", "Сбой отправки"}
    assert open_items.data["counts_by_status"]["new"] == 2

    by_category = await registry.call(
        "list_improvements", {"category": "missing_capability"}
    )
    assert [item["id"] for item in by_category.data["items"]] == [str(gap.id)]

    # После закрытия записи она не в «открытых», но видна в all.
    await ImprovementRepository(session).update_status(
        failure, ImprovementStatus.IMPLEMENTED
    )
    opened = await registry.call("list_improvements", {})
    assert {item["title"] for item in opened.data["items"]} == {
        "Нет экспорта задач в календарь"
    }
    everything = await registry.call("list_improvements", {"status": "all"})
    assert len(everything.data["items"]) == 2

    bad = await registry.call("list_improvements", {"status": "nonsense"})
    assert bad.ok is False and bad.error_kind == "validation"


async def test_create_knowledge_tool_needs_confirmation_and_flags_secrets(
    session, test_settings
):
    from jarvis.services.confirmation import ConfirmationService

    registry = make_registry(session, test_settings)
    result = await registry.call(
        "create_knowledge",
        {
            "title": "Доступ к серверу Supersport",
            "content": "ssh 2.28.205.186, user deploy, пароль supersport",
            "project": "Supersport",
        },
    )
    assert result.ok is True
    assert result.data["status"] == "confirmation_required"
    assert result.data["sensitivity"] == "sensitive"  # определили креды сами
    assert registry.pending_operation_id is not None

    # До подтверждения записи нет.
    assert await KnowledgeRepository(session).list_recent() == []

    confirmation = ConfirmationService(session, test_settings)
    outcome = await confirmation.confirm(USER, registry.pending_operation_id)
    assert outcome.ok is True

    items = await KnowledgeRepository(session).list_recent()
    assert len(items) == 1
    assert items[0].title == "Доступ к серверу Supersport"
    assert items[0].sensitivity.value == "sensitive"
    assert items[0].project_id is not None

    # Обычный текст без секретов остаётся normal.
    plain = await registry.call(
        "create_knowledge", {"title": "Мысль о бэкапах", "content": "Проверить rsync ночью"}
    )
    assert plain.data["sensitivity"] == "normal"
