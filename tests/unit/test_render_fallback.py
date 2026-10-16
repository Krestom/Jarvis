"""Фолбэк отправки: HTML-ошибка Telegram не должна терять сообщение."""

from __future__ import annotations

import pytest
from aiogram.exceptions import TelegramBadRequest

from jarvis.bot.handlers.render import answer_safe


class _FakeMessage:
    def __init__(self, fail_first: bool = True) -> None:
        self.calls: list[dict] = []
        self._fail_first = fail_first

    async def answer(self, text: str, **kwargs):
        self.calls.append({"text": text, **kwargs})
        if self._fail_first and len(self.calls) == 1:
            raise TelegramBadRequest(
                method=None,
                message=(
                    "Bad Request: can't parse entities: "
                    'Unsupported start tag "=" at byte offset 1'
                ),
            )
        return None


async def test_answer_safe_retries_as_plain_text():
    message = _FakeMessage()
    await answer_safe(message, "x <= y, <b>важное</b>", reply_markup=None)
    assert len(message.calls) == 2
    assert message.calls[1]["parse_mode"] is None
    assert message.calls[1]["text"] == "x <= y, важное"


async def test_answer_safe_passes_through_other_errors():
    class _Boom(_FakeMessage):
        async def answer(self, text: str, **kwargs):
            raise TelegramBadRequest(method=None, message="Bad Request: chat not found")

    with pytest.raises(TelegramBadRequest):
        await answer_safe(_Boom(), "привет")
