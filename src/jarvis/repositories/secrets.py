from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.db.models import Secret
from jarvis.domain.enums import SecretType


class SecretRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, **fields: Any) -> Secret:
        secret = Secret(**fields)
        self._session.add(secret)
        await self._session.flush()
        return secret

    async def get(self, secret_id: uuid.UUID) -> Secret | None:
        result = await self._session.execute(
            select(Secret).where(Secret.id == secret_id, Secret.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def find_by_name(self, name: str) -> Secret | None:
        result = await self._session.execute(
            select(Secret).where(
                func.lower(Secret.name) == name.strip().lower(),
                Secret.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def update(self, secret: Secret, **fields: Any) -> Secret:
        for key, value in fields.items():
            setattr(secret, key, value)
        await self._session.flush()
        return secret

    async def soft_delete(self, secret: Secret) -> None:
        secret.deleted_at = func.now()
        await self._session.flush()

    async def list_active(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        secret_type: SecretType | None = None,
    ) -> list[Secret]:
        stmt = select(Secret).where(Secret.deleted_at.is_(None))
        if secret_type is not None:
            stmt = stmt.where(Secret.secret_type == secret_type)
        stmt = stmt.order_by(Secret.name).limit(limit).offset(offset)
        return list((await self._session.execute(stmt)).scalars().all())

    async def search_by_name(self, query: str, *, limit: int = 10) -> list[Secret]:
        stmt = (
            select(Secret)
            .where(Secret.deleted_at.is_(None), Secret.name.ilike(f"%{query}%"))
            .order_by(Secret.name)
            .limit(limit)
        )
        return list((await self._session.execute(stmt)).scalars().all())
