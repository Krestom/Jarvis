"""E2E агента: обращение по имени, контекст, подтверждения, реальные данные."""

from __future__ import annotations

import asyncio
import json
import re

import pytest

from jarvis.ai.fake import FakeProvider
from jarvis.ai.schemas import Interpretation, PlanStep, ScreenRequest
from jarvis.conversation.flows import Flow
from jarvis.conversation.state import ConversationStore
from jarvis.domain.enums import KnowledgeSource, KnowledgeType, TaskBucket
from jarvis.repositories.comments import TaskCommentRepository
from jarvis.repositories.conversation import ConversationRepository
from jarvis.repositories.documents import DocumentRepository
from jarvis.repositories.improvements import ImprovementRepository
from jarvis.repositories.knowledge import KnowledgeRepository
from jarvis.repositories.tasks import TaskRepository
from jarvis.services.drafts import TaskDraft
from jarvis.services.tasks import TaskService
from tests.e2e.harness import DEFAULT_USER_ID
from tests.e2e.test_scenarios import confirm_last, keyboard_messages, make_harness

pytestmark = pytest.mark.integration


async def test_agent_invocation_precedence_and_real_data(
    test_settings, session_factory, session
):
    await TaskService(session).create_from_draft(TaskDraft(title="Починить сервер"))
    await session.commit()

    ai = FakeProvider()
    ai.queue_plan(PlanStep(type="tool_call", tool="list_tasks", args={"view": "no_group"}))
    ai.queue_plan(PlanStep(type="reply", text="Нашёл: «Починить сервер»."))
    harness = await make_harness(test_settings, session_factory, ai=ai)

    messages = await harness.send_text(DEFAULT_USER_ID, "Джарвис, какие у меня задачи?")
    combined = " ".join(m.text or "" for m in messages)
    assert "Нашёл: «Починить сервер»." in combined
    # Агент получил настоящие данные из БД (результат инструмента в промпте).
    assert any("Починить сервер" in call for call in ai.plan_calls[1:])

    # «Найди ...» — детерминированный поиск; обращение по имени его перебивает.
    await KnowledgeRepository(session).create(
        type=KnowledgeType.NOTE,
        title="Резервное копирование",
        content="Инструкция по бэкапу сервера",
        source_type=KnowledgeSource.TEXT,
    )
    await session.commit()
    ai.queue_plan(PlanStep(type="reply", text="Проверил знания: инструкция есть."))
    messages = await harness.send_text(
        DEFAULT_USER_ID, "Джарвис, найди инструкцию резервное копирование"
    )
    combined = " ".join(m.text or "" for m in messages)
    assert "Проверил знания: инструкция есть." in combined
    assert "Результаты по запросу" not in combined


async def test_agent_invocation_case_variants(test_settings, session_factory, session):
    ai = FakeProvider()
    for _ in range(3):
        ai.queue_plan(PlanStep(type="reply", text="Слушаю."))
    harness = await make_harness(test_settings, session_factory, ai=ai)

    for text in ("Jarvis, привет", "джарвис, привет", "JARVIS, ПРИВЕТ"):
        messages = await harness.send_text(DEFAULT_USER_ID, text)
        assert any("Слушаю." in (m.text or "") for m in messages), text
    assert len(ai.plan_calls) == 3


async def test_unknown_intent_goes_to_agent_not_dead_end(
    test_settings, session_factory, session
):
    ai = FakeProvider()
    ai.queue(Interpretation(intent="unknown", confidence=0.1))
    ai.queue_plan(PlanStep(type="reply", text="Помогу: уточните, что нужно сделать."))
    harness = await make_harness(test_settings, session_factory, ai=ai)

    messages = await harness.send_text(DEFAULT_USER_ID, "Мама мыла раму и всё такое")
    combined = " ".join(m.text or "" for m in messages)
    assert "Помогу: уточните" in combined
    assert "Не понял сообщение" not in combined


async def test_backlog_mentions_route_to_agent_without_invocation(
    test_settings, session_factory, session
):
    """«Запиши в бэклог ...» — пожелание к Jarvis, а не задача (регрессия)."""
    ai = FakeProvider()
    ai.queue_plan(PlanStep(type="tool_call", tool="list_improvements", args={"status": "open"}))
    ai.queue_plan(
        PlanStep(
            type="tool_call",
            tool="report_capability_gap",
            args={
                "title": "Уведомления о записях в диагностику",
                "summary": "Хочу видеть уведомления, когда что-то пишется в диагностику",
            },
        )
    )
    ai.queue_plan(
        PlanStep(type="reply", text="Записал в backlog: уведомления о записях в диагностику.")
    )
    harness = await make_harness(test_settings, session_factory, ai=ai)

    messages = await harness.send_text(
        DEFAULT_USER_ID, "Запиши в бэклог: хочу уведомления о записях в диагностику"
    )
    combined = " ".join(m.text or "" for m in messages)
    assert "Записал в backlog" in combined
    assert "Создать задачу?" not in combined  # не задача
    assert "Сохранить запись?" not in combined  # не заметка
    assert ai.calls == []  # обычная интерпретация не вызывалась
    await session.commit()

    items = await ImprovementRepository(session).list_items(limit=10)
    assert [item.title for item in items] == ["Уведомления о записях в диагностику"]

    # «Выведи бэклог» (без имени) тоже уходит агенту.
    ai.queue_plan(PlanStep(type="tool_call", tool="list_improvements", args={"status": "open"}))
    ai.queue_plan(PlanStep(type="reply", text="Открытых записей: 1."))
    messages = await harness.send_text(DEFAULT_USER_ID, "Выведи бэклог")
    assert any("Открытых записей: 1." in (m.text or "") for m in messages)


async def test_agent_entity_choice_updates_conversation_context(
    test_settings, session_factory, session
):
    task_a = await TaskService(session).create_from_draft(TaskDraft(title="Починить сервер"))
    task_b = await TaskService(session).create_from_draft(TaskDraft(title="Обновить документацию"))
    await session.commit()

    ai = FakeProvider()
    ai.queue_plan(PlanStep(type="tool_call", tool="list_tasks", args={"view": "no_group"}))
    ai.queue_plan(
        PlanStep(
            type="reply",
            text="Нашёл две задачи — какую уточнить?",
            ui={
                "component": "entity_choice",
                "entity_kind": "task",
                "entity_ids": [str(task_b), str(task_a)],
                "prompt": "Какую задачу имели в виду?",
            },
        )
    )
    harness = await make_harness(test_settings, session_factory, ai=ai)

    messages = await harness.send_text(DEFAULT_USER_ID, "Джарвис, какие задачи без срока?")
    choices_message = keyboard_messages(messages)[-1]
    labels = [
        button.text for row in choices_message.reply_markup.inline_keyboard for button in row
    ]
    assert any("Обновить документацию" in (label or "") for label in labels)

    picked = await harness.click_button(DEFAULT_USER_ID, choices_message, "1. ")
    assert "Выбрал задачу" in (harness.current(choices_message).text or "")
    assert any("Что с ним сделать" in (m.text or "") for m in picked)
    await session.commit()
    session.expire_all()
    await session.commit()
    session.expire_all()

    store = ConversationStore(ConversationRepository(session), test_settings)
    state = await store.get_active(DEFAULT_USER_ID)
    assert state is not None and state.flow == "agent_context"
    assert state.data["agent_selected"]["id"] == str(task_b)

    # Продолжение без имени: состояние AGENT_CONTEXT ведёт к агенту,
    # выбранная задача уже в контексте — повторять её не нужно.
    ai.queue_plan(
        PlanStep(
            type="tool_call",
            tool="add_comment",
            args={"task_id": str(task_b), "content": "Проверил конфигурацию"},
        )
    )
    ai.queue_plan(PlanStep(type="reply", text="Подготовил комментарий — подтвердите кнопкой."))
    messages = await harness.send_text(DEFAULT_USER_ID, "Добавь комментарий: проверил конфигурацию")
    assert any("Подготовил комментарий" in (m.text or "") for m in messages)
    assert any("Выбранная ранее сущность" in call for call in ai.plan_calls)
    # До подтверждения комментария нет.
    assert await TaskCommentRepository(session).list_for_task(task_b) == []

    await confirm_last(harness, DEFAULT_USER_ID, messages)
    await session.commit()
    session.expire_all()
    comments = await TaskCommentRepository(session).list_for_task(task_b)
    assert [comment.content for comment in comments] == ["Проверил конфигурацию"]


async def test_agent_delete_requires_button_confirmation(
    test_settings, session_factory, session
):
    task_id = await TaskService(session).create_from_draft(
        TaskDraft(title="Удалить по кнопке")
    )
    await session.commit()

    ai = FakeProvider()
    ai.queue_plan(PlanStep(type="tool_call", tool="delete_task", args={"task_id": str(task_id)}))
    ai.queue_plan(
        PlanStep(type="reply", text="Отправил подтверждение удаления — нажмите «Подтвердить».")
    )
    harness = await make_harness(test_settings, session_factory, ai=ai)

    messages = await harness.send_text(DEFAULT_USER_ID, "Джарвис, удали задачу про кнопку")
    assert any("подтверждение удаления" in (m.text or "") for m in messages)
    # До подтверждения задача жива.
    assert await TaskRepository(session).get(task_id) is not None

    await confirm_last(harness, DEFAULT_USER_ID, messages)
    await session.commit()
    session.expire_all()
    assert await TaskRepository(session).get(task_id) is None


async def test_agent_updates_knowledge_note_with_confirmation(
    test_settings, session_factory, session
):
    """Агент правит заметку (в т.ч. чувствительную) — превью + кнопка."""
    from jarvis.domain.enums import KnowledgeSource, KnowledgeType, Sensitivity

    item = await KnowledgeRepository(session).create(
        type=KnowledgeType.NOTE,
        title="Пароль от сервера",
        content="старый пароль",
        source_type=KnowledgeSource.TEXT,
        sensitivity=Sensitivity.SENSITIVE,
    )
    await session.commit()

    ai = FakeProvider()
    ai.queue_plan(PlanStep(type="tool_call", tool="get_knowledge", args={"item_id": str(item.id)}))
    ai.queue_plan(
        PlanStep(
            type="tool_call",
            tool="update_knowledge",
            args={"item_id": str(item.id), "content": "новый пароль"},
        )
    )
    ai.queue_plan(PlanStep(type="reply", text="Обновил заметку — подтвердите кнопкой."))
    harness = await make_harness(test_settings, session_factory, ai=ai)

    messages = await harness.send_text(
        DEFAULT_USER_ID, "Джарвис, в заметке про сервер поменяй пароль"
    )
    combined = " ".join(m.text or "" for m in messages)
    assert "Изменить запись?" in combined  # превью операции
    assert "старый пароль" in " ".join(call for call in ai.plan_calls)  # агент видит sensitive
    # До подтверждения содержимое прежнее.
    assert (await KnowledgeRepository(session).get(item.id)).content == "старый пароль"

    await confirm_last(harness, DEFAULT_USER_ID, messages)
    await session.commit()
    session.expire_all()
    items = await KnowledgeRepository(session).list_recent()
    assert len(items) == 1
    assert items[0].content == "новый пароль"
    assert items[0].sensitivity == Sensitivity.SENSITIVE


async def test_agent_multiple_operations_each_get_a_button(
    test_settings, session_factory, session
):
    """Две мутации в одном запросе — два превью с кнопками (ни одна не теряется)."""
    task_id = await TaskService(session).create_from_draft(TaskDraft(title="Существующая"))
    await session.commit()

    ai = FakeProvider()
    ai.queue_plan(
        PlanStep(
            type="tool_call",
            tool="add_comment",
            args={"task_id": str(task_id), "content": "первый"},
        )
    )
    ai.queue_plan(
        PlanStep(
            type="tool_call",
            tool="create_task",
            args={"title": "Новая задача", "due_at": "2026-10-20T12:00:00+03:00"},
        )
    )
    ai.queue_plan(PlanStep(type="reply", text="Подготовил две операции."))
    harness = await make_harness(test_settings, session_factory, ai=ai)

    messages = await harness.send_text(
        DEFAULT_USER_ID, "Джарвис, добавь комментарий и создай задачу"
    )
    combined = " ".join(m.text or "" for m in messages)
    assert "Добавить комментарий?" in combined
    assert "Создать задачу?" in combined
    keyboards = keyboard_messages(messages)
    assert len(keyboards) == 2  # на каждую операцию — свои кнопки

    for keyboard in keyboards:
        await harness.click_button(DEFAULT_USER_ID, keyboard, "Подтвердить")
    await session.commit()
    session.expire_all()
    comments = await TaskCommentRepository(session).list_for_task(task_id)
    assert [comment.content for comment in comments] == ["первый"]
    assert await TaskRepository(session).search("Новая задача")


async def test_agent_task_without_due_asks_deadline_first(
    test_settings, session_factory, session
):
    """Задача без срока: сначала вопрос про срок, превью — только после ответа."""
    ai = FakeProvider()
    ai.queue_plan(PlanStep(type="tool_call", tool="create_task", args={"title": "Полить цветы"}))
    ai.queue_plan(PlanStep(type="reply", text="Подготовил задачу."))
    harness = await make_harness(test_settings, session_factory, ai=ai)

    messages = await harness.send_text(DEFAULT_USER_ID, "Джарвис, заведи задачу: полить цветы")
    combined = " ".join(m.text or "" for m in messages)
    assert "Нужен ли срок" in combined
    assert "Создать задачу?" not in combined  # превью ещё не отправлено

    messages = await harness.send_text(DEFAULT_USER_ID, "без срока")
    combined = " ".join(m.text or "" for m in messages)
    assert "Создать задачу?" in combined
    assert keyboard_messages(messages)

    await confirm_last(harness, DEFAULT_USER_ID, messages)
    await session.commit()
    session.expire_all()
    tasks = await TaskRepository(session).search("Полить цветы")
    assert tasks and tasks[0].due_at is None


async def test_plain_text_handled_by_agent_without_invocation(
    test_settings, session_factory, session
):
    """Весь текст уходит агенту, даже без обращения по имени."""
    ai = FakeProvider()
    ai.queue_plan(
        PlanStep(
            type="tool_call",
            tool="create_task",
            args={"title": "Полить цветы", "due_at": "2026-10-20T12:00:00+03:00"},
        )
    )
    ai.queue_plan(PlanStep(type="reply", text="Подготовил задачу — подтвердите кнопкой."))
    harness = await make_harness(test_settings, session_factory, ai=ai)

    messages = await harness.send_text(DEFAULT_USER_ID, "поставь задачу: полить цветы")
    combined = " ".join(m.text or "" for m in messages)
    assert "Создать задачу?" in combined


async def test_agent_opens_tasks_screen(test_settings, session_factory, session):
    await TaskService(session).create_from_draft(
        TaskDraft(title="Починить сервер", bucket=TaskBucket.WEEK)
    )
    await session.commit()
    ai = FakeProvider()
    ai.queue_plan(
        PlanStep(
            type="reply",
            text="Открываю список задач.",
            ui=ScreenRequest(component="screen", name="tasks", params={"view": "week"}),
        )
    )
    harness = await make_harness(test_settings, session_factory, ai=ai)

    messages = await harness.send_text(DEFAULT_USER_ID, "покажи задачи на неделю")
    combined = " ".join(m.text or "" for m in messages)
    assert "Задачи на этой неделе" in combined
    assert "Починить сервер" in combined
    assert keyboard_messages(messages)  # кликабельные карточки


async def test_agent_opens_search_screen(test_settings, session_factory, session):
    await KnowledgeRepository(session).create(
        type=KnowledgeType.NOTE,
        title="Резервное копирование",
        content="Инструкция по бэкапу сервера",
        source_type=KnowledgeSource.TEXT,
    )
    await session.commit()
    ai = FakeProvider()
    ai.queue_plan(
        PlanStep(
            type="reply",
            text="Ищу в базе.",
            ui=ScreenRequest(
                component="screen", name="search", params={"query": "резервное копирование"}
            ),
        )
    )
    harness = await make_harness(test_settings, session_factory, ai=ai)

    messages = await harness.send_text(DEFAULT_USER_ID, "найди инструкцию про бэкап")
    combined = " ".join(m.text or "" for m in messages)
    assert "Результаты по запросу" in combined
    assert "Резервное копирование" in combined


async def test_agent_opens_all_tasks_screen(test_settings, session_factory, session):
    await TaskService(session).create_from_draft(
        TaskDraft(title="Починить сервер", bucket=TaskBucket.WEEK)
    )
    await TaskService(session).create_from_draft(
        TaskDraft(title="Разобрать полку", bucket=None)
    )
    await session.commit()
    ai = FakeProvider()
    ai.queue_plan(
        PlanStep(
            type="reply",
            text="Открываю все задачи.",
            ui=ScreenRequest(component="screen", name="tasks", params={"view": "all"}),
        )
    )
    harness = await make_harness(test_settings, session_factory, ai=ai)

    messages = await harness.send_text(DEFAULT_USER_ID, "покажи все задачи списком")
    combined = " ".join(m.text or "" for m in messages)
    assert "Все задачи" in combined
    assert "Починить сервер" in combined
    assert "Разобрать полку" in combined
    assert keyboard_messages(messages)  # кликабельные карточки


async def test_agent_exports_chat_as_files(test_settings, session_factory, session):
    ai = FakeProvider()
    ai.queue_plan(PlanStep(type="tool_call", tool="export_chat", args={}))
    ai.queue_plan(PlanStep(type="reply", text="Готово: журнал за сегодня во вложениях."))
    harness = await make_harness(test_settings, session_factory, ai=ai)

    messages = await harness.send_text(DEFAULT_USER_ID, "экспортируй чат за сегодня")
    combined = " ".join(m.text or "" for m in messages)
    assert "Готово" in combined
    sent = [m for m in messages if m.document is not None]
    assert len(sent) == 2
    names = sorted(m.document.file_name for m in sent)
    assert names[0].startswith("jarvis_chat_") and names[0].endswith(".json")
    assert names[1].startswith("jarvis_chat_") and names[1].endswith(".md")


async def test_agent_sends_found_document_as_file(test_settings, session_factory, session):
    ai = FakeProvider()
    harness = await make_harness(test_settings, session_factory, ai=ai)

    messages = await harness.send_document(
        DEFAULT_USER_ID, filename="passport.pdf", content=b"%PDF-1.4 data"
    )
    await confirm_last(harness, DEFAULT_USER_ID, messages)
    await session.commit()
    document = (await DocumentRepository(session).list_active())[0]

    ai.queue_plan(
        PlanStep(
            type="tool_call",
            tool="send_document",
            args={"document_id": str(document.id)},
        )
    )
    ai.queue_plan(PlanStep(type="reply", text="Отправляю файл «passport»."))
    messages = await harness.send_text(DEFAULT_USER_ID, "выведи тестовый документ файлом")

    sent = [m for m in messages if m.document is not None]
    assert len(sent) == 1
    assert sent[0].document.file_name == "passport.pdf"


async def test_agent_changes_document_sensitivity(test_settings, session_factory, session):
    ai = FakeProvider()
    harness = await make_harness(test_settings, session_factory, ai=ai)

    messages = await harness.send_document(
        DEFAULT_USER_ID, filename="passport.pdf", content=b"%PDF-1.4 data"
    )
    await confirm_last(harness, DEFAULT_USER_ID, messages)
    await session.commit()
    document = (await DocumentRepository(session).list_active())[0]
    assert document.sensitivity.value == "sensitive"

    ai.queue_plan(
        PlanStep(
            type="tool_call",
            tool="update_document",
            args={"document_id": str(document.id), "sensitive": False},
        )
    )
    ai.queue_plan(PlanStep(type="reply", text="Подготовил смену чувствительности."))
    proposal = await harness.send_text(
        DEFAULT_USER_ID, "сделай тестовый документ нечувствительным"
    )
    combined = " ".join(m.text or "" for m in proposal)
    assert "Обновить документ?" in combined
    assert "чувствительная → обычная" in combined

    await confirm_last(harness, DEFAULT_USER_ID, proposal)
    await session.commit()
    await session.refresh(document)
    assert document.sensitivity.value == "normal"


async def test_agent_opens_documents_screen(test_settings, session_factory, session):
    ai = FakeProvider()
    harness = await make_harness(test_settings, session_factory, ai=ai)

    messages = await harness.send_document(
        DEFAULT_USER_ID, filename="passport.pdf", content=b"%PDF-1.4 data"
    )
    await confirm_last(harness, DEFAULT_USER_ID, messages)
    await session.commit()

    ai.queue_plan(
        PlanStep(
            type="reply",
            text="Открываю список документов.",
            ui=ScreenRequest(component="screen", name="documents", params={}),
        )
    )
    messages = await harness.send_text(DEFAULT_USER_ID, "покажи список документов")
    combined = " ".join(m.text or "" for m in messages)
    assert "Документы" in combined
    assert "passport" in combined
    list_message = keyboard_messages(messages)[-1]

    # Кнопка из настоящего списка открывает карточку в том же сообщении.
    card = await harness.click_button(DEFAULT_USER_ID, list_message, "1. passport")
    assert card == []  # карточка правит сообщение, новых не шлёт
    card_text = harness.current(list_message).text or ""
    assert "Документ:" in card_text
    assert "passport" in card_text


async def test_agent_renames_document(test_settings, session_factory, session):
    ai = FakeProvider()
    harness = await make_harness(test_settings, session_factory, ai=ai)

    messages = await harness.send_document(
        DEFAULT_USER_ID, filename="passport.pdf", content=b"%PDF-1.4 data"
    )
    await confirm_last(harness, DEFAULT_USER_ID, messages)
    await session.commit()
    document = (await DocumentRepository(session).list_active())[0]

    ai.queue_plan(
        PlanStep(
            type="tool_call",
            tool="update_document",
            args={"document_id": str(document.id), "title": "Регалетта"},
        )
    )
    ai.queue_plan(PlanStep(type="reply", text="Подготовил переименование."))
    proposal = await harness.send_text(
        DEFAULT_USER_ID, "переименуй документ в «Регалетта»"
    )
    combined = " ".join(m.text or "" for m in proposal)
    assert "Обновить документ?" in combined
    assert "Регалетта" in combined

    await confirm_last(harness, DEFAULT_USER_ID, proposal)
    await session.commit()
    await session.refresh(document)
    assert document.title == "Регалетта"


async def test_agent_exports_backlog_as_files(test_settings, session_factory, session):
    from jarvis.services.improvements import ImprovementService

    await ImprovementService(session, test_settings).record_gap(
        title="Нет плана на отпуск", summary="Просили выгрузить бэклог файлом"
    )
    await session.commit()

    ai = FakeProvider()
    ai.queue_plan(PlanStep(type="tool_call", tool="export_issues", args={}))
    ai.queue_plan(PlanStep(type="reply", text="Готово: backlog во вложениях."))
    harness = await make_harness(test_settings, session_factory, ai=ai)

    messages = await harness.send_text(DEFAULT_USER_ID, "экспортируй backlog в файле")
    combined = " ".join(m.text or "" for m in messages)
    assert "Готово" in combined
    sent = [m for m in messages if m.document is not None]
    assert len(sent) == 2
    names = sorted(m.document.file_name for m in sent)
    assert names[0].startswith("jarvis_issues_") and names[0].endswith(".json")
    assert names[1].endswith(".md")


async def test_agent_opens_knowledge_screen(test_settings, session_factory, session):
    await KnowledgeRepository(session).create(
        type=KnowledgeType.NOTE,
        title="Резервное копирование",
        content="Инструкция по бэкапу сервера",
        source_type=KnowledgeSource.TEXT,
    )
    await session.commit()
    ai = FakeProvider()
    ai.queue_plan(
        PlanStep(
            type="reply",
            text="Открываю список знаний.",
            ui=ScreenRequest(component="screen", name="knowledge", params={}),
        )
    )
    harness = await make_harness(test_settings, session_factory, ai=ai)

    messages = await harness.send_text(DEFAULT_USER_ID, "покажи список заметок")
    combined = " ".join(m.text or "" for m in messages)
    assert "Знания" in combined
    assert "Резервное копирование" in combined
    list_message = keyboard_messages(messages)[-1]

    # Кнопка из настоящего списка открывает карточку записи в том же сообщении.
    card = await harness.click_button(
        DEFAULT_USER_ID, list_message, "1. Резервное копирование"
    )
    assert card == []
    card_text = harness.current(list_message).text or ""
    assert "Инструкция по бэкапу сервера" in card_text


async def test_agent_opens_document_card_with_live_buttons(
    test_settings, session_factory, session
):
    """Экран card: агент открывает карточку документа, кнопки на ней рабочие."""
    ai = FakeProvider()
    harness = await make_harness(test_settings, session_factory, ai=ai)

    messages = await harness.send_document(
        DEFAULT_USER_ID, filename="passport.pdf", content=b"%PDF-1.4 data"
    )
    await confirm_last(harness, DEFAULT_USER_ID, messages)
    await session.commit()
    document = (await DocumentRepository(session).list_active())[0]

    ai.queue_plan(
        PlanStep(
            type="reply",
            text="Вот карточка документа.",
            ui=ScreenRequest(
                component="screen",
                name="card",
                params={"kind": "document", "id": str(document.id)},
            ),
        )
    )
    messages = await harness.send_text(DEFAULT_USER_ID, "открой карточку документа")
    combined = " ".join(m.text or "" for m in messages)
    assert "Документ:" in combined
    card_message = keyboard_messages(messages)[-1]
    labels = [
        button.text for row in card_message.reply_markup.inline_keyboard for button in row
    ]
    assert any("Удалить" in label for label in labels)
    assert any("Получить файл" in label for label in labels)

    # Живая кнопка «🗑 Удалить» из карточки: превью + подтверждение.
    delete_messages = await harness.click_button(DEFAULT_USER_ID, card_message, "Удалить")
    assert any("Удалить документ?" in (m.text or "") for m in delete_messages)
    await confirm_last(harness, DEFAULT_USER_ID, delete_messages)
    await session.commit()
    assert await DocumentRepository(session).list_active() == []


async def test_agent_opens_task_card_screen(test_settings, session_factory, session):
    task_id = await TaskService(session).create_from_draft(TaskDraft(title="Починить сервер"))
    await session.commit()
    ai = FakeProvider()
    ai.queue_plan(
        PlanStep(
            type="reply",
            text="Вот карточка задачи.",
            ui=ScreenRequest(
                component="screen",
                name="card",
                params={"kind": "task", "id": str(task_id)},
            ),
        )
    )
    harness = await make_harness(test_settings, session_factory, ai=ai)

    messages = await harness.send_text(DEFAULT_USER_ID, "открой карточку задачи")
    combined = " ".join(m.text or "" for m in messages)
    assert "Задача:" in combined
    assert "Починить сервер" in combined
    card_message = keyboard_messages(messages)[-1]
    labels = [
        button.text for row in card_message.reply_markup.inline_keyboard for button in row
    ]
    assert any("Завершить" in label for label in labels)
    assert any("Комментарии" in label for label in labels)


async def test_agent_pages_task_list_by_seven(test_settings, session_factory, session):
    for index in range(1, 10):
        await TaskService(session).create_from_draft(TaskDraft(title=f"Задача {index:02d}"))
    await session.commit()

    ai = FakeProvider()
    ai.queue_plan(
        PlanStep(
            type="reply",
            text="Открываю все задачи.",
            ui=ScreenRequest(component="screen", name="tasks", params={"view": "all"}),
        )
    )
    harness = await make_harness(test_settings, session_factory, ai=ai)

    messages = await harness.send_text(DEFAULT_USER_ID, "покажи все задачи списком")
    page_one = " ".join(m.text or "" for m in messages)
    first_page = set(re.findall(r"Задача \d\d", page_one))
    assert len(first_page) == 7  # на странице ровно 7
    list_message = keyboard_messages(messages)[-1]

    # «➡️» правит то же сообщение и НЕ шлёт новое.
    next_messages = await harness.click_button(DEFAULT_USER_ID, list_message, "➡️")
    assert next_messages == []
    page_two = harness.current(list_message).text or ""
    second_page = set(re.findall(r"Задача \d\d", page_two))
    assert len(second_page) == 2
    all_titles = {f"Задача {index:02d}" for index in range(1, 10)}
    assert first_page | second_page == all_titles
    assert not (first_page & second_page)  # страницы не пересекаются

    # «⬅️» правит это же сообщение обратно.
    back_messages = await harness.click_button(DEFAULT_USER_ID, list_message, "⬅️")
    assert back_messages == []
    page_back = harness.current(list_message).text or ""
    assert set(re.findall(r"Задача \d\d", page_back)) == first_page


async def test_agent_keeps_last_turns_in_context(test_settings, session_factory, session):
    """Короткая память диалога: вторая реплика видит первую."""
    ai = FakeProvider()
    ai.queue_plan(PlanStep(type="reply", text="Уточни, нужен ли срок?"))
    ai.queue_plan(PlanStep(type="reply", text="Понял."))
    harness = await make_harness(test_settings, session_factory, ai=ai)

    await harness.send_text(DEFAULT_USER_ID, "поставь задачу пересадить цветок")
    await harness.send_text(DEFAULT_USER_ID, "срок не нужен, спасибо")

    assert len(ai.plan_calls) == 2
    assert "поставь задачу пересадить цветок" in ai.plan_calls[1]
    assert "Уточни, нужен ли срок?" in ai.plan_calls[1]


async def test_agent_quick_actions_keyboard_from_reply(    test_settings, session_factory, session
):
    ai = FakeProvider()
    ai.queue_plan(
        PlanStep(
            type="reply",
            text="Могу подсказать быстрые действия.",
            ui={"component": "quick_actions", "actions": ["tasks_today", "search"]},
        )
    )
    harness = await make_harness(test_settings, session_factory, ai=ai)

    messages = await harness.send_text(DEFAULT_USER_ID, "Джарвис, что ты умеешь?")
    reply_message = keyboard_messages(messages)[-1]
    labels = [
        button.text for row in reply_message.reply_markup.inline_keyboard for button in row
    ]
    assert "🔸 Задачи на сегодня" in labels
    assert "🔎 Поиск" in labels

    # Диагностика показывает последний агентский запрос.
    diagnostics = await harness.send_text(DEFAULT_USER_ID, "/diagnostics")
    combined = " ".join(m.text or "" for m in diagnostics)
    assert "AI-агент (последний запрос): completed" in combined


# 20. /export_chat: журнал диалога за день уходит файлами (MD + JSON).
async def test_scenario_chat_export(test_settings, session_factory, session, tmp_path):
    test_settings.export_dir = tmp_path / "exports"
    harness = await make_harness(test_settings, session_factory)

    await harness.send_text(DEFAULT_USER_ID, "/start")

    export_messages = await harness.send_text(DEFAULT_USER_ID, "/export_chat")
    assert any("Чат за" in (m.text or "") for m in export_messages)
    documents = [m for m in export_messages if m.document is not None]
    assert len(documents) == 2

    files = sorted((tmp_path / "exports").glob("jarvis_chat_*"))
    assert [file.suffix for file in files] == [".json", ".md"]
    markdown = next(file for file in files if file.suffix == ".md").read_text(
        encoding="utf-8"
    )
    # Записаны обе стороны: команда пользователя и ответ бота.
    assert "Пользователь (команда):" in markdown
    assert "/start" in markdown
    assert "Jarvis:" in markdown

    json_file = next(file for file in files if file.suffix == ".json")
    payload = json.loads(json_file.read_text(encoding="utf-8"))
    assert payload["count"] >= 2
    assert {message["direction"] for message in payload["messages"]} == {"in", "out"}


async def test_export_chat_reports_unwritable_directory(
    test_settings, session_factory, session, tmp_path
):
    """Если каталог выгрузок недоступен — бот честно отвечает, а не молчит."""
    blocker = tmp_path / "blocker"
    blocker.write_text("это файл, а не каталог", encoding="utf-8")
    test_settings.export_dir = blocker / "exports"  # mkdir упадёт: родитель — файл
    harness = await make_harness(test_settings, session_factory)

    messages = await harness.send_text(DEFAULT_USER_ID, "/export_chat")
    combined = " ".join(m.text or "" for m in messages)
    assert "Не удалось сохранить файлы экспорта" in combined


class _BlockingPlanner(FakeProvider):
    """Планировщик, зависающий до разрешения — для проверки /cancel."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def plan(self, messages, context):
        self.plan_calls.append("blocked")
        self.entered.set()
        await self.release.wait()
        return PlanStep(type="reply", text="Поздний ответ")


async def test_cancel_command_stops_running_agent(
    test_settings, session_factory, session
):
    planner = _BlockingPlanner()
    harness = await make_harness(test_settings, session_factory, ai=planner)

    run_task = asyncio.create_task(
        harness.send_text(DEFAULT_USER_ID, "Джарвис, займись долгой задачей")
    )
    await planner.entered.wait()

    cancel_messages = await harness.send_text(DEFAULT_USER_ID, "/cancel")
    assert any("Останавливаю текущий запрос" in (m.text or "") for m in cancel_messages)

    planner.release.set()
    agent_messages = await run_task
    combined = " ".join(m.text or "" for m in agent_messages)
    assert "Остановил выполнение по вашей команде" in combined
    assert "Поздний ответ" not in combined
    assert len(planner.plan_calls) == 1


# --- Регрессии роутинга (найдены в живом использовании 15.10) ----------------


async def test_stale_search_state_does_not_trap_messages(
    test_settings, session_factory, session
):
    """Залипшее старое состояние поиска (без step) не съедает обычные сообщения."""
    store = ConversationStore(ConversationRepository(session), test_settings)
    await store.set(DEFAULT_USER_ID, Flow.SEARCH_WAIT, data={"query": "джарвис", "page": 0})
    await TaskService(session).create_from_draft(
        TaskDraft(title="Обновить дашборд", bucket=TaskBucket.WEEK)
    )
    await session.commit()

    ai = FakeProvider()
    ai.queue_plan(PlanStep(type="tool_call", tool="list_tasks", args={"view": "week"}))
    ai.queue_plan(PlanStep(type="reply", text="На неделю: «Обновить дашборд»."))
    harness = await make_harness(test_settings, session_factory, ai=ai)

    messages = await harness.send_text(DEFAULT_USER_ID, "Какие у меня задачи на неделю?")
    combined = " ".join(m.text or "" for m in messages)
    assert "Обновить дашборд" in combined
    assert "Ничего не нашёл" not in combined


async def test_search_flow_releases_after_results(test_settings, session_factory, session):
    """После выдачи результатов следующий текст — обычное сообщение, а не поиск."""
    await KnowledgeRepository(session).create(
        type=KnowledgeType.NOTE,
        title="Резервное копирование",
        content="Инструкция по бэкапу сервера",
        source_type=KnowledgeSource.TEXT,
    )
    await TaskService(session).create_from_draft(
        TaskDraft(title="Починить сервер", bucket=TaskBucket.WEEK)
    )
    await session.commit()

    ai = FakeProvider()
    ai.queue_plan(PlanStep(type="tool_call", tool="list_tasks", args={"view": "week"}))
    ai.queue_plan(PlanStep(type="reply", text="На неделю: «Починить сервер»."))
    harness = await make_harness(test_settings, session_factory, ai=ai)

    # /search ждёт ввод — этот текст действительно ищется.
    await harness.send_text(DEFAULT_USER_ID, "/search")
    results = await harness.send_text(DEFAULT_USER_ID, "резервное копирование")
    assert any("Результаты по запросу" in (m.text or "") for m in results)

    # Следующее обычное сообщение обрабатывает агент.
    messages = await harness.send_text(DEFAULT_USER_ID, "Какие у меня задачи на неделю?")
    combined = " ".join(m.text or "" for m in messages)
    assert "Починить сервер" in combined
    assert "Ничего не нашёл" not in combined


async def test_jarvis_invocation_beats_pending_search(
    test_settings, session_factory, session
):
    """Обращение по имени работает даже когда поиск ждёт ввод."""
    store = ConversationStore(ConversationRepository(session), test_settings)
    await store.set(DEFAULT_USER_ID, Flow.SEARCH_WAIT, step="asked")
    await session.commit()

    ai = FakeProvider()
    ai.queue_plan(PlanStep(type="reply", text="Слушаю, Джарвис на связи."))
    harness = await make_harness(test_settings, session_factory, ai=ai)

    messages = await harness.send_text(DEFAULT_USER_ID, "Джарвис, привет!")
    combined = " ".join(m.text or "" for m in messages)
    assert "Слушаю, Джарвис на связи." in combined
    assert "Результаты по запросу" not in combined
    assert len(ai.plan_calls) == 1


async def test_agent_shows_backlog_on_request(test_settings, session_factory, session):
    """«Покажи бэклог» — агент читает реальный improvement backlog."""
    from jarvis.services.improvements import ImprovementService

    await ImprovementService(session, test_settings).record_gap(
        title="Нет экспорта задач в календарь",
        summary="Просили выгрузить задачи в календарь",
    )
    await session.commit()

    ai = FakeProvider()
    ai.queue_plan(PlanStep(type="tool_call", tool="list_improvements", args={}))
    ai.queue_plan(
        PlanStep(
            type="reply",
            text="В backlog одна запись: «Нет экспорта задач в календарь» (пробел, new).",
        )
    )
    harness = await make_harness(test_settings, session_factory, ai=ai)

    messages = await harness.send_text(DEFAULT_USER_ID, "Джарвис, покажи бэклог")
    combined = " ".join(m.text or "" for m in messages)
    assert "Нет экспорта задач в календарь" in combined
    # Инструмент получил настоящие данные из БД.
    assert any("Нет экспорта" in call for call in ai.plan_calls[1:])


async def test_agent_saves_credentials_through_confirmation(
    test_settings, session_factory, session
):
    """Креды через агента: pending-операция → кнопка → запись sensitive в проект."""
    ai = FakeProvider()
    ai.queue_plan(
        PlanStep(
            type="tool_call",
            tool="create_knowledge",
            args={
                "title": "Доступ к серверу Supersport",
                "content": "ssh 2.28.205.186, user deploy, пароль supersport",
                "project": "Supersport",
            },
        )
    )
    ai.queue_plan(
        PlanStep(type="reply", text="Отправил на подтверждение. Нажмите «Подтвердить».")
    )
    harness = await make_harness(test_settings, session_factory, ai=ai)

    messages = await harness.send_text(
        DEFAULT_USER_ID,
        "Джарвис, сохрани в проект Supersport креды для доступа по ssh: "
        "2.28.205.186, user deploy, пароль supersport",
    )
    assert any("Нажмите «Подтвердить»" in (m.text or "") for m in messages)
    assert await KnowledgeRepository(session).list_recent() == []

    await confirm_last(harness, DEFAULT_USER_ID, messages)
    await session.commit()
    session.expire_all()

    items = await KnowledgeRepository(session).list_recent()
    assert len(items) == 1
    assert items[0].title == "Доступ к серверу Supersport"
    assert items[0].sensitivity.value == "sensitive"
    assert items[0].project_id is not None
