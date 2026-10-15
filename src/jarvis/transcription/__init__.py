"""Фабрика провайдера транскрибации."""

from __future__ import annotations

from jarvis.config.settings import Settings
from jarvis.transcription.fake import FakeTranscription
from jarvis.transcription.openai_compatible import OpenAICompatibleTranscription
from jarvis.transcription.provider import (
    TranscriptionError,
    TranscriptionProvider,
    TranscriptionUnavailableError,
)

__all__ = [
    "TranscriptionError",
    "TranscriptionProvider",
    "TranscriptionUnavailableError",
    "create_transcription_provider",
]


def create_transcription_provider(settings: Settings) -> TranscriptionProvider | None:
    """None означает «провайдер не настроен» — бот предложит текст."""
    provider = settings.transcription_provider.strip().lower()
    if provider in ("", "none", "off", "disabled"):
        return None
    if provider == "fake":
        return FakeTranscription()
    if provider == "openai_compatible":
        if not settings.transcription_base_url:
            raise ValueError("TRANSCRIPTION_BASE_URL is not set")
        return OpenAICompatibleTranscription(
            base_url=settings.transcription_base_url,
            api_key=settings.transcription_api_key.get_secret_value(),
            model=settings.transcription_model,
            language=settings.transcription_language,
            timeout_seconds=settings.transcription_timeout_seconds,
        )
    raise ValueError(f"unknown transcription provider: {settings.transcription_provider!r}")
