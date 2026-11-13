"""Главное меню, меню задач и списки задач."""

from __future__ import annotations

import html as html_lib
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from jarvis.bot.callbacks import MenuCB, TaskCB, TaskListCB, uuid_hex
from jarvis.bot.formatting import task_card_text
from jarvis.bot.keyboards import (
    comments_keyboard,
    main_menu,
    task_card_keyboard,
    task_list_keyboard,
    tasks_menu_keyboard,
)
from jarvis.bot.rich import RichScreen, button, esc, esc_br, row
from jarvis.bot.texts import HELP_TEXT
from jarvis.conversation.preview import (
    STATUS_LABELS,
    fmt_dt_local,
    task_icon,
    task_line,
    task_list_text,
)
from jarvis.domain.windows import DONE_STATUSES


def main_menu_screen(
    *, intro: str = "Чем займёмся?", fallback: str = "Главное меню:"
) -> RichScreen:
    html = (
        "<h3>Jarvis</h3>"
        f"<p>{html_lib.escape(intro)}</p>"
        + row(
            button("🔎 Поиск", MenuCB(action="search").pack()),
            button("✅ Задачи", MenuCB(action="tasks").pack(), style="primary"),
        )
        + row(
            button("📚 Знания", MenuCB(action="knowledge").pack()),
            button("📄 Документы", MenuCB(action="documents").pack()),
        )
    )
    return RichScreen(html=html, text=fallback, markup=main_menu())


def tasks_menu_screen() -> RichScreen:
    html = (
        "<h3>📋 Задачи</h3>"
        "<p>Выберите список:</p>"
        + row(
            button("🔸 Сегодня", TaskListCB(view="today").pack(), style="primary"),
            button("📆 На этой неделе", TaskListCB(view="week").pack()),
        )
        + row(
            button("🗓 Следующая неделя", TaskListCB(view="next_week").pack()),
            button("📋 Все задачи", TaskListCB(view="all").pack()),
        )
        + row(
            button("📥 Без срока", TaskListCB(view="no_group").pack()),
            button("🏠 В меню", MenuCB(action="main").pack(), style="link"),
        )
    )
    return RichScreen(
        html=html,
        text="📋 <b>Задачи</b>\nВыберите список:",
        markup=tasks_menu_keyboard(),
    )


def task_list_screen(
    *,
    title: str,
    tasks,
    tz: ZoneInfo,
    now: datetime,
    view: str,
    page: int,
    value: str = "",
    has_next: bool,
    day=None,
) -> RichScreen:
    text = task_list_text(title, tasks, tz, now, day=day)
    markup = task_list_keyboard(tasks, view, page, has_next, value)
    parts = [f"<h3>{esc(title)}</h3>"]
    if page > 0 or has_next:
        parts.append(f"<p><i>Страница {page + 1}</i></p>")
    if not tasks:
        parts.append("<p>Пусто.</p>")
    else:
        parts.append("<p>Выберите задачу:</p>")
        for index, task in enumerate(tasks, start=1):
            label = re.sub(r"<[^>]+>", "", task_line(task, tz, index=index, now=now, day=day))
            label = html_lib.unescape(label)
            if len(label) > 64:
                label = label[:63].rstrip() + "…"
            parts.append(
                row(
                    button(
                        label,
                        TaskCB(
                            action="view", task_id=uuid_hex(task.id), value="l"
                        ).pack(),
                    )
                )
            )
    nav = []
    if page > 0:
        nav.append(button("⬅️", TaskListCB(view=view, page=page - 1, value=value).pack()))
    nav.append(button("📋 К спискам", MenuCB(action="tasks").pack()))
    if has_next:
        nav.append(button("➡️", TaskListCB(view=view, page=page + 1, value=value).pack()))
    parts.append(row(*nav))
    fallback = f"{text}\n\nВыберите задачу:" if tasks else text
    return RichScreen(html="".join(parts), text=fallback, markup=markup)


def help_screen(*, intro: str = "Коротко о возможностях:") -> RichScreen:
    html = (
        "<h3>Что я умею</h3>"
        f"<p>{esc(intro)}</p>"
        "<h4>📝 Заметки и знания</h4>"
        "<p>Пришлите мысль, факт или инструкцию — я предложу сохранить. "
        "Потом найду по смыслу и покажу источник.</p>"
        "<h4>✅ Задачи</h4>"
        "<p>Поставлю задачу со сроком, напомню заранее и дам закрыть одной кнопкой.</p>"
        "<h4>🔎 Поиск</h4>"
        "<p>Ищу по заметкам, задачам и документам — можно обычным вопросом.</p>"
        "<h4>📄 Документы и 🔐 секреты</h4>"
        "<p>Документы шифруются и доступны только здесь. Секреты не попадают "
        "в поиск, AI и MCP.</p>"
        "<h4>🤖 AI-агент</h4>"
        "<p>Обращайтесь по имени: «Джарвис, какие задачи на неделе?» — агент сам "
        "вызовет нужные инструменты и вернёт реальные данные.</p>"
        "<details><summary>Команды</summary>"
        "<p><code>/start</code> — главное меню<br>"
        "<code>/search</code> — поиск по базе<br>"
        "<code>/tasks</code> — список задач<br>"
        "<code>/today</code> — задачи на сегодня<br>"
        "<code>/documents</code> — документы<br>"
        "<code>/secret</code> — секреты<br>"
        "<code>/diagnostics</code> — диагностика и backlog<br>"
        "<code>/export_issues</code> — выгрузка backlog<br>"
        "<code>/cancel</code> — отменить сценарий<br>"
        "<code>/help</code> — эта справка</p></details>"
        + row(
            button("✅ Задачи", MenuCB(action="tasks").pack(), style="primary"),
            button("🔎 Поиск", MenuCB(action="search").pack()),
        )
        + row(
            button("📚 Знания", MenuCB(action="knowledge").pack()),
            button("📄 Документы", MenuCB(action="documents").pack()),
        )
        + row(button("🏠 В меню", MenuCB(action="main").pack(), style="link"))
    )
    return RichScreen(html=html, text=HELP_TEXT, markup=main_menu())


def task_card_screen(
    task, comments, tz: ZoneInfo, *, from_list: bool = False
) -> RichScreen:
    now = datetime.now(tz)
    overdue = task.due_at is not None and task.due_at.astimezone(tz).date() < now.date()
    icon = task_icon(task.status, overdue)
    label = STATUS_LABELS.get(task.status, str(task.status))
    task_id = uuid_hex(task.id)
    parts = [f"<h3>{icon} Задача</h3>", f"<p><b>{esc(task.title)}</b></p>"]
    if task.description:
        description = esc_br(task.description)
        if len(description) > 300:
            parts.append(
                "<details><summary>Описание</summary>"
                f"<blockquote>{description}</blockquote></details>"
            )
        else:
            parts.append(f"<blockquote>{description}</blockquote>")
    details = [f"<b>Статус:</b> {esc(label)}"]
    if task.due_at is not None:
        details.append(f"<b>Срок:</b> {esc(fmt_dt_local(task.due_at, tz))}")
    parts.append("<p>" + "<br>".join(details) + "</p>")
    if task.original_text and task.original_text.strip() != task.title.strip():
        original = task.original_text.strip()
        if len(original) > 400:
            original = original[:399] + "…"
        parts.append(
            "<details><summary>Исходный текст</summary>"
            f"<blockquote>{esc_br(original)}</blockquote></details>"
        )
    if comments:
        shown = comments[-3:]
        lines = []
        for index, comment in enumerate(shown, start=len(comments) - len(shown) + 1):
            stamp = fmt_dt_local(comment.created_at, tz)
            lines.append(f"<b>{index}.</b> {esc_br(comment.content)}<br><i>{stamp}</i>")
        if len(comments) > len(shown):
            lines.append(f"<i>… ещё {len(comments) - len(shown)}</i>")
        parts.append(
            f"<p><b>Комментарии</b></p><blockquote>{'<br>'.join(lines)}</blockquote>"
        )

    comments_label = f"💬 Комментарии ({len(comments)})" if comments else "💬 Комментарии"
    if task.status in DONE_STATUSES:
        actions = row(
            button("↩️ Вернуть в работу", TaskCB(action="reopen", task_id=task_id).pack()),
            button(comments_label, TaskCB(action="comments", task_id=task_id).pack()),
        )
        actions += row(
            button("🗑 Удалить", TaskCB(action="delete", task_id=task_id).pack(), style="danger")
        )
    else:
        actions = row(
            button("🏁 Завершить", TaskCB(action="done", task_id=task_id).pack(), style="success"),
            button("📅 Изменить срок", TaskCB(action="due", task_id=task_id).pack()),
        )
        actions += row(
            button(comments_label, TaskCB(action="comments", task_id=task_id).pack()),
            button("🗑 Удалить", TaskCB(action="delete", task_id=task_id).pack(), style="danger"),
        )
    back = (
        button("⬅️ Назад", TaskCB(action="back").pack(), style="link")
        if from_list
        else button("📋 К спискам", MenuCB(action="tasks").pack(), style="link")
    )
    actions += row(back)

    text = task_card_text(task, tz)
    if comments:
        text += "\n\n💬 <b>Комментарии</b>"
        for index, comment in enumerate(comments[-3:], start=1):
            preview = comment.content
            preview = preview if len(preview) <= 200 else preview[:199] + "…"
            text += f"\n{index}. {esc(preview)}"
        if len(comments) > 3:
            text += f"\n<i>… ещё {len(comments) - 3}</i>"
    text += "\n\nДействия:"
    markup = task_card_keyboard(
        task_id, task.status.value, len(comments), from_list=from_list
    )
    return RichScreen(html="".join(parts) + actions, text=text, markup=markup)


def comments_screen(task, comments, tz: ZoneInfo) -> RichScreen:
    task_id = uuid_hex(task.id)
    parts = ["<h3>💬 Комментарии</h3>", f"<p><b>{esc(task.title)}</b></p>"]
    if comments:
        lines = []
        for index, comment in enumerate(comments, start=1):
            stamp = fmt_dt_local(comment.created_at, tz)
            lines.append(f"<b>{index}.</b> {esc_br(comment.content)}<br><i>{stamp}</i>")
        parts.append(f"<blockquote>{'<br>'.join(lines)}</blockquote>")
    else:
        parts.append("<p>Комментариев пока нет.</p>")
    actions = row(
        button(
            "➕ Добавить комментарий",
            TaskCB(action="comment_add", task_id=task_id).pack(),
            style="primary",
        )
    )
    edit_buttons = [
        button(
            f"✏️ Изменить {index}",
            TaskCB(action="cedit", value=uuid_hex(comment.id)).pack(),
        )
        for index, comment in enumerate(comments, start=1)
    ]
    for start in range(0, len(edit_buttons), 2):
        actions += row(*edit_buttons[start : start + 2])
    actions += row(
        button("⬅️ К задаче", TaskCB(action="view", task_id=task_id).pack(), style="link")
    )

    if comments:
        text = f"💬 <b>Комментарии</b> — {esc(task.title)}\n\n"
        text += "\n".join(
            f"<b>{index}.</b> {esc(comment.content)}\n"
            f"<i>{fmt_dt_local(comment.created_at, tz)}</i>"
            for index, comment in enumerate(comments, start=1)
        )
    else:
        text = f"💬 Комментариев к <{esc(task.title)}> пока нет."
    text += "\n\nДействия:"
    return RichScreen(
        html="".join(parts) + actions,
        text=text,
        markup=comments_keyboard(comments, task_id),
    )


__all__ = [
    "comments_screen",
    "help_screen",
    "main_menu_screen",
    "task_card_screen",
    "task_list_screen",
    "tasks_menu_screen",
]
