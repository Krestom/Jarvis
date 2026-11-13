"""Rich-экраны диагностики: кнопки в пузыре, fallback, экранирование."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from jarvis.bot.callbacks import ImpCB, ImpListCB, MenuCB, uuid_hex
from jarvis.bot.keyboards import (
    IMPROVEMENT_CATEGORY_LABELS,
    IMPROVEMENT_STATUS_LABELS,
    pack_origin,
)
from jarvis.bot.screens.diagnostics import (
    backlog_card_screen,
    backlog_delete_screen,
    backlog_list_screen,
    dashboard_screen,
)

TZ = ZoneInfo("Europe/Moscow")
NOW = datetime(2026, 11, 13, 8, 0, tzinfo=UTC)


def _item(**overrides):
    data = {
        "id": uuid.uuid4(),
        "title": "ValueError: callback data too long",
        "summary": "Кнопка не отправилась",
        "category": SimpleNamespace(value="failure"),
        "source": SimpleNamespace(value="technical_event"),
        "status": SimpleNamespace(value="new"),
        "occurrence_count": 1,
        "first_seen_at": NOW,
        "last_seen_at": NOW,
        "confidence": None,
        "component": "telegram_bot",
        "expected_behavior": None,
        "actual_behavior": None,
        "facts": None,
        "assumptions": None,
        "proposed_fix": None,
        "diagnostic_context": {"correlation_id": "corr-1"},
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _rows(html: str) -> list[str]:
    return re.findall(r"<tg-button-row\b.*?</tg-button-row>", html, re.S)


def test_dashboard_has_single_primary_and_filters():
    screen = dashboard_screen(
        summary=["База данных: ✅", "Backlog улучшений: 1 записей (открытых: 1)"],
        status_counts={"new": 1},
        category_counts={"failure": 1},
    )
    assert "<h3>🩺 Диагностика</h3>" in screen.html
    assert screen.html.count('style="primary"') == 1
    assert ImpListCB(kind="open").pack() in screen.html
    assert ImpListCB(kind="all").pack() in screen.html
    assert "📥 Открытые (1)" in screen.html
    for key in IMPROVEMENT_STATUS_LABELS:
        assert ImpListCB(kind="status", value=key).pack() in screen.html
    for key in IMPROVEMENT_CATEGORY_LABELS:
        assert ImpListCB(kind="category", value=key).pack() in screen.html
    assert ImpCB(action="export").pack() in screen.html
    assert MenuCB(action="main").pack() in screen.html
    menu_row = next(row for row in _rows(screen.html) if MenuCB(action="main").pack() in row)
    assert 'style="link"' in menu_row
    assert screen.text.startswith("🩺 <b>Диагностика</b>")
    assert "Backlog улучшений: 1 записей (открытых: 1)" in screen.text
    assert screen.markup is not None


def test_backlog_list_buttons_pagination_and_fallback():
    items = [_item(title="Первая"), _item(title="Вторая", status=SimpleNamespace(value="reviewed"))]
    screen = backlog_list_screen(
        title="📥 Открытые записи backlog",
        items=items,
        total=3,
        kind="open",
        value="",
        page=0,
        has_next=True,
    )
    origin = pack_origin("open", "")
    for index, item in enumerate(items, start=1):
        callback = ImpCB(
            action="view", item_id=uuid_hex(item.id), value=origin, page=0
        ).pack()
        assert callback in screen.html
        assert f"{index}. {item.title}" in screen.html
    assert "Записей: <b>3</b>" in screen.html
    assert ImpListCB(kind="open", page=1).pack() in screen.html
    assert MenuCB(action="diagnostics").pack() in screen.html
    assert screen.text.startswith("📥 Открытые записи backlog")
    assert "1. Первая — 🆕 Новые, повторов: 1" in screen.text
    assert screen.markup is not None


def test_backlog_list_page_has_back_and_empty_state():
    screen = backlog_list_screen(
        title="Backlog",
        items=[],
        total=0,
        kind="all",
        value="",
        page=2,
        has_next=False,
    )
    assert "Страница 3" in screen.html
    assert ImpListCB(kind="all", page=1).pack() in screen.html
    assert "Пусто." in screen.html
    assert "Пусто." in screen.text


def test_backlog_card_buttons_escape_and_details():
    item = _item(
        title="<b>опасно</b>",
        summary="Кнопка <script>alert(1)</script> не отправилась",
        occurrence_count=2,
        diagnostic_context={
            "correlation_id": "corr-1",
            "traceback": "<script>alert(2)</script>",
        },
    )
    events = [SimpleNamespace(occurred_at=NOW, note="повтор", correlation_id="corr-1")]
    screen = backlog_card_screen(item, TZ, events, origin=pack_origin("open", ""), page=0)
    html = screen.html

    assert "&lt;b&gt;опасно&lt;/b&gt;" in html
    assert "<b>опасно</b>" not in html
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "<details><summary>Диагностический контекст</summary>" in html
    assert "<details><summary>События (1)</summary>" in html
    assert "повтор" in html
    assert "Повторов: <b>2</b>" in html

    for value, _label in (
        ("reviewed", "Разобран"),
        ("planned", "В план"),
        ("implemented", "Реализовано"),
        ("wont_fix", "Не чинить"),
    ):
        assert ImpCB(action="status", item_id=uuid_hex(item.id), value=value).pack() in html
    assert ImpCB(action="status", item_id=uuid_hex(item.id), value="new").pack() not in html
    assert ImpCB(action="status", item_id=uuid_hex(item.id), value="archived").pack() in html
    assert (
        ImpCB(
            action="delete",
            item_id=uuid_hex(item.id),
            value=pack_origin("open", ""),
            page=0,
        ).pack()
        in html
    )
    assert ImpListCB(kind="open", value="", page=0).pack() in html

    status_row = next(row for row in _rows(html) if "Разобран" in row)
    assert status_row.count("<tg-button ") == 2
    danger_rows = [row for row in _rows(html) if 'style="danger"' in row]
    assert len(danger_rows) == 2
    back_row = next(row for row in _rows(html) if "К списку" in row)
    assert 'style="link"' in back_row

    assert "Повторов: 2" in screen.text
    assert "&lt;b&gt;опасно&lt;/b&gt;" in screen.text
    assert screen.markup is not None


def test_backlog_card_hides_current_status_and_archive_when_archived():
    item = _item(status=SimpleNamespace(value="archived"))
    screen = backlog_card_screen(item, TZ, origin=pack_origin("status", "archived"), page=1)
    html = screen.html
    assert ImpCB(action="status", item_id=uuid_hex(item.id), value="archived").pack() not in html
    assert ImpCB(action="status", item_id=uuid_hex(item.id), value="reviewed").pack() in html
    assert ImpListCB(kind="status", value="archived", page=1).pack() in html


def test_delete_confirmation_is_dangerous_with_back():
    item = _item()
    origin = pack_origin("category", "failure")
    screen = backlog_delete_screen(item, origin=origin, page=2)
    assert "безвозвратно" in screen.html
    assert "безвозвратно" in screen.text
    assert ImpCB(action="delete_yes", item_id=uuid_hex(item.id)).pack() in screen.html
    assert ImpCB(
        action="delete_no", item_id=uuid_hex(item.id), value=origin, page=2
    ).pack() in screen.html
    assert screen.html.count('style="danger"') == 1
    assert 'style="link"' not in screen.html
    assert screen.markup is not None
