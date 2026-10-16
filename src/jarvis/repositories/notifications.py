"""Журнал отправленных уведомлений: одно и то же не отправляем дважды."""

from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.db.models import NotificationLog


class NotificationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def mark_if_new(self, kind: str, ref: str) -> bool:
        """True, если отметка создана только что (уведомление ещё не отправляли)."""
        statement = (
            pg_insert(NotificationLog)
            .values(kind=kind, ref=ref)
            .on_conflict_do_nothing(constraint="uq_notification_kind_ref")
            .returning(NotificationLog.id)
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none() is not None
