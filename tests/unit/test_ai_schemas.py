"""Схема интерпретации: гипотеза улучшения валидируется как часть ответа LLM."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from jarvis.ai.schemas import Interpretation


def test_interpretation_accepts_valid_hypothesis():
    interpretation = Interpretation.model_validate(
        {
            "intent": "smalltalk",
            "reply": "Записал в улучшения.",
            "improvement_hypothesis": {
                "category": "ux_friction",
                "title": "Слишком много подтверждений",
                "summary": "Каждое действие требует подтверждения",
                "confidence": 0.8,
                "evidence": "«слишком много кнопок»",
            },
        }
    )
    hypothesis = interpretation.improvement_hypothesis
    assert hypothesis is not None
    assert hypothesis.category == "ux_friction"
    assert hypothesis.confidence == 0.8


def test_interpretation_rejects_unknown_category_and_bad_confidence():
    with pytest.raises(ValidationError):
        Interpretation.model_validate(
            {
                "improvement_hypothesis": {
                    "category": "not_a_category",
                    "title": "Заголовок",
                    "summary": "Суть",
                    "confidence": 0.5,
                }
            }
        )
    with pytest.raises(ValidationError):
        Interpretation.model_validate(
            {
                "improvement_hypothesis": {
                    "category": "failure",
                    "title": "Заголовок",
                    "summary": "Суть",
                    "confidence": 1.5,
                }
            }
        )


def test_interpretation_without_hypothesis_is_valid():
    interpretation = Interpretation.model_validate({"intent": "create_task"})
    assert interpretation.improvement_hypothesis is None
