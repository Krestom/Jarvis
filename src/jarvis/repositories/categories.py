from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.db.models import Category

DEFAULT_CATEGORIES = [
    "Работа",
    "Дом и личные дела",
    "Проекты",
    "Серверы и инфраструктура",
    "Инструкции",
    "Документы",
    "Идеи и заметки",
    "Справочная информация",
]


class CategoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_active(self) -> list[Category]:
        result = await self._session.execute(
            select(Category)
            .where(Category.deleted_at.is_(None))
            .order_by(Category.sort_order, Category.name)
        )
        return list(result.scalars().all())

    async def get(self, category_id: uuid.UUID) -> Category | None:
        result = await self._session.execute(
            select(Category).where(
                Category.id == category_id, Category.deleted_at.is_(None)
            )
        )
        return result.scalar_one_or_none()

    async def find_by_name(self, name: str) -> Category | None:
        result = await self._session.execute(
            select(Category).where(
                func.lower(Category.name) == name.strip().lower(),
                Category.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def create(self, name: str, parent_id: uuid.UUID | None = None) -> Category:
        category = Category(name=name.strip(), parent_id=parent_id)
        self._session.add(category)
        await self._session.flush()
        return category

    async def rename(self, category: Category, new_name: str) -> Category:
        category.name = new_name.strip()
        await self._session.flush()
        return category

    async def ensure_defaults(self, names: list[str] | None = None) -> int:
        """Создать стартовые категории, если их ещё нет. Idempotent."""
        created = 0
        for sort_order, name in enumerate(names or DEFAULT_CATEGORIES):
            if await self.find_by_name(name) is not None:
                continue
            category = Category(name=name, sort_order=sort_order)
            self._session.add(category)
            created += 1
        if created:
            await self._session.flush()
        return created
