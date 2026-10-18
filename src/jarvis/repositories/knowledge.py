from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.db.models import KnowledgeItem
from jarvis.domain.enums import KnowledgeType, Sensitivity


class KnowledgeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, **fields: Any) -> KnowledgeItem:
        item = KnowledgeItem(**fields)
        self._session.add(item)
        await self._session.flush()
        return item

    async def get(self, item_id: uuid.UUID) -> KnowledgeItem | None:
        result = await self._session.execute(
            select(KnowledgeItem).where(
                KnowledgeItem.id == item_id, KnowledgeItem.deleted_at.is_(None)
            )
        )
        return result.scalar_one_or_none()

    async def soft_delete(self, item: KnowledgeItem) -> None:
        item.deleted_at = func.now()
        await self._session.flush()

    async def update(self, item: KnowledgeItem, **fields: Any) -> KnowledgeItem:
        for key, value in fields.items():
            setattr(item, key, value)
        await self._session.flush()
        return item

    async def list_recent(self, *, limit: int = 10, offset: int = 0) -> list[KnowledgeItem]:
        result = await self._session.execute(
            select(KnowledgeItem)
            .where(KnowledgeItem.deleted_at.is_(None))
            .order_by(KnowledgeItem.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def list_for_project(
        self,
        project_id: uuid.UUID,
        *,
        allowed_sensitivities: list[Sensitivity] | None = None,
        limit: int = 20,
    ) -> list[KnowledgeItem]:
        stmt = select(KnowledgeItem).where(
            KnowledgeItem.deleted_at.is_(None),
            KnowledgeItem.project_id == project_id,
        )
        if allowed_sensitivities is not None:
            stmt = stmt.where(KnowledgeItem.sensitivity.in_(allowed_sensitivities))
        stmt = stmt.order_by(KnowledgeItem.updated_at.desc()).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def search(
        self,
        query: str,
        *,
        limit: int = 10,
        offset: int = 0,
        types: list[KnowledgeType] | None = None,
        category_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
        allowed_sensitivities: list[Sensitivity] | None = None,
    ) -> list[KnowledgeItem]:
        tsquery = func.websearch_to_tsquery("russian", query)
        stmt = select(KnowledgeItem).where(
            KnowledgeItem.deleted_at.is_(None),
            KnowledgeItem.search_vector.op("@@")(tsquery),
        )
        if types:
            stmt = stmt.where(KnowledgeItem.type.in_(types))
        if category_id is not None:
            stmt = stmt.where(KnowledgeItem.category_id == category_id)
        if project_id is not None:
            stmt = stmt.where(KnowledgeItem.project_id == project_id)
        if allowed_sensitivities is not None:
            stmt = stmt.where(KnowledgeItem.sensitivity.in_(allowed_sensitivities))
        stmt = (
            stmt.order_by(
                func.ts_rank(KnowledgeItem.search_vector, tsquery).desc(),
                KnowledgeItem.created_at.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
