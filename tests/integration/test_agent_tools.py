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


async def test_list_tasks_on_date_and_category_and_create_category(session, test_settings):
    from datetime import UTC, datetime, timedelta

    from jarvis.repositories.categories import CategoryRepository

    category = await CategoryRepository(session).create("Рабочие")
    due = datetime.now(UTC) + timedelta(days=2)
    await TaskService(session).create_from_draft(TaskDraft(title="Визит", due_at=due))
    await TaskService(session).create_from_draft(
        TaskDraft(title="Отчёт", category_id=category.id)
    )
    registry = make_registry(session, test_settings)

    by_date = await registry.call("list_tasks", {"on_date": due.date().isoformat()})
    assert by_date.ok is True
    assert [item["title"] for item in by_date.data["items"]] == ["Визит"]

    by_category = await registry.call("list_tasks", {"category": "рабочие"})
    assert by_category.ok is True
    assert by_category.data["category"] == "Рабочие"
    assert [item["title"] for item in by_category.data["items"]] == ["Отчёт"]

    missing = await registry.call("list_tasks", {"category": "Нет такой"})
    assert missing.ok is False
    assert missing.error_kind == "not_found"

    created = await registry.call("create_category", {"name": "Дом"})
    assert created.ok is True
    assert created.data["status"] == "confirmation_required"
    assert registry.pending_operation_ids

    again = await registry.call("create_category", {"name": "дом"})
    assert again.ok is True
    assert again.data["status"] == "confirmation_required"
    assert again.data["note"] == (
        "категория уже предложена на подтверждение ранее в этом диалоге"
    )
    # Второго предложения не появилось.
    assert len(registry.pending_operation_ids) == 1


async def test_create_task_with_category(session, test_settings):
    from jarvis.repositories.categories import CategoryRepository
    from jarvis.services.confirmation import ConfirmationService

    category = await CategoryRepository(session).create("Рабочие")
    registry = make_registry(session, test_settings)

    result = await registry.call("create_task", {"title": "Согласовать", "category": "Рабочие"})
    assert result.ok is True
    assert result.data["status"] == "confirmation_required"
    # До подтверждения задачи нет.
    assert await TaskRepository(session).search("Согласовать") == []

    # Категории нет — система сама предлагает её создать и готовит задачу в неё.
    missing = await registry.call("create_task", {"title": "X", "category": "Нет"})
    assert missing.ok is True
    assert missing.data["status"] == "confirmation_required"
    assert missing.data["category"] == "Нет"
    assert len(registry.pending_operation_ids) == 3  # «Согласовать» + категория + задача

    confirmation = ConfirmationService(session, test_settings)
    # Подтверждаем задачу раньше категории: категория создаётся при применении.
    task_op = registry.pending_operation_ids[-1]
    assert (await confirmation.confirm(USER, task_op)).ok is True
    tasks = await TaskRepository(session).search("X")
    created_category = await CategoryRepository(session).find_by_name("Нет")
    assert created_category is not None
    assert tasks and tasks[0].category_id == created_category.id

    # Подтверждение категории после этого идемпотентно (она уже создана задачей).
    assert (await confirmation.confirm(USER, registry.pending_operation_ids[1])).ok is True

    # Подтверждение создаёт задачу с уже существующей категорией.
    outcome = await confirmation.confirm(USER, registry.pending_operation_ids[0])
    assert outcome.ok is True
    tasks = await TaskRepository(session).search("Согласовать")
    assert tasks and tasks[0].category_id == category.id


async def test_create_project_and_list_comments(session, test_settings):
    from jarvis.services.confirmation import ConfirmationService

    registry = make_registry(session, test_settings)
    proposed = await registry.call("create_project", {"name": "Летний ремонт"})
    assert proposed.ok is True
    assert proposed.data["status"] == "confirmation_required"
    assert await ProjectRepository(session).find_by_name("Летний ремонт") is None

    await ConfirmationService(session, test_settings).confirm(
        USER, registry.pending_operation_ids[-1]
    )
    assert await ProjectRepository(session).find_by_name("Летний ремонт") is not None

    again = await registry.call("create_project", {"name": "Летний ремонт"})
    assert again.ok is True
    assert again.data["note"] == "проект уже существует"

    task_id = await TaskService(session).create_from_draft(TaskDraft(title="Проверить вытяжку"))
    await TaskCommentRepository(session).create(task_id, "Позвонить мастеру")
    listed = await registry.call("list_comments", {"task": "вытяжк"})
    assert listed.ok is True
    assert listed.data["title"] == "Проверить вытяжку"
    assert [comment["content"] for comment in listed.data["comments"]] == ["Позвонить мастеру"]

    missing = await registry.call("list_comments", {"task": "такой задачи нет"})
    assert missing.ok is False
    assert missing.error_kind == "not_found"


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
    from jarvis.services.confirmation import ConfirmationService

    registry = make_registry(session, test_settings)
    repo = TaskRepository(session)
    confirmation = ConfirmationService(session, test_settings)
    task_id = await TaskService(session).create_from_draft(TaskDraft(title="Готовая задача"))

    updated = await registry.call("update_task", {"task_id": str(task_id), "status": "done"})
    assert updated.ok is True
    assert updated.data["status"] == "confirmation_required"
    assert (await repo.get(task_id)).status != TaskStatus.DONE  # ещё не применено

    commented = await registry.call(
        "add_comment", {"task_id": str(task_id), "content": "Проверил конфигурацию"}
    )
    assert commented.ok is True
    assert commented.data["status"] == "confirmation_required"

    # Каждая операция ждёт своей кнопки; подтверждаем обе.
    assert len(registry.pending_operation_ids) == 2
    for op_id in registry.pending_operation_ids:
        outcome = await confirmation.confirm(USER, op_id)
        assert outcome.ok is True

    task = await repo.get(task_id)
    assert task.status == TaskStatus.DONE
    comments = await TaskCommentRepository(session).list_for_task(task_id)
    assert [comment.content for comment in comments] == ["Проверил конфигурацию"]

    # Проект создаётся при применении create_task (как в детерминированном пути).
    created = await registry.call(
        "create_task",
        {"title": "Обновить инфраструктуру", "project": "SuperSport", "priority": "high"},
    )
    assert created.ok is True
    outcome = await confirmation.confirm(USER, registry.pending_operation_ids[-1])
    assert outcome.ok is True
    assert await ProjectRepository(session).find_by_name("SuperSport") is not None


async def test_update_task_can_clear_due_date(session, test_settings):
    from datetime import UTC, datetime, timedelta

    from jarvis.services.confirmation import ConfirmationService

    due = datetime.now(UTC) + timedelta(days=3)
    task_id = await TaskService(session).create_from_draft(TaskDraft(title="Со сроком", due_at=due))
    registry = make_registry(session, test_settings)

    result = await registry.call("update_task", {"task_id": str(task_id), "due_at": None})
    assert result.ok is True
    assert result.data["status"] == "confirmation_required"

    outcome = await ConfirmationService(session, test_settings).confirm(
        USER, registry.pending_operation_ids[0]
    )
    assert outcome.ok is True
    assert (await TaskRepository(session).get(task_id)).due_at is None


async def test_update_knowledge_tool_edits_even_sensitive_notes(session, test_settings):
    from jarvis.domain.enums import KnowledgeSource, KnowledgeType, Sensitivity
    from jarvis.services.confirmation import ConfirmationService

    repo = KnowledgeRepository(session)
    item = await repo.create(
        type=KnowledgeType.NOTE,
        title="Пароль от сервера",
        content="старый пароль",
        source_type=KnowledgeSource.TEXT,
        sensitivity=Sensitivity.SENSITIVE,
    )
    registry = make_registry(session, test_settings)

    result = await registry.call(
        "update_knowledge",
        {
            "item_id": str(item.id),
            "title": "Пароль от сервера (обновлён)",
            "content": "новый пароль",
        },
    )
    assert result.ok is True
    assert result.data["status"] == "confirmation_required"
    # До подтверждения содержимое не менялось.
    assert (await repo.get(item.id)).content == "старый пароль"

    outcome = await ConfirmationService(session, test_settings).confirm(
        USER, registry.pending_operation_ids[0]
    )
    assert outcome.ok is True
    updated = await repo.get(item.id)
    assert updated.title == "Пароль от сервера (обновлён)"
    assert updated.content == "новый пароль"
    assert updated.sensitivity == Sensitivity.SENSITIVE

    # Ошибки: несуществующая запись, пустые изменения, неизвестная категория.
    missing = await registry.call("update_knowledge", {"item_id": str(uuid.uuid4())})
    assert missing.ok is False and missing.error_kind == "not_found"
    empty = await registry.call("update_knowledge", {"item_id": str(item.id)})
    assert empty.ok is False and empty.error_kind == "validation"
    bad_category = await registry.call(
        "update_knowledge", {"item_id": str(item.id), "category": "Нет такой"}
    )
    assert bad_category.ok is False and bad_category.error_kind == "not_found"


async def test_delete_task_only_sends_confirmation(session, test_settings):
    task_id = await TaskService(session).create_from_draft(TaskDraft(title="Удалить меня"))
    registry = make_registry(session, test_settings)

    result = await registry.call("delete_task", {"task_id": str(task_id)})
    assert result.ok is True
    assert result.data["status"] == "confirmation_required"
    assert registry.pending_operation_ids

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
    assert registry.pending_operation_ids

    # До подтверждения записи нет.
    assert await KnowledgeRepository(session).list_recent() == []

    confirmation = ConfirmationService(session, test_settings)
    outcome = await confirmation.confirm(USER, registry.pending_operation_ids[0])
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
