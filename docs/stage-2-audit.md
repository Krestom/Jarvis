# Stage 2 — аудит текущего состояния Jarvis

Дата аудита: 2026-10-15. Источник: фактический код, миграции, тесты и живой запуск
(бот @Jarvis_tsygan_bot с реальным OpenCode Go и PostgreSQL в Docker).

## 1. Что уже реализовано и проверено

### Ядро (работает в проде локально)

| Компонент | Состояние |
|---|---|
| Telegram-бот (aiogram 3.31, polling) | ✅ живой запуск, allow-list, меню, inline-кнопки |
| Задачи: группы today/week/long_term/someday/no_group/inbox/done | ✅ динамические списки (`domain/buckets.py`), проверено в живом диалоге |
| Подтверждение изменений (`pending_operations`) | ✅ идемпотентно, TTL, аудит |
| Знания + FTS (русская морфология) | ✅ |
| Комментарии к задачам | ✅ добавлено в этой итерации (карточка + естественный язык + выбор задачи) |
| Проекты | ✅ создание вручную, авто-создание при ссылке из knowledge/task (`project_name`) |
| Документы | ✅ staging → AES-256-GCM → подтверждение → promote → выдача |
| Секреты | ✅ отдельная таблица + ключ, автоудаление показа, запрет в MCP |
| Force-save секретов | ✅ кнопка «Всё равно сохранить» → knowledge с sensitivity=sensitive |
| AI (OpenCode Go, `/chat/completions`) | ✅ живой API: structured output, ретраи, fallback без response_format; интенты create_task/add_comment/list_comments/list_tasks/create_knowledge/create_project/search/document_upload |
| MCP (mcp SDK v2, read-only, bearer) | ✅ 6 инструментов, sensitivity-фильтр, 401/200 проверены на живом контейнере |
| Docker Compose (app/mcp/postgres) | ✅ все healthy, миграции автоматом, изоляция |
| Бэкапы/восстановление | ✅ скрипты + self-test на изолированном compose-проекте |
| Тесты | ✅ 146 passed (unit + integration на реальной БД + 14 e2e через fake-Telegram) |
| Git | ✅ https://github.com/Krestom/Jarvis, main, push актуален |

### Инфраструктурные факты (важно для продолжения)

- Локальный запуск: PostgreSQL в Docker (`docker compose up -d postgres`, порт 127.0.0.1:5433), приложение процессом `python -m jarvis.main` (Windows-машина, VPN → `BOT_PROXY=http://127.0.0.1:10809` в `.env`).
- Прод-цель: Hetzner (Германия) — VPN/прокси там НЕ нужен (`BOT_PROXY` пусто).
- Реальные секреты только в `.env` (gitignored). Тестовые ключи пользователь удалит.
- Живой бот перезапускается так: `Get-CimInstance Win32_Process | ? { $_.CommandLine -match 'jarvis\.main' } | % { Stop-Process -Id $_.ProcessId -Force }`, затем `python -m jarvis.main`.

## 2. Исправленные дефекты (эта итерация, commit 8ae2b08)

1. «Какие у меня задачи на неделю» → «ничего не нашёл». Причина: не было интента
   `list_tasks`; AI пытался искать задачам по FTS. Исправлено: интент + рендер списка.
2. «Какие комментарии к задаче X» → «ничего не нашёл». Причина: комментарии не
   индексируются FTS и не имели интента. Исправлено: `list_comments` + выбор задачи кнопками.
3. «Сохрани креды в проект SuperSport» → проект отбрасывался. Исправлено:
   `project_name` в черновиках knowledge/task, auto-create при применении,
   строка «Проект: X (будет создан)» в превью; force-save сохраняет проект из контекста.

## 3. Чего нет (Stage 2 backlog — запланировано)

| # | Возможность | Состояние |
|---|---|---|
| 1 | **Improvement Backlog**: таблица, сервис, категории failure/missing_capability/ux_friction/improvement_idea, статусы, дедуп, экспорт MD/JSON | ❌ отсутствует полностью |
| 2 | Захват подтверждённых технических ошибок (exception handler aiogram → backlog) | ❌ |
| 3 | Гипотезы LLM с порогом достоверности (не засорять backlog) | ❌ |
| 4 | `/diagnostics` со сводкой, пагинацией, карточками, управлением статусами | ❌ |
| 5 | `/export_issues` (MD + JSON, фильтры, без секретов, лимиты) | ❌ |
| 6 | Диагностический контекст ошибок: correlation id (есть), traceback (есть в логах), сохранение в backlog (❌), буфер при недоступной БД (❌) |
| 7 | Типизированный каталог UI-компонентов для LLM-управляемых сценариев (Этап 5) | ⚠️ частично: сценарии/степперы реализованы кодово (flow states), но нет декларативной схемы «кнопки от LLM» |
| 8 | `AGENTS.md` в репозитории Jarvis с рабочим циклом backlog | ❌ (есть только глобальный у пользователя) |
| 9 | Документация Stage 2 (diagnostics, export, backlog) | ❌ |

## 4. Риски и ограничения совместимости

- Dynamic UI (Этап 5) нельзя реализовывать как исполнение произвольного UI от LLM:
  только типизированный каталог + серверная валидация + привязка callback к сценарию.
- Backlog пишется в ту же PostgreSQL (миграция Alembic), не в новый сервис.
- При недоступной БД запись backlog невозможна; допустим ограниченный локальный
  буфер (файл с ротацией, дедуп, повторная доставка), либо документированное
  ограничение — решить при реализации (Этап 4.2).
- Диагностика не должна содержать секретов: использовать существующий
  `security/redaction.py`, хранить ссылки (correlation_id), не тела сообщений.
- Существующие callback-паттерны (типизированные CallbackData + проверка
  пользователя/статуса) переиспользовать для `/diagnostics`, не изобретать новые.
- Прогон миграций Stage 2 на живой локальной БД допустим; на VPS — только после
  отдельного разрешения пользователя.

## 5. План внедрения (приоритеты)

1. **P1**: миграция + модель `ImprovementItem` (+ история повторов/дедуп),
   репозиторий, сервис (создание из технических ошибок и из гипотез с порогом),
   аудит-записи. Захват исключений: обработчик ошибок aiogram (`dp.errors`) →
   failure-запись + correlation_id + traceback(redacted).
2. **P2**: `/diagnostics` (сводка, категории/статусы, пагинация, карточка,
   статусные действия, архивирование, удаление с подтверждением) и `/export_issues`
   (Markdown + JSON, фильтры, безопасные имена файлов, лимит размера).
3. **P3**: `AGENTS.md` (цикл backlog), `docs/diagnostics.md`, обновление README.
4. **P4**: типизированный каталог UI-компонентов (Этап 5) — схема `UIRequest`
   с allow-list компонентов и валидацией; интеграция в engine точечно.
5. **P5**: локальный буфер диагностики при недоступной БД (если останется время
   и это безопасно) либо документированное ограничение.

Каждый пункт завершается тестами (unit/integration/e2e) и не ломает существующие
146 тестов.

## 6. Состояние на момент компакта (2026-10-15, вечер)

- Коммиты: `4169989` — hotfix комментариев (callback_data > 64 байт ломал кнопку
  «Изменить комментарий»; теперь кнопка несёт только id комментария, задача
  восстанавливается из строки БД; добавлена e2e-регрессия), до него `d8fe593`
  (аудит), `8ae2b08` (комментарии/force-save/проекты/list-интенты).
- Тесты: 146 passed, ruff чист, всё запушено в `main`.
- Бот остановлен по просьбе пользователя (`jarvis.main` не запущен); PostgreSQL
  в Docker работает (порт 127.0.0.1:5433).
- Живой контур: реальные ключи в `.env` (gitignored); AI проверен живьём;
  MCP контейнер поднят через compose; на VPS ещё не разворачивалось.

## 7. Журнал выполнения Stage 2

### P1 — Improvement Backlog: выполнен (2026-10-15)

- Модели `ImprovementItem` + `ImprovementEvent` (`db/models.py`), enum'ы
  (`domain/enums.py`), миграция `19d82c1a6e02` (применена к локальной БД).
- `repositories/improvements.py`: создание, поиск по сигнатуре (дедуп открытых),
  `register_occurrence` (событие + occurrence_count), фильтры/пагинация,
  статусы, counts, hard delete, merge (перенос событий, `merged_from`,
  архивирование источника с `meta.merged_into`).
- `services/improvements.py`: `record_failure` (redacted diagnostic_context,
  обрезка по `IMPROVEMENT_CONTEXT_MAX_CHARS`), `record_hypothesis` (порог
  `IMPROVEMENT_HYPOTHESIS_MIN_CONFIDENCE`, по умолчанию 0.75), `merge`.
- LLM: `ImprovementHypothesis` в `ai/schemas.py`, правило в `ai/prompts.py`;
  хук `ConversationEngine._capture_hypothesis` после interpret (и в clarify),
  ошибки записи не роняют диалог.
- Бот: `dp.errors` (`bot/app.py`) пишет failure-запись с correlation_id,
  traceback (redacted), update_type/update_id — без тела переписки.
- Тесты: +10 (integration: дедуп/порог/redaction/merge/статусы; unit: схема;
  e2e: гипотеза через fake AI, сбой через dp.errors). Всего 156 passed.
- События получают `occurred_at` из Python (в одной транзакции `now()` БД
  одинаков, иначе теряется порядок).

### P2 — /diagnostics + /export_issues: выполнен (2026-10-15)

- `bot/handlers/diagnostics.py`: `/diagnostics` (и алиас `/status`) — системная
  сводка + счётчики backlog; список с фильтрами по статусу/категории и
  пагинацией; карточка записи; смена статуса; архивирование; удаление с
  подтверждением. Кнопки `ImpListCB`/`ImpCB`.
- `/export_issues`: Markdown + JSON в `EXPORT_DIR` (по умолчанию `var/exports`),
  фильтры `status=...`/`category=...`, лимит 500 записей и 4000 символов на
  поле, повторная маскировка секретов, безопасные имена файлов; файлы уходят
  в чат документами для ручной передачи в OpenCode.
- Квирк aiogram: `CallbackData.pack()` запрещает символ «:» в значениях —
  контекст возврата кодируется как `kind|value` (иначе ValueError).
- Тесты: +6 (unit: парсинг фильтров; integration: рендер/маскировка/фильтры;
  e2e: полный цикл diagnostics и экспорта). Всего 160 passed.
- Харнесс e2e: fake-сессия теперь обрабатывает `EditMessageText` (frozen-модели
  через `model_copy`) и `SendDocument`; `harness.current(message)` отдаёт
  актуальную версию сообщения.



