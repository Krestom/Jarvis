"""Интеграционные тесты шифрованного хранилища и сервиса документов."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from jarvis.documents.storage import EncryptedFileStorage, StorageError
from jarvis.domain.enums import DocumentStatus
from jarvis.repositories.documents import DocumentRepository
from jarvis.security.crypto import KeyRing
from jarvis.services.documents import DocumentService
from jarvis.services.drafts import DocumentDraft
from jarvis.services.errors import OperationError

pytestmark = pytest.mark.integration

K1 = bytes(range(32))
K2 = bytes(range(32, 64))


def make_storage(path, key: bytes = K1, version: int = 1) -> EncryptedFileStorage:
    return EncryptedFileStorage(path, KeyRing(keys={version: key}, active_version=version))


async def test_document_full_lifecycle(session, test_settings):
    storage = EncryptedFileStorage(
        test_settings.document_storage_path, test_settings.document_keyring()
    )
    service = DocumentService(session, test_settings, storage)

    document = await service.stage_upload(
        data=b"super secret passport bytes",
        original_filename="passport.pdf",
        mime_type="application/pdf",
    )
    assert document.status == DocumentStatus.PENDING

    # Файл уже зашифрован и лежит в staging; расшифровывается только сервисом.
    staged = list(storage._staging().iterdir())
    assert len(staged) == 1
    assert b"secret" not in staged[0].read_bytes()

    _doc, data = await service.read_file(document.id)
    assert data == b"super secret passport bytes"

    confirmed = await service.confirm_save(
        DocumentDraft(
            document_id=document.id,
            title="Паспорт",
            document_type="passport",
            sensitivity="sensitive",
        )
    )
    assert confirmed.status == DocumentStatus.ACTIVE
    assert not list(storage._staging().iterdir())  # всё переехало в основное хранилище

    # Повторное подтверждение идемпотентно.
    again = await service.confirm_save(
        DocumentDraft(document_id=document.id, title="Паспорт", document_type="passport")
    )
    assert again.id == document.id

    # Удаление убирает и метаданные, и файл.
    await service.delete(document.id)
    assert await DocumentRepository(session).get(document.id) is None
    assert not storage._path_for(document.storage_key).exists()


async def test_storage_tamper_and_wrong_key(tmp_path):
    storage = make_storage(tmp_path)
    info = storage.save(b"payload-1")
    path = storage._path_for(info.storage_key)

    original = path.read_bytes()
    tampered = bytearray(original)
    tampered[-1] ^= 0x01
    path.write_bytes(bytes(tampered))
    with pytest.raises(StorageError):
        storage.read(info.storage_key)

    path.write_bytes(original)  # восстановили — читается снова
    assert storage.read(info.storage_key) == b"payload-1"

    wrong_key_storage = make_storage(tmp_path, key=K2)
    with pytest.raises(StorageError):
        wrong_key_storage.read(info.storage_key)


async def test_storage_path_traversal_rejected(tmp_path):
    storage = make_storage(tmp_path)
    with pytest.raises(StorageError):
        storage.read("../../etc/passwd")
    with pytest.raises(StorageError):
        storage.read("ZZZZ")


async def test_upload_validation(session, test_settings):
    storage = EncryptedFileStorage(
        test_settings.document_storage_path, test_settings.document_keyring()
    )
    service = DocumentService(session, test_settings, storage)

    with pytest.raises(OperationError):
        service.validate_upload(0, "application/pdf")
    with pytest.raises(OperationError):
        service.validate_upload(test_settings.max_document_bytes + 1, "application/pdf")
    with pytest.raises(OperationError):
        service.validate_upload(100, "application/x-executable")


async def test_stale_pending_documents_are_purged(session, test_settings):
    storage = EncryptedFileStorage(
        test_settings.document_storage_path, test_settings.document_keyring()
    )
    service = DocumentService(session, test_settings, storage)
    document = await service.stage_upload(
        data=b"old file", original_filename="old.pdf", mime_type="application/pdf"
    )

    purged = await service.purge_stale_pending(datetime.now(UTC) + timedelta(hours=1))
    assert purged == 1
    assert await DocumentRepository(session).get(document.id) is None
    assert not list(storage._staging().iterdir())
