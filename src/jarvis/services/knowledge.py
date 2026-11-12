from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.domain.enums import Sensitivity
from jarvis.repositories.knowledge import KnowledgeRepository
from jarvis.services.drafts import KnowledgeDraft, KnowledgeUpdate
from jarvis.services.errors import NotFoundError


class KnowledgeService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = KnowledgeRepository(session)

    async def create_from_draft(self, draft: KnowledgeDraft) -> uuid.UUID:
        item = await self._repo.create(**draft.model_dump())
        return item.id

    async def apply_update(self, item_id: uuid.UUID, changes: dict) -> uuid.UUID:
        item = await self._repo.get(item_id)
        if item is None:
            raise NotFoundError("Запись не найдена")
        update = KnowledgeUpdate.model_validate(changes)
        fields = update.model_dump(exclude_unset=True)
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
