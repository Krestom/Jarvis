"""Репозиторий improvement backlog: записи и их события-подтверждения."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.db.models import ImprovementEvent, ImprovementItem
from jarvis.domain.enums import (
    ImprovementCategory,
    ImprovementSource,
    ImprovementStatus,
)

# Открытые статусы: по ним ищется повтор проблемы (дедупликация).
_OPEN_STATUSES = (
    ImprovementStatus.NEW,
    ImprovementStatus.REVIEWED,
    ImprovementStatus.PLANNED,
)


class ImprovementRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, **fields: Any) -> ImprovementItem:
        item = ImprovementItem(**fields)
        self._session.add(item)
        await self._session.flush()
        return item

    async def get(self, item_id: uuid.UUID) -> ImprovementItem | None:
        result = await self._session.execute(
            select(ImprovementItem).where(ImprovementItem.id == item_id)
        )
        return result.scalar_one_or_none()

    async def find_open_by_signature(
        self, *, source: ImprovementSource, component: str | None, title: str
    ) -> ImprovementItem | None:
        """Открытая запись с тем же источником, компонентом и заголовком."""
        query = select(ImprovementItem).where(
            ImprovementItem.status.in_(_OPEN_STATUSES),
            ImprovementItem.source == source,
            func.lower(ImprovementItem.title) == title.strip().lower(),
        )
        if component is None:
            query = query.where(ImprovementItem.component.is_(None))
        else:
            query = query.where(ImprovementItem.component == component)
        result = await self._session.execute(
            query.order_by(ImprovementItem.last_seen_at.desc()).limit(1)
        )
        return result.scalar_one_or_none()

    async def register_occurrence(
        self,
        item: ImprovementItem,
        *,
        correlation_id: str | None = None,
        note: str | None = None,
        diagnostic_context: dict[str, Any] | None = None,
        first: bool = False,
    ) -> ImprovementEvent:
        """Событие по записи: первый случай (`first`) или повтор (+1 к счётчику)."""
        now = datetime.now(UTC)
        if first:
            item.first_seen_at = now
        else:
            item.occurrence_count += 1
        item.last_seen_at = now
        event = ImprovementEvent(
            item_id=item.id,
            occurred_at=now,
            correlation_id=correlation_id,
            note=note,
            diagnostic_context=diagnostic_context or {},
        )
        self._session.add(event)
        await self._session.flush()
        item.source_event_ids = [*item.source_event_ids, str(event.id)]
        await self._session.flush()
        return event

    async def list_items(
        self,
        *,
        status: ImprovementStatus | None = None,
        statuses: tuple[ImprovementStatus, ...] | None = None,
        category: ImprovementCategory | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ImprovementItem]:
        query = select(ImprovementItem)
        if status is not None:
            query = query.where(ImprovementItem.status == status)
        if statuses is not None:
            query = query.where(ImprovementItem.status.in_(statuses))
        if category is not None:
            query = query.where(ImprovementItem.category == category)
        result = await self._session.execute(
            query.order_by(ImprovementItem.last_seen_at.desc(), ImprovementItem.title)
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def counts_by_status(self) -> dict[ImprovementStatus, int]:
        result = await self._session.execute(
            select(ImprovementItem.status, func.count()).group_by(ImprovementItem.status)
        )
        return {row[0]: row[1] for row in result}

    async def counts_by_category(self) -> dict[ImprovementCategory, int]:
        result = await self._session.execute(
            select(ImprovementItem.category, func.count()).group_by(
                ImprovementItem.category
            )
        )
        return {row[0]: row[1] for row in result}

    async def count_items(
        self,
        *,
        status: ImprovementStatus | None = None,
        statuses: tuple[ImprovementStatus, ...] | None = None,
        category: ImprovementCategory | None = None,
    ) -> int:
        query = select(func.count()).select_from(ImprovementItem)
        if status is not None:
            query = query.where(ImprovementItem.status == status)
        if statuses is not None:
            query = query.where(ImprovementItem.status.in_(statuses))
        if category is not None:
            query = query.where(ImprovementItem.category == category)
        result = await self._session.execute(query)
        return int(result.scalar_one())

    async def update_status(
        self, item: ImprovementItem, status: ImprovementStatus
    ) -> ImprovementItem:
        item.status = status
        await self._session.flush()
        return item

    async def hard_delete(self, item: ImprovementItem) -> None:
        await self._session.delete(item)

    async def list_events(self, item_id: uuid.UUID) -> list[ImprovementEvent]:
        result = await self._session.execute(
            select(ImprovementEvent)
            .where(ImprovementEvent.item_id == item_id)
            .order_by(ImprovementEvent.occurred_at, ImprovementEvent.id)
        )
        return list(result.scalars().all())

    async def merge_into(
        self, target: ImprovementItem, sources: list[ImprovementItem]
    ) -> ImprovementItem:
        """Слить записи в `target`: события переезжают, источники архивируются."""
        merged_from = list(target.merged_from)
        for source in sources:
            if source.id == target.id:
                continue
            target.occurrence_count += source.occurrence_count
            target.first_seen_at = min(target.first_seen_at, source.first_seen_at)
            target.last_seen_at = max(target.last_seen_at, source.last_seen_at)
            target.source_event_ids = [*target.source_event_ids, *source.source_event_ids]
            if source.confidence is not None:
                target.confidence = max(target.confidence or 0.0, source.confidence)
            merged_from = [*merged_from, str(source.id), *source.merged_from]
            source.status = ImprovementStatus.ARCHIVED
            source.meta = {**source.meta, "merged_into": str(target.id)}
            await self._session.execute(
                sa_update(ImprovementEvent)
                .where(ImprovementEvent.item_id == source.id)
                .values(item_id=target.id)
            )
        target.merged_from = merged_from
        await self._session.flush()
        return target
