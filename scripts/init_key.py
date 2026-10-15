"""Генерация мастер-ключей шифрования для .env.

Запуск из корня репозитория:
    python scripts/init_key.py

Скрипт только печатает значения — ничего не записывает на диск.
Сохраните их в .env на сервере (chmod 600 .env) и не коммитьте.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.security.crypto import generate_key  # noqa: E402


def main() -> None:
    print("# Скопируйте в .env (сохраните обе копии в надёжном месте):")
    print(f"DOCUMENT_ENCRYPTION_KEY={generate_key()}")
    print("DOCUMENT_ENCRYPTION_KEY_VERSION=1")
    print()
    print("# Отдельный ключ для секретов (без него используется DOCUMENT_ENCRYPTION_KEY):")
    print(f"SECRETS_ENCRYPTION_KEY={generate_key()}")


if __name__ == "__main__":
    main()
