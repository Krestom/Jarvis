from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.db.models import Document
from jarvis.domain.enums import DocumentStatus, Sensitivity


class DocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, **fields: Any) -> Document:
        document = Document(**fields)
        self._session.add(document)
        await self._session.flush()
        return document

    async def get(self, document_id: uuid.UUID) -> Document | None:
        result = await self._session.execute(
            select(Document).where(
                Document.id == document_id, Document.deleted_at.is_(None)
            )
        )
        return result.scalar_one_or_none()

    async def update(self, document: Document, **fields: Any) -> Document:
        for key, value in fields.items():
            setattr(document, key, value)
        await self._session.flush()
        return document

    async def soft_delete(self, document: Document) -> None:
        document.deleted_at = func.now()
        await self._session.flush()

    async def list_active(
        self,
        *,
        limit: int = 10,
        offset: int = 0,
        category_id: uuid.UUID | None = None,
        allowed_sensitivities: list[Sensitivity] | None = None,
    ) -> list[Document]:
        stmt = select(Document).where(
            Document.deleted_at.is_(None), Document.status == DocumentStatus.ACTIVE
        )
        if category_id is not None:
            stmt = stmt.where(Document.category_id == category_id)
        if allowed_sensitivities is not None:
            stmt = stmt.where(Document.sensitivity.in_(allowed_sensitivities))
        stmt = stmt.order_by(Document.created_at.desc()).limit(limit).offset(offset)
        return list((await self._session.execute(stmt)).scalars().all())

    async def search_by_title(
        self,
        query: str,
        *,
        limit: int = 10,
        allowed_sensitivities: list[Sensitivity] | None = None,
    ) -> list[Document]:
        stmt = select(Document).where(
            Document.deleted_at.is_(None),
            Document.status == DocumentStatus.ACTIVE,
            Document.title.ilike(f"%{query}%"),
        )
        if allowed_sensitivities is not None:
            stmt = stmt.where(Document.sensitivity.in_(allowed_sensitivities))
        stmt = stmt.order_by(Document.created_at.desc()).limit(limit)
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_pending_older_than(self, moment) -> list[Document]:
        stmt = select(Document).where(
            Document.status == DocumentStatus.PENDING,
            Document.created_at < moment,
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def hard_delete(self, document: Document) -> None:
        await self._session.delete(document)
        await self._session.flush()
