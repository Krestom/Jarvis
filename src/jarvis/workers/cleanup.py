"""Периодическая уборка: состояния, pending-операции, updates, staged-файлы."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jarvis.config.settings import Settings
from jarvis.db.session import session_scope
from jarvis.documents.storage import EncryptedFileStorage
from jarvis.repositories.conversation import ConversationRepository
from jarvis.repositories.pending import PendingOperationRepository
from jarvis.repositories.processed_updates import ProcessedUpdateRepository
from jarvis.services.documents import DocumentService

log = logging.getLogger(__name__)

CLEANUP_INTERVAL_SECONDS = 600
STAGED_PENDING_TTL_HOURS = 24
RESOLVED_OPS_TTL_DAYS = 7


async def run_cleanup_once(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> dict[str, int]:
    now = datetime.now(UTC)
    stats: dict[str, int] = {}
    async with session_scope(session_factory) as session:
        stats["conversation_states"] = await ConversationRepository(session).purge_expired(now)
        pending = PendingOperationRepository(session)
        stats["pending_operations_expired"] = await pending.expire_stale(now)
        stats["pending_operations_purged"] = await PendingOperationRepository(session).purge_old(
            now - timedelta(days=RESOLVED_OPS_TTL_DAYS)
        )
        stats["processed_updates"] = await ProcessedUpdateRepository(session).purge_older_than(
            now - timedelta(hours=settings.processed_updates_retention_hours)
        )
        if settings.has_document_key:
            storage = EncryptedFileStorage(
                settings.document_storage_path, settings.document_keyring()
            )
            stats["staged_documents_purged"] = await DocumentService(
                session, settings, storage
            ).purge_stale_pending(now - timedelta(hours=STAGED_PENDING_TTL_HOURS))
            orphaned = 0
            for path in storage.list_staging_files_older_than(
                now - timedelta(hours=STAGED_PENDING_TTL_HOURS)
            ):
                try:
                    path.unlink(missing_ok=True)
                    orphaned += 1
                except OSError:
                    pass
            stats["orphaned_staging_files"] = orphaned
    return stats


async def cleanup_loop(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    stop: asyncio.Event,
) -> None:
    while not stop.is_set():
        try:
            stats = await run_cleanup_once(session_factory, settings)
            if any(stats.values()):
                log.info("cleanup: %s", stats)
        except Exception:  # noqa: BLE001 - воркер не должен умирать от разовой ошибки
            log.exception("cleanup run failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=CLEANUP_INTERVAL_SECONDS)
        except TimeoutError:
            continue
