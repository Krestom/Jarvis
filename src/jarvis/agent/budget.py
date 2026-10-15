"""Бюджет агентского запроса: дедлайн, счётчики, детект циклов."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

PROFILE_ORDINARY = "ordinary"
PROFILE_PLANNING = "planning"
PROFILE_RESEARCH = "research"


class BudgetExhausted(RuntimeError):
    """Бюджет исчерпан; reason — машинная причина остановки."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class LoopDetected(BudgetExhausted):
    def __init__(self) -> None:
        super().__init__("loop_detected")


class RequestCancelled(RuntimeError):
    """Запрос отменён пользователем."""


@dataclass(frozen=True)
class ExecutionProfile:
    name: str
    seconds: float
    max_steps: int
    max_web_queries: int


def _signature(name: str, args: dict[str, Any]) -> str:
    try:
        payload = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        payload = repr(args)
    return f"{name}:{payload}"


class ExecutionBudget:
    """Общий на весь запрос: дедлайн не сбрасывается вызовами инструментов."""

    ERROR_STREAK_LIMIT = 4

    def __init__(
        self,
        profile: ExecutionProfile,
        *,
        max_llm_calls: int,
        max_repeat_tool_calls: int,
        token_soft_limit: int,
        token_hard_limit: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.profile = profile
        self.max_llm_calls = max_llm_calls
        self.max_repeat_tool_calls = max_repeat_tool_calls
        self.token_soft_limit = token_soft_limit
        self.token_hard_limit = token_hard_limit
        self._clock = clock
        self._started = clock()
        self._deadline = self._started + profile.seconds
        self.steps = 0
        self.llm_calls = 0
        self.tool_calls = 0
        self.web_queries = 0
        self.tokens = 0
        self._saw_usage = False
        self._token_warning = False
        self._signatures: dict[str, int] = {}
        self._consecutive_errors = 0

    # --- Время/счётчики ---

    def elapsed_seconds(self) -> float:
        return max(0.0, self._clock() - self._started)

    def remaining_seconds(self) -> float:
        return max(0.0, self._deadline - self._clock())

    def check(self) -> None:
        """Проверить бюджет перед следующим шагом."""
        if self.remaining_seconds() <= 0:
            raise BudgetExhausted("timeout")
        if self.steps >= self.profile.max_steps:
            raise BudgetExhausted("step_limit")
        if self.llm_calls >= self.max_llm_calls:
            raise BudgetExhausted("llm_limit")

    def note_step(self) -> None:
        self.steps += 1

    def note_llm_call(self) -> None:
        self.llm_calls += 1

    def note_tool_call(self, name: str, args: dict[str, Any]) -> None:
        signature = _signature(name, args)
        repeats = self._signatures.get(signature, 0) + 1
        self._signatures[signature] = repeats
        if repeats > self.max_repeat_tool_calls:
            raise LoopDetected()
        self.tool_calls += 1

    def note_web_query(self) -> None:
        self.web_queries += 1
        if self.web_queries > self.profile.max_web_queries:
            raise BudgetExhausted("web_limit")

    def note_result(self, ok: bool) -> None:
        """Результат инструмента: серия ошибок подряд останавливает запрос."""
        if ok:
            self._consecutive_errors = 0
            return
        self._consecutive_errors += 1
        if self._consecutive_errors >= self.ERROR_STREAK_LIMIT:
            raise BudgetExhausted("error_streak")

    def note_usage(self, total_tokens: int | None) -> None:
        """Суммарный usage провайдера (если есть). Нет данных — не выдумываем."""
        if not total_tokens:
            return
        self._saw_usage = True
        self.tokens += total_tokens
        if not self._token_warning and self.tokens >= self.token_soft_limit:
            self._token_warning = True
            log.warning("agent token soft limit reached: %s", self.tokens)
        if self.tokens >= self.token_hard_limit:
            raise BudgetExhausted("token_limit")

    @property
    def has_usage_data(self) -> bool:
        return self._saw_usage


def usage_tokens(usage: dict | None) -> int | None:
    """Суммарные токены из usage провайдера (None, если данных нет)."""
    if not usage:
        return None
    total = usage.get("total_tokens")
    if isinstance(total, int):
        return total
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    if isinstance(prompt, int) or isinstance(completion, int):
        return int(prompt or 0) + int(completion or 0)
    return None


__all__ = [
    "BudgetExhausted",
    "ExecutionBudget",
    "ExecutionProfile",
    "LoopDetected",
    "PROFILE_ORDINARY",
    "PROFILE_PLANNING",
    "PROFILE_RESEARCH",
    "RequestCancelled",
    "usage_tokens",
]
