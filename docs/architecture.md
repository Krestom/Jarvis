# Архитектура Jarvis

## 1. Назначение и принципы

Jarvis — личный Telegram-first Knowledge OS: заметки, задачи, документы и секреты,
управляемые через Telegram-бота. Отдельные read-only потребители (OpenCode через MCP)
получают доступ к знаниям изолированным процессом.

Принципы, заложенные в код:

- **Telegram — единственный UI.** Все пользовательские сценарии идут через бота
  (`src/jarvis/bot/`). HTTP-сервер в приложении — только health-проверка
  (`src/jarvis/health.py`, `/healthz`), публичных ручек нет.
- **Backend проверяет всё, что предлагает LLM.** AI возвращает
  структурированный `Interpretation` (`src/jarvis/ai/schemas.py`), Pydantic
  валидирует вывод; решение о создании операции принимает backend, а не модель.
  Payload операции валидируется повторно в момент применения
  (`src/jarvis/services/operations.py`, `src/jarvis/services/drafts.py`).
- **Значимые изменения — только через явное подтверждение.** Создание/изменение/
  удаление сущностей идёт через таблицу `pending_operations`: движок предлагает
  операцию, пользователь подтверждает кнопкой, только затем она применяется
  (`src/jarvis/services/confirmation.py`).
- **Изоляция секретов и чувствительных данных.** Секреты хранятся в отдельной
  таблице и шифруются отдельным ключом; файлы документов лежат на диске только
  в зашифрованном виде; значения секретов и содержимое документов не попадают
  в AI, поиск и MCP.

## 2. Схема модулей

Зависимости слоёв (стрелка — «вызывает»):

```
                          Telegram Bot API (long polling)
                                     │
                                     ▼
┌──────────────────────────── bot/ ─────────────────────────────┐
│  app.py (Bot/Dispatcher, реестр команд, dp.errors → backlog)      │
│  middlewares.py: correlation id → dedup → allow-list → session│
│  handlers/: commands, diagnostics, menu, confirmations, tasks,│
│             knowledge, documents, secrets, search, voice,     │
│             free_text                                          │
│  render.py / keyboards.py / formatting.py / texts.py          │
└───────────────┬───────────────────────────────────────────────┘
                ▼
┌────── conversation/ ──────┐   ┌──── ai/ (адаптер) ────┐
│  engine.py (маршрутизация │──▶│  provider.py (Protocol)│
│    интентов, черновики)   │   │  prompts.py, schemas.py│
│  state.py, flows.py       │   │  opencode_go.py,       │
│  preview.py, dates.py,    │   │  none.py, fake.py      │
│  heuristics.py            │   └────────────────────────┘
│  heuristics.py            │   ┌ transcription/ (адаптер)┐
└───────────────┬───────────┘   │  provider.py, fake.py,  │
                │               │  openai_compatible.py   │
                │               └─────────────────────────┘
                ▼
┌────────────────── services/ ──────────────────┐
│  confirmation.py (pending-операции)           │
│  operations.py (apply подтверждённого)        │
│  tasks.py, knowledge.py, documents.py,        │
│  secrets.py, search.py, catalog.py, drafts.py │
│  improvements.py, improvement_export.py       │
└───────┬───────────────┬───────────────┬───────┘
        ▼               ▼               ▼
┌ repositories/ ┐ ┌ documents/ ┐ ┌ security/ ─────┐
│  по таблицам  │ │ storage.py │ │ crypto, auth,  │
│  (SQLAlchemy) │ │ (шифрование│ │ redaction      │
└───────┬───────┘ │   файлов)  │ └────────────────┘
        ▼         └────────────┘
┌──────────────── db/ ────────────────┐
│  base.py, models.py, session.py     │
└───────────────┬─────────────────────┘
                ▼
           PostgreSQL 17

Отдельный процесс (docker service mcp):
┌── mcp/ ───────────────────────────────┐
│  app.py (streamable HTTP), auth.py    │
│  (bearer), tools.py (read-only)       │
└───────────────────────────────────────┘
```

Правила слоёв:

- `bot/` не работает с ORM напрямую, кроме простых счётчиков в `/diagnostics`;
  бизнес-логика живёт в `services/`, доступ к данным — в `repositories/`.
- `conversation/` — это маршрутизация и черновики, а не доступ к БД: всё
  персистентное состояние идёт через репозитории.
- `ai/` и `transcription/` — внешние адаптеры за протоколами `AIProvider` и
  `TranscriptionProvider`. Выбор провайдера — только через конфигурацию
  (`AI_PROVIDER`, `TRANSCRIPTION_PROVIDER`); при `none` система работает в
  ограниченном режиме без AI. Транскрибация получает только аудио.
- `security/` используется всеми слоями: `auth.py` — middleware, `crypto.py` —
  хранилище и секреты, `redaction.py` — логирование.
- `mcp/` — отдельный процесс с доступом только на чтение; см. раздел 7.

## 3. Жизненный цикл запроса

1. **Update от Telegram** принимается long polling (`dispatcher.start_polling`
   в `src/jarvis/main.py`).
2. **Middleware-цепочка** (`src/jarvis/bot/middlewares.py`, порядок регистрации):
   - `CorrelationMiddleware` — новому update выдаётся correlation id для логов;
   - `UpdateDedupMiddleware` — повторная доставка `update_id` (таблица
     `processed_updates`) отбрасывается; отметка ставится **после** успешной
     обработки, поэтому повтор после сбоя безопасен;
   - `AccessMiddleware` — allow-list по Telegram user id; неавторизованные
     сообщения и нажатия кнопок молча игнорируются;
   - `DBSessionMiddleware` — на время обработки открывается сессия БД
     (`session_scope`): commit при успехе, rollback при ошибке.
3. **Handler** (роутеры из `src/jarvis/bot/handlers/__init__.py`): команды,
   кнопки меню, файлы, голос, и последним — `free_text` для произвольного текста.
4. **Conversation engine** (`src/jarvis/conversation/engine.py`):
   - сначала проверяется активный сценарий (`conversation_states`: поиск, уточнение,
     ожидание файла/значения секрета и т.д.);
   - эвристики (`heuristics.py`, `dates.py`) обрабатывают очевидные случаи
     («найди …», «завтра») без AI;
   - иначе текст уходит в `AIProvider.interpret` с минимальным контекстом
     (время, таймзона, имена категорий/проектов);
   - по `intent` формируется черновик (`services/drafts.py`) или уточняющий вопрос.
   Для ответов-подсказок (smalltalk/unknown) модель может вернуть типизированный
   `ui_request` (`quick_actions`): backend собирает кнопки строго из allow-list
   `UI_ACTION_BUTTONS` (`bot/keyboards.py`), callback'и формирует сервер —
   модель кнопок не рисует.
5. **Pending-операция.** Черновик сериализуется в `PendingOperation`
   (`ConfirmationService.propose`) с TTL (по умолчанию 30 минут), а пользователю
   уходит превью с кнопками «Применить / Изменить / Отменить»
   (`conversation/preview.py`, `bot/keyboards.py`).
6. **Confirm.** Нажатие «Применить» (`bot/handlers/confirmations.py`) вызывает
   `ConfirmationService.confirm`, который атомарно переводит операцию
   `pending → confirmed` (`UPDATE … WHERE status='pending'` в
   `repositories/pending.py`). Повторное нажатие ничего не применяет дважды —
   вернётся «уже применено»; просроченная операция переводится в `expired`.
7. **Apply.** `apply_operation` (`services/operations.py`) заново валидирует
   payload и выполняет изменение через сервисы; запись в `audit_log`, результат
   сохраняется в `pending_operations.result`.
8. **Commit.** `session_scope` коммитит транзакцию; при неожиданной ошибке —
   rollback, операция остаётся `pending` и может быть повторена безопасно.

Неожиданные исключения в обработке апдейта перехватывает `dp.errors`
(`bot/app.py`) и записывает в improvement backlog
(`ImprovementService.record_failure`): тип ошибки, traceback (redacted,
обрезан), correlation id, тип апдейта — без тел сообщений. Подробнее:
[docs/diagnostics.md](diagnostics.md).

Каждый пользователь имеет одно активное состояние диалога
(`conversation_states.telegram_user_id` — первичный ключ).

## 4. Модель данных

Таблицы из `src/jarvis/db/models.py` (одна Alembic-миграция:
`alembic/versions/8e374da5e984_initial_schema.py`). Enum-поля хранятся как VARCHAR
без CHECK-ограничений — значения валидируются на уровне Python.

| Таблица | Назначение | Ключевые поля |
|---|---|---|
| `categories` | Иерархия категорий | `name` (unique), `parent_id`, `sort_order` |
| `projects` | Проекты | `name` (unique), `status`, `category_id`, `meta` |
| `knowledge_items` | Заметки/знания | `type`, `title`, `summary`, `content`, `source_type`, `original_text`, `sensitivity`, `status`, `search_vector` (GIN, russian) |
| `tasks` | Задачи | `title`, `description`, `status`, `priority`, `bucket`, `due_at`, `completed_at`, `search_vector` (GIN, russian) |
| `task_comments` | Комментарии к задачам | `task_id` (FK, CASCADE), `content` |
| `documents` | Метаданные документов (содержимое — только на диске, зашифровано) | `title`, `document_type`, `sensitivity`, `status` (pending/active), `storage_key` (unique), `original_filename`, `mime_type`, `size_bytes`, `sha256`, `encryption_version` |
| `secrets` | Секреты (значение зашифровано) | `name` (unique), `secret_type`, `encrypted_value` (blob), `key_version`, `project_id`, `category_id` |
| `entity_links` | Полиморфные связи сущностей | `from_type/from_id`, `to_type/to_id`, `relation`, уникальность пары |
| `conversation_states` | Краткосрочный контекст сценария | PK `telegram_user_id`, `flow`, `step`, `data` (JSONB), `pending_operation_id`, `expires_at` |
| `pending_operations` | Предложенные значимые изменения | `telegram_user_id`, `operation_type`, `payload` (JSONB), `status`, `expires_at`, `resolved_at`, `result` |
| `audit_log` | Журнал значимых изменений (без чувствительного содержимого) | `actor`, `action`, `entity_type/id`, `result`, `meta`, `created_at` |
| `processed_updates` | Идемпотентность Telegram-апдейтов | PK `update_id`, `created_at` |
| `improvement_items` | Improvement backlog (Stage 2) | `category`, `source`, `status`, `confidence`, `occurrence_count`, `correlation_id`, `diagnostic_context` (JSONB), `merged_from` |
| `improvement_events` | Отдельные случаи/повторы записи backlog | `item_id` (FK, CASCADE), `occurred_at`, `correlation_id`, `note`, `diagnostic_context` |

Общие миксины (`db/base.py`): `TimestampMixin` (`created_at`, `updated_at`) и
`SoftDeleteMixin` (`deleted_at`). Удаление сущностей — мягкое, кроме жёсткого
удаления неподтверждённых документов при уборке (см. раздел 6).

## 5. Политика временных групп задач

Группа — это **намерение**, а не дата (см. `src/jarvis/domain/buckets.py`):

- `tasks.bucket` принимает `today`, `week`, `long_term`, `someday` или `NULL`
  («без группы»). Это то, что пользователь сказал, а не вычисленный календарь.
- `tasks.due_at` — отдельный опциональный tz-aware момент срока.
- «Сегодня» **никогда не сохраняется как дата**: списки «Сегодня» и
  «На этой неделе» вычисляются динамически по `APP_TIMEZONE` в момент запроса
  (`today_window`/`week_window`, `repositories/tasks.py`).

Правила членства (код `repositories/tasks.py`, `TaskService.list_view`):

- **today**: `bucket='today'` **или** `due_at < конец сегодняшнего дня`
  (включая просроченные);
- **week**: `due_at` попадает в интервал «завтра…воскресенье» **или**
  `bucket='week'` без попадания в «сегодня»;
- **long_term**: `bucket='long_term'` **или** `due_at` за пределами текущей недели;
- **someday**: `bucket='someday'`;
- **no_group**: без группы и без `due_at`; **inbox**: `status='inbox'`;
- **done**: `status='done'`.

Неделя — ISO (понедельник..воскресенье) в таймзоне пользователя. Задача,
созданная «на сегодня», завтра останется с `bucket='today'`, но её реальное
членство в списках будет определяться календарём и `due_at`. Человеческие
формулировки дат («завтра», «в пятницу») разбирает `conversation/dates.py`
до записи в `due_at`.

## 6. Жизненный цикл документа

1. **Приём.** `bot/handlers/documents.py` скачивает файл из Telegram и вызывает
   `DocumentService.validate_upload`: ненулевой размер, лимит
   `MAX_DOCUMENT_BYTES` (по умолчанию 50 МБ), MIME из белого списка префиксов
   (`image/`, `application/pdf`, `application/zip`, `application/msword`,
   `application/vnd.openxmlformats-officedocument`, `text/plain`,
   `application/octet-stream`).
2. **Staging.** `stage_upload` шифрует файл (AES-256-GCM) и кладёт его в
   `{DOCUMENT_STORAGE_PATH}/_staging/{storage_key}.enc`; в БД создаётся запись
   со `status='pending'`. `storage_key` — случайные 24 байта hex, не имя файла.
3. **Подтверждение.** Пользователь получает превью `SAVE_DOCUMENT` (название,
   тип, категория, чувствительность) с кнопками. «Применить» → `confirm_save`:
   файл перемещается (`promote`) в основное хранилище
   (`{root}/{первые 2 hex}/{key}.bin`), запись становится `active`.
   Повторное подтверждение идемпотентно: уже `active` документ возвращается как есть.
4. **Выдача.** `read_file` расшифровывает файл и сверяет SHA-256 с записанным
   при загрузке; при несовпадении/ошибке расшифровки — «Файл повреждён или
   недоступен». Файл отправляется пользователю через бота; публичной раздачи нет.
5. **Удаление.** `DELETE_DOCUMENT` после подтверждения: файл удаляется с диска,
   запись помечается `deleted_at` (мягкое удаление).
6. **Уборка сирот** (в `workers/cleanup.py`): неподтверждённые документы старше
   24 часов удаляются жёстко вместе с файлами (`purge_stale_pending`);
   файлы в `_staging`, на которые нет записей (сироты после сбоев), старше
   24 часов удаляются отдельно (`list_staging_files_older_than`).

## 7. MCP

MCP (`src/jarvis/mcp/`) — отдельный процесс (`python -m jarvis.mcp`, сервис
`mcp` в compose) с собственным ASGI-приложением поверх SDK `mcp` v2
(streamable HTTP). Политика — **read-only**:

- инструменты: `search_knowledge`, `search_instructions`, `get_knowledge_item`,
  `get_project_context`, `search_tasks`, `find_related`
  (`src/jarvis/mcp/tools.py`);
- каждый вызов проходит через те же репозитории и тот же фильтр
  чувствительности, что и Telegram-бот (`allowed_sensitivities` в
  `src/jarvis/services/knowledge.py`): по умолчанию отдаются только уровни
  `normal`, `MCP_INCLUDE_SENSITIVE=true` добавляет `sensitive`, уровень
  `secret` не отдаётся никогда;
- секреты, содержимое документов, файловая система, shell и SQL недоступны;
  инструменты не различают «не найдено» и «запрещено», чтобы не подтверждать
  существование закрытых записей;
- аутентификация — статический bearer-токен (`MCP_AUTH_TOKEN`) в ASGI-middleware
  (`src/jarvis/mcp/auth.py`); порт слушается только на `127.0.0.1`, внешний
  доступ — через ssh-туннель;
- вызовы инструментов пишутся в `audit_log` (актор `mcp`, без содержимого
  запросов);
- stdio-режим (`--stdio`) предназначен только для локальной разработки.


## 8. Фоновые задачи и TTL

В процессе приложения работает один фоновый воркер
(`src/jarvis/workers/cleanup.py`, цикл раз в 600 секунд) — процесс не должен
умирать от разовой ошибки (исключение логируется и цикл продолжается):

| Что убирается | Условие | Настройка |
|---|---|---|
| `conversation_states` | `expires_at < now` | `CONVERSATION_STATE_TTL_HOURS` (24 ч, проставляется при записи состояния) |
| `pending_operations` | `expires_at < now` → `expired`; удаление resolved/failed старше 7 дней | `PENDING_OPERATION_TTL_MINUTES` (30 мин); 7 дней — константа `RESOLVED_OPS_TTL_DAYS` |
| `processed_updates` | старше N часов | `PROCESSED_UPDATES_RETENTION_HOURS` (72 ч) |
| staged-документы (`pending` в БД и файлы) | старше 24 часов | константа `STAGED_PENDING_TTL_HOURS` |
| файлы-сироты в `_staging` | не попали под записи, старше 24 часов | то же |

Дополнительно: health-сервер (`/healthz`, проверка `SELECT 1`) поднимается в том
же процессе на `HEALTH_HOST:HEALTH_PORT`; при старте приложение ждёт БД
(15 попыток × 2 с) и засеивает стартовые категории (`CatalogService.ensure_defaults`).
