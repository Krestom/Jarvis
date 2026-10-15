"""Интеграционные тесты экспорта improvement backlog (Markdown/JSON)."""

from __future__ import annotations

import json

import pytest

from jarvis.ai.schemas import ImprovementHypothesis
from jarvis.domain.enums import ImprovementCategory
from jarvis.repositories.improvements import ImprovementRepository
from jarvis.services.improvement_export import export_backlog
from jarvis.services.improvements import ImprovementService

pytestmark = pytest.mark.integration


async def test_export_backlog_redacts_secrets_and_filters(session, test_settings):
    service = ImprovementService(session, test_settings)
    await service.record_failure(
        title="Сбой при отправке",
        summary="В логе виден токен: Authorization: Bearer abcdef0123456789abcdef",
        component="telegram_bot",
    )
    await service.record_hypothesis(
        ImprovementHypothesis(
            category="improvement_idea",
            title="Идея: тёмная тема",
            summary="Пользователь просит тёмную тему в отчётах",
            confidence=0.9,
        )
    )

    repo = ImprovementRepository(session)
    result = await export_backlog(
        repo, statuses=None, category=None, tz=test_settings.tz
    )
    assert result.item_count == 2
    assert "Сбой при отправке" in result.markdown
    assert "abcdef0123456789abcdef" not in result.markdown
    assert "[REDACTED" in result.markdown

    payload = json.loads(result.json_text)
    assert payload["count"] == 2
    assert payload["filters"]["statuses"] == "all"
    assert "abcdef0123456789abcdef" not in result.json_text
    titles = {item["title"] for item in payload["items"]}
    assert titles == {"Сбой при отправке", "Идея: тёмная тема"}
    # Свидетельства повторов попадают в JSON.
    exported = next(item for item in payload["items"] if item["occurrence_count"] == 1)
    assert len(exported["events"]) == 1

    filtered = await export_backlog(
        repo,
        statuses=None,
        category=ImprovementCategory.IMPROVEMENT_IDEA,
        tz=test_settings.tz,
    )
    assert filtered.item_count == 1
    assert "тёмная тема" in filtered.markdown
