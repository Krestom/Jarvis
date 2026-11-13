"""Типы виджетов: то, что backend отдаёт транспорту Telegram."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WidgetPayload:
    """Готовый HTML-виджет и подпись-fallback для старых клиентов."""

    html: str
    caption: str
    width: int = 0
    height: int = 0
    # Rich-message fallback: нативный структурированный вид, если Telegram
    # отклоняет виджет (INPUT_MEDIA_WIDGET_NOT_ALLOWED).
    rich_html: str | None = None


__all__ = ["WidgetPayload"]
