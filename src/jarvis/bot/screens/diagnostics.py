"""Rich-экраны диагностики и improvement backlog."""

from __future__ import annotations

import json
from collections.abc import Sequence
from zoneinfo import ZoneInfo

from jarvis.bot.callbacks import ImpCB, ImpListCB, MenuCB, uuid_hex
from jarvis.bot.keyboards import (
    IMPROVEMENT_CATEGORY_LABELS,
    IMPROVEMENT_STATUS_LABELS,
    _parse_origin,
    backlog_card_keyboard,
    backlog_delete_keyboard,
    backlog_list_keyboard,
    diagnostics_keyboard,
    pack_origin,
)
from jarvis.bot.rich import RichScreen, button, esc, esc_br, row
from jarvis.conversation.preview import fmt_dt_local
from jarvis.db.models import ImprovementEvent, ImprovementItem
from jarvis.repositories.improvements import OPEN_STATUSES

_STATUS_ACTIONS = (
    ("reviewed", "👀 Разобран"),
    ("planned", "📅 В план"),
    ("implemented", "🛠 Реализовано"),
    ("wont_fix", "🙅 Не чинить"),
)
_CONTEXT_LIMIT = 1200
_EVENTS_LIMIT = 5


def dashboard_screen(
    *, summary: Sequence[str], status_counts: dict, category_counts: dict
) -> RichScreen:
    """Сводка диагностики: системные факты + фильтры backlog."""
    open_count = sum(status_counts.get(status.value, 0) for status in OPEN_STATUSES)
    parts = ["<h3>🩺 Диагностика</h3>"]
    if summary:
        parts.append("<p>" + "<br>".join(summary) + "</p>")
    parts.append(
        row(
            button(
                f"📥 Открытые ({open_count})",
                ImpListCB(kind="open").pack(),
                style="primary",
            ),
            button("📚 Все", ImpListCB(kind="all").pack()),
        )
    )

    status_buttons = [
        button(
            f"{label} ({status_counts.get(key, 0)})",
            ImpListCB(kind="status", value=key).pack(),
        )
        for key, label in IMPROVEMENT_STATUS_LABELS.items()
    ]
    for start in range(0, len(status_buttons), 3):
        parts.append(row(*status_buttons[start : start + 3]))

    category_buttons = [
        button(
            f"{label} ({category_counts.get(key, 0)})",
            ImpListCB(kind="category", value=key).pack(),
        )
        for key, label in IMPROVEMENT_CATEGORY_LABELS.items()
    ]
    for start in range(0, len(category_buttons), 2):
        parts.append(row(*category_buttons[start : start + 2]))

    parts.append(
        row(
            button("📤 Экспорт", ImpCB(action="export").pack()),
            button("🏠 В меню", MenuCB(action="main").pack(), style="link"),
        )
    )
    text = "🩺 <b>Диагностика</b>\n" + "\n".join(summary)
    return RichScreen(
        html="".join(parts),
        text=text,
        markup=diagnostics_keyboard(status_counts, category_counts),
    )


def backlog_list_screen(
    *,
    title: str,
    items,
    total: int,
    kind: str,
    value: str,
    page: int,
    has_next: bool,
) -> RichScreen:
    """Страница списка backlog: запись на строку, пагинация внизу."""
    origin = pack_origin(kind, value)
    parts = [f"<h3>{esc(title)}</h3>", f"<p>Записей: <b>{total}</b></p>"]
    if page > 0:
        parts.append(f"<p><i>Страница {page + 1}</i></p>")
    if not items:
        parts.append("<p>Пусто.</p>")
    else:
        parts.append("<p>Выберите запись:</p>")
        for index, item in enumerate(items, start=1):
            label = item.title if len(item.title) <= 40 else item.title[:39].rstrip() + "…"
            parts.append(
                row(
                    button(
                        f"{index}. {label}",
                        ImpCB(
                            action="view",
                            item_id=uuid_hex(item.id),
                            value=origin,
                            page=page,
                        ).pack(),
                    )
                )
            )

    nav = []
    if page > 0:
        nav.append(
            button(
                "⬅️", ImpListCB(kind=kind, value=value, page=page - 1).pack()
            )
        )
    nav.append(button("⬆️ К сводке", MenuCB(action="diagnostics").pack()))
    if has_next:
        nav.append(
            button(
                "➡️", ImpListCB(kind=kind, value=value, page=page + 1).pack()
            )
        )
    parts.append(row(*nav))

    lines = [title, ""]
    if not items:
        lines.append("Пусто.")
    for index, item in enumerate(items, start=1):
        status = IMPROVEMENT_STATUS_LABELS.get(item.status.value, item.status.value)
        lines.append(
            f"{index}. {esc(item.title[:60])} — {status}, "
            f"повторов: {item.occurrence_count}"
        )
    return RichScreen(
        html="".join(parts),
        text="\n".join(lines),
        markup=backlog_list_keyboard(items, kind, value, page, has_next),
    )


def _card_text(item: ImprovementItem, tz: ZoneInfo) -> str:
    """Текст карточки для старых клиентов (как до rich-кнопок)."""
    category = IMPROVEMENT_CATEGORY_LABELS.get(item.category.value, item.category.value)
    status = IMPROVEMENT_STATUS_LABELS.get(item.status.value, item.status.value)
    lines = [
        f"{category}: <b>{esc(item.title)}</b>",
        f"Статус: {status} · Источник: {item.source.value}",
        f"Повторов: {item.occurrence_count} · "
        f"Впервые: {fmt_dt_local(item.first_seen_at, tz)} · "
        f"Последний раз: {fmt_dt_local(item.last_seen_at, tz)}",
    ]
    if item.confidence is not None:
        lines.append(f"Достоверность гипотезы: {item.confidence:.2f}")
    if item.component:
        lines.append(f"Компонент: <code>{esc(item.component)}</code>")
    lines.append("")
    lines.append(esc(item.summary))
    for label, value in (
        ("Ожидалось", item.expected_behavior),
        ("Фактически", item.actual_behavior),
        ("Факты", item.facts),
        ("Допущения", item.assumptions),
        ("Предлагаемое исправление", item.proposed_fix),
    ):
        if value:
            lines.append(f"<b>{label}:</b> {esc(value)}")
    if item.diagnostic_context.get("correlation_id"):
        lines.append(
            f"Correlation: <code>{esc(str(item.diagnostic_context['correlation_id']))}</code>"
        )
    return "\n".join(lines)


def _context_text(item: ImprovementItem) -> str | None:
    context = item.diagnostic_context or {}
    if not context:
        return None
    payload = json.dumps(context, ensure_ascii=False, sort_keys=True, default=str)
    if len(payload) > _CONTEXT_LIMIT:
        payload = payload[:_CONTEXT_LIMIT - 1].rstrip() + "…"
    return payload


def _events_html(events: Sequence[ImprovementEvent], tz: ZoneInfo) -> str:
    shown = list(events[-_EVENTS_LIMIT:])
    lines = []
    for index, event in enumerate(shown, start=len(events) - len(shown) + 1):
        parts = [f"<b>{index}.</b> {fmt_dt_local(event.occurred_at, tz)}"]
        if event.note:
            parts.append(esc_br(event.note))
        if event.correlation_id:
            parts.append(f"<code>{esc(event.correlation_id)}</code>")
        lines.append("<br>".join(parts))
    if len(events) > _EVENTS_LIMIT:
        lines.insert(0, f"<i>… ещё {len(events) - _EVENTS_LIMIT}</i>")
    return (
        f"<details><summary>События ({len(events)})</summary>"
        f"<blockquote>{'<br>'.join(lines)}</blockquote></details>"
    )


def backlog_card_screen(
    item: ImprovementItem,
    tz: ZoneInfo,
    events: Sequence[ImprovementEvent] = (),
    *,
    origin: str,
    page: int,
) -> RichScreen:
    """Карточка записи backlog с действиями смены статуса."""
    category = IMPROVEMENT_CATEGORY_LABELS.get(item.category.value, item.category.value)
    status = IMPROVEMENT_STATUS_LABELS.get(item.status.value, item.status.value)
    item_id = uuid_hex(item.id)

    parts = [
        f"<h3>{esc(item.title)}</h3>",
        f"<p>{category} · {status} · источник {esc(item.source.value)}</p>",
    ]
    facts = [
        f"Повторов: <b>{item.occurrence_count}</b>",
        f"Впервые: {fmt_dt_local(item.first_seen_at, tz)}",
        f"Последний раз: {fmt_dt_local(item.last_seen_at, tz)}",
    ]
    parts.append("<p>" + "<br>".join(facts) + "</p>")
    if item.confidence is not None:
        parts.append(f"<p>Достоверность гипотезы: {item.confidence:.2f}</p>")
    if item.component:
        parts.append(f"<p>Компонент: <code>{esc(item.component)}</code></p>")
    parts.append(f"<p>{esc_br(item.summary)}</p>")
    for label, value in (
        ("Ожидалось", item.expected_behavior),
        ("Фактически", item.actual_behavior),
        ("Факты", item.facts),
        ("Допущения", item.assumptions),
        ("Предлагаемое исправление", item.proposed_fix),
    ):
        if value:
            parts.append(f"<p><b>{label}:</b> {esc_br(value)}</p>")
    context = _context_text(item)
    if context:
        parts.append(
            "<details><summary>Диагностический контекст</summary>"
            f"<blockquote><code>{esc(context)}</code></blockquote></details>"
        )
    if events:
        parts.append(_events_html(events, tz))

    status_buttons = [
        button(
            label,
            ImpCB(action="status", item_id=item_id, value=value).pack(),
        )
        for value, label in _STATUS_ACTIONS
        if value != item.status.value
    ]
    for start in range(0, len(status_buttons), 2):
        parts.append(row(*status_buttons[start : start + 2]))
    if item.status.value != "archived":
        parts.append(
            row(
                button(
                    "🗄 В архив",
                    ImpCB(action="status", item_id=item_id, value="archived").pack(),
                    style="danger",
                )
            )
        )
    parts.append(
        row(
            button(
                "🗑 Удалить",
                ImpCB(action="delete", item_id=item_id, value=origin, page=page).pack(),
                style="danger",
            )
        )
    )
    kind, value = _parse_origin(origin)
    parts.append(
        row(
            button(
                "⬅️ К списку",
                ImpListCB(kind=kind, value=value, page=page).pack(),
                style="link",
            )
        )
    )
    return RichScreen(
        html="".join(parts),
        text=_card_text(item, tz),
        markup=backlog_card_keyboard(item, origin, page),
    )


def backlog_delete_screen(item: ImprovementItem, *, origin: str, page: int) -> RichScreen:
    """Подтверждение безвозвратного удаления записи."""
    item_id = uuid_hex(item.id)
    title = esc(item.title)
    html = (
        "<h3>🗑 Удалить запись?</h3>"
        f"<p><b>{title}</b></p>"
        "<p>Запись будет удалена <b>безвозвратно</b>. "
        "История повторов тоже будет удалена.</p>"
        + row(
            button(
                "🗑 Да, удалить",
                ImpCB(action="delete_yes", item_id=item_id).pack(),
                style="danger",
            )
        )
        + row(
            button(
                "⬅️ Назад",
                ImpCB(
                    action="delete_no", item_id=item_id, value=origin, page=page
                ).pack(),
            )
        )
    )
    text = (
        f"Удалить запись «{title}» <b>безвозвратно</b>? "
        "История повторов тоже будет удалена."
    )
    return RichScreen(
        html=html,
        text=text,
        markup=backlog_delete_keyboard(item_id, origin, page),
    )


__all__ = [
    "backlog_card_screen",
    "backlog_delete_screen",
    "backlog_list_screen",
    "dashboard_screen",
]
