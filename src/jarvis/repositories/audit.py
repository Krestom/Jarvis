from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.db.models import AuditLog


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

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
