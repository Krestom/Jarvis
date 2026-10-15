# Развёртывание Jarvis

## 1. Требования

- VPS с Linux, Docker Engine и плагином Docker Compose (`docker compose version`).
- Свободные порты на хосте (все — только на `127.0.0.1`, наружу не публикуются):
  - `5433` — PostgreSQL (`POSTGRES_PORT`, для отладки через ssh-туннель);
  - `8765` — MCP (`MCP_PORT`, доступ через ssh-туннель).
- Health-порт `8080` слушается **внутри** контейнера `app` (`HEALTH_HOST=0.0.0.0`)
  и наружу не публикуется — конфликтов с другими приложениями нет.

Compose-проект назван `jarvis` (`compose.yaml`, `name: jarvis`): свои контейнеры
(`jarvis-postgres`, `jarvis-app`, `jarvis-mcp`), свои тома (`jarvis-pgdata`,
`jarvis-files`) и своя сеть `jarvis-net`. Конфликтов с другими compose-проектами
на одном хосте нет; общий у проектов только хост-порт, если его занять — см.
раздел 8.

## 2. Подготовка

1. Клонировать репозиторий и перейти в него:

   ```sh
   git clone <URL_РЕПОЗИТОРИЯ> jarvis
   cd jarvis
   ```

2. Создать `.env` из шаблона:

   ```sh
   cp .env.example .env
   chmod 600 .env
   ```

3. Сгенерировать мастер-ключи (скрипт печатает значения, ничего не записывая):

   ```sh
   python scripts/init_key.py
   ```

   Нужен Python 3.12+ и пакет `cryptography` (например,
   `python -m venv .venv && . .venv/bin/activate && pip install cryptography`).
   Скопировать в `.env` строки `DOCUMENT_ENCRYPTION_KEY`,
   `DOCUMENT_ENCRYPTION_KEY_VERSION` и `SECRETS_ENCRYPTION_KEY`.
   **Сохранить копию ключей в надёжном месте** — без них зашифрованные
   документы и секреты не восстанавливаются.

4. Заполнить `.env`:

   | Переменная | Значение |
   |---|---|
   | `APP_ENV` | `prod` на сервере (в prod обязательны токен, allow-list и ключ шифрования: при заданном токене приложение завершается с ошибкой, без токена бот просто не запускается) |
   | `POSTGRES_PASSWORD` | свой пароль (обязателен: без него compose не поднимется) |
   | `TELEGRAM_BOT_TOKEN` | токен от @BotFather |
   | `TELEGRAM_ALLOWED_USER_IDS` | свой Telegram user id, через запятую (или JSON-списком) |
   | `DOCUMENT_ENCRYPTION_KEY`, `SECRETS_ENCRYPTION_KEY` | из шага 3 |
   | `AI_PROVIDER` | `none` — работа без AI; `fake` — детерминированный режим для проверки; строки настройки внешних провайдеров — см. таблицу переменных в `.env.example` |
   | `TRANSCRIPTION_PROVIDER` | `none` (по умолчанию) или `openai_compatible` + `TRANSCRIPTION_BASE_URL`, `TRANSCRIPTION_API_KEY`, `TRANSCRIPTION_MODEL` |
   | `MCP_AUTH_TOKEN` | случайный токен для MCP-доступа (нужен, если используется MCP) |
   | `APP_TIMEZONE` | таймзона для «сегодня»/«на этой неделе», по умолчанию `Europe/Moscow` |

   Свой user id проще всего узнать, написав @userinfobot в Telegram: он вернёт
   числовой id. Это и есть значение для allow-list. Пользователи, которых нет в
   списке, для бота не существуют — он молча игнорирует их сообщения и кнопки.

## 3. Первый запуск

```sh
docker compose up -d --build
```

Что происходит:

1. Собирается образ `jarvis-app:local` (`Dockerfile`: python:3.12-slim,
   установка пакета из `pyproject.toml`, непривилегированный пользователь
   `jarvis`, каталог `/data/documents`).
2. `postgres` (postgres:17-alpine) поднимается с healthcheck `pg_isready`;
   данные — в томе `jarvis-pgdata`.
3. `app` стартует только после `postgres` (condition: service_healthy).
   Entrypoint (`docker/entrypoint.sh`) применяет миграции: `alembic upgrade head`.
   Далее приложение ждёт доступную БД, создаёт стартовые категории, поднимает
   `/healthz` на `0.0.0.0:8080` внутри контейнера и запускает Telegram-поллинг и
   фоновую уборку.
4. `mcp` стартует после `app` с `SKIP_MIGRATIONS=1` — миграции применяет только
   `app`. MCP слушает `127.0.0.1:8765` (endpoint `/mcp`), требует
   `MCP_AUTH_TOKEN` и работает только на чтение; подробности — в `docs/mcp.md`.

Проверка:

```sh
docker compose ps            # postgres (healthy), app (healthy), ...
docker compose logs -f app   # старт, миграции, "jarvis is running"
```

и команда `/status` в боте — она покажет доступность БД, выбранные провайдеры,
таймзону и наличие ключа шифрования.

## 4. Обновление

```sh
git pull
docker compose build
docker compose up -d
```

Миграции применяются на старте `app` (entrypoint вызывает `alembic upgrade head`).
Для сервиса `mcp` миграции не выполняются (`SKIP_MIGRATIONS=1`).

Откат:

```sh
git checkout <предыдущий-коммит-или-тег>
docker compose up -d --build
```

Если новая версия применила миграцию, после отката кода схема БД может быть
новее кода. Варианты: `docker compose exec app alembic downgrade -1` (осторожно:
откат миграций удаляет таблицы — сначала сделайте бэкап) или восстановление из
резервной копии (см. `docs/backup-restore.md`).

## 5. Логи и диагностика

- Логи приложения (структурированные, с correlation id и маскированием секретов):

  ```sh
  docker compose logs -f --tail=200 app
  ```

- `/status` в боте: БД, AI-провайдер, транскрибация, таймзона, ключ шифрования,
  количество сохранённых сущностей.
- Health-эндпоинт (внутри контейнера; снаружи не публикуется):

  ```sh
  docker compose exec app python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=5).read().decode())"
  ```

  Ответ: `{"status": "ok", "version": ..., "components": {"db": true}}`;
  при недоступной БД — HTTP 503, Docker healthcheck помечает контейнер unhealthy.

### Локальная разработка из-под VPN/прокси (важно)

Если машина выходит в сеть через VPN/прокси (типично для РФ):

- **Telegram**: aiohttp не использует системный прокси Windows автоматически —
  укажите `BOT_PROXY` в `.env` (`http://127.0.0.1:10809` или
  `socks5://127.0.0.1:10809` — зависит от вашего клиента). В проде на VPS
  `BOT_PROXY` пусто.
- **AI/транскрибация (httpx)**: при локальном запуске `python -m jarvis.main`
  достаточно системного прокси Windows (httpx читает его сам); скрипт проверки
  MCP намеренно игнорирует прокси (localhost-туннель).
- **Docker на Windows**: контейнеры не видят системный прокси. Если AI/STT из
  контейнера недоступны — либо запускайте приложение локально
  (`python -m jarvis.main` при Postgres из compose), либо задайте
  `HTTP_PROXY`/`HTTPS_PROXY` в `.env` с адресом
  `http://host.docker.internal:10809` (см. `.env.example`).

## 6. MCP-доступ (для OpenCode и других клиентов)

MCP-сервис слушает `127.0.0.1:8765` на сервере (в compose) и наружу не виден.
Доступ — через SSH-туннель, токен — в заголовке (см. `MCP_AUTH_TOKEN` в `.env`).

1. Туннель с локальной машины:

   ```sh
   ssh -N -L 8765:127.0.0.1:8765 user@SERVER
   ```

   Пока туннель открыт, локальный `127.0.0.1:8765` указывает на MCP сервера.

2. Токен положить в локальную переменную окружения (не в конфиг-файл):

   ```sh
   export JARVIS_MCP_TOKEN=<MCP_AUTH_TOKEN_из_.env>
   ```

3. Конфигурация OpenCode (V2, фрагмент `opencode.jsonc`):

   ```jsonc
   {
     "$schema": "https://opencode.ai/config.json",
     "mcp": {
       "servers": {
         "jarvis": {
           "type": "remote",
           "url": "http://127.0.0.1:8765/mcp",
           "oauth": false,
           "headers": {
             "Authorization": "Bearer {env:JARVIS_MCP_TOKEN}"
           }
         }
       }
     }
   }
   ```

   Серверы по умолчанию подключаются с OAuth, поэтому для статического токена
   явно указано `oauth: false`. Полный пример и описание инструментов —
   в `docs/mcp.md` и `examples/opencode.mcp.jsonc`.

4. Проверка: `opencode mcp list` должен показать linked `connected`.
   Быстрая проверка самого MCP (до OpenCode): `python scripts/check_mcp.py` —
   без токена ожидается `401`, с токеном из `.env` — `200` и имя сервера.
   MCP — read-only доступ; `MCP_INCLUDE_SENSITIVE=false` по умолчанию закрывает
   чувствительные данные, секреты не отдаются никогда.

## 7. Бэкапы

Резервное копирование и восстановление (дамп PostgreSQL, том с зашифрованными
файлами, .env с ключами) описаны в `docs/backup-restore.md`. Ключевое правило:
**бэкап `.env` и бэкап данных должны храниться раздельно** — иначе шифрование
файлов теряет смысл.

## 8. Типичные проблемы

| Симптом | Причина и решение |
|---|---|
| `docker compose up` падает: `set POSTGRES_PASSWORD in .env` | Не заполнен `POSTGRES_PASSWORD` в `.env` |
| Порт занят (`bind: address already in use`) | Порт `5433` (или `8765`) занят другим сервисом: `ss -ltnp \| grep -E '5433\|8765'`, смените `POSTGRES_PORT`/`MCP_PORT` в `.env` |
| Бот не отвечает | Проверьте allow-list: `TELEGRAM_ALLOWED_USER_IDS` пуст или ваш id не в списке — бот молча игнорирует чужих. Также проверьте `TELEGRAM_BOT_TOKEN` и логи: `telegram bot is disabled` означает, что бот не запущен |
| Приложение не стартует в prod (`refusing to start`) | В логах `config problem: ...` — в prod обязательны `TELEGRAM_BOT_TOKEN`, непустой allow-list и `DOCUMENT_ENCRYPTION_KEY` |
| Миграции не прошли | Смотрите `docker compose logs app`: ошибка подключения (БД ещё не готова — перезапустите `docker compose up -d`) или SQL. Примените вручную: `docker compose exec app alembic upgrade head`; не выставляйте `SKIP_MIGRATIONS=1` сервису `app` |
| «Файл повреждён или недоступен», «Не удалось расшифровать секрет» | Зашифровано другим ключом: потерян/подменён `DOCUMENT_ENCRYPTION_KEY` либо в `DOCUMENT_ENCRYPTION_OLD_KEYS` нет версии ключа этого блоба. Ключ из файлов не восстанавливается — нужен правильный `.env` или бэкап с ключом |
| Контейнер `mcp` перезапускается | Не задан `MCP_AUTH_TOKEN` (сервис не стартует без него) или занят `MCP_PORT`: проверьте `docker compose logs mcp` |
