"""Сквозные сценарии (spec §17) через настоящий Dispatcher и фейковый Telegram."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from aiogram import Router
from aiogram.types import InlineKeyboardMarkup, Message
from sqlalchemy import func, select

from jarvis.ai.fake import FakeProvider
from jarvis.ai.schemas import Interpretation
from jarvis.bot.app import create_dispatcher
from jarvis.bot.chatlog import build_out_recorder
from jarvis.db.models import ChatMessage, KnowledgeItem, Task
from jarvis.documents.storage import EncryptedFileStorage
from jarvis.domain.enums import (
    DocumentStatus,
    ImprovementCategory,
    ImprovementSource,
    ImprovementStatus,
    TaskBucket,
)
from jarvis.repositories.categories import CategoryRepository
from jarvis.repositories.documents import DocumentRepository
from jarvis.repositories.improvements import ImprovementRepository
from jarvis.repositories.knowledge import KnowledgeRepository
from jarvis.repositories.secrets import SecretRepository
from jarvis.repositories.tasks import TaskRepository
from jarvis.services.catalog import CatalogService
from jarvis.services.drafts import TaskDraft
from jarvis.services.improvements import ImprovementService
from jarvis.services.secrets import SecretService
from jarvis.services.tasks import TaskService
from jarvis.transcription.fake import FakeTranscription
from tests.e2e.harness import DEFAULT_USER_ID, BotHarness, FakeTelegramSession

pytestmark = pytest.mark.integration


@pytest.fixture
def classic_settings(test_settings):
    """Детерминированный путь без агента: фолбэк, когда AI выключен или недоступен."""
    return test_settings.model_copy(update={"agent_enabled": False})


async def make_harness(
    settings, session_factory, *, ai=None, transcription=None
) -> BotHarness:
    async with session_factory() as session:
        await CatalogService(session).ensure_defaults()
        await session.commit()
    dispatcher = create_dispatcher(
        settings,
        session_factory,
        ai or FakeProvider(),
        transcription,
    )
    session = FakeTelegramSession()
    session.recorder = build_out_recorder(session_factory, settings)
    harness = BotHarness(dispatcher, session)
    await harness.start()
    return harness


def keyboard_messages(messages) -> list:
    return [m for m in messages if isinstance(m.reply_markup, InlineKeyboardMarkup)]


async def confirm_last(harness: BotHarness, user_id: int, messages) -> list:
    confirmation = keyboard_messages(messages)
    assert confirmation, "no confirmation keyboard in messages"
    return await harness.click_button(user_id, confirmation[-1], "Подтвердить")


# 1. Текстовая мысль -> подтверждение -> запись находится поиском.
async def test_scenario_text_note_to_search(classic_settings, session_factory, session):
    ai = FakeProvider()
    ai.queue(
        Interpretation(
            intent="create_knowledge",
            entities={
                "title": "Настройка резервного копирования",
                "content": "Настроил rsync бэкап сервера по ночам",
                "type": "instruction",
            },
            confidence=0.95,
        )
    )
    harness = await make_harness(classic_settings, session_factory, ai=ai)

    messages = await harness.send_text(
        DEFAULT_USER_ID, "Запомни, как я настраивал резервное копирование сервера"
    )
    assert any("Сохранить запись?" in (m.text or "") for m in messages)

    # до подтверждения записи нет
    assert await KnowledgeRepository(session).list_recent() == []

    await confirm_last(harness, DEFAULT_USER_ID, messages)
    await session.commit()

    items = await KnowledgeRepository(session).list_recent()
    assert len(items) == 1
    assert items[0].title == "Настройка резервного копирования"

    results = await harness.send_text(DEFAULT_USER_ID, "найди резервное копирование")
    assert any("Настройка резервного копирования" in (m.text or "") for m in results)


# 2. Голосовая задача -> транскрибация -> подтверждение -> задача с группой/сроком.
async def test_scenario_task_list_is_clickable(classic_settings, session_factory, session):
    """Список задач из NL («на неделю») — кнопки открытия карточки, без /tasks."""
    await TaskService(session).create_from_draft(
        TaskDraft(title="Обновить дашборд", bucket=TaskBucket.WEEK)
    )
    await session.commit()

    ai = FakeProvider()
    ai.queue(Interpretation(intent="list_tasks", entities={"view": "week"}, confidence=0.9))
    harness = await make_harness(classic_settings, session_factory, ai=ai)

    messages = await harness.send_text(DEFAULT_USER_ID, "Какие у меня задачи на неделю?")
    assert any("Обновить дашборд" in (m.text or "") for m in messages)
    choices = keyboard_messages(messages)
    assert choices, "список задач должен быть кликабельным"

    opened = await harness.click_button(DEFAULT_USER_ID, choices[-1], "Обновить дашборд")
    combined = " ".join(m.text or "" for m in opened)
    assert "Обновить дашборд" in combined  # открылась карточка задачи
    labels = [
        button.text
        for message in keyboard_messages(opened)
        for row in message.reply_markup.inline_keyboard
        for button in row
    ]
    assert "🏁 Завершить" in labels  # в карточке есть действия


async def test_scenario_voice_task(classic_settings, session_factory, session):
    from zoneinfo import ZoneInfo

    ai = FakeProvider()
    # «Завтра 18:00» считаем в таймзоне пользователя: UTC-вариант ломается
    # поздно вечером, когда в МСК уже наступил следующий день.
    tz = ZoneInfo(classic_settings.app_timezone)
    tomorrow = (datetime.now(tz) + timedelta(days=1)).replace(
        hour=18, minute=0, second=0, microsecond=0
    )
    ai.queue(
        Interpretation(
            intent="create_task",
            entities={
                "title": "Обновить инфраструктуру",
                "due_at": tomorrow.isoformat(),
                "bucket": "week",
            },
            confidence=0.9,
        )
    )
    transcription = FakeTranscription(
        transcripts=["Поставь задачу на завтра: обновить инфраструктуру"]
    )
    harness = await make_harness(
        classic_settings, session_factory, ai=ai, transcription=transcription
    )

    messages = await harness.send_voice(DEFAULT_USER_ID)
    assert any("Распознано" in (m.text or "") for m in messages)
    assert transcription.calls == ["voice.ogg"]

    # Распознанный текст попадает в журнал чата (источник voice).
    voice_rows = (
        await session.execute(select(ChatMessage).where(ChatMessage.source == "voice"))
    ).scalars().all()
    assert len(voice_rows) == 1
    assert voice_rows[0].text.startswith("Поставь задачу")

    await confirm_last(harness, DEFAULT_USER_ID, messages)
    await session.commit()

    tasks = await TaskRepository(session).list_week(datetime.now(UTC), classic_settings.tz)
    assert len(tasks) == 1
    assert tasks[0].title == "Обновить инфраструктуру"


# 2b. Ошибка/лимит транскрибации показывается прямо в чате.
async def test_scenario_voice_transcription_error_shown(
    classic_settings, session_factory, session
):
    from jarvis.transcription.provider import TranscriptionError

    transcription = FakeTranscription(
        errors=[
            TranscriptionError(
                "превышен лимит сервиса транскрибации (HTTP 429): "
                "Rate limit reached for whisper-large-v3-turbo"
            )
        ]
    )
    harness = await make_harness(
        classic_settings, session_factory, transcription=transcription
    )

    messages = await harness.send_voice(DEFAULT_USER_ID)
    combined = " ".join(m.text or "" for m in messages)
    assert "Не получилось распознать" in combined
    assert "лимит" in combined and "HTTP 429" in combined


# 3. Сценарий с документом и восстановлением состояния после «рестарта».
async def test_scenario_document_flow_survives_restart(
    classic_settings, session_factory, session
):
    ai = FakeProvider()
    ai.queue(
        Interpretation(
            intent="document_upload",
            entities={"category": "Документы"},
            confidence=0.9,
        )
    )
    harness = await make_harness(classic_settings, session_factory, ai=ai)
    messages = await harness.send_text(
        DEFAULT_USER_ID, "Сейчас пришлю паспорт, сохрани его в личных документах"
    )
    assert any("Пришлите файл" in (m.text or "") for m in messages)

    # «Рестарт»: новый dispatcher и новая сессия Telegram, та же БД.
    harness2 = await make_harness(classic_settings, session_factory, ai=FakeProvider())
    content = b"%PDF-1.4 fake passport content"
    messages = await harness2.send_document(
        DEFAULT_USER_ID, filename="passport.pdf", content=content
    )
    assert any("Сохранить документ?" in (m.text or "") for m in messages), [
        m.text for m in messages
    ]

    # Файл уже зашифрован в staging, но документ ещё не подтверждён.
    assert await DocumentRepository(session).list_active() == []

    await confirm_last(harness2, DEFAULT_USER_ID, messages)
    await session.commit()

    documents = await DocumentRepository(session).list_active()
    assert len(documents) == 1
    document = documents[0]
    assert document.status == DocumentStatus.ACTIVE

    storage = EncryptedFileStorage(
        classic_settings.document_storage_path, classic_settings.document_keyring()
    )
    saved = storage.verify(document.storage_key, document.sha256)
    assert saved == content

    # В staging после подтверждения пусто.
    assert not list(storage._staging().iterdir())


# 4. Перенос срока: без подтверждения срок не меняется.
async def test_scenario_due_change_requires_confirmation(
    classic_settings, session_factory, session
):
    task_id = await TaskService(session).create_from_draft(
        TaskDraft(title="Продлить домен", bucket=TaskBucket.TODAY)
    )
    await session.commit()

    harness = await make_harness(classic_settings, session_factory, ai=FakeProvider())
    messages = await harness.send_text(DEFAULT_USER_ID, "/today")
    list_message = keyboard_messages(messages)[-1]

    card_messages = await harness.click_button(
        DEFAULT_USER_ID, list_message, "1. Продлить домен"
    )
    card_message = keyboard_messages(card_messages)[-1]

    due_messages = await harness.click_button(DEFAULT_USER_ID, card_message, "Изменить срок")
    assert any("перенести" in (m.text or "") for m in due_messages)

    proposal = await harness.send_text(DEFAULT_USER_ID, "завтра")
    assert any("Изменить задачу?" in (m.text or "") for m in proposal)

    task = await TaskRepository(session).get(task_id)
    await session.refresh(task)
    assert task.due_at is None  # до подтверждения ничего не поменялось

    await confirm_last(harness, DEFAULT_USER_ID, proposal)
    await session.commit()

    task = await TaskRepository(session).get(task_id)
    await session.refresh(task)
    assert task.due_at is not None
    expected = (datetime.now(UTC) + timedelta(days=1)).astimezone(classic_settings.tz)
    assert task.due_at.astimezone(classic_settings.tz).date() == expected.date()


# 5. Отмена операции: запись не создаётся.
async def test_scenario_cancel_operation(classic_settings, session_factory, session):
    ai = FakeProvider()
    ai.queue(
        Interpretation(
            intent="create_knowledge",
            entities={"title": "Черновик заметки", "content": "Не сохраняй это"},
            confidence=0.9,
        )
    )
    harness = await make_harness(classic_settings, session_factory, ai=ai)
    messages = await harness.send_text(DEFAULT_USER_ID, "Запомни важную мысль")
    confirmation = keyboard_messages(messages)[-1]
    await harness.click_button(DEFAULT_USER_ID, confirmation, "Отменить")
    await session.commit()

    count = await session.scalar(select(func.count()).select_from(KnowledgeItem))
    assert count == 0


# 5.1. «Изменить» на черновике задачи применяет новое название (регрессия).
async def test_pending_task_rename_via_edit(classic_settings, session_factory, session):
    ai = FakeProvider()
    ai.queue(
        Interpretation(
            intent="create_task",
            entities={"title": "Старая формулировка", "due_at": "2026-11-01T12:00:00+03:00"},
            confidence=0.9,
        )
    )
    harness = await make_harness(classic_settings, session_factory, ai=ai)

    messages = await harness.send_text(DEFAULT_USER_ID, "поставь задачу: старая формулировка")
    preview = keyboard_messages(messages)[-1]
    assert "Старая формулировка" in (preview.text or "")

    await harness.click_button(DEFAULT_USER_ID, preview, "Изменить")
    ai.queue(
        Interpretation(
            intent="create_task",
            entities={"title": "Отчёт по УВ"},
            confidence=0.9,
        )
    )
    messages = await harness.send_text(DEFAULT_USER_ID, "Название должно быть «Отчёт по УВ»")
    updated = keyboard_messages(messages)[-1]
    assert "Отчёт по УВ" in (updated.text or "")
    assert "Старая формулировка" not in (updated.text or "")

    await confirm_last(harness, DEFAULT_USER_ID, messages)
    await session.commit()
    session.expire_all()

    tasks = await TaskRepository(session).search("Отчёт по УВ")
    assert [task.title for task in tasks] == ["Отчёт по УВ"]


# 5.2. Задача без срока: сначала уточняем, нужен ли срок.
async def test_pending_task_without_due_asks_deadline(classic_settings, session_factory, session):
    ai = FakeProvider()
    ai.queue(
        Interpretation(
            intent="create_task",
            entities={"title": "Полить цветы"},
            confidence=0.9,
        )
    )
    harness = await make_harness(classic_settings, session_factory, ai=ai)

    messages = await harness.send_text(DEFAULT_USER_ID, "поставь задачу: полить цветы")
    combined = " ".join(m.text or "" for m in messages)
    assert "Нужен ли срок" in combined
    assert not keyboard_messages(messages)  # превью ещё не отправлено

    # «без срока» — задача предлагается к подтверждению как есть.
    messages = await harness.send_text(DEFAULT_USER_ID, "без срока")
    assert "Создать задачу?" in " ".join(m.text or "" for m in messages)
    await confirm_last(harness, DEFAULT_USER_ID, messages)
    await session.commit()
    session.expire_all()
    tasks = await TaskRepository(session).search("Полить цветы")
    assert tasks and tasks[0].due_at is None


async def test_pending_task_without_due_accepts_date(classic_settings, session_factory, session):
    ai = FakeProvider()
    ai.queue(
        Interpretation(
            intent="create_task",
            entities={"title": "Пересадить цветок"},
            confidence=0.9,
        )
    )
    harness = await make_harness(classic_settings, session_factory, ai=ai)

    await harness.send_text(DEFAULT_USER_ID, "поставь задачу: пересадить цветок")
    messages = await harness.send_text(DEFAULT_USER_ID, "завтра")
    combined = " ".join(m.text or "" for m in messages)
    assert "Создать задачу?" in combined
    assert "Дедлайн" in combined  # названная дата попала в превью

    await confirm_last(harness, DEFAULT_USER_ID, messages)
    await session.commit()
    session.expire_all()
    tasks = await TaskRepository(session).search("Пересадить цветок")
    assert tasks and tasks[0].due_at is not None


async def test_pending_task_in_new_category_is_attached(
    classic_settings, session_factory, session
):
    """Категория названа, но её нет: задача привяжется по имени, категория создастся."""
    ai = FakeProvider()
    ai.queue(
        Interpretation(
            intent="create_task",
            entities={
                "title": "Вызвать мастера",
                "category": "Ванная",
                "due_at": "2026-11-01T12:00:00+03:00",
            },
            confidence=0.9,
        )
    )
    harness = await make_harness(classic_settings, session_factory, ai=ai)

    messages = await harness.send_text(
        DEFAULT_USER_ID, "Заведи задачу в категории «Ванная»: вызвать мастера"
    )
    combined = " ".join(m.text or "" for m in messages)
    assert "Создать задачу?" in combined
    assert "Ванная" in combined  # категория попала в превью

    await confirm_last(harness, DEFAULT_USER_ID, messages)
    await session.commit()
    session.expire_all()

    from jarvis.repositories.categories import CategoryRepository

    tasks = await TaskRepository(session).search("Вызвать мастера")
    category = await CategoryRepository(session).find_by_name("Ванная")
    assert category is not None
    assert tasks and tasks[0].category_id == category.id


# 6. Неавторизованный пользователь не получает доступ.
async def test_scenario_unauthorized_user_is_ignored(classic_settings, session_factory, session):
    harness = await make_harness(classic_settings, session_factory, ai=FakeProvider())
    messages = await harness.send_text(9999, "Покажи мои секреты")
    assert messages == []
    count = await session.scalar(select(func.count()).select_from(KnowledgeItem))
    assert count == 0


# 7. Повторное нажатие подтверждения не дублирует операцию.
async def test_scenario_confirm_is_idempotent(classic_settings, session_factory, session):
    ai = FakeProvider()
    ai.queue(
        Interpretation(
            intent="create_task",
            entities={"title": "Один раз", "due_at": "2026-11-01T12:00:00+03:00"},
            confidence=0.9,
        )
    )
    harness = await make_harness(classic_settings, session_factory, ai=ai)
    messages = await harness.send_text(DEFAULT_USER_ID, "Задача: один раз")
    confirmation = keyboard_messages(messages)[-1]

    await harness.click_button(DEFAULT_USER_ID, confirmation, "Подтвердить")
    await harness.click_button(DEFAULT_USER_ID, confirmation, "Подтвердить")
    await session.commit()

    count = await session.scalar(select(func.count()).select_from(Task))
    assert count == 1


# 8. Секреты шифруются, не показываются в превью и не ищутся.
async def test_scenario_secret_flow(classic_settings, session_factory, session):
    harness = await make_harness(classic_settings, session_factory, ai=FakeProvider())

    messages = await harness.send_text(DEFAULT_USER_ID, "/secret")
    assert any("Пришлите значение секрета" in (m.text or "") for m in messages)

    messages = await harness.send_text(DEFAULT_USER_ID, "ssh-ed25519 AAAATESTKEY secret")
    assert any("Как назвать секрет" in (m.text or "") for m in messages)

    messages = await harness.send_text(DEFAULT_USER_ID, "сервер hetzner ssh")
    assert any("Сохранить секрет?" in (m.text or "") for m in messages)
    preview_text = " ".join(m.text or "" for m in messages)
    assert "AAAATESTKEY" not in preview_text  # значение не показывается в превью

    await confirm_last(harness, DEFAULT_USER_ID, messages)
    await session.commit()

    secrets = await SecretRepository(session).list_active()
    assert len(secrets) == 1
    service = SecretService(session, classic_settings)
    assert service.decrypt_value(secrets[0].encrypted_value) == "ssh-ed25519 AAAATESTKEY secret"

    # Поиск не находит секреты: значение не является знанием.
    results = await harness.send_text(DEFAULT_USER_ID, "найди AAAATESTKEY")
    assert any("Ничего не нашёл" in (m.text or "") for m in results)


# 9. Комментарии к задаче через карточку: добавить, увидеть, отредактировать.
async def test_scenario_task_comments_via_card(classic_settings, session_factory, session):
    task_id = await TaskService(session).create_from_draft(
        TaskDraft(title="Проверить бэкап", bucket=TaskBucket.TODAY)
    )
    await session.commit()

    harness = await make_harness(classic_settings, session_factory, ai=FakeProvider())
    messages = await harness.send_text(DEFAULT_USER_ID, "/today")
    list_message = keyboard_messages(messages)[-1]

    card_messages = await harness.click_button(DEFAULT_USER_ID, list_message, "1. Проверить бэкап")
    card_message = keyboard_messages(card_messages)[-1]

    comments_menu = await harness.click_button(DEFAULT_USER_ID, card_message, "Комментарии")
    empty_menu = keyboard_messages(comments_menu)[-1]
    assert any("пока нет" in (m.text or "") for m in comments_menu)

    prompt = await harness.click_button(DEFAULT_USER_ID, empty_menu, "Добавить комментарий")
    assert any("Напишите комментарий" in (m.text or "") for m in prompt)

    proposal = await harness.send_text(DEFAULT_USER_ID, "Позвонить в поддержку сервера")
    assert any("Добавить комментарий?" in (m.text or "") for m in proposal)

    await confirm_last(harness, DEFAULT_USER_ID, proposal)
    await session.commit()

    from jarvis.repositories.comments import TaskCommentRepository

    comments = await TaskCommentRepository(session).list_for_task(task_id)
    assert [comment.content for comment in comments] == ["Позвонить в поддержку сервера"]

    # Комментарии видны в карточке задачи.
    card_messages = await harness.click_button(DEFAULT_USER_ID, list_message, "1. Проверить бэкап")
    assert any("Позвонить в поддержку сервера" in (m.text or "") for m in card_messages)

    # Редактирование комментария из списка (регрессия: callback ≤ 64 байт).
    card_message = keyboard_messages(card_messages)[-1]
    comments_menu = await harness.click_button(DEFAULT_USER_ID, card_message, "Комментарии")
    menu_message = keyboard_messages(comments_menu)[-1]
    edit_prompt = await harness.click_button(DEFAULT_USER_ID, menu_message, "Изменить 1")
    assert any("новую версию комментария" in (m.text or "") for m in edit_prompt)

    proposal = await harness.send_text(DEFAULT_USER_ID, "Позвонить и написать письмо")
    assert any("Изменить комментарий?" in (m.text or "") for m in proposal)
    await confirm_last(harness, DEFAULT_USER_ID, proposal)
    await session.commit()
    session.expire_all()  # приложение писало в другой сессии — сбрасываем кэш

    comments = await TaskCommentRepository(session).list_for_task(task_id)
    assert [comment.content for comment in comments] == ["Позвонить и написать письмо"]


# 10. Естественный язык: «добавь к задаче …» → выбор задачи → комментарий.
async def test_scenario_add_comment_natural_language(classic_settings, session_factory, session):
    service = TaskService(session)
    task_infra_id = await service.create_from_draft(TaskDraft(title="Обновить инфраструктуру"))
    await service.create_from_draft(TaskDraft(title="Обновить документацию"))
    await session.commit()

    ai = FakeProvider()
    ai.queue(
        Interpretation(
            intent="add_comment",
            entities={
                "target_task": "обновить",
                "content": "Согласовать окно с провайдером",
            },
            confidence=0.9,
        )
    )
    harness = await make_harness(classic_settings, session_factory, ai=ai)
    messages = await harness.send_text(
        DEFAULT_USER_ID, "Добавь к задаче про обновление: согласовать окно с провайдером"
    )
    assert any("К какой задаче добавить комментарий?" in (m.text or "") for m in messages)

    choice_message = keyboard_messages(messages)[-1]
    proposal = await harness.click_button(
        DEFAULT_USER_ID, choice_message, "Обновить инфраструктуру"
    )
    assert any("Добавить комментарий?" in (m.text or "") for m in proposal)

    await confirm_last(harness, DEFAULT_USER_ID, proposal)
    await session.commit()

    from jarvis.repositories.comments import TaskCommentRepository

    comments = await TaskCommentRepository(session).list_for_task(task_infra_id)
    assert [comment.content for comment in comments] == ["Согласовать окно с провайдером"]


# 11. Сообщение с секретом: кнопка «Всё равно сохранить» сохраняет как чувствительную заметку.
async def test_scenario_force_save_secret(classic_settings, session_factory, session):
    ai = FakeProvider()
    ai.queue(
        Interpretation(
            intent="create_knowledge",
            entities={"title": "Пароль от роутера", "content": "password=SuperSecret123"},
            sensitivity_hints=["secret"],
            confidence=0.9,
        )
    )
    harness = await make_harness(classic_settings, session_factory, ai=ai)
    messages = await harness.send_text(
        DEFAULT_USER_ID, "Запомни пароль от роутера: password=SuperSecret123"
    )
    assert any("есть секрет" in (m.text or "") for m in messages)
    assert any("Всё равно сохранить" in (b.text or "")
               for m in messages if isinstance(m.reply_markup, InlineKeyboardMarkup)
               for row in m.reply_markup.inline_keyboard for b in row)

    force_message = keyboard_messages(messages)[-1]
    chosen = await harness.click_button(DEFAULT_USER_ID, force_message, "Всё равно сохранить")
    assert any("Сохранить запись?" in (m.text or "") for m in chosen)

    await confirm_last(harness, DEFAULT_USER_ID, chosen)
    await session.commit()

    from jarvis.domain.enums import Sensitivity

    items = await KnowledgeRepository(session).list_recent()
    assert len(items) == 1
    assert items[0].sensitivity == Sensitivity.SENSITIVE
    assert "SuperSecret123" in items[0].content


# 12. «Какие у меня задачи на неделю» — выводим список, а не «ничего не нашёл».
async def test_scenario_list_tasks(classic_settings, session_factory, session):
    service = TaskService(session)
    await service.create_from_draft(TaskDraft(title="Обновить дашборд", bucket=TaskBucket.WEEK))
    await service.create_from_draft(TaskDraft(title="Позвонить в сервис", bucket=TaskBucket.TODAY))
    await session.commit()

    ai = FakeProvider()
    ai.queue(
        Interpretation(intent="list_tasks", entities={"view": "week"}, confidence=0.95)
    )
    harness = await make_harness(classic_settings, session_factory, ai=ai)
    messages = await harness.send_text(DEFAULT_USER_ID, "Какие у меня задачи на этой неделе?")

    combined = " ".join(m.text or "" for m in messages)
    assert "Обновить дашборд" in combined
    assert "ничего не нашёл" not in combined.lower()


# 13. «Какие комментарии к задаче X» — показываем комментарии.
async def test_scenario_list_comments(classic_settings, session_factory, session):
    task_id = await TaskService(session).create_from_draft(
        TaskDraft(title="Обновить инфраструктуру")
    )
    from jarvis.repositories.comments import TaskCommentRepository

    await TaskCommentRepository(session).create(task_id, "Согласовано окно с провайдером")
    await session.commit()

    ai = FakeProvider()
    ai.queue(
        Interpretation(
            intent="list_comments",
            entities={"target_task": "инфраструктуру"},
            confidence=0.9,
        )
    )
    harness = await make_harness(classic_settings, session_factory, ai=ai)
    messages = await harness.send_text(
        DEFAULT_USER_ID, "Какие комментарии у меня к задаче про инфраструктуру?"
    )
    combined = " ".join(m.text or "" for m in messages)
    assert "Согласовано окно с провайдером" in combined


# 14. «Сохрани креды в проект SuperSport» — проект создаётся, запись привязывается.
async def test_scenario_credentials_into_project(classic_settings, session_factory, session):
    ai = FakeProvider()
    ai.queue(
        Interpretation(
            intent="create_knowledge",
            entities={
                "title": "Креды SuperSport",
                "content": "логин: admin, пароль: qwerty-123",
                "project": "SuperSport",
            },
            sensitivity_hints=["secret"],
            confidence=0.9,
        )
    )
    harness = await make_harness(classic_settings, session_factory, ai=ai)
    messages = await harness.send_text(
        DEFAULT_USER_ID,
        "Создай проект SuperSport и сохрани туда креды доступа: логин admin, пароль qwerty-123",
    )
    assert any("есть секрет" in (m.text or "") for m in messages)

    force_message = keyboard_messages(messages)[-1]
    chosen = await harness.click_button(DEFAULT_USER_ID, force_message, "Всё равно сохранить")
    combined = " ".join(m.text or "" for m in chosen)
    assert "Сохранить запись?" in combined
    assert "SuperSport" in combined  # проект виден в превью

    await confirm_last(harness, DEFAULT_USER_ID, chosen)
    await session.commit()

    from jarvis.domain.enums import Sensitivity
    from jarvis.repositories.projects import ProjectRepository

    project = await ProjectRepository(session).find_by_name("SuperSport")
    assert project is not None

    items = await KnowledgeRepository(session).list_recent()
    assert len(items) == 1
    assert items[0].sensitivity == Sensitivity.SENSITIVE
    assert items[0].project_id == project.id


# 15. Жалоба на ассистента: достоверная гипотеза — в backlog, слабая — отброшена.
async def test_scenario_improvement_hypothesis_recorded(classic_settings, session_factory, session):
    ai = FakeProvider()
    ai.queue(
        Interpretation(
            intent="smalltalk",
            reply="Понял, записал в улучшения.",
            confidence=0.6,
            improvement_hypothesis={
                "category": "missing_capability",
                "title": "Нет напоминаний по срокам",
                "summary": "Пользователь ожидает напоминаний о дедлайнах",
                "confidence": 0.9,
                "evidence": "ты не напоминаешь мне про дедлайны",
            },
        )
    )
    ai.queue(
        Interpretation(
            intent="smalltalk",
            reply="Хорошо.",
            improvement_hypothesis={
                "category": "ux_friction",
                "title": "Слишком много подтверждений",
                "summary": "Возможно, подтверждения утомляют",
                "confidence": 0.2,
            },
        )
    )
    harness = await make_harness(classic_settings, session_factory, ai=ai)

    await harness.send_text(DEFAULT_USER_ID, "Ты не напоминаешь мне про дедлайны")
    await harness.send_text(DEFAULT_USER_ID, "Может, и ничего, не знаю")
    await session.commit()

    items = await ImprovementRepository(session).list_items()
    assert len(items) == 1
    assert items[0].source == ImprovementSource.LLM_HYPOTHESIS
    assert items[0].category == ImprovementCategory.MISSING_CAPABILITY
    assert items[0].confidence == 0.9
    assert items[0].occurrence_count == 1


# 16. Технический сбой обработки апдейта автоматически попадает в backlog.
async def test_scenario_technical_error_recorded(classic_settings, session_factory, session):
    harness = await make_harness(classic_settings, session_factory)

    crash_router = Router()

    @crash_router.message(lambda message: (message.text or "").strip() == "/crash-test")
    async def crash(message: Message) -> None:
        raise RuntimeError("тестовый сбой e2e")

    harness.dispatcher.sub_routers.insert(0, crash_router)

    await harness.send_text(DEFAULT_USER_ID, "/crash-test")
    await session.commit()

    items = await ImprovementRepository(session).list_items()
    assert len(items) == 1
    item = items[0]
    assert item.source == ImprovementSource.TECHNICAL_EVENT
    assert item.category == ImprovementCategory.FAILURE
    assert item.component == "telegram_bot"
    assert "RuntimeError" in item.title
    assert item.diagnostic_context["update_type"] == "message"
    assert "тестовый сбой e2e" in item.summary
    assert item.diagnostic_context["traceback"]
    assert item.correlation_id == item.diagnostic_context["correlation_id"]


# 17. /diagnostics: сводка → список → карточка → статус → удаление с подтверждением.
async def test_scenario_diagnostics_backlog_flow(classic_settings, session_factory, session):
    item = await ImprovementService(session, classic_settings).record_failure(
        title="ValueError: callback data too long",
        summary="Кнопка не отправилась",
        component="telegram_bot",
    )
    item_id = item.id
    await session.commit()

    harness = await make_harness(classic_settings, session_factory)
    messages = await harness.send_text(DEFAULT_USER_ID, "/diagnostics")
    combined = " ".join(m.text or "" for m in messages)
    assert "Backlog улучшений: 1 записей (открытых: 1)" in combined
    summary_message = keyboard_messages(messages)[-1]

    # Список открытых записей (edit_text правит то же сообщение).
    listed = await harness.click_button(DEFAULT_USER_ID, summary_message, "Открытые")
    assert listed == []
    list_view = harness.current(summary_message)
    assert "ValueError: callback data too long" in (list_view.text or "")

    # Карточка записи.
    await harness.click_button(DEFAULT_USER_ID, list_view, "1. ValueError")
    card_view = harness.current(summary_message)
    assert "Повторов: 1" in (card_view.text or "")
    assert "Кнопка не отправилась" in (card_view.text or "")

    # Смена статуса прямо из карточки.
    await harness.click_button(DEFAULT_USER_ID, card_view, "Разобран")
    await session.commit()
    session.expire_all()
    refreshed = await ImprovementRepository(session).get(item_id)
    assert refreshed.status == ImprovementStatus.REVIEWED

    # Удаление только после подтверждения.
    confirm = await harness.click_button(DEFAULT_USER_ID, card_view, "Удалить")
    assert any("безвозвратно" in (m.text or "") for m in confirm)
    confirm_message = keyboard_messages(confirm)[-1]
    await harness.click_button(DEFAULT_USER_ID, confirm_message, "Да, удалить")
    await session.commit()
    session.expire_all()
    assert await ImprovementRepository(session).get(item_id) is None


# 18. /export_issues: файлы MD+JSON без секретов уходят в чат.
async def test_scenario_export_issues(classic_settings, session_factory, session, tmp_path):
    classic_settings.export_dir = tmp_path / "exports"
    await ImprovementService(session, classic_settings).record_failure(
        title="Сбой экспорта",
        summary="Из лога утёк токен: password=hunter2secret",
        component="telegram_bot",
    )
    await session.commit()

    harness = await make_harness(classic_settings, session_factory)
    messages = await harness.send_text(DEFAULT_USER_ID, "/export_issues")
    assert any("Экспорт готов" in (m.text or "") for m in messages)
    documents = [m for m in messages if m.document is not None]
    assert len(documents) == 2

    files = sorted((tmp_path / "exports").glob("jarvis_issues_*"))
    assert [file.suffix for file in files] == [".json", ".md"]
    markdown = next(file for file in files if file.suffix == ".md").read_text(
        encoding="utf-8"
    )
    payload = json.loads(
        next(file for file in files if file.suffix == ".json").read_text(encoding="utf-8")
    )
    assert "Сбой экспорта" in markdown
    assert "hunter2secret" not in markdown
    assert payload["count"] == 1
    assert "hunter2secret" not in json.dumps(payload, ensure_ascii=False)


# 19. Типизированный UI: LLM просит кнопки, сервер собирает их по allow-list.
async def test_scenario_typed_ui_quick_actions(classic_settings, session_factory, session):
    await TaskService(session).create_from_draft(
        TaskDraft(title="Позвонить в сервис", bucket=TaskBucket.TODAY)
    )
    await session.commit()

    ai = FakeProvider()
    ai.queue(
        Interpretation(
            intent="smalltalk",
            reply="Привет! Вот что могу:",
            ui_request={
                "component": "quick_actions",
                "actions": ["tasks_today", "search"],
            },
        )
    )
    harness = await make_harness(classic_settings, session_factory, ai=ai)
    messages = await harness.send_text(DEFAULT_USER_ID, "Привет, что ты умеешь?")

    reply_message = keyboard_messages(messages)[-1]
    labels = [
        button.text
        for row in reply_message.reply_markup.inline_keyboard
        for button in row
    ]
    assert "🔸 Задачи на сегодня" in labels
    assert "🔎 Поиск" in labels

    # Кнопка ведёт в настоящий сценарий списка задач.
    await harness.click_button(DEFAULT_USER_ID, reply_message, "Задачи на сегодня")
    list_view = harness.current(reply_message)
    assert "Позвонить в сервис" in (list_view.text or "")


# 20. «Выведи задачи на <дата>» — детерминированный список по календарю.
async def test_scenario_tasks_on_named_date(classic_settings, session_factory, session):
    tz = classic_settings.tz
    due = (datetime.now(tz) + timedelta(days=3)).replace(
        hour=12, minute=0, second=0, microsecond=0
    )
    await TaskService(session).create_from_draft(TaskDraft(title="Визит к врачу", due_at=due))
    await TaskService(session).create_from_draft(TaskDraft(title="Другая задача"))
    await session.commit()

    harness = await make_harness(classic_settings, session_factory, ai=FakeProvider())
    messages = await harness.send_text(
        DEFAULT_USER_ID, f"Выведи мне задачи на {due.strftime('%d.%m.%Y')}"
    )
    combined = " ".join(m.text or "" for m in messages)
    assert f"Задачи на {due.strftime('%d.%m.%Y')}" in combined
    assert "Визит к врачу" in combined
    assert "Другая задача" not in combined


# 21. Категории-папки задач: просмотр задач по категории.
async def test_scenario_tasks_by_category(classic_settings, session_factory, session):
    category = await CategoryRepository(session).create("Рабочие")
    await TaskService(session).create_from_draft(
        TaskDraft(title="Отчёт по УВ", category_id=category.id)
    )
    await session.commit()

    harness = await make_harness(classic_settings, session_factory, ai=FakeProvider())
    messages = await harness.send_text(DEFAULT_USER_ID, "/tasks")
    menu_message = keyboard_messages(messages)[-1]

    picked_category = await harness.click_button(
        DEFAULT_USER_ID, menu_message, "По категориям"
    )
    categories_message = keyboard_messages(picked_category)[-1]
    picked = await harness.click_button(DEFAULT_USER_ID, categories_message, "Рабочие")
    assert picked  # прислано «Выберите задачу:»
    assert "Отчёт по УВ" in (harness.current(categories_message).text or "")


# 22. Категории документов: назначение из карточки и просмотр по категории.
async def test_scenario_document_category_flow(classic_settings, session_factory, session):
    harness = await make_harness(classic_settings, session_factory, ai=FakeProvider())
    messages = await harness.send_document(
        DEFAULT_USER_ID, filename="passport.pdf", content=b"%PDF-1.4 data"
    )
    await confirm_last(harness, DEFAULT_USER_ID, messages)
    await session.commit()
    session.expire_all()

    assert len(await DocumentRepository(session).list_active()) == 1

    messages = await harness.send_text(DEFAULT_USER_ID, "/documents")
    list_message = keyboard_messages(messages)[-1]
    card_messages = await harness.click_button(DEFAULT_USER_ID, list_message, "1. passport")
    actions_message = keyboard_messages(card_messages)[-1]

    picker_messages = await harness.click_button(
        DEFAULT_USER_ID, actions_message, "Категория"
    )
    picker_message = keyboard_messages(picker_messages)[-1]
    proposal = await harness.click_button(DEFAULT_USER_ID, picker_message, "Работа")
    assert any("Обновить документ?" in (m.text or "") for m in proposal)
    await confirm_last(harness, DEFAULT_USER_ID, proposal)
    await session.commit()
    session.expire_all()

    document = (await DocumentRepository(session).list_active())[0]
    category = await CategoryRepository(session).find_by_name("Работа")
    assert document.category_id == category.id

    # Просмотр по категории показывает документ.
    messages = await harness.send_text(DEFAULT_USER_ID, "/documents")
    list_message = keyboard_messages(messages)[-1]
    category_list = await harness.click_button(
        DEFAULT_USER_ID, list_message, "По категориям"
    )
    category_menu = keyboard_messages(category_list)[-1]
    filtered = await harness.click_button(DEFAULT_USER_ID, category_menu, "Работа")
    combined = " ".join(m.text or "" for m in filtered)
    assert "passport" in combined
