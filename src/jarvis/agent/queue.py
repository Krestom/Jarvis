"""Последовательная обработка агентских запросов и отмена (процесс-local).

Развёртывание Jarvis — один процесс на пользователя: per-user asyncio.Lock
сериализует агентские запросы, фиксированный дедлайн не даёт «зависшему»
запросу заблокировать очередь навсегда. Если появится несколько воркеров,
сериализацию нужно поднять в PostgreSQL (advisory lock) — см. docs/agent.md.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from dataclasses import dataclass, field

from jarvis.config.settings import Settings


class QueueTimeout(RuntimeError):
    """Пользователь занят другим запросом дольше допустимого."""


@dataclass
class RequestHandle:
    request_id: str
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def cancel(self) -> None:
        self.cancel_event.set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            from jarvis.agent.budget import RequestCancelled

            raise RequestCancelled("cancelled")


class UserSessionGate:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._locks: dict[int, asyncio.Lock] = {}
        self._active: dict[int, RequestHandle] = {}

    @contextlib.asynccontextmanager
    async def acquire(self, user_id: int):
        """Дождаться своей очереди (с таймаутом) и получить handle запроса."""
        lock = self._locks.setdefault(user_id, asyncio.Lock())
        try:
            await asyncio.wait_for(lock.acquire(), timeout=self._settings.agent_queue_wait_seconds)
        except TimeoutError as error:
            raise QueueTimeout("queue wait exceeded") from error
        handle = RequestHandle(request_id=uuid.uuid4().hex[:12])
        self._active[user_id] = handle
        try:
            yield handle
        finally:
            if self._active.get(user_id) is handle:
                self._active.pop(user_id, None)
            lock.release()

    def request_cancel(self, user_id: int) -> bool:
        """Отменить текущий запрос пользователя (для /cancel). False, если нечего."""
        handle = self._active.get(user_id)
        if handle is None:
            return False
        handle.cancel()
        return True

    @property
    def active_requests(self) -> int:
        return len(self._active)


__all__ = ["QueueTimeout", "RequestHandle", "UserSessionGate"]
