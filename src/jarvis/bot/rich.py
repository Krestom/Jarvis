"""Rich-сообщения Bot API 10.3: карточки с кнопками внутри пузыря.

Rich-кнопки — часть содержимого сообщения, а не reply_markup. Поэтому
редактировать их можно только вместе со всей карточкой. Если Telegram
отклоняет rich-сообщение, экран отправляется обычным текстом с клавиатурой.
"""

from __future__ import annotations

import html as html_lib
import logging
import re
from dataclasses import dataclass

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, InputRichMessage, Message

from jarvis.bot.ui_state import stale_message

log = logging.getLogger(__name__)

_NOT_MODIFIED = "message is not modified"
_BLOCK_TAG_RE = re.compile(r"<(p|h[1-6]|ul|ol|li|blockquote|pre|table|details|hr)\b", re.I)


@dataclass(frozen=True)
class RichScreen:
    """Rich-карточка и её текстовый fallback для старых клиентов."""

    html: str
    text: str
    markup: InlineKeyboardMarkup | None = None


def esc(value: object) -> str:
    return html_lib.escape(str(value))


def row(*buttons: str, align: str = "center") -> str:
    return f'<tg-button-row align="{align}">' + "".join(buttons) + "</tg-button-row>"


def button(label: str, data: str, *, style: str | None = None) -> str:
    style_attr = f' style="{style}"' if style else ""
    return (
        f'<tg-button type="callback_data"{style_attr} data="{html_lib.escape(data, quote=True)}">'
        f"{html_lib.escape(label)}</tg-button>"
    )


def url_button(label: str, url: str, *, style: str | None = None) -> str:
    style_attr = f' style="{style}"' if style else ""
    return (
        f'<tg-button type="url"{style_attr} url="{html_lib.escape(url, quote=True)}">'
        f"{html_lib.escape(label)}</tg-button>"
    )


def disabled_button(label: str, *, style: str | None = None) -> str:
    style_attr = f' style="{style}"' if style else ""
    return f'<tg-button type="disabled"{style_attr}>{html_lib.escape(label)}</tg-button>'


def divider() -> str:
    return "<hr/>"


def markup_rows(markup: InlineKeyboardMarkup | None) -> str:
    """Превратить fallback-клавиатуру в rich-кнопки, чтобы не терять навигацию."""
    if markup is None:
        return ""
    rows = []
    for row_buttons in markup.inline_keyboard:
        buttons = []
        for item in row_buttons:
            label = item.text or "…"
            if item.callback_data:
                buttons.append(button(label, item.callback_data, style=item.style))
            elif item.url:
                buttons.append(url_button(label, item.url, style=item.style))
            if len(buttons) == 8:
                rows.append(row(*buttons))
                buttons = []
        if buttons:
            rows.append(row(*buttons))
    return "".join(rows)


def text_screen(text: str, markup: InlineKeyboardMarkup | None = None) -> RichScreen:
    """Карточка из обычного HTML-текста; кнопки клавиатуры переносятся внутрь."""
    body = (text or "").strip()
    if _BLOCK_TAG_RE.search(body):
        html = body
    else:
        paragraphs = [part.strip() for part in body.split("\n\n") if part.strip()]
        html = "".join(
            "<p>" + part.replace("\n", "<br>") + "</p>" for part in paragraphs
        )
    return RichScreen(
        html=(html or "<p>…</p>") + markup_rows(markup),
        text=text,
        markup=markup,
    )


def plain_screen(text: str, markup: InlineKeyboardMarkup | None = None) -> RichScreen:
    return text_screen(text, markup)


async def send_rich(
    message: Message, screen: RichScreen, *, edit: bool = False
) -> Message | None:
    """Показать rich-карточку; при отказе — обычный текст с клавиатурой."""
    rich = InputRichMessage(html=screen.html)
    if edit and not stale_message.get():
        try:
            return await message.edit_text(
                rich_message=rich,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
            )
        except TelegramBadRequest as error:
            if _NOT_MODIFIED in str(error):
                return
            log.warning("rich edit rejected: %s", error)
        try:
            return await message.edit_text(
                screen.text,
                reply_markup=screen.markup
                if screen.markup is not None
                else InlineKeyboardMarkup(inline_keyboard=[]),
            )
        except TelegramBadRequest as error:
            if _NOT_MODIFIED in str(error):
                return None
            log.warning("classic edit rejected after rich failure: %s", error)
    try:
        return await message.answer_rich(rich)
    except TelegramBadRequest as error:
        log.warning("rich message rejected: %s", error)
        return await message.answer(screen.text, reply_markup=screen.markup)


async def send_rich_text(
    message: Message,
    text: str,
    markup: InlineKeyboardMarkup | None = None,
    *,
    edit: bool = False,
) -> None:
    await send_rich(message, text_screen(text, markup), edit=edit)


async def send_rich_chat(bot: Bot, chat_id: int, screen: RichScreen) -> Message:
    """Отправить карточку в чат без объекта Message (уведомления)."""
    try:
        return await bot.send_rich_message(chat_id, InputRichMessage(html=screen.html))
    except TelegramBadRequest as error:
        log.warning("rich notification rejected: %s", error)
        return await bot.send_message(
            chat_id, screen.text, reply_markup=screen.markup, parse_mode=None
        )


__all__ = [
    "RichScreen",
    "button",
    "disabled_button",
    "divider",
    "esc",
    "markup_rows",
    "plain_screen",
    "row",
    "send_rich",
    "send_rich_chat",
    "send_rich_text",
    "text_screen",
    "url_button",
]
