#!/usr/bin/env bash
# Восстановление резервной копии Jarvis в ОТДЕЛЬНОЕ ТЕСТОВОЕ окружение.
# Основной compose-проект "jarvis" не трогает (из .env только читает параметры БД).
#
# Использование:
#   scripts/restore.sh <каталог-копии> [--project-name <имя>]
#
# По умолчанию имя тестового окружения: jarvis-restore-test.
# Создаются: контейнер <имя>-postgres, тома <имя>_pgdata и <имя>_files, сеть <имя>-net.
# Секреты (пароли, ключи, токены) в вывод не печатаются.
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

PROJECT_NAME="jarvis-restore-test"
BACKUP_PATH=""

die() { echo "ОШИБКА: $*" >&2; exit 1; }

usage() {
  echo "Использование: $0 <каталог-копии> [--project-name <имя>]" >&2
  echo "  <каталог-копии>  — например backups/jarvis-backup-20260101-030000" >&2
  echo "  --project-name   — имя тестового окружения (по умолчанию: jarvis-restore-test)" >&2
}

while [ $# -gt 0 ]; do
  case "$1" in
    --project-name)
      [ $# -ge 2 ] || die "--project-name требует значение"
      PROJECT_NAME="$2"
      shift 2
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    -*)
      usage
      die "неизвестный аргумент: $1"
      ;;
    *)
      [ -z "$BACKUP_PATH" ] || die "укажите ровно один каталог-копии"
      BACKUP_PATH="$1"
      shift
      ;;
  esac
done

[ -n "$BACKUP_PATH" ] || { usage; exit 2; }
[ -d "$BACKUP_PATH" ] || die "каталог не найден: $BACKUP_PATH"
BACKUP_PATH="$(cd "$BACKUP_PATH" && pwd)"

[ "$PROJECT_NAME" != "jarvis" ] || die "нельзя восстанавливать в основной проект: задайте тестовое --project-name"
case "$PROJECT_NAME" in *[!A-Za-z0-9_.-]*) die "недопустимое имя проекта: $PROJECT_NAME" ;; esac

command -v docker >/dev/null 2>&1 || die "docker не найден в PATH"

# --- Параметры БД: из окружения, иначе из .env (значения наружу не печатаем) ---
env_get() {
  # Значение переменной из .env; пустая строка, если переменной нет.
  { grep -E "^[[:space:]]*$1=" "$ROOT_DIR/.env" 2>/dev/null || true; } \
    | tail -n1 | cut -d= -f2- | tr -d '\r' | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/"
}

POSTGRES_USER="${POSTGRES_USER:-$(env_get POSTGRES_USER)}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-$(env_get POSTGRES_PASSWORD)}"
POSTGRES_DB="${POSTGRES_DB:-$(env_get POSTGRES_DB)}"
[ -n "$POSTGRES_USER" ] || die "POSTGRES_USER не задан (нет .env и переменной окружения)"
[ -n "$POSTGRES_PASSWORD" ] || die "POSTGRES_PASSWORD не задан (нет .env и переменной окружения)"
[ -n "$POSTGRES_DB" ] || die "POSTGRES_DB не задан (нет .env и переменной окружения)"
# Значения передаются контейнеру по имени переменной (-e NAME), чтобы не светиться в списке процессов.
export POSTGRES_USER POSTGRES_PASSWORD POSTGRES_DB

PG_CONTAINER="${PROJECT_NAME}-postgres"
PG_VOLUME="${PROJECT_NAME}_pgdata"
FILES_VOLUME="${PROJECT_NAME}_files"
NETWORK="${PROJECT_NAME}-net"

echo "Jarvis: восстановление $BACKUP_PATH -> тестовое окружение '$PROJECT_NAME'"

# --- 0. Копия цела, тестовое имя свободно ---
if docker ps -a --format '{{.Names}}' | grep -qx "$PG_CONTAINER"; then
  die "контейнер $PG_CONTAINER уже существует: удалите его (docker rm -f $PG_CONTAINER) или задайте другое --project-name"
fi
if docker volume ls --format '{{.Name}}' | grep -qxE "($PG_VOLUME|$FILES_VOLUME)"; then
  die "тома $PG_VOLUME/$FILES_VOLUME уже существуют: удалите их (docker volume rm ...) или задайте другое --project-name"
fi
if docker network ls --format '{{.Name}}' | grep -qx "$NETWORK"; then
  die "сеть $NETWORK уже существует: удалите её (docker network rm $NETWORK) или задайте другое --project-name"
fi

bash "$SCRIPT_DIR/verify_backup.sh" "$BACKUP_PATH"

# --- 1. Тома и сеть ---
echo "[1/4] Создание томов и сети..."
docker volume create "$PG_VOLUME" >/dev/null
docker volume create "$FILES_VOLUME" >/dev/null
docker network create "$NETWORK" >/dev/null

# --- 2. Файловое хранилище ---
echo "[2/4] Распаковка files.tar.gz в том $FILES_VOLUME..."
docker run --rm \
  -v "$FILES_VOLUME":/data \
  -v "$BACKUP_PATH":/backup:ro \
  alpine tar -xzf /backup/files.tar.gz -C /data

# --- 3. PostgreSQL ---
echo "[3/4] Запуск $PG_CONTAINER (postgres:17-alpine)..."
docker run -d --name "$PG_CONTAINER" --network "$NETWORK" \
  -e POSTGRES_USER -e POSTGRES_PASSWORD -e POSTGRES_DB \
  -v "$PG_VOLUME":/var/lib/postgresql/data \
  postgres:17-alpine >/dev/null

printf '      Ожидание готовности PostgreSQL'
ready=0
for _ in $(seq 1 60); do
  # -h 127.0.0.1: ждём рабочий сервер, а не временный, который слушает только сокет при initdb.
  if docker exec "$PG_CONTAINER" pg_isready -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1; then
    ready=1
    break
  fi
  printf '.'
  sleep 2
done
echo
[ "$ready" = "1" ] || die "PostgreSQL не готов за 120 секунд, смотрите: docker logs $PG_CONTAINER"

# --- 4. Дамп ---
echo "[4/4] Восстановление дампа (pg_restore --no-owner)..."
if ! docker exec -i "$PG_CONTAINER" pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner \
  < "$BACKUP_PATH/pg.dump"; then
  die "pg_restore завершился с ошибкой (вывод выше). Окружение оставлено для разбора: docker logs $PG_CONTAINER"
fi

echo
echo "Готово: тестовое окружение '$PROJECT_NAME' восстановлено (копия: $BACKUP_PATH)"
echo "  PostgreSQL: $PG_CONTAINER, пользователь=$POSTGRES_USER, база=$POSTGRES_DB"
echo "  файлы: том $FILES_VOLUME; сеть: $NETWORK"
echo
echo "Следующий шаг — поднять app против тестовой БД (секреты не печатаем, значения берите из .env):"
echo "  1) сделайте тестовый env-файл (копия .env), замените в нём DATABASE_URL на:"
printf '     postgresql+asyncpg://$POSTGRES_USER:$POSTGRES_PASSWORD@%s:5432/$POSTGRES_DB\n' "$PG_CONTAINER"
echo "     при необходимости отключите Telegram (TELEGRAM_BOT_TOKEN=) и внешние AI-провайдеры."
echo "  2) запустите app (нужен собранный образ jarvis-app:local; миграции применятся при старте):"
printf '     docker run --rm --name %s-app --network %s -p 127.0.0.1:8081:8080 \\\n' "$PROJECT_NAME" "$NETWORK"
echo '       --env-file <тестовый .env> -e DOCUMENT_STORAGE_PATH=/data/documents -e HEALTH_HOST=0.0.0.0 \'
printf '       -v %s:/data/documents jarvis-app:local\n' "$FILES_VOLUME"
echo "  3) проверьте health и один документ — подробности в docs/backup-restore.md."
echo "  4) после проверки удалите тестовое окружение, например:"
printf '     docker rm -f %s && docker volume rm %s %s && docker network rm %s\n' \
  "$PG_CONTAINER" "$PG_VOLUME" "$FILES_VOLUME" "$NETWORK"
