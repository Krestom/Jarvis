"""Репозиторий журнала чата: запись, выборка за период, retention."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.db.models import ChatMessage


class ChatLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        telegram_user_id: int,
        *,
        direction: str,
        source: str,
        text: str,
        correlation_id: str | None = None,
        created_at: datetime | None = None,
    ) -> ChatMessage:
        # Клиентское время: server now() одинаков внутри транзакции, а порядок
        # сообщений в журнале должен быть детерминированным (и экспорт тоже).
        message = ChatMessage(
            telegram_user_id=telegram_user_id,
            direction=direction,
            source=source,
            text=text,
            correlation_id=correlation_id,
            created_at=created_at or datetime.now(UTC),
        )
        self._session.add(message)
        await self._session.flush()
        return message

    async def list_for_period(
        self, start: datetime, end: datetime, *, limit: int = 2000
    ) -> list[ChatMessage]:
        result = await self._session.execute(
            select(ChatMessage)
            .where(ChatMessage.created_at >= start, ChatMessage.created_at < end)
            .order_by(ChatMessage.created_at, ChatMessage.id)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def count_for_period(self, start: datetime, end: datetime) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(ChatMessage)
            .where(ChatMessage.created_at >= start, ChatMessage.created_at < end)
        )
        return int(result.scalar_one())

    async def delete_older_than(self, cutoff: datetime) -> int:
        result = await self._session.execute(
            delete(ChatMessage).where(ChatMessage.created_at < cutoff)
        )
        return int(result.rowcount or 0)
