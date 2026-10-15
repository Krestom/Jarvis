#!/usr/bin/env bash
# Резервная копия Jarvis: дамп PostgreSQL, архив тома файлов, обеззараженный .env,
# контрольные суммы и манифест.
#
# Запуск: scripts/backup.sh   (из любой директории)
# Переменные окружения:
#   BACKUP_DIR            — каталог для копий (по умолчанию ./backups в корне репозитория)
#   BACKUP_RETENTION_DAYS — сколько дней хранить копии (по умолчанию 14)
#
# Секреты (DOCUMENT_ENCRYPTION_KEY и прочие) в копию НЕ попадают:
# env.sanitized собирается с маскировкой, ключи храните отдельно от копий.
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

# Имена совпадают с compose.yaml: проект "jarvis", том файлов jarvis-files.
COMPOSE_PROJECT="jarvis"
FILES_VOLUME_KEY="jarvis-files"

BACKUP_DIR="${BACKUP_DIR:-$ROOT_DIR/backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"

die() { echo "ОШИБКА: $*" >&2; exit 1; }

case "$RETENTION_DAYS" in
  '' | *[!0-9]*) die "BACKUP_RETENTION_DAYS должен быть целым числом (сейчас: $RETENTION_DAYS)" ;;
esac

command -v docker >/dev/null 2>&1 || die "docker не найден в PATH"
docker compose version >/dev/null 2>&1 || die "нужен docker compose v2"
command -v sha256sum >/dev/null 2>&1 || die "нужен sha256sum (coreutils)"

mkdir -p "$BACKUP_DIR"
# Абсолютный путь: он уходит в docker -v, а docker не понимает относительные.
BACKUP_DIR="$(cd "$BACKUP_DIR" && pwd)"

STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$BACKUP_DIR/jarvis-backup-$STAMP"
mkdir -p "$OUT"

echo "Jarvis: создаю резервную копию -> $OUT"

# --- 1/6. PostgreSQL ---
echo "[1/6] Дамп PostgreSQL (pg_dump -Fc)..."
if [ -z "$(docker compose ps -q postgres)" ]; then
  die "контейнер postgres не запущен. Сначала: docker compose up -d"
fi
# POSTGRES_USER/POSTGRES_DB берём из окружения самого контейнера,
# чтобы не зависеть от того, экспортирован ли .env в текущей оболочке.
PG_USER="$(docker compose exec -T postgres printenv POSTGRES_USER 2>/dev/null | tr -d '\r' || true)"
PG_DB="$(docker compose exec -T postgres printenv POSTGRES_DB 2>/dev/null | tr -d '\r' || true)"
[ -n "$PG_USER" ] && [ -n "$PG_DB" ] || die "не удалось прочитать POSTGRES_USER/POSTGRES_DB из контейнера postgres"
# Без -f: -Fc пишет дамп в stdout (в PG 17 «-f -» создаёт файл с именем «-», а stdout остаётся пустым).
if ! docker compose exec -T postgres pg_dump -U "$PG_USER" -d "$PG_DB" -Fc > "$OUT/pg.dump"; then
  die "pg_dump завершился с ошибкой (вывод выше) — смотрите: docker compose logs postgres"
fi
[ -s "$OUT/pg.dump" ] || die "pg.dump пуст — смотрите: docker compose logs postgres"
[ "$(head -c 5 "$OUT/pg.dump")" = "PGDMP" ] || die "pg.dump не похож на custom-формат pg_dump (-Fc)"

# --- 2/6. Том файлов ---
echo "[2/6] Архив тома $FILES_VOLUME_KEY (шифрованные файлы)..."
FILES_VOLUME="$(docker volume ls -q \
  --filter "label=com.docker.compose.project=$COMPOSE_PROJECT" \
  --filter "label=com.docker.compose.volume=$FILES_VOLUME_KEY" | head -n1 || true)"
if [ -z "$FILES_VOLUME" ] && docker volume inspect "${COMPOSE_PROJECT}_${FILES_VOLUME_KEY}" >/dev/null 2>&1; then
  FILES_VOLUME="${COMPOSE_PROJECT}_${FILES_VOLUME_KEY}"
fi
[ -n "$FILES_VOLUME" ] || die "том $FILES_VOLUME_KEY не найден: проект ещё не запускался? (docker compose up -d)"
echo "      том: $FILES_VOLUME"
if ! docker run --rm \
  -v "$FILES_VOLUME":/data:ro \
  -v "$OUT":/backup \
  alpine tar -czf /backup/files.tar.gz -C /data .; then
  die "не удалось заархивировать том $FILES_VOLUME"
fi
[ -s "$OUT/files.tar.gz" ] || die "files.tar.gz пуст"

# --- 3/6. .env без секретов ---
echo "[3/6] Копия .env с маскировкой секретов -> env.sanitized"
if [ -f "$ROOT_DIR/.env" ]; then
  sed -E 's/^([[:space:]]*#?[[:space:]]*[A-Za-z0-9_]*(KEY|TOKEN|SECRET|PASSWORD|DATABASE_URL)[A-Za-z0-9_]*)=.*$/\1=<removed>/' \
    "$ROOT_DIR/.env" > "$OUT/env.sanitized"
  # Страховка: незамаскированных секретов в копии быть не должно.
  leaks="$(grep -E '^[[:space:]]*#?[[:space:]]*[A-Za-z0-9_]*(KEY|TOKEN|SECRET|PASSWORD|DATABASE_URL)[A-Za-z0-9_]*=' \
    "$OUT/env.sanitized" | grep -vc '=<removed>$' || true)"
  [ "$leaks" = "0" ] || die "не удалось полностью замаскировать секреты в .env — смотрите $OUT/env.sanitized"
else
  echo "      ПРЕДУПРЕЖДЕНИЕ: .env не найден, кладу заглушку"
  printf '# .env не найден на момент бэкапа (%s)\n' "$STAMP" > "$OUT/env.sanitized"
fi

# --- 4/6. Манифест ---
echo "[4/6] Манифест -> manifest.txt"
JARVIS_VERSION="$(git -C "$ROOT_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)"
{
  echo "# Jarvis backup manifest"
  echo "дата: $(date -Iseconds)"
  echo "хост: $(hostname)"
  echo "версия jarvis (git): $JARVIS_VERSION"
  echo "compose-проект: $COMPOSE_PROJECT"
  echo "состав:"
  for f in pg.dump files.tar.gz env.sanitized; do
    printf '  %-16s %s\n' "$f" "$(du -h "$OUT/$f" | cut -f1)"
  done
  echo "примечания:"
  echo "  - env.sanitized: значения переменных с KEY/TOKEN/SECRET/PASSWORD/DATABASE_URL заменены на <removed>."
  echo "  - секреты в копию не попадают; для расшифровки файлов нужен DOCUMENT_ENCRYPTION_KEY (храните его отдельно)."
} > "$OUT/manifest.txt"

# --- 5/6. Контрольные суммы ---
echo "[5/6] Контрольные суммы -> SHA256SUMS"
( cd "$OUT" && sha256sum pg.dump files.tar.gz env.sanitized manifest.txt > SHA256SUMS )

# --- 6/6. Ротация ---
echo "[6/6] Удаление копий старше $RETENTION_DAYS дн. (по маске jarvis-backup-*)"
find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -name 'jarvis-backup-*' \
  -mtime +"$RETENTION_DAYS" -print -exec rm -rf {} +

echo
echo "Готово: $OUT"
echo "Проверка целостности: scripts/verify_backup.sh '$OUT'"
echo
echo "ВАЖНО: без .env с DOCUMENT_ENCRYPTION_KEY зашифрованные файлы (files.tar.gz) не восстановить."
echo "Копию ключей храните отдельно от резервных копий: в этот каталог они не попадают."
