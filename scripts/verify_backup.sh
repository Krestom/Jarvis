#!/usr/bin/env bash
# Проверка целостности резервной копии Jarvis: состав артефактов + SHA256SUMS.
#
# Использование: scripts/verify_backup.sh <каталог-копии>
# Пример:        scripts/verify_backup.sh backups/jarvis-backup-20260101-030000
set -euo pipefail

die() { echo "ОШИБКА: $*" >&2; exit 1; }

if [ $# -ne 1 ]; then
  echo "Использование: $0 <каталог-копии>" >&2
  echo "Пример: $0 backups/jarvis-backup-20260101-030000" >&2
  exit 2
fi

DIR="$1"
[ -d "$DIR" ] || die "каталог не найден: $DIR"
DIR="$(cd "$DIR" && pwd)"

for f in pg.dump files.tar.gz env.sanitized SHA256SUMS manifest.txt; do
  [ -f "$DIR/$f" ] || die "отсутствует файл: $f"
  [ -s "$DIR/$f" ] || die "файл пуст: $f"
done

command -v sha256sum >/dev/null 2>&1 || die "нужен sha256sum (coreutils)"

echo "Проверка SHA256SUMS в $DIR"
( cd "$DIR" && sha256sum -c SHA256SUMS )

[ "$(head -c 5 "$DIR/pg.dump")" = "PGDMP" ] || die "pg.dump не похож на custom-формат pg_dump (-Fc)"

leaks="$(grep -E '^[[:space:]]*#?[[:space:]]*[A-Za-z0-9_]*(KEY|TOKEN|SECRET|PASSWORD|DATABASE_URL)[A-Za-z0-9_]*=' \
  "$DIR/env.sanitized" | grep -vc '=<removed>$' || true)"
[ "$leaks" = "0" ] || die "в env.sanitized есть незамаскированные секреты"

echo "OK: копия $DIR целостна (состав полный, контрольные суммы совпадают)"
