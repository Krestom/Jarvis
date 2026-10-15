"""Персистентное состояние диалога (переживает рестарт процесса)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from jarvis.config.settings import Settings
from jarvis.db.models import ConversationState
from jarvis.repositories.conversation import ConversationRepository


class ConversationStore:
    def __init__(self, repo: ConversationRepository, settings: Settings) -> None:
        self._repo = repo
        self._settings = settings

    async def get_active(self, telegram_user_id: int) -> ConversationState | None:
        state = await self._repo.get(telegram_user_id)
        if state is None:
            return None
        if state.expires_at <= datetime.now(UTC):
            await self._repo.clear(telegram_user_id)
            return None
        return state

    async def set(
        self,
        telegram_user_id: int,
        flow: str,
        *,
        step: str | None = None,
        data: dict[str, Any] | None = None,
        pending_operation_id: uuid.UUID | str | None = None,
    ) -> ConversationState:
        ttl = timedelta(hours=self._settings.conversation_state_ttl_hours)
        expires_at = datetime.now(UTC) + ttl
        op_id: uuid.UUID | None = None
        if pending_operation_id is not None:
            op_id = uuid.UUID(str(pending_operation_id))
        return await self._repo.upsert(
            telegram_user_id,
            flow=flow,
            step=step,
            data=data or {},
            expires_at=expires_at,
            pending_operation_id=op_id,
        )

    async def touch(self, telegram_user_id: int, **fields: Any) -> ConversationState | None:
        state = await self.get_active(telegram_user_id)
        if state is None:
            return None
        return await self.set(
            telegram_user_id,
            state.flow,
            step=fields.get("step", state.step),
            data=fields.get("data", state.data),
            pending_operation_id=fields.get("pending_operation_id", state.pending_operation_id),
        )

    async def clear(self, telegram_user_id: int) -> None:
        await self._repo.clear(telegram_user_id)
