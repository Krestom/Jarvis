"""Планировщик уведомлений: пред-напоминания о сроках, просрочка и сводки.

Каждое уведомление отправляется один раз — отметки в `notification_log`:

- `task_due_soon:{id}:{срок}` — за `NOTIFY_BEFORE_DUE_MINUTES` до срока;
- `task_due:{id}:{срок}` — после наступления срока (перенос срока перевооружает
  напоминание, т.к. в ref входит время срока);
- `digest:{дата}@{HH:MM}` — каждая сводка дня считается независимо.

Задачи без времени (дедлайн ровно в 00:00 по локальному времени) отдельных
напоминаний не получают — они видны в сводках (22:00 и утренних).

Отметки коммитятся ДО отправки; если отправка упала, отметка снимается и
повтор произойдёт в следующий проход.

Проверка идёт с шагом `NOTIFY_POLL_SECONDS`; сводка срабатывает в окне
30 минут после своего времени (переживает пропуск шага и рестарт в окне).
За один проход отправляется не больше `REMINDERS_PER_PASS` напоминаний —
шторма нет, но и голодания нет: неотмеченные задачи попадут в следующие
проходы (выборка до `REMINDER_SCAN_LIMIT`).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jarvis.config.settings import Settings
from jarvis.db.session import session_scope
from jarvis.repositories.notifications import NotificationRepository
from jarvis.repositories.tasks import TaskRepository
from jarvis.services.tasks import TaskService

log = logging.getLogger(__name__)

REMINDERS_PER_PASS = 5  # максимум напоминаний (soon + просрочка) за один проход
REMINDER_SCAN_LIMIT = 50  # сколько кандидатов просматриваем за проход
DIGEST_TASK_LIMIT = 7
DIGEST_WINDOW_MINUTES = 30
EVENING_HOUR = 20  # сводки в 20:00 и позже — расширенный вечерний формат

SendMessage = Callable[[int, str], Awaitable[None]]


def parse_digest_time(raw: str) -> time | None:
    """«HH:MM» → время; некорректное значение возвращает None."""
    try:
        hour, minute = (int(part) for part in raw.strip().split(":", 1))
    except (ValueError, TypeError, AttributeError):
        return None
    if 0 <= hour < 24 and 0 <= minute < 60:
        return time(hour=hour, minute=minute)
    return None


def parse_digest_times(raw: str) -> list[time]:
    """«08:00,09:00,22:00» → отсортированный список времён (мусор пропускается)."""
    times: list[time] = []
    for part in (raw or "").split(","):
        if not part.strip():
            continue
        parsed = parse_digest_time(part)
        if parsed is None:
            log.warning("invalid digest time skipped: %r", part)
            continue
        if parsed not in times:
            times.append(parsed)
    return sorted(times)


def _task_ref(task) -> str:
    """Ключ дедупа напоминания: id + время срока (перенос срока = новое напоминание)."""
    return f"{task.id}:{task.due_at:%Y%m%d%H%M}"


def _due_soon_text(task, now: datetime, tz: ZoneInfo) -> str:
    due = task.due_at.astimezone(tz)
    minutes = max(1, round((task.due_at - now).total_seconds() / 60))
    left = f"~{minutes} мин" if minutes < 90 else f"~{round(minutes / 60)} ч"
    return (
        f"⏳ Срок близко: «{task.title}» — до {due.strftime('%d.%m %H:%M')}, "
        f"осталось {left}. Открыть: /tasks"
    )


async def run_notifier_once(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    send: SendMessage,
) -> dict[str, int]:
    """Один проход планировщика. Возвращает счётчики отправленных уведомлений.

    Отметки о напоминаниях коммитятся до отправки (короткая транзакция),
    сами отправки идут после коммита: сбой одной отправки не теряет отметки
    об остальных, упавшая отправка повторяется в следующий проход. Повтор
    возможен, если Telegram принял сообщение, а ответ потерялся.
    """
    if not settings.notify_enabled or not settings.telegram_allowed_user_ids:
        return {}
    owner = settings.telegram_allowed_user_ids[0]
    now = datetime.now(UTC)
    local_now = now.astimezone(settings.tz)
    # (kind, ref, текст) — отправляется после коммита отметок.
    to_send: list[tuple[str, str, str]] = []
    async with session_scope(session_factory) as session:
        repo = NotificationRepository(session)
        if settings.notify_due_reminders:
            sent = 0
            if settings.notify_before_due_minutes > 0:
                soon_until = now + timedelta(minutes=settings.notify_before_due_minutes)
                upcoming = await TaskRepository(session).list_due_between(
                    now, soon_until, limit=REMINDER_SCAN_LIMIT, tz=settings.tz
                )
                for task in upcoming:
                    if sent >= REMINDERS_PER_PASS:
                        break
                    if not await repo.mark_if_new("task_due_soon", _task_ref(task)):
                        continue
                    to_send.append(
                        ("task_due_soon", _task_ref(task), _due_soon_text(task, now, settings.tz))
                    )
                    sent += 1
            overdue = await TaskRepository(session).list_due_before(
                now, limit=REMINDER_SCAN_LIMIT, tz=settings.tz
            )
            for task in overdue:
                if sent >= REMINDERS_PER_PASS:
                    break
                if not await repo.mark_if_new("task_due", _task_ref(task)):
                    continue
                due = task.due_at.astimezone(settings.tz).strftime("%d.%m %H:%M")
                to_send.append(
                    (
                        "task_due",
                        _task_ref(task),
                        f"⏰ Пора заниматься: «{task.title}» — срок {due}. Открыть: /tasks",
                    )
                )
                sent += 1
        for digest_at in parse_digest_times(settings.notify_digest_times):
            minutes_now = local_now.hour * 60 + local_now.minute
            minutes_digest = digest_at.hour * 60 + digest_at.minute
            # Учитываем переход через полночь: окно после времени сводки.
            in_window = (minutes_now - minutes_digest) % (24 * 60) < DIGEST_WINDOW_MINUTES
            if not in_window:
                continue
            ref = f"{local_now.date().isoformat()}@{digest_at.strftime('%H:%M')}"
            if not await repo.mark_if_new("digest", ref):
                continue
            to_send.append(
                ("digest", ref, await _digest_text(session, settings, local_now, digest_at))
            )

    stats = {"due": 0, "due_soon": 0, "digest": 0}
    for kind, ref, text in to_send:
        try:
            await send(owner, text)
        except Exception:  # noqa: BLE001 - отправка повторится в следующий проход
            log.exception("notifier: failed to send %s %s", kind, ref)
            try:
                async with session_scope(session_factory) as session:
                    await NotificationRepository(session).unmark(kind, ref)
            except Exception:  # noqa: BLE001 - сбой снятия отметки не рвёт проход
                log.exception("notifier: failed to unmark %s %s", kind, ref)
            continue
        if kind == "digest":
            stats["digest"] += 1
        elif kind == "task_due_soon":
            stats["due_soon"] += 1
        else:
            stats["due"] += 1
    return stats


def _task_lines(tasks: list, now_local: datetime) -> list[str]:
    lines: list[str] = []
    for task in tasks:
        parts = [f"• {task.title}"]
        if task.due_at is not None:
            due_local = task.due_at.astimezone(now_local.tzinfo)
            marker = "⚠️ " if task.due_at < now_local else ""
            parts.append(f" — {marker}до {due_local.strftime('%d.%m %H:%M')}")
        lines.append("".join(parts))
    return lines


async def _digest_text(
    session: AsyncSession, settings: Settings, local_now: datetime, digest_at: time
) -> str:
    today = await TaskService(session).list_view(
        "today", now=datetime.now(UTC), tz=settings.tz, limit=DIGEST_TASK_LIMIT
    )
    if digest_at.hour >= EVENING_HOUR:
        return await _evening_digest(session, settings, local_now, today)
    if digest_at.hour < 12:
        header = "☀️ Доброе утро!"
    elif digest_at.hour < 17:
        header = "🌤 Добрый день!"
    else:
        header = "🌆 Добрый вечер!"
    if not today:
        return f"{header} Задач на сегодня нет."
    lines = [f"{header} Задачи на сегодня:"]
    lines.extend(_task_lines(today, local_now))
    if len(today) == DIGEST_TASK_LIMIT:  # список мог быть длиннее
        lines.append("…и, возможно, ещё — смотрите /today")
    return "\n".join(lines)


async def _evening_digest(
    session: AsyncSession, settings: Settings, local_now: datetime, today_tasks: list
) -> str:
    """Расширенная вечерняя сводка: закрыто за день + осталось + завтра."""
    tomorrow = local_now.date() + timedelta(days=1)
    tomorrow_tasks = await TaskService(session).list_on_date(
        tomorrow, tz=settings.tz, limit=DIGEST_TASK_LIMIT
    )
    start_of_day = datetime.combine(local_now.date(), time.min, settings.tz)
    closed = await TaskRepository(session).count_completed_between(start_of_day, local_now)

    lines = ["🌙 Вечерняя сводка"]
    if closed:
        lines.append(f"Закрыто сегодня: {closed}")
    if today_tasks:
        lines.append("Осталось на сегодня:")
        lines.extend(_task_lines(today_tasks, local_now))
    if tomorrow_tasks:
        lines.append("Завтра:")
        lines.extend(_task_lines(tomorrow_tasks, local_now))
    if len(lines) == 1:
        lines.append("На сегодня всё закрыто, на завтра задач нет.")
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
