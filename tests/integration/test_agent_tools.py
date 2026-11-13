"""Интеграционные тесты реестра инструментов агента."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

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
from jarvis.repositories.tasks import TaskRepository
from jarvis.research.fake import FakeSearchProvider
from jarvis.research.none import NoneSearchProvider
from jarvis.research.provider import WebSearchResult
from jarvis.services.drafts import TaskDraft
from jarvis.services.tasks import TaskService
from jarvis.weather.provider import (
    CurrentWeather,
    DailyWeather,
    HourlyPoint,
    WeatherSnapshot,
)

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
        TaskDraft(title="Обновить документацию")
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

    # Обычная задача без проекта и приоритета.
    created = await registry.call(
        "create_task",
        {"title": "Обновить инфраструктуру"},
    )
    assert created.ok is True
    outcome = await confirmation.confirm(USER, registry.pending_operation_ids[-1])
    assert outcome.ok is True


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


async def test_create_task_naive_due_at_uses_user_timezone(session, test_settings):
    from jarvis.services.confirmation import ConfirmationService

    registry = make_registry(session, test_settings)
    result = await registry.call(
        "create_task",
        {"title": "Позвонить в банк", "due_at": "2026-12-01T18:00:00"},
    )
    assert result.ok is True

    outcome = await ConfirmationService(session, test_settings).confirm(
        USER, registry.pending_operation_ids[0]
    )
    assert outcome.ok is True
    task = await TaskRepository(session).get(uuid.UUID(outcome.entity_id))
    assert task.due_at.astimezone(test_settings.tz).strftime("%H:%M") == "18:00"


async def test_daily_stats_counts_and_lists(session, test_settings):
    from datetime import datetime

    from jarvis.services.confirmation import ConfirmationService
    from jarvis.services.improvements import ImprovementService

    registry = make_registry(session, test_settings)
    confirmation = ConfirmationService(session, test_settings)

    created = await registry.call("create_task", {"title": "Сделано за день"})
    assert created.ok is True
    outcome = await confirmation.confirm(USER, registry.pending_operation_ids[-1])
    assert outcome.ok is True
    task_id = outcome.entity_id

    updated = await registry.call(
        "update_task", {"task_id": task_id, "status": "done"}
    )
    assert updated.ok is True
    assert (await confirmation.confirm(USER, registry.pending_operation_ids[-1])).ok
    await ImprovementService(session, test_settings).record_gap(
        title="Новый пробел", summary="Чего-то не хватает"
    )

    stats = await registry.call("daily_stats", {})
    assert stats.ok is True
    assert stats.data["date"] == datetime.now(test_settings.tz).date().isoformat()
    assert stats.data["actions"]["confirm:create_task"] == 1
    assert stats.data["actions"]["confirm:update_task"] == 1
    assert stats.data["tasks_completed"]["count"] == 1
    assert stats.data["tasks_completed"]["items"] == ["Сделано за день"]
    assert stats.data["new_improvements"]["count"] == 1
    assert stats.data["new_improvements"]["items"] == [
        {"title": "Новый пробел", "category": "missing_capability"}
    ]


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

    # Ошибки: несуществующая запись и пустые изменения.
    missing = await registry.call("update_knowledge", {"item_id": str(uuid.uuid4())})
    assert missing.ok is False and missing.error_kind == "not_found"
    empty = await registry.call("update_knowledge", {"item_id": str(item.id)})
    assert empty.ok is False and empty.error_kind == "validation"


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
            "content": "ssh 203.0.113.10, user testuser, пароль not-a-real-password",
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

    # Обычный текст без секретов остаётся normal.
    plain = await registry.call(
        "create_knowledge", {"title": "Мысль о бэкапах", "content": "Проверить rsync ночью"}
    )
    assert plain.data["sensitivity"] == "normal"


async def test_list_tasks_all_view_returns_every_active(session, test_settings):
    from datetime import UTC, datetime, timedelta

    from jarvis.domain.enums import TaskStatus

    await TaskService(session).create_from_draft(
        TaskDraft(title="На неделе", due_at=datetime.now(UTC) + timedelta(days=2))
    )
    await TaskService(session).create_from_draft(TaskDraft(title="Без срока"))
    done_id = await TaskService(session).create_from_draft(TaskDraft(title="Сделано"))
    await TaskService(session).apply_update(done_id, {"status": TaskStatus.DONE})
    registry = make_registry(session, test_settings)

    result = await registry.call("list_tasks", {"view": "all"})
    assert result.ok is True
    titles = {item["title"] for item in result.data["items"]}
    assert titles == {"На неделе", "Без срока"}


async def test_send_document_tool_reads_encrypted_file(session, test_settings):
    from jarvis.documents.storage import EncryptedFileStorage
    from jarvis.services.documents import DocumentService

    storage = EncryptedFileStorage(
        test_settings.document_storage_path, test_settings.document_keyring()
    )
    document = await DocumentService(session, test_settings, storage).stage_upload(
        data=b"%PDF-1.4 test bytes",
        original_filename="passport.pdf",
        mime_type="application/pdf",
    )
    registry = make_registry(session, test_settings)

    result = await registry.call("send_document", {"document_id": str(document.id)})
    assert result.ok is True
    assert result.data["filename"] == "passport.pdf"
    assert len(registry.outgoing_files) == 1
    assert registry.outgoing_files[0].data == b"%PDF-1.4 test bytes"

    missing = await registry.call("send_document", {"document_id": str(uuid.uuid4())})
    assert missing.ok is False and missing.error_kind == "not_found"


async def test_update_document_sensitivity_needs_confirmation(session, test_settings):
    from jarvis.documents.storage import EncryptedFileStorage
    from jarvis.repositories.documents import DocumentRepository
    from jarvis.services.confirmation import ConfirmationService
    from jarvis.services.documents import DocumentService

    storage = EncryptedFileStorage(
        test_settings.document_storage_path, test_settings.document_keyring()
    )
    document = await DocumentService(session, test_settings, storage).stage_upload(
        data=b"bytes", original_filename="file.pdf", mime_type="application/pdf"
    )
    registry = make_registry(session, test_settings)

    result = await registry.call(
        "update_document", {"document_id": str(document.id), "sensitive": False}
    )
    assert result.ok is True
    assert result.data["status"] == "confirmation_required"
    # До подтверждения чувствительность не меняется.
    assert (await DocumentRepository(session).get(document.id)).sensitivity.value == "sensitive"

    await ConfirmationService(session, test_settings).confirm(
        USER, registry.pending_operation_ids[-1]
    )
    assert (await DocumentRepository(session).get(document.id)).sensitivity.value == "normal"

    missing = await registry.call(
        "update_document", {"document_id": str(uuid.uuid4()), "sensitive": True}
    )
    assert missing.ok is False and missing.error_kind == "not_found"


async def test_update_document_rename(session, test_settings):
    from jarvis.documents.storage import EncryptedFileStorage
    from jarvis.services.confirmation import ConfirmationService
    from jarvis.services.documents import DocumentService

    storage = EncryptedFileStorage(
        test_settings.document_storage_path, test_settings.document_keyring()
    )
    document = await DocumentService(session, test_settings, storage).stage_upload(
        data=b"bytes", original_filename="file.pdf", mime_type="application/pdf"
    )
    registry = make_registry(session, test_settings)

    result = await registry.call(
        "update_document", {"document_id": str(document.id), "title": "Регалетта"}
    )
    assert result.ok is True
    assert result.data["changes"] == ["title"]
    await ConfirmationService(session, test_settings).confirm(
        USER, registry.pending_operation_ids[-1]
    )
    assert document.title == "Регалетта"

    empty = await registry.call("update_document", {"document_id": str(document.id)})
    assert empty.ok is False and empty.error_kind == "validation"


async def test_export_issues_tool_queues_files(session, test_settings):
    from jarvis.services.improvements import ImprovementService

    await ImprovementService(session, test_settings).record_gap(
        title="Нет плана на отпуск", summary="Просили выгрузить бэклог файлом"
    )
    registry = make_registry(session, test_settings)

    result = await registry.call("export_issues", {})
    assert result.ok is True
    assert result.data["item_count"] == 1
    names = [file.filename for file in registry.outgoing_files]
    assert len(names) == 2
    assert names[0].startswith("jarvis_issues_") and names[0].endswith(".md")
    assert names[1].endswith(".json")
    assert "Нет плана на отпуск" in registry.outgoing_files[0].data.decode("utf-8")

    bad = await registry.call("export_issues", {"status": "nonsense"})
    assert bad.ok is False and bad.error_kind == "validation"


async def test_delete_document_and_knowledge_tools(session, test_settings):
    from jarvis.documents.storage import EncryptedFileStorage
    from jarvis.domain.enums import KnowledgeSource, KnowledgeType
    from jarvis.repositories.documents import DocumentRepository
    from jarvis.services.confirmation import ConfirmationService
    from jarvis.services.documents import DocumentService

    storage = EncryptedFileStorage(
        test_settings.document_storage_path, test_settings.document_keyring()
    )
    document = await DocumentService(session, test_settings, storage).stage_upload(
        data=b"bytes", original_filename="file.pdf", mime_type="application/pdf"
    )
    item = await KnowledgeRepository(session).create(
        type=KnowledgeType.NOTE,
        title="Старая заметка",
        content="Текст",
        source_type=KnowledgeSource.TEXT,
    )
    registry = make_registry(session, test_settings)
    confirmation = ConfirmationService(session, test_settings)

    doc_op = await registry.call("delete_document", {"document_id": str(document.id)})
    assert doc_op.ok is True and doc_op.data["status"] == "confirmation_required"
    knowledge_op = await registry.call("delete_knowledge", {"item_id": str(item.id)})
    assert knowledge_op.ok is True

    await confirmation.confirm(USER, registry.pending_operation_ids[0])
    await confirmation.confirm(USER, registry.pending_operation_ids[1])
    assert await DocumentRepository(session).list_active() == []
    assert await KnowledgeRepository(session).get(item.id) is None

    missing = await registry.call("delete_document", {"document_id": str(uuid.uuid4())})
    assert missing.ok is False and missing.error_kind == "not_found"


async def test_update_comment_tool(session, test_settings):
    from jarvis.repositories.comments import TaskCommentRepository
    from jarvis.services.confirmation import ConfirmationService

    task_id = await TaskService(session).create_from_draft(TaskDraft(title="Починить сервер"))
    comments = TaskCommentRepository(session)
    comment = await comments.create(task_id, "Позвонить в сервис")
    registry = make_registry(session, test_settings)

    result = await registry.call(
        "update_comment",
        {"comment_id": str(comment.id), "content": "Написать в сервис письмом"},
    )
    assert result.ok is True and result.data["status"] == "confirmation_required"
    assert comment.content == "Позвонить в сервис"  # до подтверждения не меняется

    await ConfirmationService(session, test_settings).confirm(
        USER, registry.pending_operation_ids[-1]
    )
    assert comment.content == "Написать в сервис письмом"

    missing = await registry.call(
        "update_comment", {"comment_id": str(uuid.uuid4()), "content": "текст"}
    )
    assert missing.ok is False and missing.error_kind == "not_found"


async def test_update_improvement_tool_changes_status_directly(session, test_settings):
    from jarvis.services.improvements import ImprovementService

    item = await ImprovementService(session, test_settings).record_gap(
        title="Нет календаря", summary="Хочется календарь"
    )
    registry = make_registry(session, test_settings)

    result = await registry.call(
        "update_improvement", {"item_id": str(item.id), "status": "planned"}
    )
    assert result.ok is True
    assert result.data["status"] == "planned"
    assert item.status.value == "planned"
    # Без кнопки подтверждения — pending-операций не появилось.
    assert registry.pending_operation_ids == []

    bad = await registry.call(
        "update_improvement", {"item_id": str(item.id), "status": "nonsense"}
    )
    assert bad.ok is False and bad.error_kind == "validation"


class _FakeWeather:
    name = "fake"

    async def fetch(self) -> WeatherSnapshot:
        tz = ZoneInfo("Europe/Moscow")
        now = datetime(2026, 11, 13, 8, 0, tzinfo=tz)
        return WeatherSnapshot(
            city="Москва",
            current=CurrentWeather(-3.4, -8.1, 78, 0.0, 71, 5.0, 9.0, 240, 1012.5, True),
            hourly=tuple(
                HourlyPoint(
                    now + timedelta(hours=index),
                    -3.0 - index,
                    20 + index * 5,
                    0.0,
                    71,
                    5.0,
                    True,
                )
                for index in range(8)
            ),
            daily=(
                DailyWeather(
                    now.date(),
                    71,
                    -1.0,
                    -6.2,
                    now.replace(hour=7, minute=58),
                    now.replace(hour=16, minute=20),
                    70,
                    6.0,
                ),
            ),
            fetched_at=now,
        )


async def test_get_weather_returns_snapshot(session, test_settings):
    registry = ToolRegistry(
        session,
        test_settings,
        telegram_user_id=USER,
        search_provider=NoneSearchProvider(),
        weather_provider=_FakeWeather(),
    )

    result = await registry.call("get_weather", {})

    assert result.ok is True
    assert result.data["city"] == "Москва"
    assert result.data["condition"] == "Небольшой снег"
    assert len(result.data["next_hours"]) == 6


async def test_get_weather_without_provider_is_unsupported(session, test_settings):
    registry = make_registry(session, test_settings)

    result = await registry.call("get_weather", {})

    assert result.ok is False
    assert result.error_kind == "unsupported"
