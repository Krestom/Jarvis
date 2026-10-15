"""E2E-харнесс: настоящий Dispatcher с фейковым Telegram-сеансом.

Никакой сети: все вызовы Bot API перехватываются FakeTelegramSession,
исходящие сообщения собираются в список для проверок.
"""

from __future__ import annotations

import itertools
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
    Message,
    Update,
    User,
    Voice,
)

BOT_USER = User(id=42, is_bot=True, first_name="Jarvis", username="jarvis_test_bot")
DEFAULT_USER_ID = 111

# Общий счётчик на процесс: update_id должны быть уникальны между harness'ами,
# иначе дедупликация апдейтов отбросит повторно доставленный update.
_UPDATE_IDS = itertools.count(10_000)


class FakeTelegramSession(BaseSession):
    """Мини-реализация Telegram Bot API для тестов."""

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[TelegramMethod] = []
        self.sent_messages: list[Message] = []
        self.download_payload: bytes = b"fake-binary-payload"
        self.recorder = None  # колбэк журнала чата (см. bot/chatlog.py)
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
            if self.recorder is not None and method.text:
                await self.recorder(int(method.chat_id), method.text)
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
            return message
        if name in ("EditMessageText", "EditMessageReplyMarkup"):
            # Правим сохранённое сообщение, чтобы кнопки после edit_text
            # оставались доступны для следующих кликов в тестах.
            for index, existing in enumerate(self.sent_messages):
                if existing.message_id != method.message_id:
                    continue
                updates: dict = {}
                if name == "EditMessageText" and method.text is not None:
                    updates["text"] = method.text
                if method.reply_markup is not None:
                    updates["reply_markup"] = method.reply_markup
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

    async def click_button(self, user_id: int, message: Message, label: str) -> list[Message]:
        # Сообщение могло быть отредактировано ботом — берём актуальную версию.
        current = self.current(message)
        button_data = _find_button(current.reply_markup, label)
        assert button_data is not None, f"button {label!r} not found"
        callback = CallbackQuery(
            id=uuid.uuid4().hex,
            from_user=User(id=user_id, is_bot=False, first_name="Test"),
            chat_instance="1",
            data=button_data,
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
