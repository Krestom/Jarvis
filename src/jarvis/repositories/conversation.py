from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.db.models import ConversationState


class ConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, telegram_user_id: int) -> ConversationState | None:
        result = await self._session.execute(
            select(ConversationState).where(
                ConversationState.telegram_user_id == telegram_user_id
            )
        )
        return result.scalar_one_or_none()

    async def upsert(
        self,
        telegram_user_id: int,
        *,
        flow: str,
        step: str | None,
        data: dict[str, Any],
        expires_at: datetime,
        pending_operation_id=None,
    ) -> ConversationState:
        state = await self.get(telegram_user_id)
        if state is None:
            state = ConversationState(telegram_user_id=telegram_user_id)
            self._session.add(state)
        state.flow = flow
        state.step = step
        state.data = data
        state.expires_at = expires_at
        state.pending_operation_id = pending_operation_id
        await self._session.flush()
        return state

    async def clear(self, telegram_user_id: int) -> None:
        await self._session.execute(
            delete(ConversationState).where(
                ConversationState.telegram_user_id == telegram_user_id
            )
        )

    async def purge_expired(self, now: datetime) -> int:
        result = await self._session.execute(
            delete(ConversationState).where(ConversationState.expires_at < now)
        )
        return int(result.rowcount or 0)
