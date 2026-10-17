from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.db.models import Task
from jarvis.domain.buckets import DONE_STATUSES, today_window, week_window
from jarvis.domain.enums import TaskBucket, TaskStatus


def _active() -> tuple[ColumnElement[bool], ColumnElement[bool]]:
    return Task.deleted_at.is_(None), Task.status.not_in(DONE_STATUSES)


class TaskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, **fields: Any) -> Task:
        task = Task(**fields)
        self._session.add(task)
        await self._session.flush()
        return task

    async def get(self, task_id: uuid.UUID) -> Task | None:
        result = await self._session.execute(
            select(Task).where(Task.id == task_id, Task.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def update(self, task: Task, **fields: Any) -> Task:
        for key, value in fields.items():
            setattr(task, key, value)
        await self._session.flush()
        return task

    async def soft_delete(self, task: Task) -> None:
        task.deleted_at = func.now()
        await self._session.flush()

    # --- Списки (динамические группы, см. domain/buckets.py) ---

    def _today_cond(self, now: datetime, tz: ZoneInfo) -> ColumnElement[bool]:
        _, end_today = today_window(now, tz)
        return or_(Task.bucket == TaskBucket.TODAY, Task.due_at < end_today)

    async def list_today(
        self, now: datetime, tz: ZoneInfo, *, limit: int = 20, offset: int = 0
    ) -> list[Task]:
        stmt = (
            select(Task)
            .where(*_active(), self._today_cond(now, tz))
            .order_by(Task.due_at.asc().nulls_last(), Task.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_week(
        self, now: datetime, tz: ZoneInfo, *, limit: int = 20, offset: int = 0
    ) -> list[Task]:
        _, end_today = today_window(now, tz)
        _, end_week = week_window(now, tz)
        due_this_week = (Task.due_at >= end_today) & (Task.due_at < end_week)
        week_bucket_not_today = (Task.bucket == TaskBucket.WEEK) & or_(
            Task.due_at.is_(None), Task.due_at >= end_today
        )
        stmt = (
            select(Task)
            .where(*_active(), or_(due_this_week, week_bucket_not_today))
            .order_by(Task.due_at.asc().nulls_last(), Task.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_long_term(
        self, now: datetime, tz: ZoneInfo, *, limit: int = 20, offset: int = 0
    ) -> list[Task]:
        _, end_week = week_window(now, tz)
        stmt = (
            select(Task)
            .where(*_active(), or_(Task.bucket == TaskBucket.LONG_TERM, Task.due_at >= end_week))
            .order_by(Task.due_at.asc().nulls_last(), Task.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_someday(self, *, limit: int = 20, offset: int = 0) -> list[Task]:
        stmt = (
            select(Task)
            .where(*_active(), Task.bucket == TaskBucket.SOMEDAY)
            .order_by(Task.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_no_group(self, *, limit: int = 20, offset: int = 0) -> list[Task]:
        stmt = (
            select(Task)
            .where(*_active(), Task.bucket.is_(None), Task.due_at.is_(None))
            .order_by(Task.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_inbox(self, *, limit: int = 20, offset: int = 0) -> list[Task]:
        stmt = (
            select(Task)
            .where(
                Task.deleted_at.is_(None),
                Task.status == TaskStatus.INBOX,
            )
            .order_by(Task.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_done(self, *, limit: int = 20, offset: int = 0) -> list[Task]:
        stmt = (
            select(Task)
            .where(Task.deleted_at.is_(None), Task.status == TaskStatus.DONE)
            .order_by(Task.completed_at.desc().nulls_last(), Task.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_all(self, *, limit: int = 20, offset: int = 0) -> list[Task]:
        """Все незавершённые задачи одним списком («покажи все задачи»)."""
        stmt = (
            select(Task)
            .where(*_active())
            .order_by(Task.due_at.asc().nulls_last(), Task.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_due_between(
        self, start: datetime, end: datetime, *, limit: int = 20, offset: int = 0
    ) -> list[Task]:
        """Задачи с дедлайном внутри интервала (для «задачи на <дата>»)."""
        stmt = (
            select(Task)
            .where(*_active(), Task.due_at >= start, Task.due_at < end)
            .order_by(Task.due_at.asc(), Task.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_due_before(self, moment: datetime, *, limit: int = 5) -> list[Task]:
        """Незавершённые задачи с наступившим сроком (для напоминаний)."""
        stmt = (
            select(Task)
            .where(*_active(), Task.due_at.is_not(None), Task.due_at <= moment)
            .order_by(Task.due_at.asc())
            .limit(limit)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def count_completed_between(self, start: datetime, end: datetime) -> int:
        """Сколько задач закрыто в интервале (для вечерней сводки)."""
        stmt = (
            select(func.count())
            .select_from(Task)
            .where(
                Task.deleted_at.is_(None),
                Task.status == TaskStatus.DONE,
                Task.completed_at.is_not(None),
                Task.completed_at >= start,
                Task.completed_at < end,
            )
        )
        return int((await self._session.execute(stmt)).scalar_one())

    async def list_by_category(
        self, category_id: uuid.UUID, *, limit: int = 20, offset: int = 0
    ) -> list[Task]:
        stmt = (
            select(Task)
            .where(*_active(), Task.category_id == category_id)
            .order_by(Task.due_at.asc().nulls_last(), Task.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def count_active(self) -> int:
        result = await self._session.execute(
            select(func.count()).select_from(Task).where(*_active())
        )
        return int(result.scalar_one())

    async def list_for_project(self, project_id: uuid.UUID, *, limit: int = 20) -> list[Task]:
        stmt = (
            select(Task)
            .where(Task.deleted_at.is_(None), Task.project_id == project_id)
            .order_by(Task.updated_at.desc())
            .limit(limit)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def search(self, query: str, *, limit: int = 10, offset: int = 0) -> list[Task]:
        tsquery = func.websearch_to_tsquery("russian", query)
        stmt = (
            select(Task)
            .where(
                Task.deleted_at.is_(None),
                Task.search_vector.op("@@")(tsquery),
            )
            .order_by(
                func.ts_rank(Task.search_vector, tsquery).desc(),
                Task.created_at.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def search_like(self, query: str, *, limit: int = 10) -> list[Task]:
        """Подстрочный fallback для поиска (когда FTS не находит)."""
        query = (query or "").strip()
        if not query:
            return []
        stmt = (
            select(Task)
            .where(Task.deleted_at.is_(None), Task.title.ilike(f"%{query[:80]}%"))
            .order_by(Task.created_at.desc())
            .limit(limit)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def find_candidates(self, query: str | None, *, limit: int = 5) -> list[Task]:
        """Кандидаты для привязки (комментария и т.п.): FTS, ILIKE, затем свежие."""
        query = (query or "").strip()
        if query:
            found = await self.search(query, limit=limit)
            if found:
                return found
            like = (
                select(Task)
                .where(
                    Task.deleted_at.is_(None),
                    Task.title.ilike(f"%{query[:80]}%"),
                )
                .order_by(Task.created_at.desc())
                .limit(limit)
            )
            found = list((await self._session.execute(like)).scalars().all())
            if found:
                return found
        recent = (
            select(Task)
            .where(*_active())
            .order_by(Task.created_at.desc())
            .limit(limit)
        )
        return list((await self._session.execute(recent)).scalars().all())
