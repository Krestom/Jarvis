"""Планировщик уведомлений: напоминания о наступившем сроке и утренняя сводка.

Каждое уведомление отправляется один раз — отметки в `notification_log`.
Проверка идёт с шагом `NOTIFY_POLL_SECONDS`; сводка — раз в день в окне
`NOTIFY_DIGEST_TIME` (+30 минут, чтобы пережить пропуск шага).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from datetime import time as dtime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jarvis.config.settings import Settings
from jarvis.db.session import session_scope
from jarvis.repositories.notifications import NotificationRepository
from jarvis.repositories.tasks import TaskRepository
from jarvis.services.tasks import TaskService

log = logging.getLogger(__name__)

DUE_SCAN_LIMIT = 5
DIGEST_TASK_LIMIT = 7
DIGEST_WINDOW_MINUTES = 30

SendMessage = Callable[[int, str], Awaitable[None]]


def parse_digest_time(raw: str) -> dtime | None:
    """«HH:MM» → время; некорректное значение выключает сводку."""
    try:
        hour, minute = (int(part) for part in raw.strip().split(":", 1))
    except (ValueError, TypeError, AttributeError):
        return None
    if 0 <= hour < 24 and 0 <= minute < 60:
        return dtime(hour=hour, minute=minute)
    return None


async def run_notifier_once(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    send: SendMessage,
) -> dict[str, int]:
    """Один проход планировщика. Возвращает счётчики отправленных уведомлений."""
    if not settings.notify_enabled or not settings.telegram_allowed_user_ids:
        return {}
    owner = settings.telegram_allowed_user_ids[0]
    now = datetime.now(UTC)
    local_now = now.astimezone(settings.tz)
    stats = {"due": 0, "digest": 0}
    async with session_scope(session_factory) as session:
        repo = NotificationRepository(session)
        if settings.notify_due_reminders:
            tasks = await TaskRepository(session).list_due_before(now, limit=DUE_SCAN_LIMIT)
            for task in tasks:
                if not await repo.mark_if_new("task_due", str(task.id)):
                    continue
                due = task.due_at.astimezone(settings.tz).strftime("%d.%m %H:%M")
                await send(
                    owner,
                    f"⏰ Пора заняться: «{task.title}» — срок {due}. Открыть: /tasks",
                )
                stats["due"] += 1
        digest_at = parse_digest_time(settings.notify_digest_time)
        if digest_at is not None:
            minutes_now = local_now.hour * 60 + local_now.minute
            minutes_digest = digest_at.hour * 60 + digest_at.minute
            # Учитываем переход через полночь: окно после времени сводки.
            in_window = (minutes_now - minutes_digest) % (24 * 60) < DIGEST_WINDOW_MINUTES
            if in_window and await repo.mark_if_new("digest", local_now.date().isoformat()):
                await send(owner, await _digest_text(session, settings))
                stats["digest"] = 1
    return stats


async def _digest_text(session: AsyncSession, settings: Settings) -> str:
    tasks = await TaskService(session).list_view(
        "today", now=datetime.now(UTC), tz=settings.tz, limit=DIGEST_TASK_LIMIT
    )
    if not tasks:
        return "☀️ Доброе утро! Задач на сегодня нет."
    lines = ["☀️ Доброе утро! Задачи на сегодня:"]
    lines.extend(f"• {task.title}" for task in tasks)
    if len(tasks) == DIGEST_TASK_LIMIT:  # список мог быть длиннее
        lines.append("…и, возможно, ещё — смотрите /today")
    return "\n".join(lines)


async def notifier_loop(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    stop: asyncio.Event,
    send: SendMessage,
) -> None:
    interval = min(max(settings.notify_poll_seconds, 30), 3600)
    while not stop.is_set():
        try:
            stats = await run_notifier_once(session_factory, settings, send)
            if any(stats.values()):
                log.info("notifier: %s", stats)
        except Exception:  # noqa: BLE001 - цикл не должен умирать от разовой ошибки
            log.exception("notifier run failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            continue
