"""Фабрика провайдера веб-поиска по конфигурации (WEB_SEARCH_PROVIDER)."""

from __future__ import annotations

from jarvis.config.settings import Settings
from jarvis.research.none import NoneSearchProvider
from jarvis.research.provider import WebSearchProvider


def create_search_provider(settings: Settings) -> WebSearchProvider:
    provider = settings.web_search_provider.strip().lower()
    if provider in ("", "none", "off", "disabled"):
        return NoneSearchProvider()
    if provider == "fake":
        from jarvis.research.fake import FakeSearchProvider

        return FakeSearchProvider()
    if provider == "tavily":
        from jarvis.research.tavily import TavilySearchProvider

        return TavilySearchProvider(api_key=settings.tavily_api_key.get_secret_value())
    raise ValueError(
        f"unknown web search provider: {settings.web_search_provider!r} "
        "(доступны: none, fake, tavily)"
    )


__all__ = ["create_search_provider"]
