"""Схемы агентского цикла: PlanStep и UI-описания из allow-list."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from jarvis.ai.schemas import EntityChoice, PlanStep


def test_plan_step_tool_call_requires_tool():
    with pytest.raises(ValidationError):
        PlanStep.model_validate({"type": "tool_call", "args": {}})


def test_plan_step_reply_requires_text():
    with pytest.raises(ValidationError):
        PlanStep.model_validate({"type": "reply", "text": "   "})
    step = PlanStep.model_validate({"type": "reply", "text": "Готово"})
    assert step.ui is None


def test_plan_step_accepts_entity_choice_ui():
    entity_id = str(uuid.uuid4())
    step = PlanStep.model_validate(
        {
            "type": "reply",
            "text": "Уточните задачу",
            "ui": {
                "component": "entity_choice",
                "entity_kind": "task",
                "entity_ids": [entity_id],
                "prompt": "Какую задачу?",
            },
        }
    )
    assert isinstance(step.ui, EntityChoice)
    assert step.ui.entity_ids[0] == uuid.UUID(entity_id)


def test_plan_step_rejects_unknown_ui_component_and_too_many_ids():
    with pytest.raises(ValidationError):
        PlanStep.model_validate(
            {"type": "reply", "text": "x", "ui": {"component": "raw_keyboard", "ids": []}}
        )
    with pytest.raises(ValidationError):
        EntityChoice(
            component="entity_choice",
            entity_kind="task",
            entity_ids=[uuid.uuid4() for _ in range(7)],
        )


def test_plan_step_quick_actions_allow_list():
    step = PlanStep.model_validate(
        {
            "type": "reply",
            "text": "Подсказать?",
            "ui": {"component": "quick_actions", "actions": ["tasks_today", "search"]},
        }
    )
    assert step.ui.actions == ["tasks_today", "search"]
    with pytest.raises(ValidationError):
        PlanStep.model_validate(
            {
                "type": "reply",
                "text": "x",
                "ui": {"component": "quick_actions", "actions": ["rm_rf"]},
            }
        )
