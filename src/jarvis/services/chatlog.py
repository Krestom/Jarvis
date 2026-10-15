"""Журнал чата: безопасная запись диалога (redacted) и дневной экспорт."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from jarvis import __version__
from jarvis.config.settings import Settings
from jarvis.repositories.chatlog import ChatLogRepository
from jarvis.security.redaction import redact

log = logging.getLogger(__name__)

MAX_STORED_CHARS = 4000
EXPORT_MAX_MESSAGES = 2000

DIRECTION_LABELS = {"in": "Пользователь", "out": "Jarvis"}
SOURCE_SUFFIXES = {"voice": " (голос)", "command": " (команда)"}


class ChatLogService:
    """Запись сообщений диалога; сбой журнала не ломает обработку."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._repo = ChatLogRepository(session)

    async def record(
        self,
        telegram_user_id: int,
        *,
        direction: str,
        text: str,
        source: str = "text",
        correlation_id: str | None = None,
    ) -> None:
        if not self._settings.chat_log_enabled:
            return
        cleaned = redact(str(text or "")).strip()
        if not cleaned:
            return
        try:
            await self._repo.create(
                telegram_user_id,
                direction=direction,
                source=source,
                text=cleaned[:MAX_STORED_CHARS],
                correlation_id=correlation_id,
            )
        except Exception:  # noqa: BLE001 - журнал не критичен для работы бота
            log.exception("failed to record chat message")


@dataclass
class ChatDayExport:
    day: date
    markdown: str
    json_text: str
    message_count: int
    truncated: bool


async def export_chat_day(
    session: AsyncSession, settings: Settings, day: date
) -> ChatDayExport:
    """Выгрузка диалога за локальный день (таймзона пользователя)."""
    start = datetime.combine(day, time.min, settings.tz)
    end = start + timedelta(days=1)
    repo = ChatLogRepository(session)
    total = await repo.count_for_period(start, end)
    messages = await repo.list_for_period(start, end, limit=EXPORT_MAX_MESSAGES)
    generated_at = datetime.now(settings.tz)

    payload = {
        "date": day.isoformat(),
        "timezone": settings.app_timezone,
        "generated_at": generated_at.isoformat(),
        "app_version": __version__,
        "count": len(messages),
        "total": total,
        "truncated": total > len(messages),
        "messages": [
            {
                "time": message.created_at.astimezone(settings.tz).isoformat(),
                "direction": message.direction,
                "source": message.source,
                "text": redact(message.text),
            }
            for message in messages
        ],
    }
    json_text = json.dumps(payload, ensure_ascii=False, indent=2)
    markdown = _render_markdown(messages, day=day, settings=settings, generated_at=generated_at)
    return ChatDayExport(
        day=day,
        markdown=markdown,
        json_text=json_text,
        message_count=len(messages),
        truncated=total > len(messages),
    )


def _render_markdown(messages, *, day: date, settings: Settings, generated_at: datetime) -> str:
    lines = [
        f"# Jarvis: чат за {day.isoformat()}",
        "",
        f"Часовой пояс: {settings.app_timezone} · сообщений: {len(messages)} · "
        f"сгенерировано: {generated_at.isoformat()}",
        "",
        "> Секреты в тексте маскируются автоматически.",
        "",
    ]
    if not messages:
        lines.append("За этот день сообщений нет.")
        return redact("\n".join(lines))
    for message in messages:
        local = message.created_at.astimezone(settings.tz)
        label = DIRECTION_LABELS.get(message.direction, message.direction)
        suffix = SOURCE_SUFFIXES.get(message.source, "")
        lines.append(f"**[{local.strftime('%H:%M')}] {label}{suffix}:** {redact(message.text)}")
        lines.append("")
    return redact("\n".join(lines))


__all__ = [
    "ChatDayExport",
    "ChatLogService",
    "EXPORT_MAX_MESSAGES",
    "export_chat_day",
]
