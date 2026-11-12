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


class KnowledgeSource(StrEnum):
    TEXT = "text"
    VOICE = "voice"
    DOCUMENT = "document"
    MANUAL = "manual"


class KnowledgeStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class TaskStatus(StrEnum):
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
    SECRET = "secret"


class ImprovementCategory(StrEnum):
    FAILURE = "failure"
    MISSING_CAPABILITY = "missing_capability"
    UX_FRICTION = "ux_friction"
    IMPROVEMENT_IDEA = "improvement_idea"


class ImprovementSource(StrEnum):
    TECHNICAL_EVENT = "technical_event"
    USER_OBSERVATION = "user_observation"
    LLM_HYPOTHESIS = "llm_hypothesis"


class ImprovementStatus(StrEnum):
    NEW = "new"
    REVIEWED = "reviewed"
    PLANNED = "planned"
    IMPLEMENTED = "implemented"
    WONT_FIX = "wont_fix"
    ARCHIVED = "archived"


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
    SAVE_DOCUMENT = "save_document"
    UPDATE_DOCUMENT = "update_document"
    DELETE_DOCUMENT = "delete_document"
    CREATE_SECRET = "create_secret"
    DELETE_SECRET = "delete_secret"
