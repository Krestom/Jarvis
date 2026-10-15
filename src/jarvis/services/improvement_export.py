"""Экспорт improvement backlog в Markdown и JSON (без секретов)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from jarvis import __version__
from jarvis.db.models import ImprovementEvent, ImprovementItem
from jarvis.domain.enums import (
    ImprovementCategory,
    ImprovementStatus,
)
from jarvis.repositories.improvements import ImprovementRepository
from jarvis.security.redaction import redact

# Лимит выгрузки: не больше 500 записей, строки полей обрезаются.
EXPORT_MAX_ITEMS = 500
EXPORT_FIELD_LIMIT = 4000


@dataclass
class ExportResult:
    markdown: str
    json_text: str
    item_count: int


def _clean(value: Any) -> Any:
    """Рекурсивная маскировка секретов + обрезка строк (JSON-полезная нагрузка)."""
    if isinstance(value, str):
        return redact(value)[:EXPORT_FIELD_LIMIT]
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean(item) for item in value]
    return value


def _clip(value: str) -> str:
    return value[:EXPORT_FIELD_LIMIT]


async def export_backlog(
    repo: ImprovementRepository,
    *,
    statuses: tuple[ImprovementStatus, ...] | None,
    category: ImprovementCategory | None,
    tz,
) -> ExportResult:
    items = await repo.list_items(
        statuses=statuses, category=category, limit=EXPORT_MAX_ITEMS
    )
    events = {item.id: await repo.list_events(item.id) for item in items}
    generated_at = datetime.now(tz)

    payload = {
        "generated_at": generated_at.isoformat(),
        "app_version": __version__,
        "filters": {
            "statuses": [status.value for status in statuses] if statuses else "all",
            "category": category.value if category else None,
        },
        "count": len(items),
        "items": [_item_payload(item, events[item.id]) for item in items],
    }
    json_text = json.dumps(_clean(payload), ensure_ascii=False, indent=2, default=str)
    markdown = _render_markdown(items, events, generated_at=generated_at)
    return ExportResult(markdown=markdown, json_text=json_text, item_count=len(items))


def _item_payload(item: ImprovementItem, events: list[ImprovementEvent]) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "category": item.category.value,
        "source": item.source.value,
        "status": item.status.value,
        "confidence": item.confidence,
        "title": item.title,
        "summary": item.summary,
        "details": item.details,
        "expected_behavior": item.expected_behavior,
        "actual_behavior": item.actual_behavior,
        "facts": item.facts,
        "assumptions": item.assumptions,
        "proposed_fix": item.proposed_fix,
        "suggested_priority": (
            item.suggested_priority.value if item.suggested_priority else None
        ),
        "occurrence_count": item.occurrence_count,
        "first_seen_at": item.first_seen_at.isoformat(),
        "last_seen_at": item.last_seen_at.isoformat(),
        "component": item.component,
        "app_version": item.app_version,
        "correlation_id": item.correlation_id,
        "diagnostic_context": item.diagnostic_context,
        "source_event_ids": item.source_event_ids,
        "merged_from": item.merged_from,
        "meta": item.meta,
        "events": [
            {
                "id": str(event.id),
                "occurred_at": event.occurred_at.isoformat(),
                "correlation_id": event.correlation_id,
                "note": event.note,
                "diagnostic_context": event.diagnostic_context,
            }
            for event in events
        ],
    }


def _render_markdown(
    items: list[ImprovementItem],
    events: dict,
    *,
    generated_at: datetime,
) -> str:
    lines = [
        "# Jarvis improvement backlog",
        "",
        f"Сгенерировано: {generated_at.isoformat()} · версия {__version__} · "
        f"записей: {len(items)}",
        "",
        "> Данные обезличены и не содержат секретов. Файл предназначен для "
        "ручной передачи в OpenCode.",
        "",
    ]
    if not items:
        lines.append("Backlog пуст.")
        return redact("\n".join(lines))

    for index, item in enumerate(items, start=1):
        lines.append(f"## {index}. {_clip(item.title)}")
        lines.append(
            f"- id: `{item.id}` · категория: {item.category.value} · "
            f"источник: {item.source.value} · статус: {item.status.value}"
        )
        meta_parts = [f"повторов: {item.occurrence_count}"]
        if item.component:
            meta_parts.append(f"компонент: {item.component}")
        if item.confidence is not None:
            meta_parts.append(f"достоверность: {item.confidence:.2f}")
        meta_parts.append(f"впервые: {item.first_seen_at.isoformat()}")
        meta_parts.append(f"последний раз: {item.last_seen_at.isoformat()}")
        lines.append("- " + " · ".join(meta_parts))
        lines.append(f"- сводка: {_clip(item.summary)}")
        for label, value in (
            ("ожидалось", item.expected_behavior),
            ("фактически", item.actual_behavior),
            ("факты", item.facts),
            ("допущения", item.assumptions),
            ("предлагаемое исправление", item.proposed_fix),
            ("детали", item.details),
        ):
            if value:
                lines.append(f"- {label}: {_clip(value)}")
        if item.diagnostic_context:
            lines.append(
                "- диагностический контекст: `"
                + json.dumps(item.diagnostic_context, ensure_ascii=False)
                + "`"
            )
        for event_index, event in enumerate(events.get(item.id, []), start=1):
            parts = [f"событие {event_index}: {event.occurred_at.isoformat()}"]
            if event.correlation_id:
                parts.append(f"correlation: {event.correlation_id}")
            if event.note:
                parts.append(f"заметка: {_clip(event.note)}")
            if event.diagnostic_context:
                parts.append(
                    "контекст: `"
                    + json.dumps(event.diagnostic_context, ensure_ascii=False)
                    + "`"
                )
            lines.append("- " + " · ".join(parts))
        lines.append("")

    return redact("\n".join(lines))
