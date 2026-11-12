from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.config.settings import Settings
from jarvis.domain.enums import EntityType, PendingOpType
from jarvis.repositories.tasks import TaskRepository
from jarvis.services.drafts import (
    DocumentDraft,
    DocumentUpdate,
    EntityRef,
    KnowledgeDraft,
    KnowledgeUpdate,
    SecretCreateDraft,
    TaskDraft,
    TaskUpdate,
)
from jarvis.services.errors import NotFoundError, OperationError
from jarvis.services.knowledge import KnowledgeService
from jarvis.services.tasks import TaskService


class AppliedOperation:
    def __init__(
        self,
        entity_type: EntityType | None,
        entity_id: uuid.UUID | None,
        summary: str,
    ) -> None:
        self.entity_type = entity_type
        self.entity_id = entity_id
        self.summary = summary


async def apply_operation(
    session: AsyncSession,
    settings: Settings,
    operation_type: PendingOpType,
    payload: dict,
) -> AppliedOperation:
    """Применить подтверждённую операцию. Payload валидируется заново."""
    if operation_type == PendingOpType.CREATE_TASK:
        payload = dict(payload)
        draft = TaskDraft.model_validate(payload)
        task_id = await TaskService(session).create_from_draft(draft)
        return AppliedOperation(EntityType.TASK, task_id, f"Задача создана: {draft.title}")

    if operation_type == PendingOpType.UPDATE_TASK:
        task_id = uuid.UUID(payload["task_id"])
        changes = TaskUpdate.model_validate(payload.get("changes", {})).model_dump(
            exclude_unset=True
        )
        await TaskService(session).apply_update(task_id, changes)
        return AppliedOperation(EntityType.TASK, task_id, "Задача обновлена")

    if operation_type == PendingOpType.DELETE_TASK:
        ref = EntityRef.model_validate(payload)
        await TaskService(session).soft_delete(ref.entity_id)
        return AppliedOperation(EntityType.TASK, ref.entity_id, "Задача удалена")

    if operation_type == PendingOpType.CREATE_COMMENT:
        from jarvis.repositories.comments import TaskCommentRepository
        from jarvis.services.drafts import CommentDraft

        draft = CommentDraft.model_validate(payload)
        task = await TaskRepository(session).get(draft.task_id)
        if task is None:
            raise NotFoundError("Задача не найдена")
        await TaskCommentRepository(session).create(draft.task_id, draft.content)
        return AppliedOperation(
            EntityType.TASK, draft.task_id, f"Комментарий добавлен к «{task.title}»"
        )

    if operation_type == PendingOpType.UPDATE_COMMENT:
        from jarvis.repositories.comments import TaskCommentRepository
        from jarvis.services.drafts import CommentUpdate

        update = CommentUpdate.model_validate(payload)
        comments = TaskCommentRepository(session)
        comment = await comments.get(update.comment_id)
        if comment is None:
            raise NotFoundError("Комментарий не найден")
        await comments.update(comment, update.content)
        return AppliedOperation(EntityType.TASK, update.task_id, "Комментарий обновлён")

    if operation_type == PendingOpType.CREATE_KNOWLEDGE:
        draft = KnowledgeDraft.model_validate(payload)
        item_id = await KnowledgeService(session).create_from_draft(draft)
        return AppliedOperation(EntityType.KNOWLEDGE, item_id, f"Запись сохранена: {draft.title}")

    if operation_type == PendingOpType.UPDATE_KNOWLEDGE:
        item_id = uuid.UUID(payload["item_id"])
        changes = KnowledgeUpdate.model_validate(payload.get("changes", {})).model_dump(
            exclude_unset=True
        )
        await KnowledgeService(session).apply_update(item_id, changes)
        return AppliedOperation(EntityType.KNOWLEDGE, item_id, "Запись обновлена")

    if operation_type == PendingOpType.DELETE_KNOWLEDGE:
        ref = EntityRef.model_validate(payload)
        await KnowledgeService(session).soft_delete(ref.entity_id)
        return AppliedOperation(EntityType.KNOWLEDGE, ref.entity_id, "Запись удалена")

    if operation_type == PendingOpType.SAVE_DOCUMENT:
        from jarvis.documents.storage import EncryptedFileStorage
        from jarvis.services.documents import DocumentService

        draft = DocumentDraft.model_validate(payload)
        storage = EncryptedFileStorage(
            settings.document_storage_path, settings.document_keyring()
        )
        service = DocumentService(session, settings, storage)
        document = await service.confirm_save(draft)
        return AppliedOperation(
            EntityType.DOCUMENT, document.id, f"Документ сохранён: {draft.title}"
        )

    if operation_type == PendingOpType.UPDATE_DOCUMENT:
        from jarvis.repositories.documents import DocumentRepository

        update = DocumentUpdate.model_validate(payload)
        repo = DocumentRepository(session)
        document = await repo.get(update.document_id)
        if document is None:
            raise NotFoundError("Документ не найден")
        # Меняем только явно переданные поля: title и/или sensitivity —
        # payload задаёт ровно то, что меняется.
        fields: dict = {}
        if update.title is not None:
            fields["title"] = update.title
        if update.sensitivity is not None:
            fields["sensitivity"] = update.sensitivity
        if fields:
            await repo.update(document, **fields)
        return AppliedOperation(EntityType.DOCUMENT, document.id, "Документ обновлён")

    if operation_type == PendingOpType.DELETE_DOCUMENT:
        from jarvis.documents.storage import EncryptedFileStorage
        from jarvis.services.documents import DocumentService

        ref = EntityRef.model_validate(payload)
        storage = EncryptedFileStorage(
            settings.document_storage_path, settings.document_keyring()
        )
        await DocumentService(session, settings, storage).delete(ref.entity_id)
        return AppliedOperation(EntityType.DOCUMENT, ref.entity_id, "Документ удалён")

    if operation_type == PendingOpType.CREATE_SECRET:
        from jarvis.services.secrets import SecretService

        draft = SecretCreateDraft.model_validate(payload)
        secret_id = await SecretService(session, settings).create_from_draft(draft)
        return AppliedOperation(EntityType.SECRET, secret_id, f"Секрет сохранён: {draft.name}")

    if operation_type == PendingOpType.DELETE_SECRET:
        from jarvis.services.secrets import SecretService

        ref = EntityRef.model_validate(payload)
        await SecretService(session, settings).delete(ref.entity_id)
        return AppliedOperation(EntityType.SECRET, ref.entity_id, "Секрет удалён")

    raise OperationError(f"Неподдерживаемый тип операции: {operation_type}")


__all__ = ["AppliedOperation", "apply_operation"]
