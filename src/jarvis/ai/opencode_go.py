"""Адаптер OpenCode Go API (OpenAI-совместимый протокол /chat/completions).

Проверено по документации https://opencode.ai/docs/go/:
- у моделей Go есть три семейства протоколов; этот адаптер реализует
  OpenAI-совместимый chat/completions (DeepSeek V4.1 Flash, GLM, Kimi, MiMo...);
- модели семейств /messages и /responses требуют других адаптеров;
- рекомендуется собственный User-Agent и заголовок x-opencode-session.

Адаптер не логирует содержимое запросов и ответов (персональные данные).
Общий HTTP-клиент (`OpenCodeGoClient`) используется и интерпретацией, и
агентским циклом (Stage 3), поэтому usage приходит из одного места.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import ValidationError

from jarvis.ai.prompts import SYSTEM_PROMPT, TELEGRAM_FORMAT_RULES, build_user_prompt
from jarvis.ai.provider import AIError, InterpreterContext
from jarvis.ai.schemas import Interpretation, PlanStep

log = logging.getLogger(__name__)

USER_AGENT = "jarvis-knowledge-os/0.1"
DEFAULT_SESSION_ID = "jarvis"
MAX_RESPONSE_CHARS = 20000  # защита от неожиданно длинных ответов
MAX_ANSWER_TOKENS = 1200
REPAIR_INSTRUCTION = (
    "Предыдущий ответ не соответствует требуемой JSON-схеме. "
    "Верни ТОЛЬКО корректный JSON-объект по схеме, без пояснений и markdown."
)


@dataclass
class ChatResult:
    content: str
    usage: dict[str, Any] | None = None


def _parse_json_model(content: str, model_cls):
    candidate = content.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", candidate, re.S)
    if fence:
        candidate = fence.group(1)
    else:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start != -1 and end > start:
            candidate = candidate[start : end + 1]
    # strict=False: модели иногда кладут реальные переносы строк внутрь JSON-строки —
    # это не повод для «ремонтного» запроса, который склеивает текст в одну строку.
    payload = json.loads(candidate, strict=False)
    return model_cls.model_validate(payload)


class OpenCodeGoClient:
    """HTTP-клиент с повторами и fallback без response_format.

    protocol: "chat" — OpenAI-совместимый chat/completions (DeepSeek, GLM, Kimi,
    MiMo...); "responses" — OpenAI Responses API (GPT-6 Luna, Grok 4.7, Muse Spark).
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        protocol: str = "chat",
        timeout_seconds: float = 60.0,
        max_attempts: int = 2,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("OPENCODE_GO_API_KEY is not set")
        if not base_url:
            raise ValueError("OPENCODE_GO_BASE_URL is not set")
        if protocol not in ("chat", "responses"):
            raise ValueError(f"unknown protocol: {protocol!r}")
        suffix = "/responses" if protocol == "responses" else "/chat/completions"
        self._url = base_url.rstrip("/") + suffix
        self._protocol = protocol
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_seconds
        self._max_attempts = max(1, max_attempts)
        self._transport = transport

    def _headers(self, session_id: str | None) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "User-Agent": USER_AGENT,
            "x-opencode-session": session_id or DEFAULT_SESSION_ID,
        }

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        session_id: str | None,
        response_format: dict | None = None,
        use_response_format: bool = False,
        max_tokens: int = MAX_ANSWER_TOKENS,
    ) -> ChatResult:
        payload: dict = {"model": self._model}
        if self._protocol == "responses":
            # Responses API: input вместо messages; JSON-схему не навязываем —
            # промпты и так требуют JSON, а разбор устойчив к тексту вокруг.
            payload["input"] = messages
            payload["max_output_tokens"] = max_tokens
        else:
            payload["messages"] = messages
            payload["temperature"] = 0.1
            payload["max_tokens"] = max_tokens
            if use_response_format and response_format is not None:
                payload["response_format"] = response_format

        last_error: Exception | None = None
        for attempt in range(self._max_attempts):
            if attempt:
                await asyncio.sleep(min(0.5 * 2**attempt, 4.0))
            try:
                async with httpx.AsyncClient(
                    timeout=self._timeout, transport=self._transport
                ) as client:
                    response = await client.post(
                        self._url, json=payload, headers=self._headers(session_id)
                    )
            except httpx.HTTPError as error:
                last_error = error
                log.warning("opencode-go request failed (attempt %s): %s", attempt + 1, error)
                continue

            if response.status_code == 400 and "response_format" in payload:
                # Модель не поддерживает response_format: повторяем без него,
                # JSON извлечём из текста/фенсов.
                log.warning("opencode-go: response_format rejected, falling back to plain JSON")
                payload.pop("response_format", None)
                continue
            if response.status_code == 429 or response.status_code >= 500:
                last_error = AIError(f"временная ошибка сервиса (HTTP {response.status_code})")
                log.warning("opencode-go: HTTP %s, retrying", response.status_code)
                continue
            if response.status_code >= 400:
                log.error("opencode-go: request rejected with HTTP %s", response.status_code)
                raise AIError(f"AI-сервис вернул ошибку (HTTP {response.status_code})")

            try:
                data = response.json()
                content = self._extract_content(data)
            except (ValueError, KeyError, IndexError, TypeError) as error:
                raise AIError("AI-сервис вернул неожиданный ответ") from error
            if not isinstance(content, str) or not content.strip():
                raise AIError("AI-сервис вернул пустой ответ")
            usage = data.get("usage")
            return ChatResult(
                content=content[:MAX_RESPONSE_CHARS],
                usage=usage if isinstance(usage, dict) else None,
            )

        raise AIError("AI-сервис недоступен, попробуйте позже") from last_error

    def _extract_content(self, data: dict) -> str:
        """Текст ответа по правилам выбранного протокола."""
        if self._protocol == "responses":
            for item in data.get("output") or []:
                if item.get("type") != "message":
                    continue
                for block in item.get("content") or []:
                    if block.get("type") == "output_text" and block.get("text"):
                        return block["text"]
            raise KeyError("output_text")
        return data["choices"][0]["message"]["content"]


def _usage_tokens(usage: dict[str, Any] | None) -> int | None:
    """Совместимость: см. jarvis.agent.budget.usage_tokens."""

    if not usage:
        return None
    total = usage.get("total_tokens")
    return int(total) if isinstance(total, int) else None


class OpenCodeGoProvider:
    """AIProvider поверх OpenCode Go (chat/completions или Responses API)."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        protocol: str = "chat",
        timeout_seconds: float = 60.0,
        max_attempts: int = 2,
        max_context_chars: int = 20000,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.name = f"opencode_go:{model}"
        self.protocol = protocol
        self._client = OpenCodeGoClient(
            api_key=api_key,
            base_url=base_url,
            model=model,
            protocol=protocol,
            timeout_seconds=timeout_seconds,
            max_attempts=max_attempts,
            transport=transport,
        )
        self._max_context_chars = max(1000, max_context_chars)

    # --- AIProvider ---

    async def interpret(self, text: str, context: InterpreterContext) -> Interpretation:
        prompt = build_user_prompt(text, context)[: self._max_context_chars]
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
        schema = Interpretation.model_json_schema()
        response_format = {
            "type": "json_schema",
            "json_schema": {"name": "interpretation", "strict": False, "schema": schema},
        }
        result = await self._client.chat(
            messages,
            session_id=context.session_id,
            response_format=response_format,
            use_response_format=True,
        )
        context.usage = result.usage
        try:
            return _parse_json_model(result.content, Interpretation)
        except (ValueError, ValidationError):
            log.warning("opencode-go: invalid structured output, asking for repair")
            repaired = await self._client.chat(
                [
                    *messages,
                    {"role": "assistant", "content": result.content[:4000]},
                    {"role": "user", "content": REPAIR_INSTRUCTION},
                ],
                session_id=context.session_id,
                response_format=response_format,
                use_response_format=False,
            )
            context.usage = repaired.usage
            try:
                return _parse_json_model(repaired.content, Interpretation)
            except (ValueError, ValidationError) as second_error:
                log.warning("opencode-go: repair attempt also failed")
                raise AIError(
                    "не удалось получить корректный структурированный ответ"
                ) from second_error

    async def chat_reply(self, text: str, context: InterpreterContext) -> str:
        prompt = build_user_prompt(text, context)[: self._max_context_chars]
        messages = [
            {
                "role": "system",
                "content": (
                    "Ты — дружелюбный личный ассистент Jarvis. Отвечай кратко, по-русски, "
                    "без выполнения действий: любые изменения предлагает backend "
                    "после подтверждения.\n\n" + TELEGRAM_FORMAT_RULES
                ),
            },
            {"role": "user", "content": prompt},
        ]
        result = await self._client.chat(messages, session_id=context.session_id)
        context.usage = result.usage
        return result.content

    async def plan(
        self, messages: list[dict[str, str]], context: InterpreterContext
    ) -> PlanStep:
        """Шаг агентского цикла: структурированный JSON по схеме PlanStep."""
        schema = PlanStep.model_json_schema()
        response_format = {
            "type": "json_schema",
            "json_schema": {"name": "plan_step", "strict": False, "schema": schema},
        }
        result = await self._client.chat(
            messages,
            session_id=context.session_id,
            response_format=response_format,
            use_response_format=True,
        )
        context.usage = result.usage
        try:
            return _parse_json_model(result.content, PlanStep)
        except (ValueError, ValidationError):
            log.warning("opencode-go: invalid plan step, asking for repair")
            repaired = await self._client.chat(
                [
                    *messages,
                    {"role": "assistant", "content": result.content[:4000]},
                    {"role": "user", "content": REPAIR_INSTRUCTION},
                ],
                session_id=context.session_id,
                response_format=response_format,
                use_response_format=False,
            )
            context.usage = repaired.usage
            try:
                return _parse_json_model(repaired.content, PlanStep)
            except (ValueError, ValidationError) as second_error:
                log.warning("opencode-go: plan repair attempt also failed")
                raise AIError("не удалось получить корректный шаг агента") from second_error
