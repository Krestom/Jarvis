# Погода в утренней сводке: план изменений

Обновлено: 2026-11-13. Статус: реализовано (2026-11-13), тесты зелёные;
ждёт деплоя. Код — в рабочем дереве, не закоммичен.

## Решения

- Источник данных: **Open-Meteo** (без ключа), Москва `55.7558/37.6173`,
  `timezone=Europe/Moscow`.
- Оформление: **HTML-виджет Bot API 10.4**, статический (без JS и
  `widget_query` в первой версии).
- Погода: **первая сводка дня** (самое раннее время из `NOTIFY_DIGEST_TIMES`,
  по умолчанию 08:00) + команда `/weather` + экран агента.
- Задачи и их кнопки не меняем.
- `aiogram` остаётся: виджет отправляется кастомным `TelegramMethod`
  `SendWidget`; смена фреймворка не нужна (в PTB 22.8, Telethon 1.45 и
  Pyrogram 2.0.106 поддержки 10.4 нет).

## Наблюдение за aiogram

Виджеты Bot API 10.4 в aiogram 3.31 ещё не поддерживаются. Проверять:

- PR <https://github.com/aiogram/aiogram/pull/2004> (Bot API 10.4, 3.32.0);
- issue <https://github.com/aiogram/aiogram/issues/2006>.

Маркеры в репозитории:

1. `src/jarvis/bot/methods.py` — комментарий `# shortcut: aiogram 3.31 не умеет
   sendWidget; заменить на нативный Bot.send_widget, когда выйдет 3.32/PR #2004`.
2. `tests/unit/test_aiogram_widget_watch.py` — canary-тест: пока
   `hasattr(Bot, "send_widget")` ложно, тест проходит; когда aiogram добавит
   метод, тест упадёт с сообщением «удалить кастомный SendWidget».

   ```python
   def test_aiogram_has_no_native_widget_support_yet():
       from aiogram import Bot
       assert not hasattr(Bot, "send_widget"), (
           "aiogram добавил sendWidget — удалить кастомный SendWidget "
           "и обновить docs/telegram-widgets.md"
       )
   ```
3. `docs/status.md`, раздел «Открытые хвосты» — пункт про проверку aiogram.
4. `docs/telegram-widgets.md` — раздел «Что проверить при обновлении aiogram».

## Файлы

### Новые

| Файл | Роль |
|---|---|
| `src/jarvis/weather/__init__.py` | `create_weather_provider(settings)` |
| `src/jarvis/weather/provider.py` | `WeatherSnapshot`, `WeatherUnavailable`, протокол |
| `src/jarvis/weather/open_meteo.py` | запрос Open-Meteo, парсинг, единицы |
| `src/jarvis/weather/codes.py` | WMO-коды → русская подпись + ключ иконки |
| `src/jarvis/weather/none.py` | отключённый провайдер |
| `src/jarvis/bot/widgets/__init__.py` | пакет |
| `src/jarvis/bot/widgets/types.py` | `WidgetPayload(html, caption)` |
| `src/jarvis/bot/widgets/weather.py` | HTML/CSS-шаблон, рендер, проверка zlib ≤ 4096 |
| `src/jarvis/bot/methods.py` | `SendWidget(TelegramMethod[Message])` |
| `src/jarvis/bot/widgets/send.py` | `send_widget(bot, chat_id, payload)` + fallback |
| `tests/unit/test_weather_openmeteo.py` | провайдер, единицы, ошибки |
| `tests/unit/test_weather_codes.py` | WMO-коды |
| `tests/unit/test_weather_factory.py` | `none` / `open-meteo` / неизвестный |
| `tests/unit/test_weather_widget.py` | размер, экранирование, caption |
| `tests/unit/test_aiogram_widget_watch.py` | canary aiogram |

### Изменяемые

| Файл | Что |
|---|---|
| `src/jarvis/config/settings.py` | `WEATHER_*` поля |
| `.env.example` | секция погоды |
| `src/jarvis/workers/notifier.py` | `Outgoing.wants_weather`, погода в первой сводке |
| `src/jarvis/main.py` | `send_widget` + `weather_provider` в `notifier_loop` |
| `src/jarvis/bot/handlers/commands.py` | `/weather` |
| `src/jarvis/bot/app.py` | `BotCommand("weather", ...)` |
| `src/jarvis/conversation/engine.py` | `EngineOutcome.widget`, экран `weather` |
| `src/jarvis/bot/handlers/render.py` | отправка виджета в `send_outcome` |
| `src/jarvis/agent/orchestrator.py` | провайдер погоды, `screen: weather` |
| `src/jarvis/agent/tools.py` | инструмент `get_weather` |
| `src/jarvis/agent/prompts.py` | подсказка про экран и инструмент |
| `src/jarvis/ai/schemas.py` | `ScreenRequest.name` + `"weather"` |
| `tests/e2e/harness.py` | `FakeTelegramSession` обрабатывает `SendWidget` |
| `tests/integration/test_notifier.py` | сводка с виджетом и fallback |
| `tests/integration/test_agent_tools.py` | `get_weather` |
| `docs/status.md`, `docs/telegram-widgets.md` | статус и watch-list |

## Этапы

### 0. Провайдер Open-Meteo

Пакет `src/jarvis/weather/`: `provider.py` (модели + протокол),
`open_meteo.py` (HTTP), `codes.py` (WMO), `none.py` (отключено),
`__init__.py` (фабрика).

- `WeatherSnapshot`: current (температура, ощущается, влажность, осадки,
  код, ветер м/с, порывы, направление, давление, `is_day`), hourly (8
  ближайших часов), daily (2 дня: high/low, восход/закат, вероятность
  осадков), `fetched_at`.
- Запрос: `current=temperature_2m,apparent_temperature,relative_humidity_2m,
  is_day,precipitation,weather_code,pressure_msl,wind_speed_10m,
  wind_direction_10m,wind_gusts_10m`,
  `hourly=temperature_2m,precipitation_probability,precipitation,weather_code,
  wind_speed_10m`,
  `daily=weather_code,temperature_2m_max,temperature_2m_min,sunrise,sunset,
  precipitation_probability_max,wind_speed_10m_max`,
  `timezone=Europe/Moscow`, `forecast_days=2`, **`wind_speed_unit=ms`**
  (Open-Meteo сам отдаёт м/с; проверено — 200).
- Вероятность осадков есть только в hourly/daily, в `current` её нет.
- `WeatherUnavailable` на HTTP/parse/timeout; таймаут 5 с.
- `WEATHER_PROVIDER=none` отключает погоду, не ломая сводку.

### 1. Настройки

`WEATHER_PROVIDER=none` по умолчанию; в проде — `open-meteo`.
`WEATHER_LATITUDE=55.7558`, `WEATHER_LONGITUDE=37.6173`,
`WEATHER_CITY=Москва`, `WEATHER_TIMEZONE=Europe/Moscow`,
`WEATHER_TIMEOUT_SECONDS=5`. Валидатор часового пояса расширить на
`weather_timezone`.

### 2. Виджет и отправка

- Шаблон: градиент по условию/дню-ночи, крупная температура, условие,
  high/low, полоса 6 часов, детали (ощущается, ветер, влажность, осадки),
  восход/закат, inline SVG-иконки. Без JS.
- Плейсхолдеры через `string.Template` или `.replace()`; город и подписи
  экранировать.
- Проверка размера: `len(zlib.compress(html.encode(), 9)) <= 4096`;
  при превышении — упрощённый шаблон или текст.
- `SendWidget(TelegramMethod[Message])`:
  `chat_id`, `html`, `width`, `height`, `caption`, `parse_mode`,
  `reply_markup`, `allow_queries`, `chat_data`, `public_data`,
  `protect_content`; `__api_method__ = "sendWidget"`.
- `send_widget(bot, chat_id, payload)`: `await bot(SendWidget(...))`;
  при `TelegramBadRequest`/`TelegramAPIError` — лог и fallback на текст.
- Caption обязателен (fallback для старых клиентов), ≤ 1024 символов.

### 3. Утренняя сводка

- В `run_notifier_once` вычислить `morning = min(parse_digest_times(...))`.
- Для `digest_at == morning` и `hour < 12` пометить `Outgoing` как
  `wants_weather=True`; текст сводки остаётся fallback/caption.
- Погоду получать **после** транзакции БД, перед отправкой: не держать
  соединение на HTTP.
- Отправка: виджет + caption (текст задач, обрезать до 1024 с «… /today»).
- Ошибка погоды → обычный текстовый digest через существующий `send`.
- `run_notifier_once(..., send_widget=None)` — keyword-only; старые тесты
  не меняются, без `send_widget` погода не отправляется.

### 4. `/weather`

- `cmd_weather` в `commands.py`: провайдер → `render_weather_widget` →
  `send_widget`; при ошибке — текст «Погода сейчас недоступна».
- `BotCommand("weather", "Погода")` в `app.py`.
- Caption: «Погода в Москве · обновлено HH:MM».

### 5. Агент

- `ScreenRequest.name += "weather"`; `_validate_screen` → `("weather", {})`.
- `_render_screen("weather")`: получить snapshot, вернуть
  `EngineOutcome(text="", widget=WidgetPayload(...))`; при ошибке — текстовый
  ответ.
- `EngineOutcome.widget`; `send_outcome` отправляет виджет и fallback-текст.
- Инструмент `get_weather` (read-only) в `_TOOLS` и `ToolRegistry`:
  возвращает компактные данные для текстовых вопросов. Провайдер
  инжектится в `ToolRegistry`/`AgentService` (по аналогии с `search_provider`).
- В `agent/prompts.py`: «погода — экран `weather`; для текстового ответа —
  `get_weather`».
- Виджет строит backend по шаблону, **не LLM**; HTML из модели не принимается.

### 6. Тесты

- Unit: парсинг Open-Meteo (MockTransport), WMO-коды, 8 часовых точек,
  `wind_speed_unit=ms`, ошибки/таймаут, фабрика, zlib ≤ 4096,
  экранирование, caption ≤ 1024.
- Integration: первая сводка дня с виджетом; вторая — без; ошибка провайдера →
  текст; `/weather`; `get_weather`.
- E2E: `FakeTelegramSession` обрабатывает `SendWidget`; `/weather`; агентский
  запрос «какая погода» → экран `weather`.
- Canary: `test_aiogram_widget_watch.py`.

### 7. Доки и деплой

- `docs/status.md`: Open-Meteo, виджет, `/weather`, watch-list aiogram.
- `docs/telegram-widgets.md`: статус «используется для погоды» + ограничения.
- Деплой после зелёных тестов; smoke: `/weather`, следующая утренняя сводка,
  fallback при `WEATHER_PROVIDER=none`.

## Проверка

- `python -m pytest tests/unit tests/integration -q`
- `ruff check .`
- Вживую: Telegram Desktop + iPhone/Android; старый клиент должен показать
  caption, а не пустое сообщение.
- `WEATHER_PROVIDER=none` — поведение бота не меняется.

## Риски

- Виджеты — Bot API от 2026-11-09; клиентский rollout неполный, Web A может
  показать заглушку. Caption обязателен. Текущий прод-бот получает
  `INPUT_MEDIA_WIDGET_NOT_ALLOWED`, поэтому включён rich-message fallback.
- 4 КБ zlib — жёсткий лимит; шаблон держим простым, размер проверяем тестом.
- HTML неизменяем: обновление погоды — новое сообщение, не edit.
- Open-Meteo free: 10k/день, атрибуция CC BY 4.0; добавить строку в caption.
- Внешний HTTP в digest не должен удлинять транзакцию БД.

## Вне рамок

- Кнопки задач внутри виджета.
- `widget_query`, `allow_queries`, `editMessageWidgetData`.
- Rich messages.
- MCP-инструмент погоды (можно добавить позже, Open-Meteo без ключа).
