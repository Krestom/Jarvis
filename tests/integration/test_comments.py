"""Интеграционные тесты комментариев к задачам (через подтверждение операции)."""

from __future__ import annotations

import uuid

import pytest

from jarvis.domain.enums import PendingOpType
from jarvis.repositories.comments import TaskCommentRepository
from jarvis.repositories.tasks import TaskRepository
from jarvis.services.confirmation import ConfirmationService

pytestmark = pytest.mark.integration

USER = 111


async def test_create_and_update_comment_via_confirmation(session, test_settings):
    task = await TaskRepository(session).create(title="Настроить бэкап")
    service = ConfirmationService(session, test_settings)

    create_op = await service.propose(
        USER,
        PendingOpType.CREATE_COMMENT,
        {"task_id": str(task.id), "content": "Позвонить в поддержку"},
    )
    result = await service.confirm(USER, create_op)
    assert result.ok
    assert "Настроить бэкап" in result.summary

    comments = await TaskCommentRepository(session).list_for_task(task.id)
    assert [comment.content for comment in comments] == ["Позвонить в поддержку"]

    # повторное подтверждение не создаёт дубль
    second = await service.confirm(USER, create_op)
    assert second.ok and second.already
    assert await TaskCommentRepository(session).count_for_task(task.id) == 1

    update_op = await service.propose(
        USER,
        PendingOpType.UPDATE_COMMENT,
        {
            "comment_id": str(comments[0].id),
            "task_id": str(task.id),
            "content": "Позвонить и написать письмо",
        },
    )
    updated = await service.confirm(USER, update_op)
    assert updated.ok
    await session.refresh(comments[0])
    assert comments[0].content == "Позвонить и написать письмо"


async def test_comment_for_missing_task_fails_cleanly(session, test_settings):
    service = ConfirmationService(session, test_settings)
    operation = await service.propose(
        USER,
        PendingOpType.CREATE_COMMENT,
        {"task_id": str(uuid.uuid4()), "content": "текст"},
    )
    result = await service.confirm(USER, operation)
    assert not result.ok
    assert "не найдена" in result.summary.lower()


async def test_update_missing_comment_fails_cleanly(session, test_settings):
    task = await TaskRepository(session).create(title="Задача")
    service = ConfirmationService(session, test_settings)
    operation = await service.propose(
        USER,
        PendingOpType.UPDATE_COMMENT,
        {"comment_id": str(uuid.uuid4()), "task_id": str(task.id), "content": "новый"},
    )
    result = await service.confirm(USER, operation)
    assert not result.ok
    assert "не найден" in result.summary.lower()
