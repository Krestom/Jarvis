"""Ротация мастер-ключей шифрования.

Как работает ротация:
1. Сгенерируйте новый ключ: python scripts/init_key.py (взять только нужную строку).
2. В .env сервера: старый ключ перенесите в DOCUMENT_ENCRYPTION_OLD_KEYS
   ("<старая_версия>:<старый_ключ>"), новый поставьте в DOCUMENT_ENCRYPTION_KEY,
   увеличьте DOCUMENT_ENCRYPTION_KEY_VERSION.
3. Перезапустите сервисы — чтение продолжит работать (старый ключ доступен для
   расшифровки).
4. Запустите этот скрипт: сначала без --apply (показать план), затем с --apply
   (перешифровать файлы и секреты новым ключом).
5. После успешной проверки удалите старый ключ из DOCUMENT_ENCRYPTION_OLD_KEYS
   и перезапустите сервисы.

Скрипт никогда не печатает значения ключей. По умолчанию работает в режиме
просмотра (dry-run).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import asyncpg  # noqa: E402

from jarvis.config.settings import Settings  # noqa: E402
from jarvis.security.crypto import (  # noqa: E402
    KeyRing,
    blob_key_version,
    decrypt,
    encrypt,
)


def _dsn(settings: Settings) -> str:
    return settings.database_url.replace("postgresql+asyncpg://", "postgresql://")


async def _reencrypt_secrets(
    settings: Settings, ring: KeyRing, *, apply: bool
) -> tuple[int, int]:
    """Вернуть (всего секретов, перешифровано)."""
    connection = await asyncpg.connect(_dsn(settings))
    try:
        rows = await connection.fetch(
            "SELECT id, encrypted_value, key_version FROM secrets WHERE deleted_at IS NULL"
        )
        rotated = 0
        for row in rows:
            blob = bytes(row["encrypted_value"])
            version = blob_key_version(blob)
            if version == ring.active_version:
                continue
            plaintext = decrypt(blob, ring)
            new_blob = encrypt(plaintext, ring)
            rotated += 1
            if apply:
                await connection.execute(
                    "UPDATE secrets SET encrypted_value = $1, key_version = $2, "
                    "updated_at = now() WHERE id = $3",
                    new_blob,
                    ring.active_version,
                    row["id"],
                )
        return len(rows), rotated
    finally:
        await connection.close()


async def _reencrypt_files(
    settings: Settings, ring: KeyRing, *, apply: bool
) -> tuple[int, int]:
    """Вернуть (всего файлов, перешифровано)."""
    root = settings.document_storage_path
    if not root.exists():
        return 0, 0
    candidates = list(root.rglob("*.bin")) + list((root / "_staging").glob("*.enc"))
    rotated = 0
    for path in candidates:
        try:
            blob = path.read_bytes()
            version = blob_key_version(blob)
        except Exception:  # noqa: BLE001 - повреждённые файлы пропускаем с предупреждением
            print(f"  пропуск (не читается заголовок): {path.name}")
            continue
        if version == ring.active_version:
            continue
        plaintext = decrypt(blob, ring)
        rotated += 1
        if apply:
            tmp = path.with_suffix(".rotating")
            tmp.write_bytes(encrypt(plaintext, ring))
            tmp.replace(path)
            # контрольная проверка сразу после записи
            if decrypt(path.read_bytes(), ring) != plaintext:
                raise RuntimeError(f"проверка после перешифровки не прошла: {path.name}")
    return len(candidates), rotated


async def _update_document_versions(settings: Settings, ring: KeyRing) -> int:
    connection = await asyncpg.connect(_dsn(settings))
    try:
        result = await connection.execute(
            "UPDATE documents SET encryption_version = $1 "
            "WHERE encryption_version <> $1 AND deleted_at IS NULL",
            ring.active_version,
        )
        return int(result.split()[-1])
    finally:
        await connection.close()


async def amain() -> int:
    parser = argparse.ArgumentParser(description="Ротация мастер-ключей Jarvis")
    parser.add_argument("--apply", action="store_true", help="выполнить перешифрование")
    parser.add_argument("--secrets-only", action="store_true")
    parser.add_argument("--files-only", action="store_true")
    args = parser.parse_args()

    settings = Settings()
    ring = settings.document_keyring()
    secrets_ring = settings.secrets_keyring()

    print(
        f"Активная версия ключа: {ring.active_version}; "
        f"доступно версий для чтения: {sorted(ring.keys)}"
    )
    if len(ring.keys) < 2 and not args.files_only:
        print(
            "ВНИМАНИЕ: в DOCUMENT_ENCRYPTION_OLD_KEYS нет старых ключей — "
            "перешифровать можно только данные, доступные текущим набором ключей."
        )
    mode = "ПРИМЕНЕНИЕ" if args.apply else "просмотр (dry-run, добавьте --apply)"
    print(f"Режим: {mode}\n")

    if not args.secrets_only:
        total, rotated = await _reencrypt_files(settings, ring, apply=args.apply)
        print(f"Файлы: {total} найдено, к перешифровке: {rotated}")
        if args.apply and rotated:
            updated = await _update_document_versions(settings, ring)
            print(f"Записей documents с обновлённой версией: {updated}")

    if not args.files_only:
        total, rotated = await _reencrypt_secrets(settings, secrets_ring, apply=args.apply)
        print(f"Секреты: {total} найдено, к перешифровке: {rotated}")

    print("\nГотово." if args.apply else "\nЭто был просмотр — данные не изменялись.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(amain()))
