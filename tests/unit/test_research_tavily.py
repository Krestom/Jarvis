"""Юнит-тесты провайдера веб-поиска Tavily (без сети — MockTransport)."""

from __future__ import annotations

import httpx
import pytest

from jarvis.research.provider import WebSearchUnavailable
from jarvis.research.tavily import TavilySearchProvider


def _transport(payload: dict, status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload)

    return httpx.MockTransport(handler)


async def test_requires_api_key():
    with pytest.raises(ValueError):
        TavilySearchProvider(api_key="")


async def test_parses_results_and_sends_authorization():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "results": [
                    {"title": "Дока", "url": "https://example.com/docs", "content": "Текст"},
                    {"title": "", "url": "", "content": "мусор без заголовка"},
                ]
            },
        )

    provider = TavilySearchProvider(api_key="k", transport=httpx.MockTransport(handler))
    results = await provider.search("настройка nginx", limit=3)
    assert [result.title for result in results] == ["Дока"]
    assert results[0].url == "https://example.com/docs"
    assert results[0].snippet == "Текст"
    assert seen["auth"] == "Bearer k"
    assert seen["body"]["max_results"] == 3


async def test_snippet_is_truncated():
    provider = TavilySearchProvider(
        api_key="k",
        transport=_transport(
            {"results": [{"title": "T", "url": "https://x", "content": "я" * 900}]}
        ),
    )
    results = await provider.search("q")
    assert len(results[0].snippet) == 500


async def test_http_error_maps_to_unavailable():
    provider = TavilySearchProvider(api_key="k", transport=_transport({}, status=429))
    with pytest.raises(WebSearchUnavailable):
        await provider.search("q")


async def test_broken_json_maps_to_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not-json")

    provider = TavilySearchProvider(api_key="k", transport=httpx.MockTransport(handler))
    with pytest.raises(WebSearchUnavailable):
        await provider.search("q")
