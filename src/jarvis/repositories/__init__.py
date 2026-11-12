"""Репозитории: доступ к данным. Никакой бизнес-логики — только запросы."""

from jarvis.repositories.audit import AuditRepository
from jarvis.repositories.comments import TaskCommentRepository
from jarvis.repositories.conversation import ConversationRepository
from jarvis.repositories.documents import DocumentRepository
from jarvis.repositories.improvements import ImprovementRepository
from jarvis.repositories.knowledge import KnowledgeRepository
from jarvis.repositories.pending import PendingOperationRepository, ResolutionOutcome
from jarvis.repositories.processed_updates import ProcessedUpdateRepository
from jarvis.repositories.secrets import SecretRepository
from jarvis.repositories.tasks import TaskRepository

__all__ = [
    "AuditRepository",
    "ConversationRepository",
    "DocumentRepository",
    "ImprovementRepository",
    "KnowledgeRepository",
    "PendingOperationRepository",
    "ProcessedUpdateRepository",
    "ResolutionOutcome",
    "SecretRepository",
    "TaskCommentRepository",
    "TaskRepository",
]
