from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.config.settings import Settings
from jarvis.domain.enums import EntityType, PendingOpType, Relation
from jarvis.repositories.categories import CategoryRepository
from jarvis.repositories.links import LinkRepository
from jarvis.repositories.tasks import TaskRepository
from jarvis.services.drafts import (
    CategoryDraft,
    CategoryRename,
    DocumentDraft,
    DocumentUpdate,
    EntityRef,
    KnowledgeDraft,
    KnowledgeUpdate,
    LinkDraft,
    ProjectDraft,
    SecretCreateDraft,
    SecretUpdate,
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

    if operation_type == PendingOpType.CREATE_CATEGORY:
        draft = CategoryDraft.model_validate(payload)
        repo = CategoryRepository(session)
        if await repo.find_by_name(draft.name) is not None:
            raise OperationError("Категория с таким названием уже существует")
        category = await repo.create(draft.name)
        return AppliedOperation(
            EntityType.CATEGORY, category.id, f"Категория создана: {draft.name}"
        )

    if operation_type == PendingOpType.CREATE_PROJECT:
        from jarvis.repositories.projects import ProjectRepository

        draft = ProjectDraft.model_validate(payload)
        repo = ProjectRepository(session)
        if await repo.find_by_name(draft.name) is not None:
            raise OperationError("Проект с таким названием уже существует")
        project = await repo.create(name=draft.name, description=draft.description)
        return AppliedOperation(EntityType.PROJECT, project.id, f"Проект создан: {draft.name}")

    if operation_type == PendingOpType.RENAME_CATEGORY:
        rename = CategoryRename.model_validate(payload)
        repo = CategoryRepository(session)
        category = await repo.get(rename.category_id)
        if category is None:
            raise NotFoundError("Категория не найдена")
        await repo.rename(category, rename.new_name)
        return AppliedOperation(
            EntityType.CATEGORY, category.id, f"Категория переименована: {rename.new_name}"
        )

    if operation_type == PendingOpType.CREATE_LINK:
        draft = LinkDraft.model_validate(payload)
        await LinkRepository(session).add(
            draft.from_type, draft.from_id, draft.to_type, draft.to_id, draft.relation
        )
        return AppliedOperation(None, None, "Связь создана")

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
        if update.category_id is not None and (
            await CategoryRepository(session).get(update.category_id) is None
        ):
            raise OperationError("Категория не найдена")
        await repo.update(document, category_id=update.category_id)
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

    if operation_type == PendingOpType.UPDATE_SECRET:
        from jarvis.services.secrets import SecretService

        secret_id = uuid.UUID(payload["secret_id"])
        changes = SecretUpdate.model_validate(payload.get("changes", {})).model_dump(
            exclude_unset=True
        )
        await SecretService(session, settings).apply_update(secret_id, changes)
        return AppliedOperation(EntityType.SECRET, secret_id, "Секрет обновлён")

    if operation_type == PendingOpType.DELETE_SECRET:
        from jarvis.services.secrets import SecretService

        ref = EntityRef.model_validate(payload)
        await SecretService(session, settings).soft_delete(ref.entity_id)
        return AppliedOperation(EntityType.SECRET, ref.entity_id, "Секрет удалён")

    raise OperationError(f"Неподдерживаемый тип операции: {operation_type}")


def utcnow() -> datetime:
    return datetime.now(UTC)


# Реэкспорт для удобства вызовов из engine/handlers.
__all__ = ["AppliedOperation", "apply_operation", "utcnow", "Relation"]
