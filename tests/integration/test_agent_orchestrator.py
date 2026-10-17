"""Интеграционные тесты агентского цикла: бюджет, отмена, UI, контекст."""

from __future__ import annotations

import asyncio
import time
import uuid

import pytest

from jarvis.agent import AgentService, UserSessionGate
from jarvis.agent.tools import ToolRegistry, ToolResult
from jarvis.ai.fake import FakeProvider
from jarvis.ai.schemas import PlanStep
from jarvis.conversation.state import ConversationStore
from jarvis.repositories.conversation import ConversationRepository
from jarvis.repositories.tasks import TaskRepository
from jarvis.research.none import NoneSearchProvider
from jarvis.services.drafts import TaskDraft
from jarvis.services.tasks import TaskService

pytestmark = pytest.mark.integration

USER = 111


def make_service(session, test_settings, ai, gate=None) -> AgentService:
    return AgentService(
        session,
        test_settings,
        ai,
        gate=gate or UserSessionGate(test_settings),
        search_provider=NoneSearchProvider(),
        correlation_id="test",
    )


class BlockingPlanner(FakeProvider):
    """Планировщик, который «зависает» до явного разрешения (для отмены)."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def plan(self, messages, context):
        self.plan_calls.append("blocked")
        self.entered.set()
        await self.release.wait()
        return PlanStep(type="reply", text="Поздний ответ — не должен быть принят")


class SlowPlanner(FakeProvider):
    async def plan(self, messages, context):
        self.plan_calls.append("slow")
        await asyncio.sleep(5)
        return PlanStep(type="reply", text="слишком поздно")


async def test_agent_uses_tools_replies_and_saves_context(session, test_settings):
    await TaskService(session).create_from_draft(TaskDraft(title="Починить сервер"))
    ai = FakeProvider()
    ai.queue_plan(PlanStep(type="tool_call", tool="list_tasks", args={"view": "no_group"}))
    ai.queue_plan(PlanStep(type="reply", text="Нашёл задачу: Починить сервер."))
    service = make_service(session, test_settings, ai)

    result = await service.run(USER, "Джарвис, какие у меня задачи?")
    assert result.termination == "completed"
    assert result.text == "Нашёл задачу: Починить сервер."
    assert result.tool_calls == 1
    assert result.steps == 2
    assert result.llm_calls == 2
    assert result.tokens is None  # провайдер не отдал usage — не выдумываем

    # Контекст сохранён: кандидаты и last_run.
    state = await ConversationStore(
        ConversationRepository(session), test_settings
    ).get_active(USER)
    assert state is not None and state.flow == "agent_context"
    assert state.data["agent_candidates"][0]["title"] == "Починить сервер"
    assert state.data["agent_last_run"]["termination"] == "completed"


async def test_agent_entity_choice_accepts_only_context_ids(session, test_settings):
    await TaskService(session).create_from_draft(TaskDraft(title="Починить сервер"))
    ai = FakeProvider()
    service = make_service(session, test_settings, ai)
    # Получим реальный id через реестр (в прогоне агента он тоже будет surfaced).
    registry = ToolRegistry(
        session, test_settings, telegram_user_id=USER, search_provider=NoneSearchProvider()
    )
    listing = await registry.call("list_tasks", {"view": "no_group"})
    real_id = listing.data["items"][0]["id"]

    foreign_id = str(uuid.uuid4())
    ai.queue_plan(PlanStep(type="tool_call", tool="list_tasks", args={"view": "no_group"}))
    ai.queue_plan(
        PlanStep(
            type="reply",
            text="Уточните задачу",
            ui={
                "component": "entity_choice",
                "entity_kind": "task",
                "entity_ids": [real_id, foreign_id],
                "prompt": "Какую?",
            },
        )
    )
    # Второй прогон с регистром, найденным выше (тот же session, кандидаты совпадут).
    result = await service.run(USER, "Джарвис, уточни задачи")
    assert len(result.entity_choices) == 1
    assert str(result.entity_choices[0].id) == real_id
    assert result.choice_prompt == "Какую?"


async def test_agent_foreign_entity_choice_dropped(session, test_settings):
    ai = FakeProvider()
    ai.queue_plan(
        PlanStep(
            type="reply",
            text="Вот варианты",
            ui={
                "component": "entity_choice",
                "entity_kind": "task",
                "entity_ids": [str(uuid.uuid4())],
            },
        )
    )
    service = make_service(session, test_settings, ai)
    result = await service.run(USER, "Джарвис, покажи варианты")
    assert result.entity_choices == []
    assert result.text == "Вот варианты"


async def test_agent_loop_protection_stops_repeats(session, test_settings):
    ai = FakeProvider()
    for _ in range(3):
        ai.queue_plan(
            PlanStep(type="tool_call", tool="list_tasks", args={"view": "no_group"})
        )
    ai.queue_plan(PlanStep(type="reply", text="Остановился: повторял одно и то же."))
    service = make_service(session, test_settings, ai)

    result = await service.run(USER, "Джарвис, зациклись")
    assert result.termination == "loop_detected"
    assert result.tool_calls == 2  # третий вызов не выполнялся
    assert "повторял" in result.text


async def test_agent_step_limit_returns_partial_result(session, test_settings):
    test_settings.agent_ordinary_steps = 1
    ai = FakeProvider()
    ai.queue_plan(PlanStep(type="tool_call", tool="list_tasks", args={"view": "no_group"}))
    ai.queue_plan(PlanStep(type="reply", text="Частичный итог: дошёл до лимита."))
    service = make_service(session, test_settings, ai)

    result = await service.run(USER, "Джарвис, посмотри задачи")
    assert result.termination == "step_limit"
    assert result.text == "Частичный итог: дошёл до лимита."


async def test_agent_token_hard_limit_when_usage_available(session, test_settings):
    test_settings.agent_token_hard_limit = 100
    ai = FakeProvider()
    ai.usage_per_call = 60
    ai.queue_plan(PlanStep(type="tool_call", tool="list_tasks", args={"view": "no_group"}))
    service = make_service(session, test_settings, ai)

    result = await service.run(USER, "Джарвис, посмотри задачи")
    assert result.termination == "token_limit"
    assert result.tokens is not None and result.tokens >= 100
    assert "лимит токенов" in result.text


async def test_agent_wall_clock_timeout_and_recovery(session, test_settings):
    test_settings.agent_ordinary_seconds = 0.2
    gate = UserSessionGate(test_settings)
    slow = SlowPlanner()
    service = make_service(session, test_settings, slow, gate=gate)
    started = time.monotonic()
    result = await service.run(USER, "Джарвис, зависни")
    assert result.termination == "timeout"
    assert time.monotonic() - started < 2.0
    assert "время" in result.text

    # Зависший запрос не блокирует следующие: очередь свободна.
    fast = FakeProvider()
    fast.queue_plan(PlanStep(type="reply", text="Снова на связи."))
    ok = await make_service(session, test_settings, fast, gate=gate).run(
        USER, "Джарвис, ты тут?"
    )
    assert ok.termination == "completed"
    assert ok.text == "Снова на связи."


async def test_agent_cancellation_stops_further_steps(session, test_settings):
    gate = UserSessionGate(test_settings)
    planner = BlockingPlanner()
    service = make_service(session, test_settings, planner, gate=gate)

    task = asyncio.create_task(service.run(USER, "Джарвис, длинная задача"))
    await planner.entered.wait()
    assert gate.request_cancel(USER) is True
    planner.release.set()
    result = await task

    assert result.termination == "cancelled"
    assert "Остановил" in result.text
    assert "Поздний ответ" not in result.text  # поздний результат не принимается
    assert len(planner.plan_calls) == 1  # новых шагов после отмены не было


async def test_agent_transient_retry_is_bounded(session, test_settings, monkeypatch):
    original = ToolRegistry.call
    attempts = {"count": 0}

    async def flaky(self, name, args):
        attempts["count"] += 1
        if attempts["count"] == 1:
            return ToolResult.failure("transient", "временная ошибка")
        return await original(self, name, args)

    monkeypatch.setattr(ToolRegistry, "call", flaky)
    ai = FakeProvider()
    ai.queue_plan(PlanStep(type="tool_call", tool="list_tasks", args={"view": "no_group"}))
    ai.queue_plan(PlanStep(type="reply", text="Готово после повтора."))
    service = make_service(session, test_settings, ai)

    result = await service.run(USER, "Джарвис, сбойни один раз")
    assert result.termination == "completed"
    assert result.tool_calls == 1  # повторы внутри одного логического вызова
    assert attempts["count"] == 2


async def test_agent_error_streak_stops(session, test_settings):
    ai = FakeProvider()
    for index in range(4):
        ai.queue_plan(
            PlanStep(type="tool_call", tool=f"unknown_tool_{index}", args={})
        )
    ai.queue_plan(PlanStep(type="reply", text="Остановился из-за ошибок."))
    service = make_service(session, test_settings, ai)

    result = await service.run(USER, "Джарвис, ломайся")
    assert result.termination == "error_streak"
    assert "Остановился" in result.text


async def test_agent_queue_timeout_when_user_busy(session, test_settings):
    test_settings.agent_queue_wait_seconds = 0.05
    gate = UserSessionGate(test_settings)
    hold = asyncio.Event()

    async def blocker() -> None:
        async with gate.acquire(USER):
            await hold.wait()

    blocker_task = asyncio.create_task(blocker())
    await asyncio.sleep(0.01)
    ai = FakeProvider()
    service = make_service(session, test_settings, ai, gate=gate)

    result = await service.run(USER, "Джарвис, ты занят?")
    assert result.termination == "queue_timeout"
    assert "занят" in result.text
    assert ai.plan_calls == []

    hold.set()
    await blocker_task


async def test_agent_delete_task_returns_pending_operation(session, test_settings):
    task_id = await TaskService(session).create_from_draft(TaskDraft(title="Удалить меня"))
    ai = FakeProvider()
    ai.queue_plan(PlanStep(type="tool_call", tool="delete_task", args={"task_id": str(task_id)}))
    ai.queue_plan(PlanStep(type="reply", text="Отправил подтверждение удаления."))
    service = make_service(session, test_settings, ai)

    result = await service.run(USER, "Джарвис, удали задачу")
    assert result.pending_operation_ids
    assert await TaskRepository(session).get(task_id) is not None  # ещё не удалена


async def test_agent_create_task_proposes_confirmation(session, test_settings):
    ai = FakeProvider()
    ai.queue_plan(PlanStep(type="tool_call", tool="create_task", args={"title": "Купить хлеб"}))
    ai.queue_plan(PlanStep(type="reply", text="Подготовил задачу — подтвердите кнопкой."))
    service = make_service(session, test_settings, ai)

    result = await service.run(USER, "Джарвис, создай задачу купить хлеб")
    assert result.pending_operation_ids
    # Задача ещё не создана: ждёт кнопки пользователя.
    assert await TaskRepository(session).search("Купить хлеб") == []


async def test_agent_queue_timeout_keeps_previous_context(session, test_settings):
    await TaskService(session).create_from_draft(TaskDraft(title="Починить сервер"))
    ai = FakeProvider()
    ai.queue_plan(PlanStep(type="tool_call", tool="list_tasks", args={"view": "no_group"}))
    ai.queue_plan(PlanStep(type="reply", text="Нашёл задачу."))
    gate = UserSessionGate(test_settings)
    await make_service(session, test_settings, ai, gate=gate).run(USER, "Джарвис, задачи?")

    store = ConversationStore(ConversationRepository(session), test_settings)
    state = await store.get_active(USER)
    assert state.data["agent_candidates"]

    # Занятая очередь не должна стирать контекст прошлого успешного запроса.
    test_settings.agent_queue_wait_seconds = 0.05
    hold = asyncio.Event()

    async def blocker() -> None:
        async with gate.acquire(USER):
            await hold.wait()

    blocker_task = asyncio.create_task(blocker())
    await asyncio.sleep(0.01)
    busy = FakeProvider()
    result = await make_service(session, test_settings, busy, gate=gate).run(
        USER, "Джарвис, ты занят?"
    )
    assert result.termination == "queue_timeout"
    hold.set()
    await blocker_task

    state = await store.get_active(USER)
    assert state.data["agent_candidates"]  # кандидаты уцелели


async def test_agent_quick_actions_ui_passthrough(session, test_settings):
    ai = FakeProvider()
    ai.queue_plan(
        PlanStep(
            type="reply",
            text="Могу подсказать.",
            ui={"component": "quick_actions", "actions": ["tasks_today"]},
        )
    )
    service = make_service(session, test_settings, ai)
    result = await service.run(USER, "Джарвис, что умеешь?")
    assert result.quick_actions == ["tasks_today"]
