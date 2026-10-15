from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.db.models import EntityLink
from jarvis.domain.enums import EntityType, Relation


@dataclass(frozen=True)
class LinkedEntity:
    """Соседняя сущность связи (в какую сторону ни смотрела бы связь)."""

    entity_type: EntityType
    entity_id: uuid.UUID
    relation: Relation
    outbound: bool  # True: текущая сущность — from-сторона


class LinkRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        from_type: EntityType,
        from_id: uuid.UUID,
        to_type: EntityType,
        to_id: uuid.UUID,
        relation: Relation = Relation.RELATES_TO,
    ) -> None:
        """Идемпотентно добавить связь (ON CONFLICT DO NOTHING)."""
        stmt = (
            pg_insert(EntityLink)
            .values(
                from_type=from_type,
                from_id=from_id,
                to_type=to_type,
                to_id=to_id,
                relation=relation,
            )
            .on_conflict_do_nothing(constraint="uq_entity_links_pair")
        )
        await self._session.execute(stmt)

    async def list_for(
        self, entity_type: EntityType, entity_id: uuid.UUID
    ) -> list[LinkedEntity]:
        stmt = select(EntityLink).where(
            or_(
                (EntityLink.from_type == entity_type) & (EntityLink.from_id == entity_id),
                (EntityLink.to_type == entity_type) & (EntityLink.to_id == entity_id),
            )
        )
        links = list((await self._session.execute(stmt)).scalars().all())
        result: list[LinkedEntity] = []
        for link in links:
            outbound = link.from_type == entity_type and link.from_id == entity_id
            if outbound:
                result.append(
                    LinkedEntity(
                        entity_type=EntityType(link.to_type),
                        entity_id=link.to_id,
                        relation=Relation(link.relation),
                        outbound=True,
                    )
                )
            else:
                result.append(
                    LinkedEntity(
                        entity_type=EntityType(link.from_type),
                        entity_id=link.from_id,
                        relation=Relation(link.relation),
                        outbound=False,
                    )
                )
        return result

    async def remove(self, link_id: uuid.UUID) -> bool:
        link = await self._session.get(EntityLink, link_id)
        if link is None:
            return False
        await self._session.delete(link)
        return True
