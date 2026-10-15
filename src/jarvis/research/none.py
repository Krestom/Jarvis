"""Провайдер без веб-поиска: честно сообщает, что функция не настроена."""

from __future__ import annotations

from jarvis.research.provider import WebSearchResult, WebSearchUnavailable


class NoneSearchProvider:
    name = "none"

    async def search(self, query: str, *, limit: int = 5) -> list[WebSearchResult]:
        raise WebSearchUnavailable(
            "веб-поиск не настроен: задайте WEB_SEARCH_PROVIDER и ключ провайдера"
        )


__all__ = ["NoneSearchProvider"]
