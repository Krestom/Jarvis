from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.db.models import AuditLog


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def count_actions_between(
        self, start: datetime, end: datetime
    ) -> dict[str, int]:
        """Успешные действия из аудита в интервале, по типу действия."""
        stmt = (
            select(AuditLog.action, func.count())
            .where(
                AuditLog.created_at >= start,
                AuditLog.created_at < end,
                AuditLog.result == "ok",
            )
            .group_by(AuditLog.action)
        )
        result = await self._session.execute(stmt)
        return {action: int(count) for action, count in result}

    async def log(
        self,
        *,
        actor: str,
        action: str,
        entity_type: str | None = None,
        entity_id: uuid.UUID | None = None,
        result: str = "ok",
        meta: dict[str, Any] | None = None,
    ) -> None:
        """meta должен содержать только нечувствительные данные."""
        self._session.add(
            AuditLog(
                actor=actor,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                result=result,
                meta=meta or {},
            )
        )
        await self._session.flush()
