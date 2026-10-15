"""Диагностика и improvement backlog: сводка, карточки, статусы, экспорт."""

from __future__ import annotations

import logging
from datetime import datetime

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, FSInputFile, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.ai import AIProvider
from jarvis.bot.callbacks import ImpCB, ImpListCB, uuid_hex
from jarvis.bot.formatting import esc
from jarvis.bot.handlers.menu import MenuCBFilter
from jarvis.bot.handlers.open_entity import parse_hex_uuid
from jarvis.bot.keyboards import (
    IMPROVEMENT_CATEGORY_LABELS,
    IMPROVEMENT_STATUS_LABELS,
    PAGE_SIZE,
    backlog_card_keyboard,
    backlog_delete_keyboard,
    backlog_list_keyboard,
    diagnostics_keyboard,
)
from jarvis.config.settings import Settings
from jarvis.conversation.preview import fmt_dt_local
from jarvis.db.models import Document, ImprovementItem, KnowledgeItem, Secret, Task
from jarvis.domain.enums import ImprovementCategory, ImprovementStatus
from jarvis.repositories.improvements import ImprovementRepository
from jarvis.services.improvement_export import export_backlog

log = logging.getLogger(__name__)

# «Открытые» — записи, которые ещё требуют внимания.
OPEN_STATUSES = (
    ImprovementStatus.NEW,
    ImprovementStatus.REVIEWED,
    ImprovementStatus.PLANNED,
)

_EXPORT_USAGE = (
    "Формат: <code>/export_issues [status=...] [category=...]</code>\n"
    "status: new, reviewed, planned, implemented, wont_fix, archived (через "
    "запятую) или all. category: failure, missing_capability, ux_friction, "
    "improvement_idea. Без фильтров — только открытые записи."
)


async def _system_summary(
    session: AsyncSession, settings: Settings, ai: AIProvider, user_id: int
) -> tuple[list[str], dict, dict]:
    """Строки системной сводки + счётчики backlog (пустые при недоступной БД)."""
    lines = ["<b>Диагностика</b>"]
    status_counts: dict = {}
    category_counts: dict = {}
    try:
        counts: dict[str, int] = {}
        for model, label in (
            (Task, "задач"),
            (KnowledgeItem, "записей знаний"),
            (Document, "документов"),
            (Secret, "секретов"),
        ):
            result = await session.execute(
                select(func.count()).select_from(model).where(model.deleted_at.is_(None))
            )
            counts[label] = int(result.scalar_one())
        repo = ImprovementRepository(session)
        status_counts = await repo.counts_by_status()
        category_counts = await repo.counts_by_category()
        lines.append("База данных: ✅")
        lines.append(
            "Сохранено: " + ", ".join(f"{value} {label}" for label, value in counts.items())
        )
        total = sum(status_counts.values())
        opened = sum(
            status_counts.get(status, 0) for status in OPEN_STATUSES
        )
        lines.append(f"Backlog улучшений: {total} записей (открытых: {opened})")
        agent_line = await _agent_last_run_line(session, settings, user_id)
        if agent_line:
            lines.append(agent_line)
    except Exception:  # noqa: BLE001 - статус не должен падать
        lines.append("База данных: ❌")
    lines.extend(
        [
            f"AI-провайдер: <code>{settings.ai_provider}</code> ({ai.name})",
            f"Транскрибация: <code>{settings.transcription_provider}</code>",
            f"Часовой пояс: <code>{settings.app_timezone}</code>",
            "Шифрование хранилища: "
            + ("✅ настроено" if settings.has_document_key else "❌ нет ключа"),
        ]
    )
    return lines, status_counts, category_counts


async def cmd_diagnostics(
    message: Message, session: AsyncSession, settings: Settings, ai: AIProvider
) -> None:
    lines, status_counts, category_counts = await _system_summary(
        session, settings, ai, message.from_user.id
    )
    await message.answer(
        "\n".join(lines), reply_markup=diagnostics_keyboard(status_counts, category_counts)
    )


async def menu_diagnostics(
    callback: CallbackQuery, session: AsyncSession, settings: Settings, ai: AIProvider
) -> None:
    lines, status_counts, category_counts = await _system_summary(
        session, settings, ai, callback.from_user.id
    )
    await callback.answer()
    await callback.message.answer(
        "\n".join(lines), reply_markup=diagnostics_keyboard(status_counts, category_counts)
    )


async def _agent_last_run_line(
    session: AsyncSession, settings: Settings, user_id: int
) -> str | None:
    """Сводка последнего агентского запроса (без содержимого диалога)."""
    from jarvis.agent.routing import AGENT_FLOW
    from jarvis.conversation.state import ConversationStore
    from jarvis.repositories.conversation import ConversationRepository

    state = await ConversationStore(ConversationRepository(session), settings).get_active(
        user_id
    )
    if state is None or state.flow != AGENT_FLOW:
        return None
    run = (state.data or {}).get("agent_last_run")
    if not isinstance(run, dict):
        return None
    tokens = run.get("tokens")
    tokens_text = f", токенов {tokens}" if tokens else ""
    return (
        "AI-агент (последний запрос): "
        f"{esc(str(run.get('termination', '?')))}, профиль {esc(str(run.get('profile', '?')))}, "
        f"шагов {run.get('steps', '?')}, инструментов {run.get('tool_calls', '?')}"
        f"{tokens_text}, {run.get('duration_ms', '?')} мс"
    )


# --- Список backlog -----------------------------------------------------------


def _filter_from(kind: str, value: str):
    if kind == "status":
        return {"status": ImprovementStatus(value)}
    if kind == "category":
        return {"category": ImprovementCategory(value)}
    if kind == "all":
        return {}
    return {"statuses": OPEN_STATUSES}


def _list_title(kind: str, value: str) -> str:
    if kind == "status":
        return IMPROVEMENT_STATUS_LABELS.get(value, "Backlog")
    if kind == "category":
        return IMPROVEMENT_CATEGORY_LABELS.get(value, "Backlog")
    if kind == "all":
        return "📚 Все записи backlog"
    return "📥 Открытые записи backlog"


def render_backlog_card(item: ImprovementItem, tz) -> str:
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


async def build_backlog_page(
    session: AsyncSession, settings: Settings, kind: str, value: str, page: int
) -> tuple[str, object]:
    repo = ImprovementRepository(session)
    filters = _filter_from(kind, value)
    items = await repo.list_items(
        limit=PAGE_SIZE, offset=page * PAGE_SIZE, **filters
    )
    total = await repo.count_items(**filters)
    lines = [_list_title(kind, value), ""]
    if not items:
        lines.append("Пусто.")
    for index, item in enumerate(items, start=1):
        status = IMPROVEMENT_STATUS_LABELS.get(item.status.value, item.status.value)
        lines.append(
            f"{index}. {esc(item.title[:60])} — {status}, "
            f"повторов: {item.occurrence_count}"
        )
    text = "\n".join(lines)
    has_next = page * PAGE_SIZE + len(items) < total
    markup = backlog_list_keyboard(items, kind, value, page, has_next)
    return text, markup


async def _edit_or_answer(callback: CallbackQuery, text: str, markup) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except Exception:  # noqa: BLE001 - сообщение могло быть недоступно для правки
        await callback.message.answer(text, reply_markup=markup)


async def backlog_list_action(
    callback: CallbackQuery, callback_data: ImpListCB, session: AsyncSession, settings: Settings
) -> None:
    await callback.answer()
    try:
        text, markup = await build_backlog_page(
            session, settings, callback_data.kind, callback_data.value, max(0, callback_data.page)
        )
    except ValueError:
        await callback.answer("Фильтр устарел — откройте /diagnostics заново", show_alert=True)
        return
    await _edit_or_answer(callback, text, markup)


# --- Карточка и действия ------------------------------------------------------


async def _render_card(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
    item_id: str,
    origin: str,
    page: int,
) -> None:
    item = await ImprovementRepository(session).get(parse_hex_uuid(item_id))
    if item is None:
        await callback.answer("Запись не найдена", show_alert=True)
        return
    markup = backlog_card_keyboard(item, origin, page)
    await _edit_or_answer(callback, render_backlog_card(item, settings.tz), markup)


async def imp_action(
    callback: CallbackQuery,
    callback_data: ImpCB,
    session: AsyncSession,
    settings: Settings,
) -> None:
    action = callback_data.action
    repo = ImprovementRepository(session)

    if action == "export":
        await callback.answer("Готовлю экспорт…")
        await send_issue_export(
            callback.message, session, settings, statuses=None, category=None
        )
        return

    item_id = parse_hex_uuid(callback_data.item_id)
    item = await repo.get(item_id) if item_id else None

    if action == "view":
        if item is None:
            await callback.answer("Запись не найдена", show_alert=True)
            return
        await callback.answer()
        markup = backlog_card_keyboard(item, callback_data.value, callback_data.page)
        await _edit_or_answer(callback, render_backlog_card(item, settings.tz), markup)
        return

    if action == "status":
        if item is None:
            await callback.answer("Запись не найдена", show_alert=True)
            return
        status = ImprovementStatus(callback_data.value)
        await repo.update_status(item, status)
        await callback.answer("Статус обновлён")
        await _render_card(callback, session, settings, callback_data.item_id, "open", 0)
        return

    if action == "delete":
        if item is None:
            await callback.answer("Запись не найдена", show_alert=True)
            return
        await callback.answer()
        await callback.message.answer(
            f"Удалить запись «{esc(item.title)}» <b>безвозвратно</b>? "
            "История повторов тоже будет удалена.",
            reply_markup=backlog_delete_keyboard(
                uuid_hex(item.id), callback_data.value, callback_data.page
            ),
        )
        return

    if action == "delete_yes":
        if item is None:
            await callback.answer("Уже удалено")
            return
        await repo.hard_delete(item)
        await callback.answer("Удалено")
        await _edit_or_answer(callback, "🗑 Запись удалена.", None)
        return

    if action == "delete_no":
        await callback.answer("Отменено")
        await _render_card(
            callback,
            session,
            settings,
            callback_data.item_id,
            callback_data.value or "open",
            callback_data.page,
        )
        return

    await callback.answer("Неизвестное действие", show_alert=True)


# --- Экспорт ------------------------------------------------------------------


def parse_export_filters(
    text: str | None,
) -> tuple[tuple[ImprovementStatus, ...] | None, ImprovementCategory | None]:
    """Разбор `/export_issues [status=...] [category=...]`."""
    statuses: tuple[ImprovementStatus, ...] | None = OPEN_STATUSES
    category: ImprovementCategory | None = None
    for token in (text or "").split()[1:]:
        key, _, value = token.partition("=")
        if key == "status":
            if value == "all":
                statuses = None
            else:
                statuses = tuple(
                    ImprovementStatus(part) for part in value.split(",") if part
                )
        elif key == "category":
            category = ImprovementCategory(value)
    return statuses, category


async def send_issue_export(
    target: Message,
    session: AsyncSession,
    settings: Settings,
    *,
    statuses: tuple[ImprovementStatus, ...] | None,
    category: ImprovementCategory | None,
) -> None:
    """Выгрузить backlog в Markdown+JSON и отправить файлы в чат."""
    repo = ImprovementRepository(session)
    result = await export_backlog(repo, statuses=statuses, category=category, tz=settings.tz)

    directory = settings.export_dir
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(settings.tz).strftime("%Y%m%d_%H%M%S")
    md_path = directory / f"jarvis_issues_{stamp}.md"
    json_path = directory / f"jarvis_issues_{stamp}.json"
    md_path.write_text(result.markdown, encoding="utf-8")
    json_path.write_text(result.json_text, encoding="utf-8")

    await target.answer(
        f"📤 Экспорт готов: {result.item_count} записей. Файлы обезличены — их можно "
        "передать в OpenCode вручную."
    )
    await target.answer_document(FSInputFile(md_path))
    await target.answer_document(FSInputFile(json_path))


async def cmd_export_issues(
    message: Message, session: AsyncSession, settings: Settings
) -> None:
    try:
        statuses, category = parse_export_filters(message.text)
    except ValueError:
        await message.answer("Не понял фильтр.\n\n" + _EXPORT_USAGE)
        return
    await send_issue_export(
        message, session, settings, statuses=statuses, category=category
    )


def build_router() -> Router:
    router = Router(name="diagnostics")
    router.message.register(cmd_diagnostics, Command("diagnostics", "status"))
    router.message.register(cmd_export_issues, Command("export_issues"))
    router.callback_query.register(menu_diagnostics, MenuCBFilter("diagnostics"))
    router.callback_query.register(backlog_list_action, ImpListCB.filter())
    router.callback_query.register(imp_action, ImpCB.filter())
    return router
