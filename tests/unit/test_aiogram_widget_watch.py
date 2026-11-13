"""Canary: напоминание проверить нативную поддержку виджетов в aiogram.

Когда aiogram добавит `Bot.send_widget` (PR #2004 / 3.32+), этот тест упадёт —
значит, кастомный `SendWidget` и обходные пути пора удалить.
"""

from __future__ import annotations

from aiogram import Bot


def test_aiogram_has_no_native_widget_support_yet():
    assert not hasattr(Bot, "send_widget"), (
        "aiogram добавил sendWidget — удалить кастомный SendWidget "
        "из src/jarvis/bot/methods.py и обновить docs/telegram-widgets.md"
    )
