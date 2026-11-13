"""E2E-харнесс: настоящий Dispatcher с фейковым Telegram-сеансом.

Никакой сети: все вызовы Bot API перехватываются FakeTelegramSession,
исходящие сообщения собираются в список для проверок.
"""

from __future__ import annotations

import html as html_lib
import itertools
import re
import uuid
from datetime import UTC, datetime

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import TelegramMethod
from aiogram.types import (
    CallbackQuery,
    Chat,
    Document,
    File,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
    User,
    Voice,
)

from jarvis.bot.ui_state import MessageTracker

BOT_USER = User(id=42, is_bot=True, first_name="Jarvis", username="jarvis_test_bot")
DEFAULT_USER_ID = 111

# Общий счётчик на процесс: update_id должны быть уникальны между harness'ами,
# иначе дедупликация апдейтов отбросит повторно доставленный update.
_UPDATE_IDS = itertools.count(10_000)

_ATTR_RE = re.compile(r'([a-zA-Z-]+)="([^"]*)"')
_BUTTON_RE = re.compile(r"<tg-button\b([^>]*)>(.*?)</tg-button>", re.S)
_ROW_RE = re.compile(r"<tg-button-row\b[^>]*>(.*?)</tg-button-row>", re.S)


def _rich_text(html: str) -> str:
    """Видимый текст rich-карточки для проверок в тестах."""
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    text = re.sub(r"</(?:p|h[1-6]|tg-button-row)>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_lib.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n", text).strip()


def _rich_button(match: re.Match) -> InlineKeyboardButton | None:
    attrs = dict(_ATTR_RE.findall(match.group(1)))
    label = re.sub(r"<[^>]+>", "", match.group(2))
    label = html_lib.unescape(label).strip()
    if not label:
        return None
    kind = attrs.get("type")
    if kind == "callback_data":
        return InlineKeyboardButton(
            text=label, callback_data=attrs.get("data"), style=attrs.get("style")
        )
    if kind == "url":
        return InlineKeyboardButton(text=label, url=attrs.get("url"), style=attrs.get("style"))
    return None


def _rich_markup(html: str) -> InlineKeyboardMarkup | None:
    """Синтетическая клавиатура из <tg-button>, чтобы клики в тестах работали."""
    rows: list[list[InlineKeyboardButton]] = []
    for row_match in _ROW_RE.finditer(html):
        row = [b for m in _BUTTON_RE.finditer(row_match.group(1)) if (b := _rich_button(m))]
        if row:
            rows.append(row)
    outside = _ROW_RE.sub("", html)
    for match in _BUTTON_RE.finditer(outside):
        button = _rich_button(match)
        if button is not None:
            rows.append([button])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def _rich_message(html: str) -> dict:
    return {"text": _rich_text(html), "reply_markup": _rich_markup(html)}


class FakeTelegramSession(BaseSession):
    """Мини-реализация Telegram Bot API для тестов."""

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[TelegramMethod] = []
        self.sent_messages: list[Message] = []
        self.download_payload: bytes = b"fake-binary-payload"
        self.tracker = MessageTracker()
        self._message_id = 1000

    async def close(self) -> None:
        pass

    async def make_request(
        self, bot: Bot, method: TelegramMethod, timeout: int | None = None
    ):
        self.requests.append(method)
        name = type(method).__name__
        if name == "GetMe":
            return BOT_USER
        if name == "SendMessage":
            self._message_id += 1
            message = Message(
                message_id=self._message_id,
                date=datetime.now(UTC),
                chat=Chat(id=int(method.chat_id), type="private"),
                from_user=BOT_USER,
                text=method.text,
                reply_markup=method.reply_markup,
            )
            self.sent_messages.append(message)
            self.tracker.note(int(method.chat_id), message.message_id)
            return message
        if name == "SendRichMessage":
            self._message_id += 1
            rich = method.rich_message
            message = Message(
                message_id=self._message_id,
                date=datetime.now(UTC),
                chat=Chat(id=int(method.chat_id), type="private"),
                from_user=BOT_USER,
                **_rich_message(rich.html or rich.markdown or ""),
            )
            self.sent_messages.append(message)
            self.tracker.note(int(method.chat_id), message.message_id)
            return message
        if name == "SendDocument":
            self._message_id += 1
            document = method.document
            message = Message(
                message_id=self._message_id,
                date=datetime.now(UTC),
                chat=Chat(id=int(method.chat_id), type="private"),
                from_user=BOT_USER,
                document=Document(
                    file_id=f"file-{self._message_id}",
                    file_unique_id=f"file-{self._message_id}-u",
                    file_name=getattr(document, "filename", None),
                ),
            )
            self.sent_messages.append(message)
            self.tracker.note(int(method.chat_id), message.message_id)
            return message
        if name == "SendWidget":
            self._message_id += 1
            message = Message(
                message_id=self._message_id,
                date=datetime.now(UTC),
                chat=Chat(id=int(method.chat_id), type="private"),
                from_user=BOT_USER,
                widget={"id": f"widget-{self._message_id}", "html": method.html},
            )
            self.sent_messages.append(message)
            self.tracker.note(int(method.chat_id), message.message_id)
            return message
        if name in ("EditMessageText", "EditMessageReplyMarkup"):
            # Правим сохранённое сообщение, чтобы кнопки после edit_text
            # оставались доступны для следующих кликов в тестах.
            for index, existing in enumerate(self.sent_messages):
                if existing.message_id != method.message_id:
                    continue
                updates: dict = {}
                rich_html = None
                if name == "EditMessageText" and method.rich_message is not None:
                    rich = method.rich_message
                    rich_html = rich.html or rich.markdown or ""
                    updates.update(_rich_message(rich_html))
                elif name == "EditMessageText" and method.text is not None:
                    updates["text"] = method.text
                if method.reply_markup is not None:
                    updates["reply_markup"] = method.reply_markup
                if rich_html is not None:
                    # В rich-правке пустая клавиатура очищает старую, а кнопки
                    # живут в HTML — для тестов синтезируем их из карточки.
                    updates["reply_markup"] = _rich_markup(rich_html)
                edited = existing.model_copy(update=updates)
                self.sent_messages[index] = edited
                return edited
            return True
        if name == "GetFile":
            return File(
                file_id=method.file_id,
                file_unique_id="unique",
                file_path="files/test.bin",
                file_size=len(self.download_payload),
            )
        return True

    async def stream_content(
        self,
        url: str,
        headers=None,
        timeout: int = 30,
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ):
        yield self.download_payload


class BotHarness:
    """Помощник для отправки апдейтов и кликов по кнопкам."""

    def __init__(self, dispatcher: Dispatcher, session: FakeTelegramSession) -> None:
        self.dispatcher = dispatcher
        self.session = session
        self.bot: Bot | None = None
        self._message_id = 0

    async def start(self) -> None:
        self.bot = Bot(token="123456:TESTTOKEN", session=self.session)
        await self.bot.get_me()

    def _next_update_id(self) -> int:
        return next(_UPDATE_IDS)

    def _next_message_id(self) -> int:
        self._message_id += 1
        return self._message_id

    def _user_message(self, user_id: int, **kwargs) -> Message:
        return Message(
            message_id=self._next_message_id(),
            date=datetime.now(UTC),
            chat=Chat(id=user_id, type="private"),
            from_user=User(id=user_id, is_bot=False, first_name="Test"),
            **kwargs,
        )

    async def send_text(self, user_id: int, text: str) -> list[Message]:
        message = self._user_message(user_id, text=text)
        return await self._feed(Update(update_id=self._next_update_id(), message=message))

    async def send_document(
        self,
        user_id: int,
        *,
        filename: str,
        content: bytes,
        mime_type: str = "application/pdf",
    ) -> list[Message]:
        self.session.download_payload = content
        message = self._user_message(
            user_id,
            document=Document(
                file_id="doc-1",
                file_unique_id="doc-1-u",
                file_name=filename,
                mime_type=mime_type,
                file_size=len(content),
            ),
        )
        return await self._feed(Update(update_id=self._next_update_id(), message=message))

    async def send_voice(
        self, user_id: int, *, payload: bytes = b"ogg-bytes"
    ) -> list[Message]:
        self.session.download_payload = payload
        message = self._user_message(
            user_id,
            voice=Voice(
                file_id="voice-1",
                file_unique_id="voice-1-u",
                duration=3,
                mime_type="audio/ogg",
                file_size=len(payload),
            ),
        )
        return await self._feed(Update(update_id=self._next_update_id(), message=message))

    async def click_button(
        self,
        user_id: int,
        message: Message,
        label: str,
    ) -> list[Message]:
        # Сообщение могло быть отредактировано ботом — берём актуальную версию.
        current = self.current(message)
        button_data = _find_button(current.reply_markup, label)
        assert button_data is not None, f"button {label!r} not found"
        return await self.click_data(user_id, message, button_data)

    async def click_data(
        self,
        user_id: int,
        message: Message,
        data: str,
    ) -> list[Message]:
        """Клик по произвольному callback_data (например, кнопке старой версии)."""
        current = self.current(message)
        callback = CallbackQuery(
            id=uuid.uuid4().hex,
            from_user=User(id=user_id, is_bot=False, first_name="Test"),
            chat_instance="1",
            data=data,
            message=current,
        )
        return await self._feed(
            Update(update_id=self._next_update_id(), callback_query=callback)
        )

    def current(self, message: Message) -> Message:
        """Актуальная версия отправленного сообщения (после edit_text)."""
        for stored in reversed(self.session.sent_messages):
            if stored.message_id == message.message_id:
                return stored
        return message

    async def _feed(self, update: Update) -> list[Message]:
        assert self.bot is not None, "call await harness.start() first"
        before = len(self.session.sent_messages)
        await self.dispatcher.feed_update(self.bot, update)
        return self.session.sent_messages[before:]


def _find_button(reply_markup, label: str) -> str | None:
    if reply_markup is None or getattr(reply_markup, "inline_keyboard", None) is None:
        return None
    for row in reply_markup.inline_keyboard:
        for button in row:
            if label in (button.text or "") and button.callback_data:
                return button.callback_data
    return None
