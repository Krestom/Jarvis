"""Rich-экраны поиска и выбора сущности агентом."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from aiogram.types import InlineKeyboardButton

from jarvis.bot.callbacks import AgentPickCB, MenuCB, SearchCB, TaskCB, uuid_hex
from jarvis.bot.keyboards import (
    agent_entity_keyboard,
    comment_target_keyboard,
    main_menu,
    search_results_keyboard,
)
from jarvis.bot.rich import button
from jarvis.bot.screens.search import (
    entity_choice_screen,
    search_empty_screen,
    search_results_screen,
    task_choice_screen,
)


def _hit(kind: str, title: str, *, snippet: str = ""):
    return SimpleNamespace(
        kind=kind, entity_id=uuid.uuid4(), title=title, snippet=snippet
    )


def _search(hits, *, query="кофе", page=0, has_next=False, back=None):
    markup = search_results_keyboard(hits, query, page, has_next=has_next)
    screen = search_results_screen(
        hits, query, page, has_next=has_next, markup=markup, back=back
    )
    return screen, markup


def test_search_results_card_has_query_page_and_result_buttons():
    hits = [_hit("knowledge", "Рецепт кофе"), _hit("task", "Купить кофе")]
    screen, markup = _search(hits, page=1, has_next=True)

    assert "<h3>🔎 Поиск</h3>" in screen.html
    assert "Результаты по запросу: «кофе»" in screen.html
    assert "Страница 2" in screen.html
    assert (
        button(
            "1. 📚 Рецепт кофе",
            SearchCB(
                action="open",
                kind="knowledge",
                entity=uuid_hex(hits[0].entity_id),
                page=1,
            ).pack(),
        )
        in screen.html
    )
    assert button(
        "2. ✅ Купить кофе",
        SearchCB(
            action="open", kind="task", entity=uuid_hex(hits[1].entity_id), page=1
        ).pack(),
    ) in screen.html
    assert SearchCB(action="page_prev", page=0).pack() in screen.html
    assert SearchCB(action="page_next", page=2).pack() in screen.html
    assert (
        button("🏠 В меню", MenuCB(action="main").pack(), style="link")
        in screen.html
    )
    assert "Результаты по запросу" in screen.text
    assert "Рецепт кофе" in screen.text
    assert screen.markup is markup


def test_search_results_escape_query_and_title():
    screen, _ = _search([_hit("knowledge", "<b>опасно</b>")], query="<script>")

    assert "<script>" not in screen.html
    assert "&lt;script&gt;" in screen.html
    assert "<b>опасно</b>" not in screen.html
    assert "&lt;b&gt;опасно&lt;/b&gt;" in screen.html


def test_search_results_first_page_hides_prev_and_next():
    screen, _ = _search([_hit("document", "Инструкция")])

    assert "Страница 1" in screen.html
    assert SearchCB(action="page_prev", page=-1).pack() not in screen.html
    assert SearchCB(action="page_next", page=1).pack() not in screen.html


def test_search_results_back_link_is_styled_and_kept_in_markup():
    back = ("⬅️ К записи", "kb:view:abc")
    screen, markup = _search([_hit("knowledge", "Похожее")], back=back)
    markup.inline_keyboard.append(
        [InlineKeyboardButton(text=back[0], callback_data=back[1])]
    )

    assert button("⬅️ К записи", "kb:view:abc", style="link") in screen.html
    assert any(
        item.text == "⬅️ К записи"
        for row in markup.inline_keyboard
        for item in row
    )


def test_search_empty_first_page_has_query_and_menu():
    markup = main_menu()
    screen = search_empty_screen(
        "кофе", page=0, text="Ничего не нашёл.", markup=markup
    )

    assert "<h3>🔎 Поиск</h3>" in screen.html
    assert "Ничего не нашёл." in screen.html
    assert "Запрос: кофе" in screen.html
    assert (
        button("🏠 В меню", MenuCB(action="main").pack(), style="link")
        in screen.html
    )
    assert "sr:" not in screen.html
    assert screen.text == "Ничего не нашёл.\n\n<i>Запрос: кофе</i>"
    assert screen.markup is markup


def test_search_empty_later_page_has_no_query():
    screen = search_empty_screen(
        "кофе", page=1, text="Больше результатов нет.", markup=main_menu()
    )

    assert "Больше результатов нет." in screen.html
    assert "Запрос:" not in screen.html
    assert screen.text == "Больше результатов нет."


def test_entity_choice_card_uses_indexes_and_escapes_titles():
    candidates = [
        {"kind": "task", "title": "Починить <сервер>"},
        {"kind": "knowledge", "title": "Заметка"},
    ]
    markup = agent_entity_keyboard(candidates)
    screen = entity_choice_screen(candidates, prompt="Какую задачу?", markup=markup)

    assert "<h3>Уточните</h3>" in screen.html
    assert "1. ✅ Починить &lt;сервер&gt;" in screen.html
    assert "2. 📚 Заметка" in screen.html
    assert AgentPickCB(index=0).pack() in screen.html
    assert AgentPickCB(index=1).pack() in screen.html
    assert AgentPickCB(index=2).pack() not in screen.html
    assert (
        button("🏠 В меню", MenuCB(action="main").pack(), style="link")
        in screen.html
    )
    assert screen.text == "Какую задачу?"
    assert screen.markup is markup


def test_task_choice_card_preserves_action_and_titles():
    tasks = [SimpleNamespace(id=uuid.uuid4(), title="Обновить инфраструктуру")]
    markup = comment_target_keyboard(tasks, action="comments")
    screen = task_choice_screen(
        tasks, action="comments", prompt="К какой задаче?", markup=markup
    )

    assert "1. ✅ Обновить инфраструктуру" in screen.html
    assert (
        TaskCB(action="comments", task_id=uuid_hex(tasks[0].id)).pack()
        in screen.html
    )
    assert screen.text == "К какой задаче?"
    assert screen.markup is markup
