"""Unit-тесты OpenAI-совместимого адаптера транскрибации (httpx.MockTransport)."""

from __future__ import annotations

import httpx
import pytest

from jarvis.transcription.openai_compatible import OpenAICompatibleTranscription
from jarvis.transcription.provider import TranscriptionError


def make_provider(handler) -> OpenAICompatibleTranscription:
    return OpenAICompatibleTranscription(
        base_url="https://stt.invalid/v1",
        api_key="test-key",
        model="whisper-1",
        language="ru",
        transport=httpx.MockTransport(handler),
    )


async def test_transcribe_success_and_request_shape():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["content_type"] = request.headers.get("content-type", "")
        captured["body"] = request.content
        return httpx.Response(200, json={"text": "  обновить инфраструктуру  "})

    provider = make_provider(handler)
    text = await provider.transcribe(b"ogg-bytes", filename="voice.ogg", mime_type="audio/ogg")

    assert text == "обновить инфраструктуру"
    assert captured["url"].endswith("/audio/transcriptions")
    assert captured["auth"] == "Bearer test-key"
    assert captured["content_type"].startswith("multipart/form-data")
    assert b"whisper-1" in captured["body"]
    assert b"ogg-bytes" in captured["body"]


async def test_transcribe_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with pytest.raises(TranscriptionError, match="ошибку"):
        await make_provider(handler).transcribe(b"x", filename="v.ogg")


async def test_transcribe_network_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    with pytest.raises(TranscriptionError, match="недоступен"):
        await make_provider(handler).transcribe(b"x", filename="v.ogg")


async def test_transcribe_empty_result():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"text": "   "})

    with pytest.raises(TranscriptionError, match="пустой"):
        await make_provider(handler).transcribe(b"x", filename="v.ogg")


async def test_transcribe_non_json_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    with pytest.raises(TranscriptionError, match="некорректный"):
        await make_provider(handler).transcribe(b"x", filename="v.ogg")


async def test_missing_base_url_rejected():
    with pytest.raises(ValueError, match="TRANSCRIPTION_BASE_URL"):
        OpenAICompatibleTranscription(base_url="", api_key="k", model="whisper-1")
