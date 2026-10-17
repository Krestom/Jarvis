"""Improvement backlog: фиксация сбоев, гипотез и объединение записей."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from jarvis import __version__
from jarvis.ai.schemas import ImprovementHypothesis
from jarvis.config.settings import Settings
from jarvis.db.models import ImprovementItem
from jarvis.domain.enums import ImprovementCategory, ImprovementSource
from jarvis.repositories.improvements import ImprovementRepository
from jarvis.security.redaction import redact
from jarvis.services.errors import NotFoundError, OperationError

log = logging.getLogger(__name__)


class ImprovementService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._repo = ImprovementRepository(session)

    # --- Фиксация -------------------------------------------------------------

    async def record_failure(
        self,
        *,
        title: str,
        summary: str,
        component: str | None = None,
        correlation_id: str | None = None,
        error_type: str | None = None,
        traceback_text: str | None = None,
        context: dict[str, Any] | None = None,
        category: ImprovementCategory = ImprovementCategory.FAILURE,
    ) -> ImprovementItem:
        """Зафиксировать подтверждённый сбой.

        Повтор той же проблемы (источник+компонент+заголовок) не создаёт
        дубликат: добавляется событие и растёт occurrence_count.
        """
        diagnostic = self._diagnostic_context(
            correlation_id=correlation_id,
            error_type=error_type,
            traceback_text=traceback_text,
            extra=context,
        )
        title = title.strip()[:300]
        existing = await self._repo.find_open_by_signature(
            source=ImprovementSource.TECHNICAL_EVENT, component=component, title=title
        )
        if existing is not None:
            await self._repo.register_occurrence(
                existing,
                correlation_id=correlation_id,
                note=summary.strip()[:500] or None,
                diagnostic_context=diagnostic,
            )
            log.info("improvement item %s: occurrence %s", existing.id, existing.occurrence_count)
            return existing

        item = await self._repo.create(
            category=category,
            source=ImprovementSource.TECHNICAL_EVENT,
            title=title,
            summary=summary.strip() or title,
            component=component,
            correlation_id=correlation_id,
            app_version=__version__,
            diagnostic_context=diagnostic,
            meta={},
        )
        await self._repo.register_occurrence(
            item, correlation_id=correlation_id, diagnostic_context=diagnostic, first=True
        )
        return item

    async def record_hypothesis(
        self,
        hypothesis: ImprovementHypothesis | None,
        *,
        correlation_id: str | None = None,
    ) -> ImprovementItem | None:
        """Сохранить гипотезу LLM, если её достоверность не ниже порога."""
        if hypothesis is None:
            return None
        threshold = self._settings.improvement_hypothesis_min_confidence
        if hypothesis.confidence < threshold:
            log.info(
                "improvement hypothesis skipped: confidence %.2f < %.2f",
                hypothesis.confidence,
                threshold,
            )
            return None

        diagnostic = self._diagnostic_context(
            correlation_id=correlation_id,
            extra={"evidence": hypothesis.evidence} if hypothesis.evidence else None,
        )
        title = redact(hypothesis.title.strip()[:300])
        existing = await self._repo.find_open_by_signature(
            source=ImprovementSource.LLM_HYPOTHESIS, component=None, title=title
        )
        if existing is not None:
            existing.confidence = max(existing.confidence or 0.0, hypothesis.confidence)
            await self._repo.register_occurrence(
                existing,
                correlation_id=correlation_id,
                note=redact(hypothesis.summary.strip()[:500]) or None,
                diagnostic_context=diagnostic,
            )
            return existing

        item = await self._repo.create(
            category=ImprovementCategory(hypothesis.category),
            source=ImprovementSource.LLM_HYPOTHESIS,
            confidence=hypothesis.confidence,
            title=title,
            summary=redact(hypothesis.summary.strip()) or title,
            correlation_id=correlation_id,
            app_version=__version__,
            diagnostic_context=diagnostic,
            meta={},
        )
        await self._repo.register_occurrence(
            item, correlation_id=correlation_id, diagnostic_context=diagnostic, first=True
        )
        return item

    async def record_gap(
        self,
        *,
        title: str,
        summary: str,
        evidence: str | None = None,
        correlation_id: str | None = None,
    ) -> ImprovementItem:
        """Зафиксировать объективно отсутствующую возможность (не сбой и не гипотеза).

        Дедуп — по открытой записи с тем же заголовком.
        """
        diagnostic = self._diagnostic_context(
            correlation_id=correlation_id,
            extra={"evidence": evidence} if evidence else None,
        )
        title = title.strip()[:300]
        existing = await self._repo.find_open_by_signature(
            source=ImprovementSource.USER_OBSERVATION, component=None, title=title
        )
        if existing is not None:
            await self._repo.register_occurrence(
                existing,
                correlation_id=correlation_id,
                note=summary.strip()[:500] or None,
                diagnostic_context=diagnostic,
            )
            return existing
        item = await self._repo.create(
            category=ImprovementCategory.MISSING_CAPABILITY,
            source=ImprovementSource.USER_OBSERVATION,
            title=title,
            summary=summary.strip() or title,
            correlation_id=correlation_id,
            app_version=__version__,
            diagnostic_context=diagnostic,
            meta={},
        )
        await self._repo.register_occurrence(
            item, correlation_id=correlation_id, diagnostic_context=diagnostic, first=True
        )
        return item

    async def merge(
        self, target_id: uuid.UUID, source_ids: list[uuid.UUID]
    ) -> ImprovementItem:
        """Объединить записи, сохранив их события и происхождение."""
        target = await self._repo.get(target_id)
        if target is None:
            raise NotFoundError("Целевая запись не найдена")
        sources: list[ImprovementItem] = []
        for source_id in source_ids:
            if source_id == target_id:
                continue
            source = await self._repo.get(source_id)
            if source is None:
                raise NotFoundError(f"Запись {source_id} не найдена")
            sources.append(source)
        if not sources:
            raise OperationError("Нечего объединять: укажите другие записи")
        return await self._repo.merge_into(target, sources)

    # --- Внутреннее -----------------------------------------------------------

    def _diagnostic_context(
        self,
        *,
        correlation_id: str | None,
        error_type: str | None = None,
        traceback_text: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Безопасный контекст: секреты вырезаны, размеры ограничены настройками."""
        limit = self._settings.improvement_context_max_chars
        context: dict[str, Any] = {}
        if correlation_id:
            context["correlation_id"] = correlation_id
        if error_type:
            context["error_type"] = error_type
        if traceback_text:
            context["traceback"] = redact(traceback_text)[:limit]
        for key, value in (extra or {}).items():
            if value is None:
                continue
            context[key] = redact(str(value))[:limit] if isinstance(value, str) else value
        return context
