"""Интеграционные тесты сервиса секретов."""

from __future__ import annotations

import base64

import pytest

from jarvis.config.settings import Settings
from jarvis.db.models import Secret
from jarvis.repositories.secrets import SecretRepository
from jarvis.services.drafts import SecretCreateDraft
from jarvis.services.errors import OperationError
from jarvis.services.secrets import SecretService

pytestmark = pytest.mark.integration


async def test_secret_create_reveal_and_duplicate_name(session, test_settings):
    service = SecretService(session, test_settings)
    encrypted_b64, key_version = service.encrypt_value("token-12345")

    secret_id = await service.create_from_draft(
        SecretCreateDraft(
            name="API сервиса",
            purpose="Для вызовов API",
            secret_type="api_token",
            encrypted_value_b64=encrypted_b64,
            key_version=key_version,
        )
    )
    assert await service.reveal(secret_id) == "token-12345"

    with pytest.raises(OperationError, match="уже существует"):
        await service.create_from_draft(
            SecretCreateDraft(
                name="API сервиса",
                secret_type="api_token",
                encrypted_value_b64=encrypted_b64,
                key_version=key_version,
            )
        )

    listed = await service.list_metadata()
    assert [secret.name for secret in listed] == ["API сервиса"]


async def test_secret_encrypted_at_rest(session, test_settings):
    service = SecretService(session, test_settings)
    encrypted_b64, key_version = service.encrypt_value("plain-text-value")
    secret_id = await service.create_from_draft(
        SecretCreateDraft(
            name="Пароль",
            secret_type="password",
            encrypted_value_b64=encrypted_b64,
            key_version=key_version,
        )
    )

    stored = await SecretRepository(session).get(secret_id)
    assert stored is not None
    assert b"plain-text-value" not in stored.encrypted_value


async def test_secret_delete_removes_row(session, test_settings):
    """Удаление секрета необратимо: строка стирается из БД (не soft delete)."""
    service = SecretService(session, test_settings)
    encrypted_b64, key_version = service.encrypt_value("to-delete")
    secret_id = await service.create_from_draft(
        SecretCreateDraft(
            name="Удаляемый",
            secret_type="other",
            encrypted_value_b64=encrypted_b64,
            key_version=key_version,
        )
    )

    await service.delete(secret_id)

    assert await session.get(Secret, secret_id) is None  # строки нет даже без фильтра
    assert await service.list_metadata() == []

    with pytest.raises(OperationError, match="не найден"):
        await service.delete(secret_id)


async def test_secret_with_wrong_key_cannot_be_revealed(session, tmp_path):
    good_settings = Settings(
        _env_file=None,
        app_env="dev",
        document_encryption_key=base64.urlsafe_b64encode(K1()).decode(),
        secrets_encryption_key=base64.urlsafe_b64encode(K1()).decode(),
        document_storage_path=tmp_path / "docs",
    )
    bad_settings = Settings(
        _env_file=None,
        app_env="dev",
        document_encryption_key=base64.urlsafe_b64encode(K2()).decode(),
        secrets_encryption_key=base64.urlsafe_b64encode(K2()).decode(),
        document_storage_path=tmp_path / "docs",
    )

    service = SecretService(session, good_settings)
    encrypted_b64, key_version = service.encrypt_value("sensitive")
    await service.create_from_draft(
        SecretCreateDraft(
            name="Ключ с чужим ключом",
            secret_type="other",
            encrypted_value_b64=encrypted_b64,
            key_version=key_version,
        )
    )

    bad_service = SecretService(session, bad_settings)
    with pytest.raises(OperationError, match="расшифровать"):
        await bad_service.reveal((await bad_service.list_metadata())[0].id)


def K1() -> bytes:
    return bytes(range(32))


def K2() -> bytes:
    return bytes(range(32, 64))
