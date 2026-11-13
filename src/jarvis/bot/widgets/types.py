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


__all__ = ["WidgetPayload"]
