"""Детекция «задачи на <дата>» в движке (детерминированно, без AI)."""

from __future__ import annotations

from jarvis.ai.fake import FakeProvider
from jarvis.conversation.engine import ConversationEngine


def make_engine(settings) -> ConversationEngine:
    return ConversationEngine(None, settings, FakeProvider())


def test_date_list_detected_for_requests(settings):
    engine = make_engine(settings)
    assert engine._date_list_day("Выведи мне задачи на 22.10.2026") is not None
    assert engine._date_list_day("какие у меня задачи на завтра?") is not None


def test_date_list_not_stolen_from_task_creation(settings):
    engine = make_engine(settings)
    assert engine._date_list_day("поставь задачу на 22.10.2026") is None
    assert engine._date_list_day("добавь задачу на завтра: позвонить") is None
    assert engine._date_list_day("найди заметку про отпуск") is None
