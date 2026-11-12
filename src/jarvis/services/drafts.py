"""Валидируемые черновики операций.

Payload pending-операции — сериализованный черновик. Валидация происходит
дважды: при предложении операции и при её применении (backend не доверяет
сохранённому payload).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from jarvis.domain.enums import (
    DocumentType,
    KnowledgeSource,
    KnowledgeType,
    SecretType,
    Sensitivity,
    TaskStatus,
)


class TaskDraft(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str | None = None
    original_text: str | None = None
    status: TaskStatus = TaskStatus.TODO
    due_at: datetime | None = None


class TaskUpdate(BaseModel):
    """Частичное обновление: применяются только явно переданные поля."""

    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = None
    status: TaskStatus | None = None
    due_at: datetime | None = None


class CommentDraft(BaseModel):
    task_id: uuid.UUID
    content: str = Field(min_length=1, max_length=4000)


class CommentUpdate(BaseModel):
    comment_id: uuid.UUID
    task_id: uuid.UUID  # для аудита и навигации
    content: str = Field(min_length=1, max_length=4000)


class KnowledgeDraft(BaseModel):
    type: KnowledgeType = KnowledgeType.NOTE
    title: str = Field(min_length=1, max_length=300)
    summary: str | None = None
    content: str = Field(min_length=1)
    source_type: KnowledgeSource = KnowledgeSource.TEXT
    original_text: str | None = None
    sensitivity: Sensitivity = Sensitivity.NORMAL


class KnowledgeUpdate(BaseModel):
    type: KnowledgeType | None = None
    title: str | None = Field(default=None, min_length=1, max_length=300)
    summary: str | None = None
    content: str | None = Field(default=None, min_length=1)
    sensitivity: Sensitivity | None = None


class DocumentDraft(BaseModel):
    """Подтверждение уже загруженного (зашифрованного) документа."""

    document_id: uuid.UUID
    title: str = Field(min_length=1, max_length=300)
    document_type: DocumentType = DocumentType.OTHER
    sensitivity: Sensitivity = Sensitivity.SENSITIVE


class DocumentUpdate(BaseModel):
    """Изменение метаданных документа (название, чувствительность)."""

    document_id: uuid.UUID
    title: str | None = Field(default=None, min_length=1, max_length=300)
    sensitivity: Sensitivity | None = None


class SecretCreateDraft(BaseModel):
    """Значение секрета шифруется до сохранения в payload pending-операции."""

    name: str = Field(min_length=1, max_length=200)
    purpose: str | None = None
    secret_type: SecretType = SecretType.OTHER
    encrypted_value_b64: str
    key_version: int


class EntityRef(BaseModel):
    """Ссылка на сущность для операций удаления."""

    entity_id: uuid.UUID
