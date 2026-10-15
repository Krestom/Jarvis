from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.domain.enums import Sensitivity
from jarvis.repositories.documents import DocumentRepository
from jarvis.repositories.knowledge import KnowledgeRepository
from jarvis.repositories.tasks import TaskRepository
from jarvis.services.knowledge import allowed_sensitivities

SNIPPET_LENGTH = 160


@dataclass(frozen=True)
class SearchHit:
    kind: Literal["knowledge", "task", "document"]
    entity_id: uuid.UUID
    title: str
    snippet: str
    created_at: datetime


def _snippet(text: str | None) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= SNIPPET_LENGTH:
        return cleaned
    return cleaned[: SNIPPET_LENGTH - 1] + "…"


class SearchService:
    """Единый поиск по знаниям, задачам и метаданным документов.

    allowed_sensitivities=None означает отсутствие фильтра (владелец в Telegram);
    MCP всегда передаёт явный список допустимых уровней.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._knowledge = KnowledgeRepository(session)
        self._tasks = TaskRepository(session)
        self._documents = DocumentRepository(session)

    async def search(
        self,
        query: str,
        *,
        limit: int = 9,
        offset: int = 0,
        max_sensitivity: Sensitivity | None = None,
    ) -> list[SearchHit]:
        query = query.strip()
        if not query:
            return []
        sensitivities = allowed_sensitivities(max_sensitivity)
        hits: list[SearchHit] = []

        knowledge_items = await self._knowledge.search(
            query,
            limit=limit,
            offset=offset,
            allowed_sensitivities=sensitivities,
        )
        for item in knowledge_items:
            hits.append(
                SearchHit(
                    kind="knowledge",
                    entity_id=item.id,
                    title=item.title,
                    snippet=_snippet(item.summary or item.content),
                    created_at=item.created_at,
                )
            )

        tasks = await self._tasks.search(query, limit=limit, offset=offset)
        for task in tasks:
            hits.append(
                SearchHit(
                    kind="task",
                    entity_id=task.id,
                    title=task.title,
                    snippet=_snippet(task.description),
                    created_at=task.created_at,
                )
            )

        documents = await self._documents.search_by_title(
            query, limit=limit, allowed_sensitivities=sensitivities
        )
        for document in documents:
            hits.append(
                SearchHit(
                    kind="document",
                    entity_id=document.id,
                    title=document.title,
                    snippet=f"Документ · {document.document_type}",
                    created_at=document.created_at,
                )
            )

        hits.sort(key=lambda hit: hit.created_at, reverse=True)
        return hits[: 3 * limit]
