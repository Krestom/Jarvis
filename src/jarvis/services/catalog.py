from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.db.models import Category
from jarvis.repositories.categories import DEFAULT_CATEGORIES, CategoryRepository


class CatalogService:
    def __init__(self, session: AsyncSession) -> None:
        self._repo = CategoryRepository(session)

    async def ensure_defaults(self) -> int:
        return await self._repo.ensure_defaults(DEFAULT_CATEGORIES)

    async def list_categories(self) -> list[Category]:
        return await self._repo.list_active()

    async def resolve_name(self, name: str | None) -> Category | None:
        if not name:
            return None
        return await self._repo.find_by_name(name)
