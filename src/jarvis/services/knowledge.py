from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.domain.enums import Sensitivity
from jarvis.repositories.categories import CategoryRepository
from jarvis.repositories.knowledge import KnowledgeRepository
from jarvis.services.drafts import KnowledgeDraft, KnowledgeUpdate
from jarvis.services.errors import NotFoundError, OperationError


class KnowledgeService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = KnowledgeRepository(session)
        self._categories = CategoryRepository(session)

    async def _ensure_category(self, category_id: uuid.UUID | None) -> None:
        if category_id is None:
            return
        if await self._categories.get(category_id) is None:
            raise OperationError("Категория не найдена")

    async def _resolve_project(self, draft: KnowledgeDraft) -> uuid.UUID | None:
        """Проект по id, либо по имени (создаётся, если ещё нет)."""
        if draft.project_id is not None:
            return draft.project_id
        name = (draft.project_name or "").strip()
        if not name:
            return None
        from jarvis.repositories.projects import ProjectRepository

        repo = ProjectRepository(self._session)
        project = await repo.find_by_name(name)
        if project is None:
            project = await repo.create(name=name[:200])
        return project.id

    async def create_from_draft(self, draft: KnowledgeDraft) -> uuid.UUID:
        await self._ensure_category(draft.category_id)
        fields = draft.model_dump()
        fields.pop("project_name", None)
        fields["project_id"] = await self._resolve_project(draft)
        item = await self._repo.create(**fields)
        return item.id

    async def apply_update(self, item_id: uuid.UUID, changes: dict) -> uuid.UUID:
        item = await self._repo.get(item_id)
        if item is None:
            raise NotFoundError("Запись не найдена")
        update = KnowledgeUpdate.model_validate(changes)
        fields = update.model_dump(exclude_unset=True)
        if "category_id" in fields:
            await self._ensure_category(fields["category_id"])
        await self._repo.update(item, **fields)
        return item.id

    async def soft_delete(self, item_id: uuid.UUID) -> uuid.UUID:
        item = await self._repo.get(item_id)
        if item is None:
            raise NotFoundError("Запись не найдена")
        await self._repo.soft_delete(item)
        return item.id

    async def get(self, item_id: uuid.UUID):
        return await self._repo.get(item_id)


SENSITIVITY_ORDER = {
    Sensitivity.NORMAL: 0,
    Sensitivity.SENSITIVE: 1,
    Sensitivity.SECRET: 2,
}


def allowed_sensitivities(max_level: Sensitivity | None) -> list[Sensitivity] | None:
    """Список допустимых уровней чувствительности не выше max_level."""
    if max_level is None:
        return None
    limit = SENSITIVITY_ORDER[max_level]
    return [level for level, value in SENSITIVITY_ORDER.items() if value <= limit]
