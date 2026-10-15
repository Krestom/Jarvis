"""Заготовка провайдера «AI выключен»: включает ограниченный режим."""

from __future__ import annotations

from jarvis.ai.provider import AIUnavailableError, InterpreterContext
from jarvis.ai.schemas import Interpretation


class NoneProvider:
    name = "none"

    async def interpret(self, text: str, context: InterpreterContext) -> Interpretation:
        raise AIUnavailableError("AI-провайдер не настроен")

    async def chat_reply(self, text: str, context: InterpreterContext) -> str:
        raise AIUnavailableError("AI-провайдер не настроен")
