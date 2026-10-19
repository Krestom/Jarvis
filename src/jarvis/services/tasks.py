from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.domain.enums import TaskStatus
from jarvis.repositories.categories import CategoryRepository
from jarvis.repositories.tasks import TaskRepository
from jarvis.services.drafts import TaskDraft, TaskUpdate
from jarvis.services.errors import NotFoundError, OperationError

TASK_VIEWS = (
    "today",
    "week",
    "month",
    "long_term",
    "someday",
    "no_group",
    "inbox",
    "all",
    "done",
)


class TaskService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = TaskRepository(session)
        self._categories = CategoryRepository(session)

    async def _ensure_category(self, category_id: uuid.UUID | None) -> None:
        if category_id is None:
            return
        if await self._categories.get(category_id) is None:
            raise OperationError("Категория не найдена")

    async def _ensure_project(self, project_id: uuid.UUID | None) -> None:
        if project_id is None:
            return
        from jarvis.repositories.projects import ProjectRepository

        if await ProjectRepository(self._session).get(project_id) is None:
            raise OperationError("Проект не найден")

    async def _resolve_project_name(self, name: str) -> uuid.UUID | None:
        name = (name or "").strip()
        if not name:
            return None
        from jarvis.repositories.projects import ProjectRepository

        repo = ProjectRepository(self._session)
        project = await repo.find_by_name(name)
        if project is None:
            project = await repo.create(name=name[:200])
        return project.id

    async def _resolve_project(self, draft: TaskDraft) -> uuid.UUID | None:
        if draft.project_id is not None:
            return draft.project_id
        return await self._resolve_project_name(draft.project_name or "")

    async def create_from_draft(self, draft: TaskDraft) -> uuid.UUID:
        await self._ensure_category(draft.category_id)
        fields = draft.model_dump()
        fields.pop("project_name", None)
        fields.pop("category_name", None)
        fields["project_id"] = await self._resolve_project(draft)
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
        if "category_id" in fields:
            await self._ensure_category(fields["category_id"])
        project_name = fields.pop("project_name", None)
        if project_name and "project_id" not in fields:
            fields["project_id"] = await self._resolve_project_name(project_name)
        if "project_id" in fields:
            await self._ensure_project(fields["project_id"])
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
        if view == "month":
            return await self._repo.list_month(now, tz, limit=limit, offset=offset)
        if view == "long_term":
            return await self._repo.list_long_term(now, tz, limit=limit, offset=offset)
        if view == "someday":
            return await self._repo.list_someday(limit=limit, offset=offset)
        if view == "no_group":
            return await self._repo.list_no_group(limit=limit, offset=offset)
        if view == "inbox":
            return await self._repo.list_inbox(limit=limit, offset=offset)
        if view == "all":
            return await self._repo.list_all(limit=limit, offset=offset)
        if view == "done":
            return await self._repo.list_done(limit=limit, offset=offset)
        raise OperationError(f"Неизвестный список задач: {view}")

    async def list_on_date(
        self, day: date, *, tz: ZoneInfo, limit: int = 20, offset: int = 0
    ) -> list:
        """Задачи с дедлайном на конкретный день (в таймзоне пользователя)."""
        start = datetime.combine(day, time.min, tz)
        end = start + timedelta(days=1)
        return await self._repo.list_due_between(start, end, limit=limit, offset=offset)

    async def list_by_category(
        self, category_id: uuid.UUID, *, limit: int = 20, offset: int = 0
    ) -> list:
        return await self._repo.list_by_category(category_id, limit=limit, offset=offset)
