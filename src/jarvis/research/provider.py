"""Интерфейс веб-поиска: провайдер-независимый, подключается по конфигу."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class WebSearchUnavailable(RuntimeError):
    """Провайдер веб-поиска не настроен или недоступен."""


@dataclass(frozen=True)
class WebSearchResult:
    title: str
    url: str
    snippet: str


class WebSearchProvider(Protocol):
    name: str

    async def search(self, query: str, *, limit: int = 5) -> list[WebSearchResult]: ...
