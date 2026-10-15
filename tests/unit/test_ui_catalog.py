"""Типизированный UI-каталог: allow-list действий и сборка клавиатуры."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from jarvis.ai.schemas import UIRequest
from jarvis.bot.keyboards import quick_actions_keyboard


def test_ui_request_accepts_allow_list_actions():
    request = UIRequest.model_validate(
        {"component": "quick_actions", "actions": ["search", "tasks_today"]}
    )
    assert request.actions == ["search", "tasks_today"]


def test_ui_request_rejects_unknown_component_and_actions():
    with pytest.raises(ValidationError):
        UIRequest.model_validate({"component": "keyboard", "actions": ["search"]})
    with pytest.raises(ValidationError):
        UIRequest.model_validate({"component": "quick_actions", "actions": ["run_ssh"]})
    with pytest.raises(ValidationError):
        UIRequest.model_validate({"component": "quick_actions", "actions": []})


def _button_labels(markup) -> list[str]:
    return [button.text or "" for row in markup.inline_keyboard for button in row]


def test_quick_actions_keyboard_deduplicates_and_filters():
    markup = quick_actions_keyboard(["search", "search", "unknown_action", "documents"])
    labels = _button_labels(markup)
    assert labels.count("🔎 Поиск") == 1
    assert "📄 Документы" in labels
    assert "🏠 В меню" in labels


def test_quick_actions_keyboard_empty_for_unknown_actions():
    assert quick_actions_keyboard([]) is None
    assert quick_actions_keyboard(["nope"]) is None
