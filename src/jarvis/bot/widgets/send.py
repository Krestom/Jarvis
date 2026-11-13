"""Отправка виджетов через Bot API 10.4."""

from __future__ import annotations

from aiogram import Bot
from aiogram.types import Message

from jarvis.bot.methods import SendWidget
from jarvis.bot.widgets.types import WidgetPayload


async def send_widget(bot: Bot, chat_id: int, payload: WidgetPayload) -> Message:
    """Отправить виджет; ошибки Telegram пробрасываются вызывающему."""
    return await bot(
        SendWidget(
            chat_id=chat_id,
            html=payload.html,
            width=payload.width or None,
            height=payload.height or None,
            caption=payload.caption or None,
        )
    )


__all__ = ["send_widget"]
