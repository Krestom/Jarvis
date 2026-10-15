"""Фабрика AI-провайдера по конфигурации."""

from __future__ import annotations

from jarvis.ai.fake import FakeProvider
from jarvis.ai.none import NoneProvider
from jarvis.ai.provider import AIError, AIProvider, AIUnavailableError, InterpreterContext
from jarvis.ai.schemas import Interpretation
from jarvis.config.settings import Settings


def create_ai_provider(settings: Settings) -> AIProvider:
    provider = settings.ai_provider.strip().lower()
    if provider in ("", "none", "off", "disabled"):
        return NoneProvider()
    if provider == "fake":
        return FakeProvider()
    if provider == "opencode_go":
        from jarvis.ai.opencode_go import OpenCodeGoProvider  # ленивый импорт

        problems = []
        if not settings.opencode_go_api_key.get_secret_value():
            problems.append("OPENCODE_GO_API_KEY is not set")
        if problems:
            raise ValueError("; ".join(problems))
        return OpenCodeGoProvider(
            api_key=settings.opencode_go_api_key.get_secret_value(),
            base_url=settings.opencode_go_base_url,
            model=settings.ai_model,
            timeout_seconds=settings.ai_timeout_seconds,
            max_attempts=settings.ai_max_attempts,
            max_context_chars=settings.ai_max_context_chars,
        )
    raise ValueError(f"unknown AI provider: {settings.ai_provider!r}")


__all__ = [
    "AIError",
    "AIProvider",
    "AIUnavailableError",
    "InterpreterContext",
    "Interpretation",
    "create_ai_provider",
]
