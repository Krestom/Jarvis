"""Сквозные сценарии (spec §17) через настоящий Dispatcher и фейковый Telegram."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from aiogram.types import InlineKeyboardMarkup
from sqlalchemy import func, select

from jarvis.ai.fake import FakeProvider
from jarvis.ai.schemas import Interpretation
from jarvis.bot.app import create_dispatcher
from jarvis.db.models import KnowledgeItem, Task
from jarvis.documents.storage import EncryptedFileStorage
from jarvis.domain.enums import DocumentStatus, TaskBucket
from jarvis.repositories.documents import DocumentRepository
from jarvis.repositories.knowledge import KnowledgeRepository
from jarvis.repositories.secrets import SecretRepository
from jarvis.repositories.tasks import TaskRepository
from jarvis.services.catalog import CatalogService
from jarvis.services.drafts import TaskDraft
from jarvis.services.secrets import SecretService
from jarvis.services.tasks import TaskService
from jarvis.transcription.fake import FakeTranscription
from tests.e2e.harness import DEFAULT_USER_ID, BotHarness, FakeTelegramSession

pytestmark = pytest.mark.integration


async def make_harness(
    test_settings, session_factory, *, ai=None, transcription=None
) -> BotHarness:
    async with session_factory() as session:
        await CatalogService(session).ensure_defaults()
        await session.commit()
    dispatcher = create_dispatcher(
        test_settings,
        session_factory,
        ai or FakeProvider(),
        transcription,
    )
    harness = BotHarness(dispatcher, FakeTelegramSession())
    await harness.start()
    return harness


def keyboard_messages(messages) -> list:
    return [m for m in messages if isinstance(m.reply_markup, InlineKeyboardMarkup)]


async def confirm_last(harness: BotHarness, user_id: int, messages) -> list:
    confirmation = keyboard_messages(messages)
    assert confirmation, "no confirmation keyboard in messages"
    return await harness.click_button(user_id, confirmation[-1], "Подтвердить")


# 1. Текстовая мысль -> подтверждение -> запись находится поиском.
async def test_scenario_text_note_to_search(test_settings, session_factory, session):
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
    harness = await make_harness(test_settings, session_factory, ai=ai)

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
async def test_scenario_voice_task(test_settings, session_factory, session):
    ai = FakeProvider()
    tomorrow = (datetime.now(UTC) + timedelta(days=1)).replace(
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
        test_settings, session_factory, ai=ai, transcription=transcription
    )

    messages = await harness.send_voice(DEFAULT_USER_ID)
    assert any("Распознано" in (m.text or "") for m in messages)
    assert transcription.calls == ["voice.ogg"]

    await confirm_last(harness, DEFAULT_USER_ID, messages)
    await session.commit()

    tasks = await TaskRepository(session).list_week(datetime.now(UTC), test_settings.tz)
    assert len(tasks) == 1
    assert tasks[0].title == "Обновить инфраструктуру"


# 3. Сценарий с документом и восстановлением состояния после «рестарта».
async def test_scenario_document_flow_survives_restart(
    test_settings, session_factory, session
):
    ai = FakeProvider()
    ai.queue(
        Interpretation(
            intent="document_upload",
            entities={"category": "Документы"},
            confidence=0.9,
        )
    )
    harness = await make_harness(test_settings, session_factory, ai=ai)
    messages = await harness.send_text(
        DEFAULT_USER_ID, "Сейчас пришлю паспорт, сохрани его в личных документах"
    )
    assert any("Пришлите файл" in (m.text or "") for m in messages)

    # «Рестарт»: новый dispatcher и новая сессия Telegram, та же БД.
    harness2 = await make_harness(test_settings, session_factory, ai=FakeProvider())
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
        test_settings.document_storage_path, test_settings.document_keyring()
    )
    saved = storage.verify(document.storage_key, document.sha256)
    assert saved == content

    # В staging после подтверждения пусто.
    assert not list(storage._staging().iterdir())


# 4. Перенос срока: без подтверждения срок не меняется.
async def test_scenario_due_change_requires_confirmation(
    test_settings, session_factory, session
):
    task_id = await TaskService(session).create_from_draft(
        TaskDraft(title="Продлить домен", bucket=TaskBucket.TODAY)
    )
    await session.commit()

    harness = await make_harness(test_settings, session_factory, ai=FakeProvider())
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
    expected = (datetime.now(UTC) + timedelta(days=1)).astimezone(test_settings.tz)
    assert task.due_at.astimezone(test_settings.tz).date() == expected.date()


# 5. Отмена операции: запись не создаётся.
async def test_scenario_cancel_operation(test_settings, session_factory, session):
    ai = FakeProvider()
    ai.queue(
        Interpretation(
            intent="create_knowledge",
            entities={"title": "Черновик заметки", "content": "Не сохраняй это"},
            confidence=0.9,
        )
    )
    harness = await make_harness(test_settings, session_factory, ai=ai)
    messages = await harness.send_text(DEFAULT_USER_ID, "Запомни важную мысль")
    confirmation = keyboard_messages(messages)[-1]
    await harness.click_button(DEFAULT_USER_ID, confirmation, "Отменить")
    await session.commit()

    count = await session.scalar(select(func.count()).select_from(KnowledgeItem))
    assert count == 0


# 6. Неавторизованный пользователь не получает доступ.
async def test_scenario_unauthorized_user_is_ignored(test_settings, session_factory, session):
    harness = await make_harness(test_settings, session_factory, ai=FakeProvider())
    messages = await harness.send_text(9999, "Покажи мои секреты")
    assert messages == []
    count = await session.scalar(select(func.count()).select_from(KnowledgeItem))
    assert count == 0


# 7. Повторное нажатие подтверждения не дублирует операцию.
async def test_scenario_confirm_is_idempotent(test_settings, session_factory, session):
    ai = FakeProvider()
    ai.queue(
        Interpretation(
            intent="create_task",
            entities={"title": "Один раз"},
            confidence=0.9,
        )
    )
    harness = await make_harness(test_settings, session_factory, ai=ai)
    messages = await harness.send_text(DEFAULT_USER_ID, "Задача: один раз")
    confirmation = keyboard_messages(messages)[-1]

    await harness.click_button(DEFAULT_USER_ID, confirmation, "Подтвердить")
    await harness.click_button(DEFAULT_USER_ID, confirmation, "Подтвердить")
    await session.commit()

    count = await session.scalar(select(func.count()).select_from(Task))
    assert count == 1


# 8. Секреты шифруются, не показываются в превью и не ищутся.
async def test_scenario_secret_flow(test_settings, session_factory, session):
    harness = await make_harness(test_settings, session_factory, ai=FakeProvider())

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
    service = SecretService(session, test_settings)
    assert service.decrypt_value(secrets[0].encrypted_value) == "ssh-ed25519 AAAATESTKEY secret"

    # Поиск не находит секреты: значение не является знанием.
    results = await harness.send_text(DEFAULT_USER_ID, "найди AAAATESTKEY")
    assert any("Ничего не нашёл" in (m.text or "") for m in results)
