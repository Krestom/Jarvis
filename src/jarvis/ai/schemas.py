"""Схемы структурированного вывода LLM (валидируются на стороне приложения)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Intent = Literal[
    "create_task",
    "create_knowledge",
    "search",
    "smalltalk",
    "clarify",
    "document_upload",
    "unknown",
]


class Interpretation(BaseModel):
    """Результат разбора пользовательского сообщения.

    LLM предлагает интерпретацию; backend проверяет её и решает,
    какая операция будет предложена пользователю.
    """

    intent: Intent = "unknown"
    entities: dict[str, Any] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    clarification_question: str | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    sensitivity_hints: list[str] = Field(default_factory=list)
    reply: str | None = None
