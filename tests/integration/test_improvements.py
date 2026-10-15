"""Интеграционные тесты improvement backlog (Stage 2, P1)."""

from __future__ import annotations

import uuid

import pytest

from jarvis.ai.schemas import ImprovementHypothesis
from jarvis.domain.enums import (
    ImprovementCategory,
    ImprovementSource,
    ImprovementStatus,
)
from jarvis.repositories.improvements import ImprovementRepository
from jarvis.services.errors import NotFoundError, OperationError
from jarvis.services.improvements import ImprovementService

pytestmark = pytest.mark.integration


async def test_record_failure_deduplicates_by_signature(session, test_settings):
    service = ImprovementService(session, test_settings)
    repo = ImprovementRepository(session)

    first = await service.record_failure(
        title="ValueError: callback data too long",
        summary="Кнопка не отправилась",
        component="telegram_bot",
        correlation_id="corr-1",
        error_type="ValueError",
    )
    assert first.source == ImprovementSource.TECHNICAL_EVENT
    assert first.category == ImprovementCategory.FAILURE
    assert first.status == ImprovementStatus.NEW
    assert first.occurrence_count == 1
    assert first.confidence is None
    assert first.diagnostic_context["correlation_id"] == "corr-1"
    assert first.diagnostic_context["error_type"] == "ValueError"
    assert len(await repo.list_events(first.id)) == 1

    # Повтор той же проблемы — событие, а не дубликат.
    second = await service.record_failure(
        title="valueerror: Callback Data Too Long",
        summary="Снова не отправилась",
        component="telegram_bot",
        correlation_id="corr-2",
    )
    assert second.id == first.id
    assert second.occurrence_count == 2
    assert len(second.source_event_ids) == 2
    events = await repo.list_events(first.id)
    assert [event.correlation_id for event in events] == ["corr-1", "corr-2"]

    # Другой компонент — отдельная запись.
    other = await service.record_failure(
        title="ValueError: callback data too long",
        summary="Из MCP",
        component="mcp",
    )
    assert other.id != first.id
    assert await repo.count_items() == 2


async def test_diagnostic_context_is_redacted_and_truncated(session, test_settings):
    service = ImprovementService(session, test_settings)
    item = await service.record_failure(
        title="RuntimeError: boom",
        summary="Сбой",
        error_type="RuntimeError",
        traceback_text=(
            "RuntimeError: boom\n"
            "Authorization: Bearer abcdef0123456789abcdef\n"
            "password=hunter2\n"
            + "x" * (test_settings.improvement_context_max_chars * 2)
        ),
    )
    trace = item.diagnostic_context["traceback"]
    assert "abcdef0123456789abcdef" not in trace
    assert "hunter2" not in trace
    assert "[REDACTED" in trace
    assert len(trace) <= test_settings.improvement_context_max_chars


async def test_hypothesis_respects_confidence_threshold(session, test_settings):
    service = ImprovementService(session, test_settings)

    weak = ImprovementHypothesis(
        category="ux_friction",
        title="Слишком много подтверждений",
        summary="Пользователю кажется, что подтверждений слишком много",
        confidence=test_settings.improvement_hypothesis_min_confidence - 0.05,
    )
    assert await service.record_hypothesis(weak) is None
    assert await ImprovementRepository(session).count_items() == 0

    strong = ImprovementHypothesis(
        category="missing_capability",
        title="Нет напоминаний по срокам",
        summary="Пользователь ожидает напоминания о дедлайнах",
        confidence=0.9,
        evidence="«ты не напомнил мне про задачу»",
    )
    item = await service.record_hypothesis(strong, correlation_id="hyp-1")
    assert item is not None
    assert item.source == ImprovementSource.LLM_HYPOTHESIS
    assert item.category == ImprovementCategory.MISSING_CAPABILITY
    assert item.confidence == 0.9
    assert item.component is None
    assert "напомнил" in item.diagnostic_context["evidence"]

    # Повтор: счётчик растёт, достоверность — максимум из наблюдённых.
    again = await service.record_hypothesis(strong.model_copy(update={"confidence": 0.95}))
    assert again.id == item.id
    assert again.occurrence_count == 2
    assert again.confidence == 0.95
    assert await ImprovementRepository(session).count_items() == 1


async def test_merge_moves_events_and_archives_sources(session, test_settings):
    service = ImprovementService(session, test_settings)
    repo = ImprovementRepository(session)

    target = await service.record_failure(title="Ошибка A", summary="Первый случай")
    source = await service.record_failure(title="Ошибка B", summary="Похожий случай")
    source_id = source.id

    merged = await service.merge(target.id, [source_id, target.id])

    assert str(source_id) in merged.merged_from
    assert merged.occurrence_count == 2
    archived = await repo.get(source_id)
    assert archived.status == ImprovementStatus.ARCHIVED
    assert archived.meta["merged_into"] == str(target.id)
    # События источника переехали к цели, свидетельство сохранено.
    assert len(await repo.list_events(target.id)) == 2
    assert await repo.list_events(source_id) == []

    # Ошибки merge различаются по типу.
    with pytest.raises(NotFoundError):
        await service.merge(uuid.uuid4(), [source_id])
    with pytest.raises(OperationError):
        await service.merge(target.id, [target.id])


async def test_list_filters_counts_and_status_changes(session, test_settings):
    service = ImprovementService(session, test_settings)
    repo = ImprovementRepository(session)

    failure = await service.record_failure(title="Ошибка A", summary="A")
    idea = await service.record_failure(
        title="Идея B",
        summary="B",
        category=ImprovementCategory.IMPROVEMENT_IDEA,
    )

    assert await repo.count_items(status=ImprovementStatus.NEW) == 2
    assert await repo.count_items(category=ImprovementCategory.IMPROVEMENT_IDEA) == 1
    assert await repo.counts_by_status() == {ImprovementStatus.NEW: 2}

    items = await repo.list_items(category=ImprovementCategory.IMPROVEMENT_IDEA)
    assert [item.id for item in items] == [idea.id]

    await repo.update_status(failure, ImprovementStatus.REVIEWED)
    assert await repo.counts_by_status() == {
        ImprovementStatus.NEW: 1,
        ImprovementStatus.REVIEWED: 1,
    }

    await repo.hard_delete(idea)
    assert await repo.count_items() == 1
