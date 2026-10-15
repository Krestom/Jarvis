"""Интерфейс провайдера транскрибации."""

from __future__ import annotations

from typing import Protocol


class TranscriptionError(Exception):
    """Ошибка транскрибации (недоступен сервис, пустой результат)."""


class TranscriptionUnavailableError(TranscriptionError):
    """Провайдер не настроен — бот предложит прислать текст."""


class TranscriptionProvider(Protocol):
    name: str

    async def transcribe(
        self, audio: bytes, *, filename: str, mime_type: str | None = None
    ) -> str: ...
