"""Детерминированный роутинг: обращение по имени и профили запроса."""

from __future__ import annotations

import pytest

from jarvis.agent.routing import detect_profile, invokes_jarvis, mentions_backlog


@pytest.mark.parametrize(
    "text",
    [
        "Jarvis, какие у меня задачи?",
        "JARVIS покажи задачи на неделю",
        "jarvis, добавь комментарий",
        "Джарвис, привет",
        "джарвис, что там по серверу?",
        "Привет, Джарвис!",
        "ДЖАРВИС, АУ",
    ],
)
def test_invocation_matches_case_variants(text):
    assert invokes_jarvis(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "",
        "привет, как дела?",
        "jarvisница",
        "myjarvis",
        "джарвисы",
        "jarvis-обёртка",
        "почини джарвисоподобное",
    ],
)
def test_invocation_does_not_match_inside_words(text):
    assert invokes_jarvis(text) is False


def test_detect_profile_marks_research_and_planning(settings):
    assert detect_profile("исследуй, как настроить nginx", settings) == "research"
    assert detect_profile("Research the best backup tool", settings) == "research"
    assert detect_profile("давай спланируем следующую неделю", settings) == "planning"
    assert detect_profile("составь план на день", settings) == "planning"
    assert detect_profile("покажи задачи на сегодня", settings) == "ordinary"
    assert detect_profile("Джарвис, привет", settings) == "ordinary"


@pytest.mark.parametrize(
    "text",
    [
        "выведи бэклог",
        "Выведи бэклог проблем",
        "запиши в бэклог на улучшение: хочу уведомления",
        "добавь в improvement backlog заметку",
        "что у тебя в диагностике?",
    ],
)
def test_backlog_mentions_match(text):
    assert mentions_backlog(text) is True


@pytest.mark.parametrize(
    "text",
    ["", "поставь задачу на завтра", "найди заметку про сервер", "купить провод type-c"],
)
def test_backlog_mentions_do_not_match_normal_tasks(text):
    assert mentions_backlog(text) is False
