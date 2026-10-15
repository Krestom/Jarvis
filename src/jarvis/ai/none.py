"""Заготовка провайдера «AI выключен»: включает ограниченный режим."""

from __future__ import annotations

from jarvis.ai.provider import AIUnavailableError, InterpreterContext
from jarvis.ai.schemas import Interpretation, PlanStep


class NoneProvider:
    name = "none"

    async def interpret(self, text: str, context: InterpreterContext) -> Interpretation:
        raise AIUnavailableError("AI-провайдер не настроен")

    async def chat_reply(self, text: str, context: InterpreterContext) -> str:
        raise AIUnavailableError("AI-провайдер не настроен")

    async def plan(
        self, messages: list[dict[str, str]], context: InterpreterContext
    ) -> PlanStep:
        raise AIUnavailableError("AI-провайдер не настроен")
