"""Схемы агентского контекста (реальные сущности приложения)."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field


class EntityCandidate(BaseModel):
    """Реальная сущность из данных приложения (для кнопок и контекста)."""

    kind: Literal["task", "project", "knowledge"]
    id: uuid.UUID
    title: str = Field(max_length=300)


__all__ = ["EntityCandidate"]
