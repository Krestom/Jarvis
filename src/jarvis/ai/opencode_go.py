"""Адаптер OpenCode Go API (OpenAI-совместимый протокол /chat/completions).

Проверено по документации https://opencode.ai/docs/go/:
- у моделей Go есть три семейства протоколов; этот адаптер реализует
  OpenAI-совместимый chat/completions (DeepSeek V4.1 Flash, GLM, Kimi, MiMo...);
- модели семейств /messages и /responses требуют других адаптеров;
- рекомендуется собственный User-Agent и заголовок x-opencode-session.

Адаптер не логирует содержимое запросов и ответов (персональные данные).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

import httpx
from pydantic import ValidationError

from jarvis.ai.prompts import SYSTEM_PROMPT, build_user_prompt
from jarvis.ai.provider import AIError, InterpreterContext
from jarvis.ai.schemas import Interpretation

log = logging.getLogger(__name__)

USER_AGENT = "jarvis-knowledge-os/0.1"
DEFAULT_SESSION_ID = "jarvis"
MAX_RESPONSE_CHARS = 20000  # защита от неожиданно длинных ответов
MAX_ANSWER_TOKENS = 1200
REPAIR_INSTRUCTION = (
    "Предыдущий ответ не соответствует требуемой JSON-схеме. "
    "Верни ТОЛЬКО корректный JSON-объект по схеме, без пояснений и markdown."
)


class OpenCodeGoProvider:
    """AIProvider поверх OpenCode Go (OpenAI-совместимый протокол)."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float = 60.0,
        max_attempts: int = 2,
        max_context_chars: int = 20000,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("OPENCODE_GO_API_KEY is not set")
        if not base_url:
            raise ValueError("OPENCODE_GO_BASE_URL is not set")
        self.name = f"opencode_go:{model}"
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_seconds
        self._max_attempts = max(1, max_attempts)
        self._max_context_chars = max(1000, max_context_chars)
        self._transport = transport

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
        content = await self._chat(
            messages,
            session_id=context.session_id,
            response_format=response_format,
            use_response_format=True,
        )
        try:
            return self._parse_interpretation(content)
        except (ValueError, ValidationError):
            log.warning("opencode-go: invalid structured output, asking for repair")
            repaired = await self._chat(
                [
                    *messages,
                    {"role": "assistant", "content": content[:4000]},
                    {"role": "user", "content": REPAIR_INSTRUCTION},
                ],
                session_id=context.session_id,
                response_format=response_format,
                use_response_format=False,
            )
            try:
                return self._parse_interpretation(repaired)
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
                    "после подтверждения."
                ),
            },
            {"role": "user", "content": prompt},
        ]
        return await self._chat(messages, session_id=context.session_id)

    # --- Внутреннее ---

    @staticmethod
    def _parse_interpretation(content: str) -> Interpretation:
        candidate = content.strip()
        fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", candidate, re.S)
        if fence:
            candidate = fence.group(1)
        else:
            start = candidate.find("{")
            end = candidate.rfind("}")
            if start != -1 and end > start:
                candidate = candidate[start : end + 1]
        payload = json.loads(candidate)
        return Interpretation.model_validate(payload)

    def _headers(self, session_id: str | None) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "User-Agent": USER_AGENT,
            "x-opencode-session": session_id or DEFAULT_SESSION_ID,
        }

    async def _chat(
        self,
        messages: list[dict[str, str]],
        *,
        session_id: str | None,
        response_format: dict | None = None,
        use_response_format: bool = False,
    ) -> str:
        payload: dict = {
            "model": self._model,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": MAX_ANSWER_TOKENS,
        }
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
                        self._url,
                        json=payload,
                        headers=self._headers(session_id),
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
                content = data["choices"][0]["message"]["content"]
            except (ValueError, KeyError, IndexError, TypeError) as error:
                raise AIError("AI-сервис вернул неожиданный ответ") from error
            if not isinstance(content, str) or not content.strip():
                raise AIError("AI-сервис вернул пустой ответ")
            return content[:MAX_RESPONSE_CHARS]

        raise AIError("AI-сервис недоступен, попробуйте позже") from last_error
