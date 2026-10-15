"""Доменные перечисления и типы."""

from __future__ import annotations

from enum import StrEnum


class Sensitivity(StrEnum):
    NORMAL = "normal"
    SENSITIVE = "sensitive"
    SECRET = "secret"


class KnowledgeType(StrEnum):
    NOTE = "note"
    REFERENCE = "reference"
    INSTRUCTION = "instruction"
    DECISION = "decision"
    PROJECT_CONTEXT = "project_context"


class KnowledgeSource(StrEnum):
    TEXT = "text"
    VOICE = "voice"
    DOCUMENT = "document"
    MANUAL = "manual"


class KnowledgeStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class TaskStatus(StrEnum):
    INBOX = "inbox"
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    WAITING = "waiting"
    DONE = "done"
    CANCELLED = "cancelled"


class TaskPriority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class TaskBucket(StrEnum):
    """Группа-намерение. Отсутствие группы (None) = "без группы".

    «Сегодня»/«На этой неделе» — динамические списки, вычисляемые по календарю,
    см. jarvis.domain.buckets.
    """

    TODAY = "today"
    WEEK = "week"
    LONG_TERM = "long_term"
    SOMEDAY = "someday"


class ProjectStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    DONE = "done"
    ARCHIVED = "archived"


class DocumentType(StrEnum):
    PASSPORT = "passport"
    ID_CARD = "id_card"
    DRIVERS_LICENSE = "drivers_license"
    CERTIFICATE = "certificate"
    CONTRACT = "contract"
    FINANCE = "finance"
    MANUAL = "manual"
    OTHER = "other"


class DocumentStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"


class SecretType(StrEnum):
    SSH_KEY = "ssh_key"
    API_TOKEN = "api_token"
    PASSWORD = "password"
    CREDENTIAL = "credential"
    OTHER = "other"


class EntityType(StrEnum):
    TASK = "task"
    KNOWLEDGE = "knowledge"
    DOCUMENT = "document"
    PROJECT = "project"
    SECRET = "secret"
    CATEGORY = "category"


class Relation(StrEnum):
    RELATES_TO = "relates_to"
    PART_OF = "part_of"
    DEPENDS_ON = "depends_on"
    DOCUMENTS = "documents"
    DECIDED_BY = "decided_by"
    REFERS_TO = "refers_to"


class PendingStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    FAILED = "failed"


class PendingOpType(StrEnum):
    CREATE_TASK = "create_task"
    UPDATE_TASK = "update_task"
    DELETE_TASK = "delete_task"
    CREATE_COMMENT = "create_comment"
    UPDATE_COMMENT = "update_comment"
    CREATE_KNOWLEDGE = "create_knowledge"
    UPDATE_KNOWLEDGE = "update_knowledge"
    DELETE_KNOWLEDGE = "delete_knowledge"
    CREATE_PROJECT = "create_project"
    CREATE_CATEGORY = "create_category"
    RENAME_CATEGORY = "rename_category"
    SAVE_DOCUMENT = "save_document"
    DELETE_DOCUMENT = "delete_document"
    CREATE_SECRET = "create_secret"
    UPDATE_SECRET = "update_secret"
    DELETE_SECRET = "delete_secret"
    CREATE_LINK = "create_link"
