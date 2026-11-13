from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.domain.enums import TaskStatus
from jarvis.repositories.tasks import TaskRepository
from jarvis.services.drafts import TaskDraft, TaskUpdate
from jarvis.services.errors import NotFoundError, OperationError

TASK_VIEWS = (
    "today",
    "week",
    "no_group",
    "all",
)


class TaskService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = TaskRepository(session)

    async def create_from_draft(self, draft: TaskDraft) -> uuid.UUID:
        fields = draft.model_dump()
        if fields["status"] == TaskStatus.DONE:
            fields["completed_at"] = datetime.now(UTC)
        task = await self._repo.create(**fields)
        return task.id

    async def apply_update(self, task_id: uuid.UUID, changes: dict) -> uuid.UUID:
        task = await self._repo.get(task_id)
        if task is None:
            raise NotFoundError("Задача не найдена")
        update = TaskUpdate.model_validate(changes)
        fields = update.model_dump(exclude_unset=True)
        new_status = fields.get("status")
        if new_status is not None and new_status != task.status:
            if new_status == TaskStatus.DONE:
                fields["completed_at"] = datetime.now(UTC)
            elif task.status == TaskStatus.DONE:
                fields["completed_at"] = None
        await self._repo.update(task, **fields)
        return task.id

    async def soft_delete(self, task_id: uuid.UUID) -> uuid.UUID:
        task = await self._repo.get(task_id)
        if task is None:
            raise NotFoundError("Задача не найдена")
        await self._repo.soft_delete(task)
        return task.id

    async def get(self, task_id: uuid.UUID):
        return await self._repo.get(task_id)

    async def list_view(
        self,
        view: str,
        *,
        now: datetime,
        tz: ZoneInfo,
        limit: int = 10,
        offset: int = 0,
    ) -> list:
        if view == "today":
            return await self._repo.list_today(now, tz, limit=limit, offset=offset)
        if view == "week":
            return await self._repo.list_week(now, tz, limit=limit, offset=offset)
        if view == "no_group":
            return await self._repo.list_no_group(limit=limit, offset=offset)
        if view == "all":
            return await self._repo.list_all(limit=limit, offset=offset)
        raise OperationError(f"Неизвестный список задач: {view}")

    async def list_on_date(
        self, day: date, *, tz: ZoneInfo, limit: int = 20, offset: int = 0
    ) -> list:
        """Задачи с дедлайном на конкретный день (в таймзоне пользователя)."""
        start = datetime.combine(day, time.min, tz)
        end = start + timedelta(days=1)
        return await self._repo.list_due_between(start, end, limit=limit, offset=offset)

    async def list_period(
        self, start: date, end: date, *, tz: ZoneInfo, limit: int = 20, offset: int = 0
    ) -> list:
        """Задачи с дедлайном в включительном периоде [start, end]."""
        start_dt = datetime.combine(start, time.min, tz)
        end_dt = datetime.combine(end + timedelta(days=1), time.min, tz)
        return await self._repo.list_due_between(
            start_dt, end_dt, limit=limit, offset=offset
        )
