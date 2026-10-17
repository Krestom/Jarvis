"""Веб-поиск через Tavily API (POST https://api.tavily.com/search).

Ключ — только из конфигурации (TAVILY_API_KEY); в логи не попадает.
Ответ: {"results": [{"title", "url", "content", "score"}]}.
"""

from __future__ import annotations

import logging

import httpx

from jarvis.research.provider import WebSearchResult, WebSearchUnavailable

log = logging.getLogger(__name__)

TAVILY_URL = "https://api.tavily.com/search"
SNIPPET_LIMIT = 500


class TavilySearchProvider:
    name = "tavily"

    def __init__(
        self,
        *,
        api_key: str,
        timeout_seconds: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("TAVILY_API_KEY is not set")
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._transport = transport

    async def search(self, query: str, *, limit: int = 5) -> list[WebSearchResult]:
        payload = {"query": query, "max_results": limit, "search_depth": "basic"}
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.post(TAVILY_URL, json=payload, headers=headers)
        except httpx.HTTPError as error:
            log.warning("tavily request failed: %s", error)
            raise WebSearchUnavailable("сервис поиска недоступен") from error
        if response.status_code >= 400:
            log.warning("tavily: HTTP %s", response.status_code)
            raise WebSearchUnavailable(
                f"сервис поиска вернул ошибку (HTTP {response.status_code})"
            )
        try:
            data = response.json()
        except ValueError as error:
            raise WebSearchUnavailable("некорректный ответ сервиса поиска") from error
        results: list[WebSearchResult] = []
        for item in data.get("results") or []:
            title = str(item.get("title") or "").strip()
            url = str(item.get("url") or "").strip()
            snippet = str(item.get("content") or "").strip()[:SNIPPET_LIMIT]
            if title and url:
                results.append(WebSearchResult(title=title, url=url, snippet=snippet))
        return results


__all__ = ["TavilySearchProvider"]
