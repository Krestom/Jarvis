"""Read-only MCP-инструменты.

Политика доступа:
- только чтение; никаких значений секретов, shell, SQL и файловой системы;
- записи с чувствительностью выше разрешённого уровня (MCP_INCLUDE_SENSITIVE)
  не возвращаются, а сам факт их существования не подтверждается;
- содержимое документов недоступно, только метаданные заголовка в связях;
- контекст минимальный: id, тип, заголовок, фрагмент, связи.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jarvis.config.settings import Settings
from jarvis.db.session import session_scope
from jarvis.domain.enums import KnowledgeType, Sensitivity
from jarvis.repositories.audit import AuditRepository
from jarvis.repositories.categories import CategoryRepository
from jarvis.repositories.knowledge import KnowledgeRepository
from jarvis.repositories.projects import ProjectRepository
from jarvis.repositories.tasks import TaskRepository
from jarvis.services.knowledge import allowed_sensitivities
from jarvis.services.tasks import TASK_VIEWS, TaskService

log = logging.getLogger(__name__)

SNIPPET_LENGTH = 400
TASK_VIEW_NAMES = ", ".join(TASK_VIEWS)


async def _audit(session: AsyncSession, tool: str, count: int) -> None:
    await AuditRepository(session).log(
        actor="mcp",
        action=f"tool:{tool}",
        result="ok",
        meta={"count": count},
    )


def _parse_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError):
        return None


def _snippet(text: str | None) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= SNIPPET_LENGTH:
        return cleaned
    return cleaned[: SNIPPET_LENGTH - 1] + "…"


class _Serializer:
    """Сериализация сущностей в безопасный вид для MCP."""

    def __init__(self, session: AsyncSession, allowed: list[Sensitivity]) -> None:
        self._session = session
        self._allowed = allowed
        self._categories = CategoryRepository(session)

    async def knowledge(self, item, *, include_content: bool = False) -> dict[str, Any]:
        category = None
        if item.category_id:
            found = await self._categories.get(item.category_id)
            category = found.name if found else None
        data: dict[str, Any] = {
            "id": str(item.id),
            "type": item.type.value if hasattr(item.type, "value") else str(item.type),
            "title": item.title,
            "summary": item.summary,
            "category": category,
            "sensitivity": item.sensitivity.value,
            "updated_at": item.updated_at.isoformat() if item.updated_at else None,
        }
        if include_content:
            data["content"] = item.content
        else:
            data["snippet"] = _snippet(item.summary or item.content)
        return data

    def task(self, task) -> dict[str, Any]:
        return {
            "id": str(task.id),
            "title": task.title,
            "description": _snippet(task.description),
            "status": task.status.value,
            "bucket": task.bucket.value if task.bucket else None,
            "priority": task.priority.value,
            "due_at": task.due_at.isoformat() if task.due_at else None,
            "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        }


def register_tools(
    server,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    allowed = allowed_sensitivities(
        Sensitivity.SENSITIVE if settings.mcp_include_sensitive else Sensitivity.NORMAL
    )
    assert allowed is not None

    @server.tool(
        name="search_knowledge",
        title="Поиск знаний",
        description=(
            "Найти записи базы знаний (заметки, решения, справочная информация) по запросу. "
            "Секреты и чувствительные документы недоступны."
        ),
    )
    async def search_knowledge(query: str, limit: int = 10) -> dict[str, Any]:
        """Поиск по знаниям: возвращает id, заголовок и фрагмент."""
        limit = max(1, min(int(limit), 50))
        async with session_scope(session_factory) as session:
            items = await KnowledgeRepository(session).search(
                query, limit=limit, allowed_sensitivities=allowed
            )
            serializer = _Serializer(session, allowed)
            results = [await serializer.knowledge(item) for item in items]
            await _audit(session, "search_knowledge", len(results))
            return {"query": query, "count": len(results), "results": results}

    @server.tool(
        name="search_instructions",
        title="Поиск инструкций",
        description="Найти инструкции и how-to записи по запросу.",
    )
    async def search_instructions(query: str, limit: int = 10) -> dict[str, Any]:
        """Поиск только по записям типа «инструкция»."""
        limit = max(1, min(int(limit), 50))
        async with session_scope(session_factory) as session:
            items = await KnowledgeRepository(session).search(
                query,
                limit=limit,
                types=[KnowledgeType.INSTRUCTION],
                allowed_sensitivities=allowed,
            )
            serializer = _Serializer(session, allowed)
            results = [await serializer.knowledge(item) for item in items]
            await _audit(session, "search_instructions", len(results))
            return {"query": query, "count": len(results), "results": results}

    @server.tool(
        name="get_knowledge_item",
        title="Запись знания по ID",
        description="Получить полный текст записи знаний по её id (см. search_knowledge).",
    )
    async def get_knowledge_item(item_id: str) -> dict[str, Any]:
        """Полный текст одной записи, если она доступна по политике."""
        parsed = _parse_uuid(item_id)
        if parsed is None:
            return {"error": "invalid_id"}
        async with session_scope(session_factory) as session:
            item = await KnowledgeRepository(session).get(parsed)
            if item is None or item.sensitivity not in allowed:
                await _audit(session, "get_knowledge_item", 0)
                return {"error": "not_found"}
            serializer = _Serializer(session, allowed)
            result = await serializer.knowledge(item, include_content=True)
            await _audit(session, "get_knowledge_item", 1)
            return result

    @server.tool(
        name="get_project_context",
        title="Контекст проекта",
        description=(
            "Получить описание проекта и последние связанные записи знаний — "
            "контекст, нужный для работы над проектом."
        ),
    )
    async def get_project_context(project_name: str, limit: int = 20) -> dict[str, Any]:
        """Проект + свежие знания и задачи, привязанные к нему."""
        limit = max(1, min(int(limit), 50))
        async with session_scope(session_factory) as session:
            projects = ProjectRepository(session)
            project = await projects.find_by_name(project_name)
            if project is None:
                names = [p.name for p in await projects.list_active(limit=50)]
                return {"error": "not_found", "available_projects": names}
            knowledge = await KnowledgeRepository(session).list_for_project(
                project.id, allowed_sensitivities=allowed, limit=limit
            )
            tasks = await TaskRepository(session).list_for_project(project.id, limit=limit)
            serializer = _Serializer(session, allowed)
            result = {
                "project": {
                    "id": str(project.id),
                    "name": project.name,
                    "description": project.description,
                    "status": project.status.value,
                },
                "knowledge": [await serializer.knowledge(item) for item in knowledge],
                "tasks": [serializer.task(task) for task in tasks],
            }
            await _audit(session, "get_project_context", len(knowledge) + len(tasks))
            return result

    @server.tool(
        name="search_tasks",
        title="Поиск задач",
        description=(
            "Найти задачи по тексту и/или списку. view: today, week, long_term, "
            "someday, no_group, inbox, done."
        ),
    )
    async def search_tasks(query: str = "", view: str = "", limit: int = 10) -> dict[str, Any]:
        """Поиск задач: текстом или по динамическому списку."""
        limit = max(1, min(int(limit), 50))
        query = (query or "").strip()
        view = (view or "").strip()
        if not query and not view:
            return {"error": "specify_query_or_view", "views": TASK_VIEWS}
        async with session_scope(session_factory) as session:
            if query:
                tasks = await TaskRepository(session).search(query, limit=limit)
            else:
                if view not in TASK_VIEWS:
                    return {"error": "unknown_view", "views": TASK_VIEWS}
                tasks = await TaskService(session).list_view(
                    view, now=datetime.now(UTC), tz=settings.tz, limit=limit
                )
            serializer = _Serializer(session, allowed)
            results = [serializer.task(task) for task in tasks]
            await _audit(session, "search_tasks", len(results))
            return {"count": len(results), "results": results}