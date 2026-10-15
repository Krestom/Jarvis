"""Схемы структурированного вывода LLM (валидируются на стороне приложения)."""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator

UIAction = Literal[
    "search",
    "tasks_today",
    "tasks_week",
    "tasks_inbox",
    "documents",
    "projects",
    "settings",
    "diagnostics",
]

Intent = Literal[
    "create_task",
    "add_comment",
    "list_comments",
    "list_tasks",
    "create_knowledge",
    "create_project",
    "search",
    "smalltalk",
    "clarify",
    "document_upload",
    "unknown",
]


class ImprovementHypothesis(BaseModel):
    """Гипотеза о проблеме или упущенной возможности Jarvis из реплики пользователя."""

    category: Literal["failure", "missing_capability", "ux_friction", "improvement_idea"]
    title: str = Field(min_length=3, max_length=300)
    summary: str = Field(min_length=3, max_length=2000)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: str | None = None


class UIRequest(BaseModel):
    """Декларативный запрос UI: LLM называет намерение, backend строит клавиатуру.

    Каталог компонентов и действий — allow-list; всё, что вне его, отклоняется
    валидацией. Callback'и формирует сервер, модель их не придумывает.
    """

    component: Literal["quick_actions"]
    actions: list[UIAction] = Field(min_length=1, max_length=8)


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
    improvement_hypothesis: ImprovementHypothesis | None = None
    ui_request: UIRequest | None = None


# --- Агентский цикл (Stage 3): шаг планирования и UI-описания ----------------


class EntityChoice(BaseModel):
    """Выбор из реальных кандидатов; id проверяет сервер."""

    component: Literal["entity_choice"]
    entity_kind: Literal["task", "project", "knowledge"]
    entity_ids: list[uuid.UUID] = Field(min_length=1, max_length=6)
    prompt: str | None = Field(default=None, max_length=200)


AgentUI = Annotated[EntityChoice | UIRequest, Field(discriminator="component")]


class PlanStep(BaseModel):
    """Один шаг агента: вызвать инструмент или дать финальный ответ."""

    type: Literal["tool_call", "reply"]
    tool: str | None = Field(default=None, max_length=40)
    args: dict[str, Any] = Field(default_factory=dict)
    text: str | None = Field(default=None, max_length=4000)
    ui: AgentUI | None = None

    @model_validator(mode="after")
    def _check(self) -> PlanStep:
        if self.type == "tool_call" and not self.tool:
            raise ValueError("tool_call требует поле tool")
        if self.type == "reply" and not (self.text or "").strip():
            raise ValueError("reply требует непустой text")
        return self
