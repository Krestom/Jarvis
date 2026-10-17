"""ORM-модели Jarvis (PostgreSQL).

Enum'ы хранятся как VARCHAR без CHECK-ограничений: значения валидируются
на уровне Python, а развитие списка значений не требует ALTER CONSTRAINT.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum as PyEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Enum as SAEnum
from sqlalchemy.types import Uuid

from jarvis.db.base import Base, SoftDeleteMixin, TimestampMixin
from jarvis.domain.enums import (
    DocumentStatus,
    DocumentType,
    EntityType,
    ImprovementCategory,
    ImprovementSource,
    ImprovementStatus,
    KnowledgeSource,
    KnowledgeStatus,
    KnowledgeType,
    PendingOpType,
    PendingStatus,
    ProjectStatus,
    SecretType,
    Sensitivity,
    TaskBucket,
    TaskPriority,
    TaskStatus,
)


def _enum(enum_cls: type[PyEnum], name: str) -> SAEnum:
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=False,
        length=32,
        create_constraint=False,
        validate_strings=True,
        values_callable=lambda cls: [member.value for member in cls],
    )


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


_ALL_ENTITY_TYPES = [member.value for member in EntityType]


# --- Категории и проекты -----------------------------------------------------


class Category(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "categories"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), nullable=True
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (Index("ix_categories_parent", "parent_id"),)


class Project(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[ProjectStatus] = mapped_column(
        _enum(ProjectStatus, "project_status"), nullable=False, default=ProjectStatus.ACTIVE
    )
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), nullable=True
    )
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (Index("ix_projects_category", "category_id"),)


# --- Знания ------------------------------------------------------------------


class KnowledgeItem(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "knowledge_items"

    id: Mapped[uuid.UUID] = _uuid_pk()
    type: Mapped[KnowledgeType] = mapped_column(
        _enum(KnowledgeType, "knowledge_type"), nullable=False, default=KnowledgeType.NOTE
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[KnowledgeSource] = mapped_column(
        _enum(KnowledgeSource, "knowledge_source"),
        nullable=False,
        default=KnowledgeSource.MANUAL,
    )
    original_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), nullable=True
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    sensitivity: Mapped[Sensitivity] = mapped_column(
        _enum(Sensitivity, "sensitivity"), nullable=False, default=Sensitivity.NORMAL
    )
    status: Mapped[KnowledgeStatus] = mapped_column(
        _enum(KnowledgeStatus, "knowledge_status"),
        nullable=False,
        default=KnowledgeStatus.ACTIVE,
    )
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    search_vector: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('russian', "
            "coalesce(title, '') || ' ' || coalesce(summary, '') || ' ' || coalesce(content, ''))",
            persisted=True,
        ),
        nullable=True,
    )

    __table_args__ = (
        Index("ix_knowledge_fts", "search_vector", postgresql_using="gin"),
        Index("ix_knowledge_category", "category_id"),
        Index("ix_knowledge_project", "project_id"),
        Index("ix_knowledge_type_status", "type", "status"),
    )


# --- Задачи ------------------------------------------------------------------


class Task(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = _uuid_pk()
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[TaskStatus] = mapped_column(
        _enum(TaskStatus, "task_status"), nullable=False, default=TaskStatus.TODO
    )
    priority: Mapped[TaskPriority] = mapped_column(
        _enum(TaskPriority, "task_priority"), nullable=False, default=TaskPriority.NORMAL
    )
    bucket: Mapped[TaskBucket | None] = mapped_column(
        _enum(TaskBucket, "task_bucket"), nullable=True
    )
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), nullable=True
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    search_vector: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('russian', "
            "coalesce(title, '') || ' ' || coalesce(description, ''))",
            persisted=True,
        ),
        nullable=True,
    )

    __table_args__ = (
        Index("ix_tasks_fts", "search_vector", postgresql_using="gin"),
        Index("ix_tasks_status_bucket", "status", "bucket"),
        Index("ix_tasks_due_at", "due_at"),
        Index("ix_tasks_category", "category_id"),
        Index("ix_tasks_project", "project_id"),
    )


# --- Документы ---------------------------------------------------------------


class Document(Base, TimestampMixin, SoftDeleteMixin):
    """Метаданные документа; содержимое лежит только в зашифрованном виде на диске."""

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = _uuid_pk()
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    document_type: Mapped[DocumentType] = mapped_column(
        _enum(DocumentType, "document_type"), nullable=False, default=DocumentType.OTHER
    )
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), nullable=True
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    sensitivity: Mapped[Sensitivity] = mapped_column(
        _enum(Sensitivity, "sensitivity"), nullable=False, default=Sensitivity.SENSITIVE
    )
    status: Mapped[DocumentStatus] = mapped_column(
        _enum(DocumentStatus, "document_status"), nullable=False, default=DocumentStatus.PENDING
    )
    storage_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    encryption_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        Index("ix_documents_category", "category_id"),
        Index("ix_documents_project", "project_id"),
        Index("ix_documents_status", "status"),
    )


# --- Секреты -----------------------------------------------------------------


class Secret(Base, TimestampMixin, SoftDeleteMixin):
    """Секрет: значение только в зашифрованном виде, никогда не попадает в LLM/MCP."""

    __tablename__ = "secrets"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    purpose: Mapped[str | None] = mapped_column(Text, nullable=True)
    secret_type: Mapped[SecretType] = mapped_column(
        _enum(SecretType, "secret_type"), nullable=False, default=SecretType.OTHER
    )
    encrypted_value: Mapped[bytes] = mapped_column(nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), nullable=True
    )
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class TaskComment(Base, TimestampMixin, SoftDeleteMixin):
    """Комментарий к задаче: добавляется и редактируется через подтверждение."""

    __tablename__ = "task_comments"

    id: Mapped[uuid.UUID] = _uuid_pk()
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (Index("ix_task_comments_task", "task_id", "created_at"),)


# --- Диалог и операции -------------------------------------------------------


class ConversationState(Base, TimestampMixin):
    """Краткосрочный контекст сценария; переживает рестарт процесса."""

    __tablename__ = "conversation_states"

    telegram_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    flow: Mapped[str] = mapped_column(String(64), nullable=False)
    step: Mapped[str | None] = mapped_column(String(64), nullable=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    pending_operation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pending_operations.id", ondelete="SET NULL"), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PendingOperation(Base):
    """Предложение значимого изменения, ожидающее подтверждения пользователя."""

    __tablename__ = "pending_operations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    operation_type: Mapped[PendingOpType] = mapped_column(
        _enum(PendingOpType, "pending_op_type"), nullable=False
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[PendingStatus] = mapped_column(
        _enum(PendingStatus, "pending_status"), nullable=False, default=PendingStatus.PENDING
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        Index("ix_pending_operations_user_status", "telegram_user_id", "status"),
        Index("ix_pending_operations_expires", "expires_at"),
    )


class AuditLog(Base):
    """Журнал значимых изменений. Без чувствительного содержимого."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (Index("ix_audit_log_created", "created_at"),)


class ProcessedUpdate(Base):
    """Идемпотентность обработки Telegram updates."""

    __tablename__ = "processed_updates"

    update_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


# --- Improvement backlog (Stage 2) -------------------------------------------


class ImprovementItem(Base, TimestampMixin):
    """Наблюдение о проблеме или возможности развития Jarvis.

    Отдельная сущность: не смешивается с задачами, знаниями и диалогами.
    Подтверждённые технические события (source=technical_event) отличаются
    от гипотез (source=llm_hypothesis, confidence обязателен).
    """

    __tablename__ = "improvement_items"

    id: Mapped[uuid.UUID] = _uuid_pk()
    category: Mapped[ImprovementCategory] = mapped_column(
        _enum(ImprovementCategory, "improvement_category"),
        nullable=False,
    )
    source: Mapped[ImprovementSource] = mapped_column(
        _enum(ImprovementSource, "improvement_source"),
        nullable=False,
    )
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_behavior: Mapped[str | None] = mapped_column(Text, nullable=True)
    actual_behavior: Mapped[str | None] = mapped_column(Text, nullable=True)
    facts: Mapped[str | None] = mapped_column(Text, nullable=True)
    assumptions: Mapped[str | None] = mapped_column(Text, nullable=True)
    proposed_fix: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggested_priority: Mapped[TaskPriority | None] = mapped_column(
        _enum(TaskPriority, "task_priority"), nullable=True
    )
    status: Mapped[ImprovementStatus] = mapped_column(
        _enum(ImprovementStatus, "improvement_status"),
        nullable=False,
        default=ImprovementStatus.NEW,
    )
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    component: Mapped[str | None] = mapped_column(String(64), nullable=True)
    app_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    diagnostic_context: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    source_event_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    merged_from: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        Index("ix_improvement_items_status_category", "status", "category"),
        Index("ix_improvement_items_last_seen", "last_seen_at"),
        Index("ix_improvement_items_correlation", "correlation_id"),
    )


class ImprovementEvent(Base):
    """Исходное событие/повтор: позволяет вернуться к отдельным случаям."""

    __tablename__ = "improvement_events"

    id: Mapped[uuid.UUID] = _uuid_pk()
    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("improvement_items.id", ondelete="CASCADE"), nullable=False
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    diagnostic_context: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )

    __table_args__ = (Index("ix_improvement_events_item", "item_id", "occurred_at"),)


# --- Журнал чата (экспорт диалога) -------------------------------------------


class ChatMessage(Base):
    """Сообщение диалога (входящее/исходящее) для дневного экспорта.

    Текст сохраняется уже redacted (секреты вырезаны при записи); журнал
    выключается CHAT_LOG_ENABLED=false и чистится по retention.
    """

    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = _uuid_pk()
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)  # in | out
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="text")
    text: Mapped[str] = mapped_column(Text, nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_chat_messages_user_time", "telegram_user_id", "created_at"),
        Index("ix_chat_messages_created", "created_at"),
    )


# --- Журнал уведомлений (напоминания и сводки) -------------------------------


class NotificationLog(Base):
    """Отметки об отправленных уведомлениях: (kind, ref) отправляется один раз."""

    __tablename__ = "notification_log"

    id: Mapped[uuid.UUID] = _uuid_pk()
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    ref: Mapped[str] = mapped_column(String(64), nullable=False)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (UniqueConstraint("kind", "ref", name="uq_notification_kind_ref"),)
