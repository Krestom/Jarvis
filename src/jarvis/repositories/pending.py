from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.db.models import PendingOperation
from jarvis.domain.enums import PendingOpType, PendingStatus


@dataclass(frozen=True)
class ResolutionOutcome:
    """Результат атомарного перехода статуса pending-операции."""

    resolved: PendingOperation | None
    reason: str  # ok | not_found | already_resolved | expired


class PendingOperationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        telegram_user_id: int,
        operation_type: PendingOpType,
        payload: dict[str, Any],
        expires_at: datetime,
    ) -> PendingOperation:
        operation = PendingOperation(
            telegram_user_id=telegram_user_id,
            operation_type=operation_type,
            payload=payload,
            expires_at=expires_at,
        )
        self._session.add(operation)
        await self._session.flush()
        return operation

    async def get(self, operation_id: uuid.UUID) -> PendingOperation | None:
        result = await self._session.execute(
            select(PendingOperation).where(PendingOperation.id == operation_id)
        )
        return result.scalar_one_or_none()

    async def resolve(
        self,
        operation_id: uuid.UUID,
        telegram_user_id: int,
        target: PendingStatus,
        *,
        result: dict[str, Any] | None = None,
        now: datetime,
    ) -> ResolutionOutcome:
        """Атомарный переход PENDING -> target.

        Повторный вызов не применяет изменение второй раз: UPDATE ...
        WHERE status='pending' не найдёт строку, и мы вернём already_resolved.
        """
        current = await self.get(operation_id)
        if current is None or current.telegram_user_id != telegram_user_id:
            return ResolutionOutcome(resolved=None, reason="not_found")

        stmt = (
            update(PendingOperation)
            .where(
                PendingOperation.id == operation_id,
                PendingOperation.status == PendingStatus.PENDING,
            )
            .values(status=target, resolved_at=now, result=result)
            .returning(PendingOperation)
        )
        resolved = (await self._session.execute(stmt)).scalar_one_or_none()
        if resolved is None:
            return ResolutionOutcome(resolved=current, reason="already_resolved")
        if current.expires_at < now:
            # Переход состоялся, но операция была просрочена: откатываем на expired.
            stmt_expire = (
                update(PendingOperation)
                .where(
                    PendingOperation.id == operation_id,
                    PendingOperation.status == target,
                )
                .values(status=PendingStatus.EXPIRED, result=None)
                .returning(PendingOperation)
            )
            expired = (await self._session.execute(stmt_expire)).scalar_one_or_none()
            return ResolutionOutcome(resolved=expired or resolved, reason="expired")
        return ResolutionOutcome(resolved=resolved, reason="ok")

    async def expire_stale(self, now: datetime) -> int:
        result = await self._session.execute(
            update(PendingOperation)
            .where(
                PendingOperation.status == PendingStatus.PENDING,
                PendingOperation.expires_at < now,
            )
            .values(status=PendingStatus.EXPIRED, resolved_at=now)
        )
        return int(result.rowcount or 0)

    async def set_result(self, operation_id: uuid.UUID, result: dict[str, Any]) -> None:
        await self._session.execute(
            update(PendingOperation)
            .where(PendingOperation.id == operation_id)
            .values(result=result)
        )

    async def mark_failed(self, operation_id: uuid.UUID) -> None:
        await self._session.execute(
            update(PendingOperation)
            .where(PendingOperation.id == operation_id)
            .values(status=PendingStatus.FAILED, resolved_at=datetime.now(UTC))
        )

    async def purge_old(self, older_than: datetime) -> int:
        result = await self._session.execute(
            delete(PendingOperation).where(
                PendingOperation.status != PendingStatus.PENDING,
                PendingOperation.resolved_at < older_than,
            )
        )
        return int(result.rowcount or 0)
