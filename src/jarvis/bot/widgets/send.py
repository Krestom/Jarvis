"""Отправка виджетов через Bot API 10.4."""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InputRichMessage, Message

from jarvis.bot.methods import SendWidget
from jarvis.bot.widgets.types import WidgetPayload

log = logging.getLogger(__name__)


async def send_widget(bot: Bot, chat_id: int, payload: WidgetPayload) -> Message:
    """Отправить виджет; если Telegram его не разрешает — rich-карточку."""
    try:
        return await bot(
            SendWidget(
                chat_id=chat_id,
                html=payload.html,
                width=payload.width or None,
                height=payload.height or None,
                caption=payload.caption or None,
            )
        )
    except TelegramBadRequest as error:
        if "INPUT_MEDIA_WIDGET_NOT_ALLOWED" not in str(error) or not payload.rich_html:
            raise
        log.warning("widgets are not allowed in this chat, using rich message")
        return await bot.send_rich_message(
            chat_id=chat_id,
            rich_message=InputRichMessage(html=payload.rich_html),
        )


__all__ = ["send_widget"]
