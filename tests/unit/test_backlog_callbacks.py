"""Регрессия: callback_data кнопок backlog должны помещаться в 64 байта."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from jarvis.bot.keyboards import (
    _parse_origin,
    backlog_card_keyboard,
    backlog_delete_keyboard,
    backlog_list_keyboard,
    pack_origin,
)

CATEGORY_VALUES = ["failure", "missing_capability", "ux_friction", "improvement_idea"]
STATUS_VALUES = ["new", "reviewed", "planned", "implemented", "wont_fix", "archived"]


def _item(status: str = "new"):
    return SimpleNamespace(id=uuid.uuid4(), title="Х" * 60, status=SimpleNamespace(value=status))


def _callbacks(markup) -> list[str]:
    return [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data
    ]


def test_backlog_callbacks_fit_telegram_limit():
    """Худшие входы (длинные категории/статусы, большая страница) — все ≤ 64 байт."""
    page = 99
    variants = [("category", values) for values in [CATEGORY_VALUES]] + [
        ("status", values) for values in [STATUS_VALUES]
    ]
    variants += [("open", [""]), ("all", [""])]
    for kind, values in variants:
        for value in values:
            origin = pack_origin(kind, value)
            item = _item()
            markups = [
                backlog_list_keyboard([item], kind, value, page, has_next=True),
                backlog_card_keyboard(item, origin, page),
                backlog_delete_keyboard("f" * 32, origin, page),
            ]
            for markup in markups:
                for packed in _callbacks(markup):
                    assert len(packed.encode()) <= 64, packed


def test_parse_origin_round_trip_and_legacy():
    assert _parse_origin(pack_origin("category", "missing_capability")) == (
        "category",
        "missing_capability",
    )
    assert _parse_origin(pack_origin("status", "implemented")) == ("status", "implemented")
    assert _parse_origin(pack_origin("open", "")) == ("open", "")
    # legacy-формат из старых сообщений со старыми кнопками
    assert _parse_origin("category|failure") == ("category", "failure")
    assert _parse_origin("open") == ("open", "")
