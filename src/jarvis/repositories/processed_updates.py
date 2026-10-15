from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.db.models import ProcessedUpdate


class ProcessedUpdateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def is_processed(self, update_id: int) -> bool:
        result = await self._session.execute(
            select(ProcessedUpdate.update_id).where(ProcessedUpdate.update_id == update_id)
        )
        return result.scalar_one_or_none() is not None

    async def mark(self, update_id: int) -> bool:
        """Отметить update как обработанный. False — уже обрабатывался ранее."""
        stmt = (
            pg_insert(ProcessedUpdate)
            .values(update_id=update_id)
            .on_conflict_do_nothing(index_elements=["update_id"])
            .returning(ProcessedUpdate.update_id)
        )
        inserted = (await self._session.execute(stmt)).scalar_one_or_none()
        return inserted is not None

    async def purge_older_than(self, moment: datetime) -> int:
        result = await self._session.execute(
            delete(ProcessedUpdate).where(ProcessedUpdate.created_at < moment)
        )
        return int(result.rowcount or 0)
