"""Оркестратор агентского цикла: LLM → инструменты → UI, в рамках бюджета."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.agent.budget import (
    BudgetExhausted,
    ExecutionBudget,
    RequestCancelled,
    usage_tokens,
)
from jarvis.agent.prompts import build_context_block, build_system_prompt
from jarvis.agent.queue import QueueTimeout, RequestHandle, UserSessionGate
from jarvis.agent.routing import AGENT_FLOW, build_profile, detect_profile
from jarvis.agent.schemas import EntityCandidate, OutgoingFile
from jarvis.agent.tools import ToolRegistry, ToolResult
from jarvis.ai import AIError, AIProvider, AIUnavailableError, InterpreterContext
from jarvis.ai.prompts import format_llm_text
from jarvis.ai.schemas import EntityChoice, PlanStep, ScreenRequest, UIRequest
from jarvis.config.settings import Settings
from jarvis.conversation.state import ConversationStore
from jarvis.repositories.conversation import ConversationRepository
from jarvis.repositories.knowledge import KnowledgeRepository
from jarvis.repositories.projects import ProjectRepository
from jarvis.repositories.tasks import TaskRepository
from jarvis.research import create_search_provider
from jarvis.research.provider import WebSearchProvider
from jarvis.services.tasks import TASK_VIEWS

log = logging.getLogger(__name__)

MESSAGES_CHAR_BUDGET = 60000
PARTIAL_TEMPLATE = "Не успел довести до конца ({reason}). Что удалось выяснить: {findings}"


@dataclass
class AgentResult:
    text: str
    termination: str = "completed"
    profile: str = "ordinary"
    steps: int = 0
    tool_calls: int = 0
    llm_calls: int = 0
    tokens: int | None = None
    duration_ms: int = 0
    pending_operation_ids: list[str] = field(default_factory=list)
    quick_actions: list[str] = field(default_factory=list)
    entity_choices: list[EntityCandidate] = field(default_factory=list)
    choice_prompt: str | None = None
    surfaced: list[EntityCandidate] = field(default_factory=list)
    # Детерминированный экран, который попросил открыть агент (name, params).
    screen: tuple[str, dict] | None = None
    # Файлы к отправке в чат (документ, экспорт чата) — собираются инструментами.
    files: list[OutgoingFile] = field(default_factory=list)
    # Текст исходного запроса — для короткой памяти диалога.
    request_text: str = ""


class AgentService:
    """Один агентский запрос: планирование, инструменты, контекст, бюджет."""

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        ai: AIProvider,
        *,
        gate: UserSessionGate,
        search_provider: WebSearchProvider | None = None,
        correlation_id: str | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._ai = ai
        self._gate = gate
        self._search_provider = search_provider or create_search_provider(settings)
        self._correlation_id = correlation_id
        self._store = ConversationStore(ConversationRepository(session), settings)

    async def run(
        self, telegram_user_id: int, text: str, *, profile_name: str | None = None
    ) -> AgentResult:
        profile_name = profile_name or detect_profile(text, self._settings)
        profile = build_profile(profile_name, self._settings)
        started = time.monotonic()
        try:
            async with self._gate.acquire(telegram_user_id) as handle:
                result = await self._run_locked(handle, telegram_user_id, text, profile.name)
        except QueueTimeout:
            log.info("agent queue timeout for user %s", telegram_user_id)
            return AgentResult(
                text="Я ещё занят предыдущим запросом. Попробуйте ещё раз чуть позже.",
                termination="queue_timeout",
                profile=profile.name,
            )
        result.duration_ms = int((time.monotonic() - started) * 1000)
        result.request_text = text
        await self._save_context(telegram_user_id, result)
        log.info(
            "agent request done user=%s profile=%s termination=%s "
            "steps=%s tools=%s llm=%s tokens=%s ms=%s",
            telegram_user_id,
            result.profile,
            result.termination,
            result.steps,
            result.tool_calls,
            result.llm_calls,
            result.tokens,
            result.duration_ms,
        )
        return result

    # --- Цикл -----------------------------------------------------------------

    async def _run_locked(
        self, handle: RequestHandle, telegram_user_id: int, text: str, profile_name: str
    ) -> AgentResult:
        settings = self._settings
        budget = ExecutionBudget(
            build_profile(profile_name, settings),
            max_llm_calls=settings.agent_max_llm_calls,
            max_repeat_tool_calls=settings.agent_max_repeat_tool_calls,
            token_soft_limit=settings.agent_token_soft_limit,
            token_hard_limit=settings.agent_token_hard_limit,
        )
        registry = ToolRegistry(
            self._session,
            settings,
            telegram_user_id=telegram_user_id,
            search_provider=self._search_provider,
            correlation_id=self._correlation_id,
        )
        stored = await self._stored_context(telegram_user_id)
        context = InterpreterContext(
            now=datetime.now(settings.tz),
            tz_name=settings.app_timezone,
            session_id=f"tg-{telegram_user_id}",
        )
        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": build_system_prompt(
                    registry.catalog(),
                    now=context.now,
                    tz_name=settings.app_timezone,
                    profile=profile_name,
                    web_available=self._search_provider.name != "none",
                ),
            },
            {
                "role": "user",
                "content": build_context_block(
                    text,
                    selected=stored.get("agent_selected"),
                    candidates=stored.get("agent_candidates") or [],
                    history=stored.get("agent_history") or [],
                ),
            },
        ]

        result = AgentResult(text="", profile=profile_name)
        findings: list[str] = []
        try:
            while True:
                budget.check()
                handle.raise_if_cancelled()
                step = await self._plan(messages, context, budget)
                # Поздний результат после отмены не применяем.
                handle.raise_if_cancelled()
                budget.note_step()
                budget.note_llm_call()
                budget.note_usage(usage_tokens(context.usage))

                if step.type == "reply":
                    result.text = format_llm_text((step.text or "").strip())
                    await self._apply_ui(step, registry, stored, result)
                    break

                tool_name = step.tool or ""
                args = step.args or {}
                budget.note_tool_call(tool_name, args)
                if tool_name == "web_search":
                    budget.note_web_query()
                tool_result = await self._execute_tool(registry, tool_name, args, budget, handle)
                budget.note_result(tool_result.ok)
                if tool_result.ok:
                    summary = _finding(tool_name, tool_result)
                    if summary:
                        findings.append(summary)
                messages.append(
                    {
                        "role": "assistant",
                        "content": json.dumps(
                            step.model_dump(mode="json"), ensure_ascii=False
                        ),
                    }
                )
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"Результат инструмента {tool_name}: "
                            + json.dumps(tool_result.payload(), ensure_ascii=False)
                        ),
                    }
                )
                messages = _trim_messages(messages)
        except BudgetExhausted as error:
            result.termination = error.reason
            result.text = await self._wrap_up(messages, context, budget, error.reason, findings)
        except RequestCancelled:
            result.termination = "cancelled"
            result.text = "Остановил выполнение по вашей команде. Уже выполненные шаги сохранены."
        except AIUnavailableError:
            result.termination = "unavailable"
            result.text = "AI-сервис не настроен — не могу выполнить агентский запрос."
        except AIError:
            log.exception("agent planner failed")
            result.termination = "ai_error"
            result.text = "AI-сервис временно недоступен. Попробуйте позже."

        result.steps = budget.steps
        result.tool_calls = budget.tool_calls
        result.llm_calls = budget.llm_calls
        result.tokens = budget.tokens if budget.has_usage_data else None
        result.pending_operation_ids = list(registry.pending_operation_ids)
        result.surfaced = registry.surfaced
        result.files = list(registry.outgoing_files)
        if not result.text:
            result.text = "Не получилось сформулировать ответ. Попробуйте переформулировать запрос."
        return result

    # --- Шаги -----------------------------------------------------------------

    async def _plan(
        self, messages: list[dict[str, str]], context: InterpreterContext, budget: ExecutionBudget
    ) -> PlanStep:
        remaining = budget.remaining_seconds()
        if remaining <= 0:
            raise BudgetExhausted("timeout")
        try:
            return await asyncio.wait_for(self._ai.plan(messages, context), timeout=remaining)
        except TimeoutError:
            raise BudgetExhausted("timeout") from None

    async def _execute_tool(
        self,
        registry: ToolRegistry,
        name: str,
        args: dict,
        budget: ExecutionBudget,
        handle: RequestHandle,
    ) -> ToolResult:
        attempts = 1 + max(0, self._settings.agent_max_tool_retries)
        result = ToolResult.failure("internal", "инструмент не вызывался")
        for _ in range(attempts):
            handle.raise_if_cancelled()
            budget.check()
            result = await registry.call(name, args)
            if result.ok or result.error_kind != "transient":
                break
        return result

    async def _wrap_up(
        self,
        messages: list[dict[str, str]],
        context: InterpreterContext,
        budget: ExecutionBudget,
        reason: str,
        findings: list[str],
    ) -> str:
        """Один финальный проход без инструментов: честный частичный итог."""
        notes = (
            f"Бюджет запроса исчерпан ({reason}). НЕ вызывай инструменты. "
            "Кратко (2-4 предложения) скажи пользователю, что уже известно из "
            "результатов выше и что осталось незавершённым. Формат: reply."
        )
        try:
            if budget.remaining_seconds() > 1 and budget.llm_calls < budget.max_llm_calls:
                step = await asyncio.wait_for(
                    self._ai.plan([*messages, {"role": "user", "content": notes}], context),
                    timeout=min(10.0, budget.remaining_seconds()),
                )
                budget.note_llm_call()
                budget.note_usage(usage_tokens(context.usage))
                if step.type == "reply" and (step.text or "").strip():
                    return format_llm_text(step.text.strip())
        except (AIError, TimeoutError, BudgetExhausted):
            log.info("agent wrap-up skipped (%s)", reason)
        findings_text = "; ".join(findings[-3:]) if findings else "ничего подтверждённого"
        return PARTIAL_TEMPLATE.format(
            reason=_REASON_LABELS.get(reason, reason), findings=findings_text
        )

    # --- UI и контекст --------------------------------------------------------

    async def _apply_ui(
        self,
        step: PlanStep,
        registry: ToolRegistry,
        stored: dict,
        result: AgentResult,
    ) -> None:
        if step.ui is None:
            return
        if isinstance(step.ui, UIRequest):
            result.quick_actions = list(step.ui.actions)
            return
        if isinstance(step.ui, ScreenRequest):
            screen = self._validate_screen(step.ui)
            if screen is not None:
                result.screen = screen
            else:
                log.info("agent screen request dropped: %s", step.ui.model_dump())
            return
        if isinstance(step.ui, EntityChoice):
            choices = await self._validate_choices(step.ui, registry, stored)
            if choices:
                result.entity_choices = choices
                result.choice_prompt = step.ui.prompt or "Уточните, что имелось в виду:"
            else:
                log.info("agent entity_choice dropped: no valid candidates")

    def _validate_screen(self, ui: ScreenRequest) -> tuple[str, dict] | None:
        """Экраны — allow-list; параметры проверяются, иначе запрос отбрасываем."""
        params = dict(ui.params or {})
        if ui.name == "tasks":
            clean: dict = {}
            view = str(params.get("view") or "").strip()
            if view:
                if view not in TASK_VIEWS:
                    return None
                clean["view"] = view
            on_date = str(params.get("on_date") or "").strip()
            if on_date:
                try:
                    clean["on_date"] = date.fromisoformat(on_date)
                except ValueError:
                    return None
            category = str(params.get("category") or "").strip()
            if category:
                clean["category"] = category[:120]
            return ("tasks", clean)
        if ui.name == "search":
            query = str(params.get("query") or "").strip()
            if not query:
                return None
            return ("search", {"query": query[:200]})
        if ui.name == "documents":
            clean: dict = {}
            category = str(params.get("category") or "").strip()
            if category:
                clean["category"] = category[:120]
            return ("documents", clean)
        if ui.name == "card":
            kind = str(params.get("kind") or "").strip()
            if kind not in ("task", "knowledge", "document"):
                return None
            try:
                entity_id = uuid.UUID(str(params.get("id") or ""))
            except (ValueError, AttributeError):
                return None
            return ("card", {"kind": kind, "id": str(entity_id)})
        return None

    async def _validate_choices(
        self, ui: EntityChoice, registry: ToolRegistry, stored: dict
    ) -> list[EntityCandidate]:
        """Разрешены только реальные сущности из контекста запроса; заголовки — из БД."""
        allowed = {str(candidate.id) for candidate in registry.surfaced}
        allowed |= {
            str(candidate.get("id")) for candidate in (stored.get("agent_candidates") or [])
        }
        candidates: list[EntityCandidate] = []
        for entity_id in ui.entity_ids:
            if str(entity_id) not in allowed:
                log.info("agent entity_choice id %s not in context, dropped", entity_id)
                continue
            candidate = await self._load_candidate(ui.entity_kind, entity_id)
            if candidate is not None:
                candidates.append(candidate)
        return candidates

    async def _load_candidate(self, kind: str, entity_id) -> EntityCandidate | None:
        if kind == "task":
            task = await TaskRepository(self._session).get(entity_id)
            if task is not None:
                return EntityCandidate(kind="task", id=task.id, title=task.title)
        elif kind == "knowledge":
            item = await KnowledgeRepository(self._session).get(entity_id)
            if item is not None:
                return EntityCandidate(kind="knowledge", id=item.id, title=item.title)
        elif kind == "project":
            project = await ProjectRepository(self._session).get(entity_id)
            if project is not None:
                return EntityCandidate(kind="project", id=project.id, title=project.name)
        return None

    async def _stored_context(self, telegram_user_id: int) -> dict:
        state = await self._store.get_active(telegram_user_id)
        if state is None or state.flow != AGENT_FLOW:
            return {}
        return dict(state.data or {})

    async def _save_context(self, telegram_user_id: int, result: AgentResult) -> None:
        stored = await self._stored_context(telegram_user_id)
        # Показывали entity_choice — сохраняем его; иначе всё, что нашли за запрос.
        # Пустой результат (занятая очередь, отмена) не стирает прежний контекст:
        # «добавь к первой» должен ссылаться на задачи прошлого успешного запроса.
        candidates = result.entity_choices or result.surfaced
        # Короткая память диалога: последние реплики (обрезанные) для контекста агента.
        history = list(stored.get("agent_history") or [])
        if result.request_text.strip():
            history.append({"role": "user", "text": result.request_text.strip()[:300]})
        if result.text.strip():
            history.append({"role": "assistant", "text": result.text.strip()[:300]})
        history = history[-8:]
        data = {
            **stored,
            "agent_history": history,
            "agent_candidates": [
                candidate.model_dump(mode="json") for candidate in candidates
            ]
            if candidates
            else stored.get("agent_candidates", []),
            "agent_last_run": {
                "profile": result.profile,
                "termination": result.termination,
                "steps": result.steps,
                "tool_calls": result.tool_calls,
                "llm_calls": result.llm_calls,
                "tokens": result.tokens,
                "duration_ms": result.duration_ms,
                "finished_at": datetime.now(self._settings.tz).isoformat(),
            },
        }
        await self._store.set(telegram_user_id, AGENT_FLOW, data=data)


_REASON_LABELS = {
    "timeout": "истекло время выполнения",
    "step_limit": "достигнут лимит шагов",
    "llm_limit": "достигнут лимит обращений к модели",
    "token_limit": "достигнут лимит токенов",
    "web_limit": "достигнут лимит веб-запросов",
    "loop_detected": "обнаружено повторение одинаковых действий",
    "error_streak": "подряд не удалось выполнить несколько действий",
}


def _usage_tokens(usage: dict | None) -> int | None:  # noqa: D103 - совместимость старых импортов
    return usage_tokens(usage)


def _finding(tool_name: str, result: ToolResult) -> str:
    data = result.data or {}
    if tool_name == "list_tasks":
        items = data.get("items") or []
        return f"задачи ({data.get('view')}): " + ", ".join(item["title"] for item in items[:5])
    if tool_name in ("search_tasks", "search_all"):
        items = data.get("items") or []
        titles = ", ".join(item["title"] for item in items[:5])
        return f"найдено по «{data.get('query')}»: {titles}"
    if tool_name == "get_task" and data.get("task"):
        return f"задача: {data['task'].get('title')}"
    if tool_name == "web_search":
        return f"веб-поиск «{data.get('query')}»: {len(data.get('results') or [])} результатов"
    return ""


def _trim_messages(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    """Не даём истории агента расти бесконечно: system + свежий хвост."""
    total = sum(len(message.get("content") or "") for message in messages)
    if total <= MESSAGES_CHAR_BUDGET or len(messages) <= 4:
        return messages
    head = messages[:2]  # system + исходный запрос с контекстом
    tail = messages[-4:]
    return [*head, *tail]


__all__ = ["AgentResult", "AgentService"]
