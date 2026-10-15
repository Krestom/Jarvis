"""Очередь агентских запросов: сериализация, отмена, таймауты."""

from __future__ import annotations

import asyncio

import pytest

from jarvis.agent.queue import QueueTimeout, UserSessionGate

USER = 111


async def test_requests_for_same_user_are_serialized(test_settings):
    gate = UserSessionGate(test_settings)
    order: list[tuple[str, int]] = []

    async def worker(index: int) -> None:
        async with gate.acquire(USER) as handle:
            assert handle.cancelled is False
            order.append(("start", index))
            await asyncio.sleep(0.05)
            order.append(("end", index))

    await asyncio.gather(worker(1), worker(2))

    assert order == [("start", 1), ("end", 1), ("start", 2), ("end", 2)]
    assert gate.active_requests == 0


async def test_queue_wait_timeout_does_not_block_forever(test_settings):
    test_settings.agent_queue_wait_seconds = 0.05
    gate = UserSessionGate(test_settings)
    hold = asyncio.Event()

    async def blocker() -> None:
        async with gate.acquire(USER):
            await hold.wait()

    task = asyncio.create_task(blocker())
    await asyncio.sleep(0.01)
    with pytest.raises(QueueTimeout):
        async with gate.acquire(USER):
            pass
    hold.set()
    await task


async def test_cancel_marks_active_request_only(test_settings):
    gate = UserSessionGate(test_settings)
    assert gate.request_cancel(USER) is False  # нечего отменять

    entered = asyncio.Event()
    release = asyncio.Event()

    async def holder() -> None:
        async with gate.acquire(USER) as handle:
            entered.set()
            await release.wait()
            assert handle.cancelled is True

    task = asyncio.create_task(holder())
    await entered.wait()
    assert gate.request_cancel(USER) is True
    release.set()
    await task
    assert gate.request_cancel(USER) is False  # после завершения — нечего


async def test_stalled_request_releases_lock_with_timeout(test_settings):
    """Даже если держатель «завис», очередь освобождается после его ухода."""
    test_settings.agent_queue_wait_seconds = 0.2
    gate = UserSessionGate(test_settings)

    async def stalled() -> None:
        async with gate.acquire(USER):
            await asyncio.sleep(0.05)

    await stalled()
    # лок свободен — следующий запрос проходит
    async with gate.acquire(USER) as handle:
        assert handle.cancelled is False
