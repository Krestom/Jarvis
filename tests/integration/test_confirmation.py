"""Интеграционные тесты подтверждения операций."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from jarvis.db.models import AuditLog, Task
from jarvis.domain.enums import PendingOpType, PendingStatus, TaskBucket
from jarvis.repositories.categories import CategoryRepository
from jarvis.repositories.pending import PendingOperationRepository
from jarvis.repositories.tasks import TaskRepository
from jarvis.services.confirmation import ConfirmationService
from jarvis.services.drafts import TaskDraft

pytestmark = pytest.mark.integration

USER = 111


def task_payload(title: str = "Сделать бэкап") -> dict:
    return TaskDraft(title=title).model_dump(mode="json")


async def test_confirm_creates_task_and_is_idempotent(session, test_settings):
    service = ConfirmationService(session, test_settings)
    operation_id = await service.propose(USER, PendingOpType.CREATE_TASK, task_payload())

    first = await service.confirm(USER, operation_id)
    assert first.ok and not first.already

    second = await service.confirm(USER, operation_id)
    assert second.ok and second.already

    count = await session.scalar(select(func.count()).select_from(Task))
    assert count == 1

    audit_count = await session.scalar(select(func.count()).select_from(AuditLog))
    assert audit_count == 1  # аудит пишется один раз, при фактическом применении


async def test_cancelled_operation_cannot_be_confirmed(session, test_settings):
    service = ConfirmationService(session, test_settings)
    operation_id = await service.propose(USER, PendingOpType.CREATE_TASK, task_payload())

    cancelled = await service.cancel(USER, operation_id)
    assert cancelled.ok

    result = await service.confirm(USER, operation_id)
    assert not result.ok

    count = await session.scalar(select(func.count()).select_from(Task))
    assert count == 0


async def test_other_user_cannot_confirm(session, test_settings):
    service = ConfirmationService(session, test_settings)
    operation_id = await service.propose(USER, PendingOpType.CREATE_TASK, task_payload())

    result = await service.confirm(999, operation_id)
    assert not result.ok
    assert await session.scalar(select(func.count()).select_from(Task)) == 0


async def test_expired_operation_is_not_applied(session, test_settings):
    repo = PendingOperationRepository(session)
    expired = await repo.create(
        USER,
        PendingOpType.CREATE_TASK,
        task_payload(),
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    result = await ConfirmationService(session, test_settings).confirm(USER, expired.id)
    assert not result.ok
    assert "истекл" in result.summary.lower()
    assert await session.scalar(select(func.count()).select_from(Task)) == 0

    await session.refresh(expired)
    assert expired.status == PendingStatus.EXPIRED


async def test_update_task_applies_changes(session, test_settings):
    task = await TaskRepository(session).create(title="Срок")
    task_id = task.id

    service = ConfirmationService(session, test_settings)
    operation_id = await service.propose(
        USER,
        PendingOpType.UPDATE_TASK,
        {"task_id": str(task_id), "changes": {"bucket": TaskBucket.WEEK.value}},
    )
    result = await service.confirm(USER, operation_id)
    assert result.ok

    task = await TaskRepository(session).get(task_id)
    assert task is not None
    assert task.bucket == TaskBucket.WEEK


async def test_failed_apply_is_reported_and_recorded(session, test_settings):
    repo = CategoryRepository(session)
    await repo.create("Работа")

    service = ConfirmationService(session, test_settings)
    operation_id = await service.propose(
        USER, PendingOpType.CREATE_CATEGORY, {"name": "Работа"}
    )
    result = await service.confirm(USER, operation_id)
    assert not result.ok
    assert "существует" in result.summary

    pending_repo = PendingOperationRepository(session)
    operation = await pending_repo.get(__import__("uuid").UUID(operation_id))
    assert operation is not None
    assert operation.status == PendingStatus.FAILED
