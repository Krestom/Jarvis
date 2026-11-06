"""Личный дневник: записи по дням и запрошенные переносы напоминаний."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.db.models import DiaryEntry, DiarySnooze


class DiaryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        telegram_user_id: int,
        content: str,
        *,
        entry_date: date,
        now: datetime | None = None,
    ) -> DiaryEntry:
        moment = now or datetime.now(UTC)
        entry = DiaryEntry(
            telegram_user_id=telegram_user_id,
            entry_date=entry_date,
            content=content,
            created_at=moment,
            updated_at=moment,
        )
        self._session.add(entry)
        await self._session.flush()
        return entry

    async def get(self, telegram_user_id: int, entry_id: uuid.UUID) -> DiaryEntry | None:
        result = await self._session.execute(
            select(DiaryEntry).where(
                DiaryEntry.id == entry_id,
                DiaryEntry.telegram_user_id == telegram_user_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_day(self, telegram_user_id: int, day: date) -> list[DiaryEntry]:
        result = await self._session.execute(
            select(DiaryEntry)
            .where(
                DiaryEntry.telegram_user_id == telegram_user_id,
                DiaryEntry.entry_date == day,
            )
            .order_by(DiaryEntry.created_at.asc(), DiaryEntry.id.asc())
        )
        return list(result.scalars().all())

    async def has_entry(self, telegram_user_id: int, day: date) -> bool:
        result = await self._session.execute(
            select(DiaryEntry.id)
            .where(
                DiaryEntry.telegram_user_id == telegram_user_id,
                DiaryEntry.entry_date == day,
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def update_content(self, entry: DiaryEntry, content: str) -> DiaryEntry:
        entry.content = content
        await self._session.flush()
        return entry

    async def delete(self, entry: DiaryEntry) -> None:
        await self._session.delete(entry)
        await self._session.flush()


class DiarySnoozeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def schedule(
        self, telegram_user_id: int, entry_date: date, remind_at: datetime
    ) -> DiarySnooze:
        # Один ожидающий перенос на день: повторный «Перенести» заменяет прежний.
        await self._session.execute(
            delete(DiarySnooze).where(
                DiarySnooze.telegram_user_id == telegram_user_id,
                DiarySnooze.entry_date == entry_date,
                DiarySnooze.sent_at.is_(None),
            )
        )
        snooze = DiarySnooze(
            telegram_user_id=telegram_user_id,
            entry_date=entry_date,
            remind_at=remind_at,
        )
        self._session.add(snooze)
        await self._session.flush()
        return snooze

    async def due(self, telegram_user_id: int, now: datetime) -> list[DiarySnooze]:
        result = await self._session.execute(
            select(DiarySnooze)
            .where(
                DiarySnooze.telegram_user_id == telegram_user_id,
                DiarySnooze.sent_at.is_(None),
                DiarySnooze.remind_at <= now,
            )
            .order_by(DiarySnooze.remind_at.asc())
        )
        return list(result.scalars().all())

    async def mark_sent(self, snooze: DiarySnooze, now: datetime | None = None) -> None:
        snooze.sent_at = now or datetime.now(UTC)
        await self._session.flush()

    async def reset(self, snooze_id: uuid.UUID) -> None:
        await self._session.execute(
            update(DiarySnooze)
            .where(DiarySnooze.id == snooze_id)
            .values(sent_at=None)
        )

    async def delete(self, snooze: DiarySnooze) -> None:
        await self._session.delete(snooze)
        await self._session.flush()

    async def delete_for_date(self, telegram_user_id: int, entry_date: date) -> None:
        await self._session.execute(
            delete(DiarySnooze).where(
                DiarySnooze.telegram_user_id == telegram_user_id,
                DiarySnooze.entry_date == entry_date,
                DiarySnooze.sent_at.is_(None),
            )
        )


__all__ = ["DiaryRepository", "DiarySnoozeRepository"]
