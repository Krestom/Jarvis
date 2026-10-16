"""Уведомления владельцу о сбоях, записанных в диагностику."""

from __future__ import annotations

import jarvis.bot.app as app_module
from jarvis.bot.app import notification_allowed, send_failure_notification


async def test_send_failure_notification_is_plain_text():
    sent: dict = {}

    class _Bot:
        async def send_message(self, chat_id, text, **kwargs):
            sent["chat_id"] = chat_id
            sent["text"] = text
            sent["kwargs"] = kwargs

    await send_failure_notification(_Bot(), 111, "ValueError: boom", 3)
    assert sent["chat_id"] == 111
    assert "ValueError: boom" in sent["text"]
    assert "3" in sent["text"]
    # Без HTML: уведомление не должно падать на неэкранированных символах.
    assert sent["kwargs"]["parse_mode"] is None


def test_notification_throttle_limits_repeats():
    app_module._notified_titles.clear()
    assert notification_allowed("ValueError: boom") is True
    assert notification_allowed("ValueError: boom") is False
    assert notification_allowed("PermissionError: other") is True
