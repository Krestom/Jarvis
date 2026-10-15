"""Сервис секретов: отдельное шифрование, значение не покидает сервис случайно."""

from __future__ import annotations

import base64
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.config.settings import Settings
from jarvis.repositories.secrets import SecretRepository
from jarvis.security.crypto import KeyRing, decrypt, encrypt
from jarvis.services.drafts import SecretCreateDraft, SecretUpdate
from jarvis.services.errors import NotFoundError, OperationError


class SecretService:
    """Значения секретов шифруются отдельным ключом и не попадают
    в LLM-контекст, поиск, логи и MCP."""

    def __init__(self, session: AsyncSession, settings: Settings, keyring: KeyRing | None = None):
        self._session = session
        self._settings = settings
        self._keyring = keyring or settings.secrets_keyring()
        self._repo = SecretRepository(session)

    def encrypt_value(self, value: str) -> tuple[str, int]:
        ring = self._keyring
        blob = encrypt(value.encode("utf-8"), ring)
        return base64.b64encode(blob).decode("ascii"), ring.active_version

    def decrypt_value(self, encrypted_value: bytes) -> str:
        try:
            return decrypt(encrypted_value, self._keyring).decode("utf-8")
        except Exception as error:  # noqa: BLE001 - детали крипто не показываем
            raise OperationError("Не удалось расшифровать секрет (проверьте ключ)") from error

    async def create_from_draft(self, draft: SecretCreateDraft) -> uuid.UUID:
        if await self._repo.find_by_name(draft.name) is not None:
            raise OperationError("Секрет с таким названием уже существует")
        blob = base64.b64decode(draft.encrypted_value_b64)
        secret = await self._repo.create(
            name=draft.name,
            purpose=draft.purpose,
            secret_type=draft.secret_type,
            encrypted_value=blob,
            key_version=draft.key_version,
            project_id=draft.project_id,
            category_id=draft.category_id,
        )
        return secret.id

    async def apply_update(self, secret_id: uuid.UUID, changes: dict) -> uuid.UUID:
        secret = await self._repo.get(secret_id)
        if secret is None:
            raise NotFoundError("Секрет не найден")
        update = SecretUpdate.model_validate(changes)
        fields = update.model_dump(exclude_unset=True)
        if "encrypted_value_b64" in fields:
            fields["encrypted_value"] = base64.b64decode(fields.pop("encrypted_value_b64"))
        await self._repo.update(secret, **fields)
        return secret.id

    async def list_metadata(self, *, limit: int = 10, offset: int = 0):
        return await self._repo.list_active(limit=limit, offset=offset)

    async def get(self, secret_id: uuid.UUID):
        return await self._repo.get(secret_id)

    async def reveal(self, secret_id: uuid.UUID) -> str:
        """Явно запрошенное получение значения (отдельный сценарий)."""
        secret = await self._repo.get(secret_id)
        if secret is None:
            raise NotFoundError("Секрет не найден")
        return self.decrypt_value(secret.encrypted_value)

    async def soft_delete(self, secret_id: uuid.UUID) -> uuid.UUID:
        secret = await self._repo.get(secret_id)
        if secret is None:
            raise NotFoundError("Секрет не найден")
        await self._repo.soft_delete(secret)
        return secret.id
