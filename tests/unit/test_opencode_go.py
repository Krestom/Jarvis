"""Unit-тесты адаптера OpenCode Go (без сети, через httpx.MockTransport)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from jarvis.ai.opencode_go import USER_AGENT, OpenCodeGoProvider
from jarvis.ai.provider import AIError, InterpreterContext

CTX = InterpreterContext(
    now=datetime(2026, 10, 15, 12, 0, tzinfo=UTC),
    tz_name="Europe/Moscow",
    categories=["Работа"],
    session_id="tg-111",
)


def make_provider(handler, **overrides) -> OpenCodeGoProvider:
    values = {
        "api_key": "test-key",
        "base_url": "https://go.invalid/v1",
        "model": "deepseek-v4.1-flash",
        "timeout_seconds": 5.0,
        "max_attempts": 2,
        "transport": httpx.MockTransport(handler),
    }
    values.update(overrides)
    return OpenCodeGoProvider(**values)


def ok_response(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


async def test_interpret_parses_json_and_sends_expected_request():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        content = json.dumps(
            {
                "intent": "create_task",
                "entities": {"title": "Обновить инфраструктуру"},
                "confidence": 0.9,
            }
        )
        return ok_response(content)

    provider = make_provider(handler)
    result = await provider.interpret("Поставь задачу: обновить инфраструктуру", CTX)

    assert result.intent == "create_task"
    assert result.entities["title"] == "Обновить инфраструктуру"
    assert captured["headers"]["authorization"] == "Bearer test-key"
    assert captured["headers"]["x-opencode-session"] == "tg-111"
    assert captured["headers"]["user-agent"] == USER_AGENT
    body = captured["body"]
    assert body["model"] == "deepseek-v4.1-flash"
    assert body["response_format"]["type"] == "json_schema"
    assert "Работа" in body["messages"][1]["content"]


async def test_retry_on_server_error_then_success():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(500, text="boom")
        return ok_response(json.dumps({"intent": "smalltalk", "confidence": 0.5}))

    provider = make_provider(handler)
    result = await provider.interpret("привет", CTX)
    assert result.intent == "smalltalk"
    assert calls["count"] == 2


async def test_exhausted_retries_raise_ai_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    provider = make_provider(handler, max_attempts=2)
    with pytest.raises(AIError):
        await provider.interpret("привет", CTX)


async def test_client_error_raises_without_retry():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(403, text="forbidden")

    provider = make_provider(handler)
    with pytest.raises(AIError):
        await provider.interpret("привет", CTX)
    assert calls["count"] == 1


async def test_invalid_json_is_repaired_on_second_attempt():
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 1:
            return ok_response("это вообще не JSON")
        return ok_response(json.dumps({"intent": "search", "confidence": 0.8}))

    provider = make_provider(handler)
    result = await provider.interpret("найди что-нибудь", CTX)
    assert result.intent == "search"
    assert len(calls) == 2
    assert "response_format" not in calls[1]  # ремонтный запрос — без строгой схемы


async def test_invalid_json_twice_raises_ai_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return ok_response("не JSON")

    provider = make_provider(handler)
    with pytest.raises(AIError):
        await provider.interpret("что-то", CTX)


async def test_response_format_rejected_falls_back_to_plain():
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 1:
            return httpx.Response(400, text="response_format is not supported")
        fenced = (
            "```json\n"
            + json.dumps({"intent": "create_knowledge", "confidence": 0.7})
            + "\n```"
        )
        return ok_response(fenced)

    provider = make_provider(handler)
    result = await provider.interpret("запомни", CTX)
    assert result.intent == "create_knowledge"
    assert "response_format" in calls[0]
    assert "response_format" not in calls[1]


async def test_chat_reply_returns_text():
    def handler(request: httpx.Request) -> httpx.Response:
        return ok_response("Привет! Чем помочь?")

    provider = make_provider(handler)
    reply = await provider.chat_reply("привет", CTX)
    assert reply == "Привет! Чем помочь?"


async def test_long_prompt_is_truncated():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return ok_response(json.dumps({"intent": "unknown", "confidence": 0.1}))

    provider = make_provider(handler, max_context_chars=1000)
    await provider.interpret("x" * 5000, CTX)
    user_content = captured["body"]["messages"][1]["content"]
    assert len(user_content) <= 1100
