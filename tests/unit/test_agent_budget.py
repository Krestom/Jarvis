"""Бюджет выполнения агента: дедлайны, лимиты, детект циклов."""

from __future__ import annotations

import pytest

from jarvis.agent.budget import (
    BudgetExhausted,
    ExecutionBudget,
    ExecutionProfile,
    LoopDetected,
)


class FakeClock:
    def __init__(self) -> None:
        self.value = 1000.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def make_budget(
    clock: FakeClock,
    *,
    seconds: float = 60.0,
    steps: int = 12,
    web: int = 1,
    llm: int = 18,
    repeat: int = 2,
    soft: int = 25000,
    hard: int = 50000,
) -> ExecutionBudget:
    return ExecutionBudget(
        ExecutionProfile("ordinary", seconds, steps, web),
        max_llm_calls=llm,
        max_repeat_tool_calls=repeat,
        token_soft_limit=soft,
        token_hard_limit=hard,
        clock=clock,
    )


def test_timeout_by_monotonic_clock():
    clock = FakeClock()
    budget = make_budget(clock, seconds=60)
    budget.check()
    clock.advance(59.5)
    budget.check()
    clock.advance(1.0)
    with pytest.raises(BudgetExhausted) as error:
        budget.check()
    assert error.value.reason == "timeout"


def test_step_and_llm_limits():
    clock = FakeClock()
    budget = make_budget(clock, steps=2, llm=2)
    budget.note_step()
    budget.note_step()
    with pytest.raises(BudgetExhausted) as error:
        budget.check()
    assert error.value.reason == "step_limit"

    clock2 = FakeClock()
    budget2 = make_budget(clock2, steps=5, llm=1)
    budget2.note_llm_call()
    with pytest.raises(BudgetExhausted) as error2:
        budget2.check()
    assert error2.value.reason == "llm_limit"


def test_identical_tool_calls_are_limited_but_different_args_allowed():
    clock = FakeClock()
    budget = make_budget(clock, repeat=2)
    budget.note_tool_call("list_tasks", {"view": "today"})
    budget.note_tool_call("list_tasks", {"view": "today"})  # повтор допустим
    budget.note_tool_call("list_tasks", {"view": "week"})  # другие аргументы — ок
    with pytest.raises(LoopDetected):
        budget.note_tool_call("list_tasks", {"view": "today"})


def test_web_query_limit():
    clock = FakeClock()
    budget = make_budget(clock, web=1)
    budget.note_web_query()
    with pytest.raises(BudgetExhausted) as error:
        budget.note_web_query()
    assert error.value.reason == "web_limit"


def test_token_limits_only_when_usage_available():
    clock = FakeClock()
    budget = make_budget(clock, soft=100, hard=250)
    assert budget.has_usage_data is False

    budget.note_usage(None)  # нет metadata — не выдумываем
    assert budget.tokens == 0
    assert budget.has_usage_data is False

    budget.note_usage(120)  # soft — только предупреждение в лог
    assert budget.tokens == 120
    with pytest.raises(BudgetExhausted) as error:
        budget.note_usage(200)
    assert error.value.reason == "token_limit"


def test_error_streak_stops_and_success_resets():
    clock = FakeClock()
    budget = make_budget(clock)
    budget.note_result(False)
    budget.note_result(True)
    for _ in range(3):
        budget.note_result(False)
    with pytest.raises(BudgetExhausted) as error:
        budget.note_result(False)
    assert error.value.reason == "error_streak"


def test_elapsed_and_remaining_are_consistent():
    clock = FakeClock()
    budget = make_budget(clock, seconds=30)
    clock.advance(10)
    assert budget.elapsed_seconds() == pytest.approx(10.0)
    assert budget.remaining_seconds() == pytest.approx(20.0)
