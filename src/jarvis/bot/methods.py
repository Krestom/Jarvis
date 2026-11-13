"""Кастомные методы Bot API, которых ещё нет в aiogram."""

from __future__ import annotations

from aiogram.methods.base import TelegramMethod
from aiogram.types import ChatIdUnion, InlineKeyboardMarkup, Message


class SendWidget(TelegramMethod[Message]):
    """Bot API 10.4 `sendWidget`.

    shortcut: aiogram 3.31 не умеет sendWidget (PR #2004, issue #2006);
    заменить на нативный `Bot.send_widget`, когда выйдет 3.32+.
    """

    __returning__ = Message
    __api_method__ = "sendWidget"

    chat_id: ChatIdUnion
    html: str
    width: int | None = None
    height: int | None = None
    caption: str | None = None
    parse_mode: str | None = None
    reply_markup: InlineKeyboardMarkup | None = None
    allow_queries: bool | None = None
    chat_data: str | None = None
    public_data: str | None = None
    protect_content: bool | None = None


__all__ = ["SendWidget"]
