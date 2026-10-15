"""Тестовый AI-провайдер со скриптованными ответами."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from jarvis.ai.provider import InterpreterContext
from jarvis.ai.schemas import Interpretation


@dataclass
class FakeProvider:
    """Очередь заранее заданных интерпретаций; пустая очередь — дефолтная."""

    name: str = "fake"
    interpretations: deque[Interpretation] = field(default_factory=deque)
    replies: deque[str] = field(default_factory=deque)
    calls: list[str] = field(default_factory=list)

    def queue(self, interpretation: Interpretation) -> None:
        self.interpretations.append(interpretation)

    def queue_reply(self, reply: str) -> None:
        self.replies.append(reply)

    async def interpret(self, text: str, context: InterpreterContext) -> Interpretation:
        self.calls.append(text)
        if self.interpretations:
            return self.interpretations.popleft()
        return Interpretation(
            intent="create_knowledge",
            entities={"title": text.strip()[:80] or "Заметка", "content": text},
            confidence=0.9,
        )

    async def chat_reply(self, text: str, context: InterpreterContext) -> str:
        self.calls.append(f"reply:{text}")
        if self.replies:
            return self.replies.popleft()
        return "Это тестовый ответ."
