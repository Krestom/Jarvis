"""Rich-экраны поиска и выбора сущности агентом."""

from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup

from jarvis.bot.callbacks import AgentPickCB, MenuCB, SearchCB, TaskCB, uuid_hex
from jarvis.bot.rich import RichScreen, button, esc, row

_KIND_EMOJI = {"knowledge": "📚", "task": "✅", "document": "📄"}


def _clip(value: object, limit: int = 64) -> str:
    text = str(value or "—")
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _menu_row() -> str:
    return row(button("🏠 В меню", MenuCB(action="main").pack(), style="link"))


def _choice_screen(
    *, prompt: str, buttons: list[str], markup: InlineKeyboardMarkup
) -> RichScreen:
    parts = ["<h3>Уточните</h3>", f"<p>{esc(prompt)}</p>"]
    parts.extend(row(item) for item in buttons)
    parts.append(_menu_row())
    return RichScreen(html="".join(parts), text=prompt, markup=markup)


def search_results_screen(
    hits,
    query: str,
    page: int,
    *,
    has_next: bool,
    markup: InlineKeyboardMarkup,
    back: tuple[str, str] | None = None,
) -> RichScreen:
    parts = [
        "<h3>🔎 Поиск</h3>",
        f"<p><i>Результаты по запросу: «{esc(query)}»</i></p>",
        f"<p><i>Страница {page + 1}</i></p>",
    ]
    lines = [f"🔎 <b>Результаты по запросу</b> «{esc(query)}» — стр. {page + 1}\n"]
    for index, hit in enumerate(hits, start=1):
        kind_emoji = _KIND_EMOJI.get(hit.kind, "•")
        parts.append(
            row(
                button(
                    f"{index}. {kind_emoji} {_clip(hit.title)}",
                    SearchCB(
                        action="open",
                        kind=hit.kind,
                        entity=uuid_hex(hit.entity_id),
                        page=page,
                    ).pack(),
                )
            )
        )
        snippet = f" — <i>{esc(hit.snippet)}</i>" if hit.snippet else ""
        lines.append(f"{index}. {kind_emoji} {esc(hit.title)}{snippet}")

    nav = []
    if page > 0:
        nav.append(button("⬅️", SearchCB(action="page_prev", page=page - 1).pack()))
    nav.append(button("🏠 В меню", MenuCB(action="main").pack(), style="link"))
    if has_next:
        nav.append(button("➡️", SearchCB(action="page_next", page=page + 1).pack()))
    parts.append(row(*nav))
    if back is not None:
        parts.append(row(button(back[0], back[1], style="link")))

    text = "\n".join(lines) + "\n\nВыберите запись, чтобы открыть:"
    return RichScreen(html="".join(parts), text=text, markup=markup)


def search_empty_screen(
    query: str, *, page: int, text: str, markup: InlineKeyboardMarkup
) -> RichScreen:
    parts = ["<h3>🔎 Поиск</h3>", f"<p>{esc(text)}</p>"]
    fallback = text
    if page == 0:
        parts.append(f"<p><i>Запрос: {esc(query)}</i></p>")
        fallback = f"{text}\n\n<i>Запрос: {esc(query)}</i>"
    parts.append(_menu_row())
    return RichScreen(html="".join(parts), text=fallback, markup=markup)


def entity_choice_screen(
    candidates, *, prompt: str, markup: InlineKeyboardMarkup
) -> RichScreen:
    buttons = []
    for index, candidate in enumerate(candidates, start=1):
        kind = str(candidate.get("kind") or "")
        title = candidate.get("title") or "—"
        buttons.append(
            button(
                f"{index}. {_KIND_EMOJI.get(kind, '•')} {_clip(title)}",
                AgentPickCB(index=index - 1).pack(),
            )
        )
    return _choice_screen(prompt=prompt, buttons=buttons, markup=markup)


def task_choice_screen(
    tasks, *, action: str, prompt: str, markup: InlineKeyboardMarkup
) -> RichScreen:
    buttons = [
        button(
            f"{index}. ✅ {_clip(task.title)}",
            TaskCB(action=action, task_id=uuid_hex(task.id)).pack(),
        )
        for index, task in enumerate(tasks, start=1)
    ]
    return _choice_screen(prompt=prompt, buttons=buttons, markup=markup)


__all__ = [
    "entity_choice_screen",
    "search_empty_screen",
    "search_results_screen",
    "task_choice_screen",
]
