# Telegram HTML-виджеты (Bot API 10.4)

Обновлено: 2026-11-12. Статус: механизм используется для карточки погоды
(2026-11-13); остальные сценарии — исследование.

Документ описывает механизм Telegram-виджетов: что это, как устроено,
какие у них ограничения, где они работают в продакшене и что важно учесть
при интеграции в Jarvis. Источники — в конце.

## 1. Коротко

Виджет — это **отдельный тип сообщения**, внутри которого Telegram рисует
небольшой HTML-документ с inline CSS и JavaScript. Это не `parse_mode="HTML"`
и не Mini App.

- HTML **неизменяем** после отправки: новое расположение — новое сообщение.
- Меняется только **состояние**, и только ботом: `chat_data` и `public_data`.
- JavaScript работает в **QuickJS-песочнице без сети**.
- В продакшене (на 12.11.2026) виджеты работают **только в личке с ботом**.
- Старые клиенты показывают **caption** вместо виджета, поэтому caption нужен всегда.

Не путать:

| Что | Чем является |
|---|---|
| `parse_mode="HTML"` | разметка обычного текста сообщения |
| Telegram HTML-виджет | HTML-документ, отрисованный внутри пузыря |
| Mini App | WebView с сетью, открывается по кнопке/ссылке |
| Telegram Login/Post widgets | виджеты для сайтов, не для сообщений |

`TelegramMessenger/widget-sandbox` — это runtime клиентов и спецификация
профиля, **не SDK для бота**. Для бота полезны `docs/`, `corpus/bot-demos`,
`scripts/bot-demos.ts` и harness `sandbox-node.ts`.

## 2. Как устроен механизм

```text
Бот
 └─ sendWidget(html, chat_data, public_data, allow_queries)
      ↓
Telegram хранит HTML и состояние в сообщении
      ↓
Клиент (iOS/Android/Desktop/Web)
 └─ QuickJS-песочница
      ├─ DOM shim
      ├─ validator (allowlist профиля)
      └─ renderer (рисует только валидные операции)
      ↓
Пользователь нажимает кнопку
 └─ TelegramWidget.request("add")
      ↓
Бот получает update.widget_query
 ├─ editMessageWidgetData(...)  — меняет состояние
 └─ answerWidgetQuery(...)      — отвечает виджету за 15 секунд
```

Ключевая идея: **виджет — это представление, а не база данных**.
Авторитетное состояние должно жить на стороне бота; `widget_query`
не приносит состояние с собой.

### 2.1. HTML и песочница

- Один самодостаточный HTML-документ (или фрагмент) с inline `<style>` и `<script>`.
- Внешние скрипты, стили, шрифты и картинки не загружаются.
- Картинки — только `data:` PNG/JPEG/GIF/WebP (до 1024×1024), либо inline SVG.
- `fetch`, `XMLHttpRequest`, `WebSocket`, `EventSource` и т.п. выбрасывают
  `NetworkDisabledError`.
- Навигация запрещена; `window.open` идёт через `openLink`.
- Нет клавиатуры и фокуса; `input`, `select`, `textarea` удаляются.
- Нет скролла внутри виджета: лишнее обрезается по высоте пузыря.
- Вертикальные drag’ы всегда забирает чат; горизонтальные drag’ы и удержания
  виджет может забрать через CSS `touch-action`.

### 2.2. Профиль виджета

Профиль — контракт между виджетом и клиентами. Сейчас это **profile 1**.
Всё, чего нет в профиле, клиент молча удаляет: остальной виджет продолжает
работать. Поэтому «в браузере выглядит нормально» ничего не гарантирует.

Что входит (кратко):

- HTML: текст, заголовки, списки, таблицы, `details/summary`, `img` с `data:`,
  `canvas`, `button`, inline SVG.
- CSS: flex, grid, таблицы, позиционирование, рамки, радиусы, тени, градиенты,
  2D-трансформации, `opacity`, `filter`/`backdrop-filter` с `blur()`,
  `brightness()`, `drop-shadow()`, transitions, `@keyframes`, custom properties,
  `calc/min/max/clamp`, `:hover` (только мышь), `:active`, `::before/::after`,
  `prefers-color-scheme`, `prefers-reduced-motion`, `@supports`.
- JS: ES2023, Promises, таймеры, `requestAnimationFrame`, DOM, canvas 2D,
  Web Animations, `Intl`, `crypto`, `TextEncoder`, `URL`, `structuredClone`,
  `localStorage`/`sessionStorage`, `eval`/`new Function` внутри песочницы.
- Шрифты: только generic-семейства (`system-ui`, `sans-serif`, `serif`,
  `monospace`, `ui-*`).

Чего нет:

- `input`, `select`, `textarea`, `form`-контролы, `contenteditable`;
- клавиатура, фокус, `:focus`, key events;
- `audio`, `video`, `iframe`, `object`, `embed`;
- внешние `script src`, `@font-face`, `@import`;
- `vh`/`dvh`/`svh`, `position: sticky`, `overflow: auto/scroll`;
- 3D-трансформации, `clip-path`, маски, blend modes, `text-shadow`;
- `oklch()`, `color-mix()`, `lab()`, `lch()`;
- `:has()`, `@container`, `@layer`, CSS nesting;
- `getImageData`, `toDataURL`, WebGL, OffscreenCanvas;
- `navigator.clipboard`, `share`, `vibrate`, геолокация, уведомления;
- `alert`, `confirm`, `prompt`, `print`;
- `TelegramWidget.sendMessage` (удалён).

Проверять поддержку можно через `TelegramWidget.profile`,
`TelegramWidget.isProfileAtLeast(n)`, `CSS.supports()` и `@supports`.

## 3. Состояние

Виджет = фиксированный код + изменяемое состояние. Пишет состояние **только бот**.

| Состояние | Где видно | Пересылка | Лимит |
|---|---|---|---|
| `chat_data` | только в этом сообщении/чате; в группе — всем участникам | никогда не переносится | 1024 байта UTF-8 |
| `public_data` | везде, где показан виджет | копия на момент пересылки; у shared-виджета — общее текущее | 1024 байта UTF-8 |

- Оба поля — просто текст, часто JSON. Сервер его не интерпретирует.
- 1024 байта — это примерно 512 кириллических букв.
- Пустая строка очищает состояние; виджет видит `null`, не `""`.
- Изменения приходят виджету событиями `chatDataChanged` /
  `publicDataChanged`; ничего не перезапускается.
- Изменения состояния **тихие**: без пометки «edited» и без уведомления.

### 3.1. Static и shared

| | Static | Shared |
|---|---|---|
| Создание | `sendWidget` без `is_shared` | первый `sendWidget` с `is_shared=True` |
| HTML, размер, флаг запросов | в сообщении | в объекте, одинаковые для всех сообщений |
| `public_data` | в сообщении; пересылка получает копию | одно на все сообщения и пересылки |
| Идентификатор | нет | `widget.id`, он же `widget_id` |
| Отправить ещё раз | пересылкой (в проде пока нельзя) | `sendSharedWidget(widget_id, chat_data)` |
| Изменить общее состояние | только у оригинала | `editSharedWidgetData(widget_id, public_data, cache_time)` |

Shared-виджет — как опрос: объект живёт дольше сообщений, его можно
отправлять по `widget_id`, а общее состояние меняется для всех сразу.
`cache_time` задаёт период опроса клиентами: 5–3600 секунд, по умолчанию 30.
Живое обновление — только если виджет вызвал `TelegramWidget.watchPublicData()`.

Для одного владельца (как в Jarvis) shared-виджет обычно не нужен:
достаточно static.

## 4. Запросы к боту

Запрос — это кнопка внутри виджета, аналог callback-кнопки.

```js
button.addEventListener("click", () => {
  TelegramWidget.request("add").then(
    (answer) => show(answer.result),   // { result: string | null }
    (error) => showError(error.code),  // NOT_ALLOWED, BUSY, ...
  );
});
```

Правила:

- `data` — строка 1–64 байта UTF-8.
- Вызов — **синхронно в обработчике тапа**; один тап разблокирует один вызов
  (общий с `openLink`, `openTelegramLink`, `copyText`).
- Один запрос в полёте; второй получит `BUSY`.
- Ответ — до 1024 байт, получает только спросивший виджет.
- Ответ **не меняет состояние**; для этого нужен отдельный edit.
- Бот должен ответить за **15 секунд**, иначе у виджета будет `TIMEOUT`.
- Отвечать нужно **всегда**, даже если запрос отклонён или что-то упало.
- Лимит — 5 запросов за 5 секунд на пользователя и бота.
- В личке с ботом разрешены **background-запросы**: один при появлении
  виджета на экране, затем не чаще одного раза в 5 секунд, пока виджет виден.
  В группах, via-bot и пересылках — только по тапу.

Где виджет может спрашивать:

| Сообщение | По тапу | В фоне | `viewer` | `chatType` |
|---|---|---|---|---|
| Оригинал в личке с ботом | да | да | пользователь | `private` |
| Оригинал в группе | да | нет | пользователь | `group`/`supergroup` |
| Оригинал via-bot | да | нет | пользователь | тип чата |
| Пересылка shared-виджета | да | нет | пользователь | `null` |
| Пересылка static-виджета | нет | нет | `null` | `null` |

Без `allow_queries` виджет не может спрашивать нигде, и `viewer` у него `null`.

### 4.1. `widget_query` и `shared_widget_query`

`widget_query` приходит из оригинала (личка, группа, via-bot):

- `id`, `from`, `data`, `type`;
- `chat` + `message_id` — для обычного сообщения;
- `inline_message_id` — для via-bot;
- `chat_instance`, `chat_type` — для via-bot;
- `widget_id` — если виджет shared.

`shared_widget_query` приходит из пересланной копии shared-виджета:

- `id`, `from`, `data`, `widget_id`;
- **нет** `chat`, `message_id`, `chat_instance`, `chat_type`.

`from` и `widget_id` нельзя подделать: `from` подтверждён Telegram,
`widget_id` вычислен сервером из сообщения. Для «один голос на пользователя»
в пересылках ключ — `(widget_id, from.id)`.

## 5. Bot API 10.4

Релиз — 9 ноября 2026. Добавлены методы, типы и два update’а.

| Метод | Назначение |
|---|---|
| `sendWidget` | отправить сообщение с HTML-виджетом |
| `sendSharedWidget` | отправить существующий shared-виджет по `widget_id` |
| `answerWidgetQuery` | ответить на запрос виджета за 15 секунд |
| `editMessageWidgetData` | изменить `chat_data`/`public_data` одного сообщения |
| `editSharedWidgetData` | изменить `public_data` shared-виджета везде |

| Тип / update | Что это |
|---|---|
| `Widget` | поле `widget` в `Message`: `id`, `width`, `height`, `html`, `public_data`, `chat_data` |
| `WidgetQuery` | запрос из виджета в сообщении бота или via-bot |
| `SharedWidgetQuery` | запрос из пересланной копии shared-виджета |
| `WidgetQueryType` | `data`, `sign_data`, `sent_transaction`, `wallet_card` |
| `InputWidgetMessageContent` | виджет как inline-результат (тест-сервер) |
| `InputSharedWidgetMessageContent` | shared-виджет как inline-результат (тест-сервер) |

### 5.1. `sendWidget`

Основные параметры:

| Параметр | Обязательный | Смысл |
|---|---|---|
| `chat_id` | да | чат; в проде — только личка |
| `html` | да | 1–32768 байт UTF-8; без NUL |
| `width`, `height` | нет | подсказки размера в logical px; `0`/omit = auto |
| `public_data` | нет | общее состояние, 0–1024 байта |
| `chat_data` | нет | состояние сообщения, 0–1024 байта |
| `caption` | нет | 0–1024 символа; fallback и текст для поиска |
| `parse_mode`, `caption_entities` | нет | разметка caption |
| `show_caption_above_media` | нет | caption над виджетом; время уходит в угол |
| `allow_queries` | нет | разрешить запросы к боту |
| `is_shared` | нет | создать shared-виджет |
| `protect_content` | нет | защита от пересылки/сохранения |
| `disable_notification` | нет | без звука |
| `message_effect_id` | нет | эффект сообщения (личка) |
| `reply_parameters` | нет | ответ на сообщение |
| `reply_markup` | нет | обычная inline-клавиатура под виджетом |

`sendWidget` возвращает `Message` с полем `widget`.

### 5.2. `sendSharedWidget`

Параметры: `chat_id`, `widget_id`, `chat_data`, `caption`, `parse_mode`,
`caption_entities`, `show_caption_above_media`, `disable_notification`,
`protect_content`, `message_effect_id`, `reply_parameters`, `reply_markup`.

HTML, размер и флаг запросов берутся из объекта; `public_data` отправкой
не меняется.

### 5.3. `answerWidgetQuery`

```json
{
  "widget_query_id": "8123456789012345678",
  "data": "{\"n\": 1}"
}
```

`data` — 0–1024 байта. Ответ получает только спросивший виджет.
Никаких toast/alert/URL/изменения состояния в ответе нет.

### 5.4. `editMessageWidgetData`

Меняет состояние одного сообщения:

```json
{
  "chat_id": 111111111,
  "message_id": 4242,
  "chat_data": "{\"n\": 1}"
}
```

Или по `inline_message_id` для via-bot. Поля не переданы — не меняются;
пустая строка очищает. Если ничего не изменилось, Telegram вернёт
`message is not modified` — считайте это успехом и всё равно отвечайте
на запрос.

`editMessageWidgetData` считается как одно отправленное сообщение
против лимитов бота. При массовых обновлениях коалесцируйте edits.

### 5.5. `editSharedWidgetData`

```json
{
  "widget_id": "7770011223344556677",
  "public_data": "{\"p\": 5.47}",
  "cache_time": 10
}
```

`cache_time` — 5–3600 секунд. Shared-виджет можно редактировать
не чаще одного раза в секунду.

### 5.6. Минимальный цикл

```python
# 1. Отправка
msg = await api("sendWidget", {
    "chat_id": chat_id,
    "html": HTML,
    "chat_data": json.dumps({"n": 0}),
    "allow_queries": True,
    "caption": "Счётчик",
})

# 2. Пришёл widget_query
q = update["widget_query"]
n = state[(q["chat"]["id"], q["message_id"])]

# 3. Меняем состояние
await api("editMessageWidgetData", {
    "chat_id": q["chat"]["id"],
    "message_id": q["message_id"],
    "chat_data": json.dumps({"n": n + 1}),
})

# 4. Обязательно отвечаем
await api("answerWidgetQuery", {
    "widget_query_id": q["id"],
    "data": json.dumps({"n": n + 1}),
})
```

## 6. JavaScript API `TelegramWidget`

`window.TelegramWidget` существует до первого скрипта виджета.
Вне Telegram его нет — проверяйте `if (window.TelegramWidget)`.

| API | Назначение |
|---|---|
| `profile`, `isProfileAtLeast(n)` | версия профиля |
| `colorScheme`, `themeParams` | тема чата |
| `onEvent/offEvent` | `themeChanged`, `activated`/`deactivated`, `chatDataChanged`, `publicDataChanged`, `contentSafeAreaChanged` |
| `chatData`, `publicData` | состояние (строка или `null`) |
| `isForward`, `chatType`, `viewer`, `isPreview` | контекст показа |
| `request(data, options?)` | запрос к боту; `options.wallet` — TON-кошелёк |
| `watchPublicData()`, `unwatchPublicData()` | живое общее состояние shared-виджета |
| `copyText(text)` | скопировать 1–256 символов, только по тапу |
| `openLink(url)` | внешняя `https:`-ссылка, только по тапу, с подтверждением |
| `openTelegramLink(url)` | `t.me`-ссылка, только по тапу, без отдельного подтверждения |
| `HapticFeedback` | тактильная отдача |
| `setResizeAnchor("top"\|"bottom")` | какая грань пузыря остаётся на месте |
| `contentSafeAreaInset` | зона, занятая временем сообщения |
| `wallet` | TON-кошелёк или `null` |
| `ready()` | первый кадр нарисован |

Ограничения API:

- `openLink`/`openTelegramLink`/`copyText`/`request` — только из обработчика
  реального тапа, синхронно, один вызов на тап.
- `openLink` — только `https:`, до 256 символов, не `t.me`.
- `openTelegramLink` — `https://t.me/...`, до 256 символов.
- `localStorage` — 4 КБ на сообщение на устройстве; не отправляется боту.
- `sessionStorage` — до перезапуска приложения.
- `sendMessage` отсутствует: виджет ничего не отправляет в чат сам.

## 7. Где работает в продакшене

| Место | Прод | Тест-сервер |
|---|---|---|
| Личка с ботом, запросы и кошелёк | **да** | да |
| Группы и супергруппы | нет | да |
| Inline-режим (via bot) | нет | да |
| Пересылка виджета | нет | да |
| Каналы | нельзя | нельзя |

Дополнительно:

- Отправленный виджет автоматически получает protected content и не пересылается.
- `copyMessage`/`copyMessages` виджеты не копируют.
- В группах (на тесте) нужен `can_send_other_messages`.
- В каналы виджет нельзя отправить даже через inline; только пересылка (тест).
- Старые клиенты показывают caption вместо виджета.
- Официальных минимальных версий клиентов нет. По проверке tgkit 11.11.2026,
  Telegram Desktop рисовал виджет, а Telegram Web A показывал заглушку.
  Это rollout, а не официальное ограничение.
- Дат для групп/inline/пересылок в проде Telegram не называет.

## 8. Лимиты

| Что | Лимит |
|---|---|
| HTML | 32 768 байт UTF-8 |
| Сжатый HTML | 4 096 байт после zlib level 9 |
| `chat_data`, `public_data` | по 1 024 байта UTF-8 |
| `request.data` | 1–64 байта |
| `answer.data` | 0–1 024 байта |
| Ответ на запрос | 15 секунд |
| Запросы | 5 за 5 секунд на пользователя и бота |
| Запросы на виджет | один в полёте, один на тап |
| Background-запросы | только личка, один при появлении, затем 1/5 сек |
| `editMessageWidgetData` | как одно отправленное сообщение |
| `editSharedWidgetData` | не чаще 1 раза в секунду |
| `cache_time` shared | 5–3 600 секунд, по умолчанию 30 |
| Ширина пузыря | 200–600 px, на телефонах ~320–430 |
| Высота | 50–600 px; лишнее обрезается |
| `localStorage`/`sessionStorage` | 4 КБ на область на сообщение |
| CPU | 300 мс JS в секунду |
| Одна задача JS | 500 мс; дольше — виджет останавливается |
| Layout reads | 128 запросов в одной задаче, до 1 000 мс |
| Память | 8 МБ |
| Таймеры | 64 живых |
| Узлы | 2 000 |
| DOM-изменения | 10 000 в одной задаче |
| Текст | 32 К символов на значение, 48 К в документе |
| CSS | 400 правил, 32 `@keyframes` |
| Canvas | 4 000 команд на canvas в задаче, 2048×2048 px суммарно |
| Картинки `data:` | до 1024×1024 px |
| Тяжёлые элементы | 16 с blur/filter/opacity<1, из них 2 с `backdrop-filter` |
| Падения | 2 подряд — виджет остаётся остановленным |

Размер нужно мерить как сервер:

```bash
python -c "import sys,zlib; print(len(zlib.compress(sys.stdin.buffer.read(), 9)))" < widget.html
```

`INPUT_MEDIA_WIDGET_TOO_LARGE` при HTML меньше 32 КБ почти всегда означает
превышение compressed-лимита 4 КБ.

## 9. Паттерны применения

Официальные демо (`corpus/bot-demos`):

| Демо | Что показывает |
|---|---|
| Order tracker | `chat_data` — состав заказа, адрес, карта; `public_data` — статус; background-poll и кнопки Refresh/Cancel |
| Poll | shared-виджет: вопрос и голоса в `public_data`; live-счётчики через `watchPublicData()` |
| RSVP | групповой список: имена в `chat_data`, счётчик в `public_data`, «You» через `viewer.id` |
| Tic-tac-toe | доска в `chat_data`, ход по `viewer.id`, коммит после успешного edit |
| Ticker | живой курс: shared-виджет без запросов, бот меняет `public_data` каждые 10 секунд |
| Reaction | общий лидерборд: ответ на запрос содержит актуальную таблицу |
| Wallet | TON: подпись текста и перевод 0.01 TON |

Community-проекты:

- **Doom в Telegram** — бот рендерит кадр ViZDoom и присылает новый после
  каждого хода; виджет — тонкий клиент к серверному состоянию.
- **Pacman**, калькулятор, погода, выбор маршрута, «карманный inbox».
- Commerce-карточки: заказ, рейтинг, подписка, промокод, чек-лист, countdown,
  бар-чарт.
- Инструменты: `TG Widget Kit`, `widgetgram`, `telegram-widget-skill`,
  `Awesome Telegram Widgets`.

Что хорошо ложится:

- статус заказа/задачи, который меняется в одном сообщении;
- опрос/голосование с живыми процентами;
- тикер/курс/счёт;
- компактная карточка с 2–5 действиями;
- прогресс, «сколько осталось», маленький график;
- мини-игра на 1–2 экрана.

Что не стоит делать виджетом:

- формы и любой ввод текста;
- длинные списки и таблицы со скроллом;
- полноценные приложения;
- сценарии, где нужна пересылка или группа (пока тест-сервер);
- замену обычных inline-кнопок там, где они уже работают.

## 10. Лучшие практики

### 10.1. Дизайн

- **Всегда отправляйте caption.** Это fallback для старых клиентов, текст
  в списке чатов, уведомлениях и поиске.
- **Рисуйте первый кадр синхронно** из уже имеющегося состояния.
- **Проектируйте под 320 px** и держите высоту до ~560 px.
- **Используйте тему**: `--tg-theme-bg-color`, `--tg-theme-text-color`,
  `--tg-theme-hint-color`, `--tg-theme-button-color` и т.д.
- **Не полагайтесь на hover и клавиатуру** — на телефоне их нет.
- **Не кладите секреты** в HTML, `chat_data` или `public_data`.
- **Дублируйте важное в caption**: accessibility-семантики в виджетах нет,
  `aria-*` сохраняются, но не работают.
- **Экономьте байты**: 4 КБ compressed — это примерно 12–20 КБ обычного
  HTML/CSS/JS. `data:`-картинки сжимаются плохо; лучше SVG/CSS.
- **Оставляйте место под время сообщения**, если caption снизу нет:
  `padding-bottom: max(8px, env(safe-area-inset-bottom))`.
- **Не используйте `opacity` для приглушения**: каждый элемент с
  `opacity < 1` считается тяжёлым. Используйте цвет и `rgba()`.

### 10.2. Логика бота

- **Храните авторитетное состояние у себя**, ключом `(chat_id, message_id)`
  или `widget_id`. Виджет — представление, а не база.
- **`allow_queries` только если виджету действительно нужно общаться с ботом.**
- **Отвечайте на каждый `widget_query` в течение 15 секунд**, даже с ошибкой.
  Медленную работу делайте после ответа.
- **Коммитьте состояние только после успешного edit.** Если edit упал —
  не «забирайте» ход/голос.
- **`message is not modified` считайте успехом** и всё равно отвечайте.
- **Коалесцируйте edits**: `editMessageWidgetData` — как отправка сообщения;
  shared — максимум 1 edit/сек.
- **Добавьте ревизию в `public_data`** (`"r": 41`) и игнорируйте устаревшие
  значения: poll может принести старое состояние после ответа или нового опроса.
- **Static достаточно для одного владельца.** Shared нужен для многих чатов
  и пересылок; в проде пересылки пока недоступны.
- **HTML не обновляется.** Новый layout = новое сообщение; старые сообщения
  остаются со старым виджетом. Версионируйте шаблоны.

### 10.3. Безопасность

- **`data` от виджета — недоверенный ввод**, как `callback_data`.
- **Экранируйте пользовательский текст.** Если собираете HTML через
  `innerHTML`, имена и названия могут сломать вёрстку или добавить ссылку.
  Лучше `textContent`/`createElement`.
- **Ссылки — только по allowlist**, если HTML генерирует LLM.
  `openLink` показывает полный URL и просит подтверждение;
  `openTelegramLink` открывает `t.me` без отдельного подтверждения.
- **Не давайте агенту писать произвольный HTML в чат без валидатора.**
  Для Jarvis безопаснее шаблоны + данные, а не генерация HTML на каждый запрос.
- **Не кладите токены/ключи в виджет.** Ответ `answerWidgetQuery` видит только
  спросивший виджет, но это всё равно не канал для секретов.

### 10.4. Тестирование

- `widget-sandbox` — runtime клиентов и спецификация, не SDK для бота.
  Но там есть `sandbox-node.ts`, dev app и corpus: удобно гонять виджет
  в пузыре 320–430 px и смотреть, что профиль вырезал.
- Группы/inline/пересылки тестируйте на **тестовом сервере** с отдельным
  ботом и аккаунтом.
- В CI проверяйте compressed-размер и наличие caption.
- Проверяйте на реальных клиентах: Desktop, iOS, Android, Web.
  Web A может отставать.
- Проверяйте fallback: старый клиент должен показать нормальный caption.

## 11. Интеграция с Jarvis

### 11.1. aiogram

`aiogram 3.31` поддерживает Bot API **10.3**, нативного 10.4 ещё нет.
Issue [#2006](https://github.com/aiogram/aiogram/issues/2006) открыт.

Что это значит практически:

- `Bot.send_widget` и типизированных `WidgetQuery` в aiogram 3.31 нет.
- `Update` сохраняет неизвестные поля в `model_extra`, поэтому
  `update.widget_query` можно достать, но типизированного роутинга
  и фильтров нет.
- Варианты:
  1. raw HTTP/`bot.session` + свой обработчик `dp.update`;
  2. сторонняя библиотека `widgetgram` (aiogram 3, Bot API 10.4,
     `widget_query`-фильтры, state storage, auto-ack, edit queue);
  3. ждать официальной поддержки 10.4 в aiogram.

Если задаёте `allowed_updates` явно — добавьте `widget_query`
и `shared_widget_query`. При пустом списке они приходят по умолчанию.

### 11.2. Что нужно будет сделать

- Собрать HTML-шаблоны и проверку размера (zlib-9, 4 КБ).
- Хранить состояние в Postgres, ключом `(chat_id, message_id)`.
- Обрабатывать `widget_query`: валидировать `data`, отвечать за 15 секунд,
  делать edit, коммитить состояние после успеха.
- Следить за rate limit на edit и flood wait.
- Добавить fallback caption и проверить старые клиенты.
- Решить, где виджет заменяет inline-клавиатуру, а где нет.

### 11.3. Кандидаты для Jarvis

Хорошо ложится:

- карточка задачи с живым статусом, прогрессом, дедлайном и 2–4 действиями;
- дневная/недельная статистика, heatmap, «сколько сделано»;
- карточка дня/недели со статистикой и быстрыми действиями;
- напоминание с быстрыми действиями;
- диагностика/бэклог как компактный dashboard.

Плохо ложится:

- ввод произвольного текста — останется обычным сообщением;
- длинные списки задач — пагинация и inline-клавиатуры уже работают;
- группы/каналы — в проде виджетов там нет;
- замена всех inline-кнопок — виджет дороже по коду, состоянию и тестированию.

Предварительный вывод: **не переписывать UI на виджеты, а добавить 1–2 карточки
там, где важно живое состояние в одном сообщении.** Например, карточка задачи
или дневная статистика. Inline-клавиатуры оставить для действий, которые уже
работают.

## 12. Открытые вопросы и риски

- Фиче 3 дня: прод-опыт маленький, часть ограничений и доступность в группах
  могут измениться.
- `aiogram`-поддержка 10.4 пока не вышла; raw API/сторонняя библиотека —
  дополнительный слой, который может сломаться при обновлении.
- 4 КБ compressed и отсутствие ввода текста сильно ограничивают UX.
- HTML неизменяем: ошибка в layout требует нового сообщения.
- Старые клиенты и Web A могут не рисовать виджет; caption обязателен.
- TON-кошелёк в виджетах не нужен Jarvis; Telegram Stars в документации
  виджетов не описаны, а Bot Developer Terms всё ещё требуют Stars для
  цифровых товаров.
- Официальных дат для групп/inline/пересылок в проде нет.

## 13. Источники

- [Telegram Widgets — официальный гайд](https://core.telegram.org/bots/widgets)
- [Bot API 10.4 — changelog](https://core.telegram.org/bots/api-changelog#november-9-2026)
- [Bot API: sendWidget](https://core.telegram.org/bots/api#sendwidget)
- [widget-sandbox — runtime и спецификации](https://github.com/TelegramMessenger/widget-sandbox)
- [Официальные bot-demos](https://github.com/TelegramMessenger/widget-sandbox/tree/main/corpus/bot-demos)
- [Awesome Telegram Widgets](https://github.com/ADanMan/awesome-telegram-widgets)
- [widgetgram — Bot API 10.4 для aiogram 3](https://github.com/krendel001/widgetgram)
- [TG Widget Kit — playground](https://github.com/advanceddev/telegram-widget-kit)
- [aiogram issue #2006 — поддержка 10.4](https://github.com/aiogram/aiogram/issues/2006)

## 14. Watch list: нативная поддержка в aiogram

Пока aiogram не умеет Bot API 10.4, Jarvis отправляет виджеты кастомным
`SendWidget` (`src/jarvis/bot/methods.py`). Проверять:

- PR <https://github.com/aiogram/aiogram/pull/2004> — Bot API 10.4, 3.32.0;
- issue <https://github.com/aiogram/aiogram/issues/2006>.

Когда появится `Bot.send_widget`:

1. заменить кастомный `SendWidget` на нативный вызов;
2. удалить `tests/unit/test_aiogram_widget_watch.py` (canary специально
   упадёт и напомнит);
3. обновить этот раздел и `docs/status.md`.
