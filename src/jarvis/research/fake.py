"""Фейковый веб-поиск со скриптованными ответами (тесты, локальная разработка)."""

from __future__ import annotations

from dataclasses import dataclass, field

from jarvis.research.provider import WebSearchResult, WebSearchUnavailable


@dataclass
class FakeSearchProvider:
    name: str = "fake"
    responses: list[list[WebSearchResult]] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    unavailable: bool = False

    def queue(self, results: list[WebSearchResult]) -> None:
        self.responses.append(results)

    async def search(self, query: str, *, limit: int = 5) -> list[WebSearchResult]:
        self.calls.append(query)
        if self.unavailable:
            raise WebSearchUnavailable("fake provider unavailable")
        if self.responses:
            return self.responses.pop(0)[:limit]
        return [
            WebSearchResult(
                title=f"Результат по «{query}»",
                url="https://example.com/test",
                snippet="Тестовый результат веб-поиска.",
            )
        ][:limit]


__all__ = ["FakeSearchProvider"]
