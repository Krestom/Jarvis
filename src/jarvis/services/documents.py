"""Сервис документов: staging → подтверждение → выдача/удаление файлов."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.config.settings import Settings
from jarvis.documents.storage import EncryptedFileStorage, StorageError
from jarvis.domain.enums import DocumentStatus
from jarvis.repositories.categories import CategoryRepository
from jarvis.repositories.documents import DocumentRepository
from jarvis.services.drafts import DocumentDraft
from jarvis.services.errors import NotFoundError, OperationError

log = logging.getLogger(__name__)

ALLOWED_DOCUMENT_MIME_PREFIXES = (
    "image/",
    "application/pdf",
    "application/zip",
    "application/msword",
    "application/vnd.openxmlformats-officedocument",
    "text/plain",
    "application/octet-stream",
)


class DocumentService:
    def __init__(self, session: AsyncSession, settings: Settings, storage: EncryptedFileStorage):
        self._session = session
        self._settings = settings
        self._storage = storage
        self._repo = DocumentRepository(session)
        self._categories = CategoryRepository(session)

    def validate_upload(self, size_bytes: int, mime_type: str | None) -> None:
        if size_bytes <= 0:
            raise OperationError("Пустой файл — нечего сохранять")
        if size_bytes > self._settings.max_document_bytes:
            limit_mb = self._settings.max_document_bytes // (1024 * 1024)
            raise OperationError(f"Файл больше лимита {limit_mb} МБ")
        if mime_type and not any(
            mime_type.startswith(prefix) for prefix in ALLOWED_DOCUMENT_MIME_PREFIXES
        ):
            raise OperationError(f"Тип файла не поддерживается: {mime_type}")

    async def stage_upload(
        self,
        *,
        data: bytes,
        original_filename: str | None,
        mime_type: str | None,
    ):
        """Зашифровать файл и создать PENDING-запись (ещё не подтверждено)."""
        self.validate_upload(len(data), mime_type)
        info = self._storage.save(data, staging=True)
        title = (original_filename or "Документ").rsplit(".", 1)[0][:300] or "Документ"
        document = await self._repo.create(
            title=title,
            storage_key=info.storage_key,
            original_filename=original_filename,
            mime_type=mime_type,
            size_bytes=info.size_bytes,
            sha256=info.sha256,
            encryption_version=info.key_version,
            status=DocumentStatus.PENDING,
        )
        return document

    async def confirm_save(self, draft: DocumentDraft) -> Document:  # noqa: F821
        document = await self._repo.get(draft.document_id)
        if document is None:
            raise NotFoundError("Документ не найден (возможно, устарел)")
        if document.status == DocumentStatus.ACTIVE:
            return document  # идемпотентно
        if draft.category_id is not None and (
            await self._categories.get(draft.category_id) is None
        ):
            raise OperationError("Категория не найдена")
        self._storage.promote(document.storage_key)
        await self._repo.update(
            document,
            title=draft.title,
            document_type=draft.document_type,
            category_id=draft.category_id,
            sensitivity=draft.sensitivity,
            status=DocumentStatus.ACTIVE,
        )
        return document

    async def discard_staged(self, document_id: uuid.UUID) -> None:
        document = await self._repo.get(document_id)
        if document is None:
            return
        self._storage.delete(document.storage_key)
        await self._repo.hard_delete(document)

    async def read_file(self, document_id: uuid.UUID) -> tuple[Document, bytes]:  # noqa: F821
        document = await self._repo.get(document_id)
        if document is None:
            raise NotFoundError("Документ не найден")
        try:
            data = self._storage.verify(document.storage_key, document.sha256)
        except StorageError as error:
            log.error("document %s integrity problem: %s", document.id, error)
            raise OperationError("Файл повреждён или недоступен") from error
        return document, data

    async def delete(self, document_id: uuid.UUID) -> uuid.UUID:
        document = await self._repo.get(document_id)
        if document is None:
            raise NotFoundError("Документ не найден")
        self._storage.delete(document.storage_key)
        await self._repo.soft_delete(document)
        return document.id

    async def purge_stale_pending(self, older_than) -> int:
        """Убрать неподтверждённые документы и их файлы (вызывается воркером)."""
        purged = 0
        for document in await self._repo.list_pending_older_than(older_than):
            self._storage.delete(document.storage_key)
            await self._repo.hard_delete(document)
            purged += 1
        return purged
