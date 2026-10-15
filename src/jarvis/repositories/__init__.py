"""Репозитории: доступ к данным. Никакой бизнес-логики — только запросы."""

from jarvis.repositories.audit import AuditRepository
from jarvis.repositories.categories import DEFAULT_CATEGORIES, CategoryRepository
from jarvis.repositories.chatlog import ChatLogRepository
from jarvis.repositories.comments import TaskCommentRepository
from jarvis.repositories.conversation import ConversationRepository
from jarvis.repositories.documents import DocumentRepository
from jarvis.repositories.improvements import ImprovementRepository
from jarvis.repositories.knowledge import KnowledgeRepository
from jarvis.repositories.links import LinkedEntity, LinkRepository
from jarvis.repositories.pending import PendingOperationRepository, ResolutionOutcome
from jarvis.repositories.processed_updates import ProcessedUpdateRepository
from jarvis.repositories.projects import ProjectRepository
from jarvis.repositories.secrets import SecretRepository
from jarvis.repositories.tasks import TaskRepository

__all__ = [
    "AuditRepository",
    "CategoryRepository",
    "ChatLogRepository",
    "ConversationRepository",
    "DEFAULT_CATEGORIES",
    "DocumentRepository",
    "ImprovementRepository",
    "KnowledgeRepository",
    "LinkRepository",
    "LinkedEntity",
    "PendingOperationRepository",
    "ProcessedUpdateRepository",
    "ProjectRepository",
    "ResolutionOutcome",
    "SecretRepository",
    "TaskCommentRepository",
    "TaskRepository",
]
