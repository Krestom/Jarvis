#!/bin/sh
set -eu

# Миграции применяет сервис app; остальные сервисы стартуют с SKIP_MIGRATIONS=1.
if [ "${SKIP_MIGRATIONS:-0}" != "1" ]; then
  echo "jarvis: applying database migrations"
  alembic upgrade head
fi

exec "$@"
