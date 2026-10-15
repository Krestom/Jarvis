"""OpenAI-совместимая транскрибация (/audio/transcriptions).

Работает с OpenAI, Groq и локальными whisper-серверами с совместимым API.
Важно: аудио уходит во внешний сервис только если провайдер настроен явно.
"""

from __future__ import annotations

import logging

import httpx

from jarvis.transcription.provider import TranscriptionError

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 120.0


class OpenAICompatibleTranscription:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        language: str = "ru",
        timeout_seconds: float = TIMEOUT_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("TRANSCRIPTION_BASE_URL is not set")
        self.name = f"openai_compatible:{model}"
        self._url = base_url.rstrip("/") + "/audio/transcriptions"
        self._api_key = api_key
        self._model = model
        self._language = language
        self._timeout = timeout_seconds
        self._transport = transport

    async def transcribe(
        self, audio: bytes, *, filename: str, mime_type: str | None = None
    ) -> str:
        headers = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        files = {"file": (filename, audio, mime_type or "application/octet-stream")}
        data = {"model": self._model}
        if self._language:
            data["language"] = self._language
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.post(self._url, headers=headers, files=files, data=data)
        except httpx.HTTPError as error:
            raise TranscriptionError("сервис транскрибации недоступен") from error
        if response.status_code >= 400:
            log.warning("transcription API error status=%s", response.status_code)
            raise TranscriptionError("сервис транскрибации вернул ошибку")
        try:
            payload = response.json()
        except ValueError as error:
            raise TranscriptionError("некорректный ответ сервиса транскрибации") from error
        text = str(payload.get("text", "")).strip()
        if not text:
            raise TranscriptionError("транскрибация вернула пустой результат")
        return text
