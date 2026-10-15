"""Аутентифицированное шифрование для файлового хранилища и секретов.

Формат блоба: magic(4) | format_version(1) | key_version(4, BE) | nonce(12)
| ciphertext||tag. Весь заголовок используется как AAD, поэтому подмена
версии ключа ломает расшифровку.
"""

from __future__ import annotations

import base64
import binascii
import os
import struct
from collections.abc import Mapping
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"JVSF"
FORMAT_VERSION = 1
NONCE_SIZE = 12
KEY_SIZE = 32
GCM_TAG_SIZE = 16

_HEADER_PREFIX = struct.Struct(">4sBI")
_HEADER_SIZE = _HEADER_PREFIX.size + NONCE_SIZE


class CryptoError(Exception):
    """Базовый класс ошибок шифрования."""


class FormatError(CryptoError):
    """Блоб повреждён или имеет неизвестный формат."""


class UnknownKeyVersion(CryptoError):
    """Для блоба нет подходящего ключа в keyring."""


class DecryptionError(CryptoError):
    """Неверный ключ или повреждённый ciphertext."""


def generate_key() -> str:
    """Сгенерировать новый мастер-ключ (url-safe base64, 32 байта)."""
    return base64.urlsafe_b64encode(os.urandom(KEY_SIZE)).decode("ascii")


def decode_key(value: str, name: str = "key") -> bytes:
    """Декодировать base64-ключ и проверить длину (AES-256)."""
    try:
        raw = base64.b64decode(value, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"{name} is not valid base64") from exc
    if len(raw) != KEY_SIZE:
        raise ValueError(f"{name} must decode to {KEY_SIZE} bytes, got {len(raw)}")
    return raw


def blob_key_version(blob: bytes) -> int:
    """Версия ключа из заголовка блоба (для ротации ключей)."""
    if len(blob) < _HEADER_SIZE:
        raise FormatError("blob is too short")
    magic, format_version, key_version = _HEADER_PREFIX.unpack_from(blob)
    if magic != MAGIC:
        raise FormatError("bad magic bytes")
    if format_version != FORMAT_VERSION:
        raise FormatError(f"unsupported format version {format_version}")
    return key_version


def keyring_from_config(
    active_key: str,
    active_version: int,
    old_keys: str = "",
) -> KeyRing:
    """Собрать KeyRing из env-значений.

    old_keys — строка вида "1:<base64>,2:<base64>" (только для расшифровки
    после ротации ключа).
    """
    if not active_key:
        raise ValueError("active encryption key is not configured")
    if active_version < 1:
        raise ValueError("key version must be >= 1")
    keys: dict[int, bytes] = {active_version: decode_key(active_key, "active key")}
    for item in old_keys.split(","):
        item = item.strip()
        if not item:
            continue
        version_text, _, key_text = item.partition(":")
        if not version_text.strip().isdigit() or not key_text.strip():
            raise ValueError(f"invalid old key entry: {item!r}")
        version = int(version_text)
        if version in keys:
            raise ValueError(f"duplicate key version {version}")
        keys[version] = decode_key(key_text.strip(), f"old key v{version}")
    return KeyRing(keys=keys, active_version=active_version)


@dataclass(frozen=True)
class KeyRing:
    """Набор ключей по версиям; active_version используется для шифрования."""

    keys: Mapping[int, bytes]
    active_version: int

    def __post_init__(self) -> None:
        if self.active_version not in self.keys:
            raise ValueError(f"active key version {self.active_version} is missing from keyring")
        for version, key in self.keys.items():
            if len(key) != KEY_SIZE:
                raise ValueError(f"key v{version} must be {KEY_SIZE} bytes")

    def key_for(self, version: int) -> bytes:
        try:
            return self.keys[version]
        except KeyError as exc:
            raise UnknownKeyVersion(f"no key for version {version}") from exc


def encrypt(plaintext: bytes, ring: KeyRing) -> bytes:
    """Зашифровать данные активным ключом keyring."""
    nonce = os.urandom(NONCE_SIZE)
    header = _HEADER_PREFIX.pack(MAGIC, FORMAT_VERSION, ring.active_version) + nonce
    ciphertext = AESGCM(ring.key_for(ring.active_version)).encrypt(nonce, plaintext, header)
    return header + ciphertext


def decrypt(blob: bytes, ring: KeyRing) -> bytes:
    """Расшифровать блоб, проверив заголовок и целостность (GCM tag)."""
    if len(blob) < _HEADER_SIZE + GCM_TAG_SIZE:
        raise FormatError("blob is too short")
    magic, format_version, key_version = _HEADER_PREFIX.unpack_from(blob)
    if magic != MAGIC:
        raise FormatError("bad magic bytes")
    if format_version != FORMAT_VERSION:
        raise FormatError(f"unsupported format version {format_version}")
    header = blob[:_HEADER_SIZE]
    nonce = blob[_HEADER_PREFIX.size : _HEADER_SIZE]
    ciphertext = blob[_HEADER_SIZE:]
    key = ring.key_for(key_version)  # UnknownKeyVersion, если ключа нет
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, header)
    except InvalidTag as exc:
        raise DecryptionError("decryption failed: wrong key or corrupted data") from exc
