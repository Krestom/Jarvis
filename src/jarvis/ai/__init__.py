"""Фабрика AI-провайдера по конфигурации."""

from __future__ import annotations

from jarvis.ai.fake import FakeProvider
from jarvis.ai.none import NoneProvider
from jarvis.ai.provider import AIError, AIProvider, AIUnavailableError, InterpreterContext
from jarvis.ai.schemas import Interpretation
from jarvis.config.settings import Settings

# Модели OpenCode Go по протоколам (https://opencode.ai/docs/go/):
# responses — OpenAI Responses API; messages — Anthropic (адаптера пока нет).
RESPONSES_PROTOCOL_MODELS = {
    "gpt-6-luna",
    "gpt-5.6-luna",
    "grok-4.7",
    "muse-spark-1.2-contributor",
    "muse-spark-1.3-contributor",
}
ANTHROPIC_PROTOCOL_MODELS = {
    "claude-haiku-5-5",
    "qwen3.8-flash",
    "qwen3.7-plus",
    "qwen3.8-max",
    "minimax-m3",
}


def create_ai_provider(settings: Settings) -> AIProvider:
    provider = settings.ai_provider.strip().lower()
    if provider in ("", "none", "off", "disabled"):
        return NoneProvider()
    if provider == "fake":
        return FakeProvider()
    if provider == "opencode_go":
        from jarvis.ai.opencode_go import OpenCodeGoProvider  # ленивый импорт

        model = settings.ai_model.strip().lower()
        if model in ANTHROPIC_PROTOCOL_MODELS:
            raise ValueError(
                f"модель {model!r} говорит на протоколе Anthropic (/messages) — "
                "адаптер не реализован; выберите модель семейств chat/completions "
                "или responses"
            )
        protocol = "responses" if model in RESPONSES_PROTOCOL_MODELS else "chat"
        problems = []
        if not settings.opencode_go_api_key.get_secret_value():
            problems.append("OPENCODE_GO_API_KEY is not set")
        if problems:
            raise ValueError("; ".join(problems))
        return OpenCodeGoProvider(
            api_key=settings.opencode_go_api_key.get_secret_value(),
            base_url=settings.opencode_go_base_url,
            model=settings.ai_model,
            protocol=protocol,
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
