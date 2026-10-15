"""Шифрованное файловое хранилище.

Файлы на диске всегда зашифрованы (AES-256-GCM), путь строится из случайного
storage_key, а не из имени файла. Каталог хранилища изолирован от кода.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import secrets as secrets_module
import shutil
from dataclasses import dataclass
from pathlib import Path

from jarvis.security.crypto import KeyRing, decrypt, encrypt

STAGING_DIR_NAME = "_staging"


class StorageError(Exception):
    """Ошибка хранилища (повреждение, отсутствие файла, неверный ключ)."""


@dataclass(frozen=True)
class StoredFileInfo:
    storage_key: str
    size_bytes: int
    sha256: str
    key_version: int


class EncryptedFileStorage:
    def __init__(self, root: Path, keyring: KeyRing) -> None:
        self._root = root
        self._keyring = keyring

    @property
    def root(self) -> Path:
        return self._root

    @property
    def key_version(self) -> int:
        return self._keyring.active_version

    def ensure_dirs(self) -> None:
        import contextlib

        self._root.mkdir(parents=True, exist_ok=True)
        # Каталог хранилища не должен быть доступен другим локальным пользователям.
        with contextlib.suppress(OSError):
            # Windows и смонтированные volumes могут не поддерживать chmod.
            os.chmod(self._root, 0o700)
        self._staging().mkdir(exist_ok=True)

    def _staging(self) -> Path:
        return self._root / STAGING_DIR_NAME

    def _path_for(self, storage_key: str) -> Path:
        if not storage_key or len(storage_key) < 3 or "/" in storage_key or "\\" in storage_key:
            raise StorageError("invalid storage key")
        # Проверка на path traversal: ключи генерируем только мы (hex).
        if any(char not in "0123456789abcdef" for char in storage_key):
            raise StorageError("invalid storage key charset")
        return self._root / storage_key[:2] / f"{storage_key}.bin"

    @staticmethod
    def new_storage_key() -> str:
        return secrets_module.token_hex(24)

    def save(self, data: bytes, *, staging: bool = False) -> StoredFileInfo:
        """Зашифровать и атомарно записать данные. staging=True — временная зона."""
        self.ensure_dirs()
        storage_key = self.new_storage_key()
        blob = encrypt(data, self._keyring)
        if staging:
            target = self._staging() / f"{storage_key}.enc"
        else:
            target = self._path_for(storage_key)
            target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_bytes(blob)
        os.replace(tmp, target)
        return StoredFileInfo(
            storage_key=storage_key,
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            key_version=self._keyring.active_version,
        )

    def read(self, storage_key: str) -> bytes:
        path = self._path_for(storage_key)
        if not path.exists():
            staging_path = self._staging() / f"{storage_key}.enc"
            if staging_path.exists():
                path = staging_path
            else:
                raise StorageError("file not found")
        try:
            data = decrypt(path.read_bytes(), self._keyring)
        except Exception as exc:  # noqa: BLE001 - все ошибки крипто наследуются от CryptoError
            raise StorageError("failed to decrypt file") from exc
        return data

    def verify(self, storage_key: str, expected_sha256: str) -> bytes:
        data = self.read(storage_key)
        if hashlib.sha256(data).hexdigest() != expected_sha256:
            raise StorageError("integrity check failed: sha256 mismatch")
        return data

    def promote(self, storage_key: str) -> None:
        """Перевести staged-файл в основное хранилище (после подтверждения)."""
        staging_path = self._staging() / f"{storage_key}.enc"
        if not staging_path.exists():
            return  # уже в основном хранилище
        target = self._path_for(storage_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staging_path), str(target))

    def delete(self, storage_key: str) -> None:
        for path in (
            self._path_for(storage_key),
            self._staging() / f"{storage_key}.enc",
        ):
            with contextlib.suppress(OSError):
                path.unlink(missing_ok=True)

    def list_staging_files_older_than(self, moment) -> list[Path]:
        if not self._staging().exists():
            return []
        cutoff = moment.timestamp()
        return [
            path
            for path in self._staging().iterdir()
            if path.is_file() and path.stat().st_mtime < cutoff
        ]
