"""Интерфейс AI-провайдера и контекст интерпретации."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from jarvis.ai.schemas import Interpretation, PlanStep


class AIError(Exception):
    """Ошибка AI-провайдера (сеть, лимиты, неверный вывод)."""


class AIUnavailableError(AIError):
    """Провайдер не настроен или недоступен; доступен ограниченный режим."""


@dataclass
class InterpreterContext:
    """Минимальный безопасный контекст. Никаких секретов и документов."""

    now: datetime
    tz_name: str
    categories: list[str] = field(default_factory=list)
    projects: list[str] = field(default_factory=list)
    active_flow: str | None = None
    draft: dict[str, Any] | None = None
    session_id: str | None = None  # для x-opencode-session (prompt caching)
    usage: dict[str, Any] | None = None  # usage последнего вызова (если провайдер отдал)


class AIProvider(Protocol):
    name: str

    async def interpret(self, text: str, context: InterpreterContext) -> Interpretation: ...

    async def chat_reply(self, text: str, context: InterpreterContext) -> str: ...

    async def plan(
        self, messages: list[dict[str, str]], context: InterpreterContext
    ) -> PlanStep: ...
