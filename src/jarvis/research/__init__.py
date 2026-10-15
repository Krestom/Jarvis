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
    raise ValueError(
        f"unknown web search provider: {settings.web_search_provider!r} "
        "(доступны: none, fake)"
    )


__all__ = ["create_search_provider"]
