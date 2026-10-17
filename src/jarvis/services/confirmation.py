"""Подтверждение значимых изменений через pending-операции."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.config.settings import Settings
from jarvis.domain.enums import PendingOpType, PendingStatus
from jarvis.repositories.audit import AuditRepository
from jarvis.repositories.pending import PendingOperationRepository
from jarvis.services.errors import OperationError
from jarvis.services.operations import apply_operation

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ConfirmResult:
    ok: bool
    summary: str
    already: bool = False
    operation_type: PendingOpType | None = None
    entity_id: str | None = None


class ConfirmationService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._pending = PendingOperationRepository(session)
        self._audit = AuditRepository(session)

    async def propose(
        self,
        telegram_user_id: int,
        operation_type: PendingOpType,
        payload: dict,
    ) -> str:
        """Создать предложение операции. Возвращает id операции (str)."""
        now = datetime.now(UTC)
        expires_at = now + timedelta(minutes=self._settings.pending_operation_ttl_minutes)
        operation = await self._pending.create(
            telegram_user_id, operation_type, payload, expires_at=expires_at
        )
        return str(operation.id)

    async def confirm(self, telegram_user_id: int, operation_id) -> ConfirmResult:
        now = datetime.now(UTC)
        try:
            op_id = uuid.UUID(str(operation_id))
        except ValueError:
            return ConfirmResult(ok=False, summary="Некорректная операция")

        outcome = await self._pending.resolve(
            op_id, telegram_user_id, PendingStatus.CONFIRMED, now=now
        )
        if outcome.reason == "not_found":
            return ConfirmResult(ok=False, summary="Операция не найдена. Возможно, устарела.")
        if outcome.reason == "expired":
            return ConfirmResult(ok=False, summary="Операция истекла. Сформируйте её заново.")
        if outcome.reason == "already_resolved":
            op = outcome.resolved
            if op is not None and op.status == PendingStatus.CONFIRMED and op.result:
                return ConfirmResult(
                    ok=True,
                    summary=str(op.result.get("summary", "Операция уже применена")),
                    already=True,
                )
            return ConfirmResult(
                ok=False, summary="Операция уже была отменена или истекла. Повторно не применяю."
            )

        operation = outcome.resolved
        assert operation is not None
        try:
            applied = await apply_operation(
                self._session, self._settings, operation.operation_type, operation.payload
            )
        except OperationError as error:
            await self._pending.set_result(operation.id, {"error": str(error)})
            await self._pending.mark_failed(operation.id)
            await self._audit.log(
                actor=f"telegram:{telegram_user_id}",
                action=f"confirm:{operation.operation_type.value}",
                result="failed",
                meta={"error": str(error)},
            )
            return ConfirmResult(ok=False, summary=str(error))
        except Exception:
            # Неожиданную ошибку пробрасываем: транзакция откатится целиком,
            # операция останется pending и её можно повторить безопасно.
            log.exception("operation apply failed unexpectedly")
            raise

        result = {
            "summary": applied.summary,
            "entity_type": applied.entity_type.value if applied.entity_type else None,
            "entity_id": str(applied.entity_id) if applied.entity_id else None,
        }
        await self._pending.set_result(operation.id, result)
        await self._audit.log(
            actor=f"telegram:{telegram_user_id}",
            action=f"confirm:{operation.operation_type.value}",
            entity_type=applied.entity_type.value if applied.entity_type else None,
            entity_id=applied.entity_id,
            result="ok",
        )
        return ConfirmResult(
            ok=True,
            summary=applied.summary,
            operation_type=operation.operation_type,
            entity_id=str(applied.entity_id) if applied.entity_id else None,
        )

    async def cancel(self, telegram_user_id: int, operation_id) -> ConfirmResult:
        now = datetime.now(UTC)
        try:
            op_id = uuid.UUID(str(operation_id))
        except ValueError:
            return ConfirmResult(ok=False, summary="Некорректная операция")
        outcome = await self._pending.resolve(
            op_id, telegram_user_id, PendingStatus.CANCELLED, now=now
        )
        if outcome.reason == "not_found":
            return ConfirmResult(ok=False, summary="Операция не найдена")
        if outcome.reason == "already_resolved":
            return ConfirmResult(
                ok=False, summary="Операция уже завершена или отменена", already=True
            )
        if outcome.reason == "expired":
            return ConfirmResult(ok=False, summary="Операция истекла")
        await self._audit.log(
            actor=f"telegram:{telegram_user_id}",
            action="cancel",
            result="ok",
        )
        return ConfirmResult(ok=True, summary="Операция отменена")
