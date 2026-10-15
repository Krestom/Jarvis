from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.db.models import TaskComment


class TaskCommentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, task_id: uuid.UUID, content: str) -> TaskComment:
        comment = TaskComment(task_id=task_id, content=content)
        self._session.add(comment)
        await self._session.flush()
        return comment

    async def get(self, comment_id: uuid.UUID) -> TaskComment | None:
        result = await self._session.execute(
            select(TaskComment).where(
                TaskComment.id == comment_id, TaskComment.deleted_at.is_(None)
            )
        )
        return result.scalar_one_or_none()

    async def update(self, comment: TaskComment, content: str) -> TaskComment:
        comment.content = content
        await self._session.flush()
        return comment

    async def list_for_task(
        self, task_id: uuid.UUID, *, limit: int | None = None
    ) -> list[TaskComment]:
        stmt = (
            select(TaskComment)
            .where(TaskComment.task_id == task_id, TaskComment.deleted_at.is_(None))
            .order_by(TaskComment.created_at)
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return list((await self._session.execute(stmt)).scalars().all())

    async def count_for_task(self, task_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(TaskComment)
            .where(TaskComment.task_id == task_id, TaskComment.deleted_at.is_(None))
        )
        return int(result.scalar_one())
