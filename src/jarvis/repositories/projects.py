from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.db.models import Project


class ProjectRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, **fields: Any) -> Project:
        project = Project(**fields)
        self._session.add(project)
        await self._session.flush()
        return project

    async def get(self, project_id: uuid.UUID) -> Project | None:
        result = await self._session.execute(
            select(Project).where(Project.id == project_id, Project.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def find_by_name(self, name: str) -> Project | None:
        result = await self._session.execute(
            select(Project).where(
                func.lower(Project.name) == name.strip().lower(),
                Project.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def list_active(self, *, limit: int = 20, offset: int = 0) -> list[Project]:
        stmt = (
            select(Project)
            .where(Project.deleted_at.is_(None))
            .order_by(Project.name)
            .limit(limit)
            .offset(offset)
        )
        return list((await self._session.execute(stmt)).scalars().all())
