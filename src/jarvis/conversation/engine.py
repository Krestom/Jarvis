"""Диалоговый движок: маршрутизация сообщений, черновики, предложения операций."""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from html import escape as html_escape
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.ai import AIError, AIProvider, AIUnavailableError, InterpreterContext
from jarvis.ai.prompts import format_llm_text
from jarvis.ai.schemas import Interpretation
from jarvis.config.settings import Settings
from jarvis.conversation.dates import parse_human_date
from jarvis.conversation.flows import Flow
from jarvis.conversation.heuristics import (
    extract_search_query,
    first_line,
    is_question,
    looks_like_search,
)
from jarvis.conversation.preview import fmt_dt_local, render_preview
from jarvis.conversation.state import ConversationStore
from jarvis.domain.enums import (
    KnowledgeSource,
    KnowledgeType,
    PendingOpType,
    Sensitivity,
    TaskBucket,
    TaskPriority,
)
from jarvis.logging_setup import get_correlation_id
from jarvis.repositories.comments import TaskCommentRepository
from jarvis.repositories.conversation import ConversationRepository
from jarvis.repositories.documents import DocumentRepository
from jarvis.repositories.projects import ProjectRepository
from jarvis.repositories.tasks import TaskRepository
from jarvis.services.catalog import CatalogService
from jarvis.services.confirmation import ConfirmationService
from jarvis.services.drafts import KnowledgeDraft, TaskDraft
from jarvis.services.improvements import ImprovementService
from jarvis.services.search import SearchHit, SearchService
from jarvis.services.tasks import TASK_VIEWS, TaskService

if TYPE_CHECKING:
    from jarvis.agent.queue import UserSessionGate

log = logging.getLogger(__name__)

SEARCH_PAGE_SIZE = 7
TASK_LIST_LIMIT = 7  # как PAGE_SIZE в bot/keyboards.py (пагинация TaskListCB)
DOCUMENT_LIST_LIMIT = 7  # как PAGE_SIZE в bot/keyboards.py (пагинация DocListCB)

_TASK_INTENTS = ("create_task",)
_KNOWLEDGE_INTENTS = ("create_knowledge",)


def _ui_actions(interpretation: Interpretation) -> list[str]:
    """Действия типизированного UI-запроса (пусто, если его нет)."""
    if interpretation.ui_request is None:
        return []
    return list(interpretation.ui_request.actions)

TASK_VIEW_LABELS = {
    "today": "Задачи на сегодня",
    "week": "Задачи на этой неделе",
    "long_term": "Долгосрочные задачи",
    "someday": "Когда-нибудь",
    "no_group": "Без срока",
    "inbox": "Inbox",
    "all": "Все задачи",
    "done": "Выполненные",
}

# «Задачи на 19 октября»: глаголы-просьбы, для которых дата — фильтр списка,
# а не срок создания задачи.
_DATE_LIST_PREFIX = re.compile(r"(?i)^\s*(какие|что|выведи|покажи|дай|список|найди)\b")

# Ответы «срок не нужен» на вопрос «Нужен ли срок?» (задача без дедлайна).
NO_DUE_ANSWERS = ("нет", "не надо", "не нужно", "без срока", "без дедлайна")


@dataclass
class EngineOutcome:
    text: str
    # (id операции, текст превью) — на каждую операцию отдельное сообщение с кнопками.
    pending_ops: list[tuple[str, str]] = field(default_factory=list)
    search_hits: list[SearchHit] = field(default_factory=list)
    search_query: str | None = None
    search_page: int = 0
    menu: bool = False
    awaiting_document: bool = False
    awaiting_secret: bool = False
    force_save: bool = False
    ui_actions: list[str] = field(default_factory=list)
    entity_choices: list = field(default_factory=list)
    entity_choice_prompt: str | None = None
    task_choices: list = field(default_factory=list)
    task_choices_action: str = "comment_choose"
    task_choices_prompt: str | None = None
    # Файлы (документы, экспорт чата), которые агент отдаёт вместе с ответом.
    files: list = field(default_factory=list)
    # Документы для экрана «Список документов» (кнопки карточек).
    documents: list = field(default_factory=list)
    # Пагинация списка задач: (view, page, value, has_next) — как в /tasks.
    task_page: tuple[str, int, str, bool] | None = None
    # Карточка сущности, которую просит открыть агент: (kind, id).
    card: tuple[str, str] | None = None


class ConversationEngine:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        ai: AIProvider,
        *,
        gate: UserSessionGate | None = None,
        search_provider=None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._ai = ai
        self._gate = gate
        self._search_provider = search_provider
        self._store = ConversationStore(ConversationRepository(session), settings)
        self._confirmation = ConfirmationService(session, settings)
        self._catalog = CatalogService(session)
        self._projects = ProjectRepository(session)
        self._search = SearchService(session)
        self._improvements = ImprovementService(session, settings)

    # --- Публичные точки входа ---

    async def handle_text(
        self,
        telegram_user_id: int,
        text: str,
        *,
        source: KnowledgeSource = KnowledgeSource.TEXT,
    ) -> EngineOutcome:
        text = str(text or "").strip()
        if not text:
            return EngineOutcome("Пустое сообщение — нечего обрабатывать.", menu=self._menu_flag())

        state = await self._store.get_active(telegram_user_id)

        # Жёсткие пошаговые сценарии (значения секретов, файлы, редакторы):
        # они не сбиваются ни агента, ни поиском; выход — /cancel.
        if state is not None and state.flow == Flow.EDIT_PENDING:
            return await self._handle_edit(telegram_user_id, state, text)

        if state is not None and state.flow == Flow.CLARIFY_DRAFT:
            return await self._handle_clarify(telegram_user_id, state, text)

        if state is not None and state.flow == Flow.AWAIT_SECRET_VALUE:
            return await self._handle_secret_value(telegram_user_id, state, text)

        if state is not None and state.flow == Flow.AWAIT_DOCUMENT_UPLOAD:
            return EngineOutcome(
                "Я всё ещё жду файл по начатому сценарию. Пришлите документ, "
                "отправьте /cancel для отмены или задайте вопрос командой /help.",
                awaiting_document=True,
            )

        if state is not None and state.flow == Flow.CREATE_CATEGORY:
            await self._store.clear(telegram_user_id)
            return await self._propose_payload(
                telegram_user_id, PendingOpType.CREATE_CATEGORY, {"name": text[:120]}
            )

        if state is not None and state.flow == Flow.CREATE_PROJECT:
            await self._store.clear(telegram_user_id)
            return await self._propose_payload(
                telegram_user_id, PendingOpType.CREATE_PROJECT, {"name": text[:200]}
            )

        if state is not None and state.flow == Flow.EDIT_CATEGORY:
            category_id = state.data.get("category_id")
            await self._store.clear(telegram_user_id)
            if not category_id:
                return EngineOutcome("Категория потерялась, начните заново из /settings.")
            return await self._propose_payload(
                telegram_user_id,
                PendingOpType.RENAME_CATEGORY,
                {"category_id": category_id, "new_name": text[:120]},
            )

        if state is not None and state.flow == Flow.EDIT_TASK_DUE:
            task_id = str(state.data.get("task_id", ""))
            await self._store.clear(telegram_user_id)
            if not task_id:
                return EngineOutcome("Задача потерялась, откройте её заново из /tasks.")
            parsed, matched = parse_human_date(text, datetime.now(UTC), self._settings.tz)
            if parsed is None or not matched:
                return EngineOutcome(
                    "Не понял дату. Примеры: «завтра», «в пятницу», «22.10.2026», «22.10 в 18:00»."
                )
            return await self._propose_payload(
                telegram_user_id,
                PendingOpType.UPDATE_TASK,
                {"task_id": task_id, "changes": {"due_at": parsed.isoformat()}},
            )

        if state is not None and state.flow == Flow.AWAIT_TASK_DUE:
            return await self._handle_task_due_answer(telegram_user_id, state, text)

        if state is not None and state.flow == Flow.AWAIT_COMMENT:
            task_id = str(state.data.get("task_id", ""))
            await self._store.clear(telegram_user_id)
            if not task_id:
                return EngineOutcome("Задача потерялась, откройте её заново из /tasks.")
            from jarvis.services.drafts import CommentDraft

            draft = CommentDraft(task_id=task_id, content=text[:4000])
            return await self._propose(telegram_user_id, PendingOpType.CREATE_COMMENT, draft)

        if state is not None and state.flow == Flow.EDIT_COMMENT:
            data = dict(state.data)
            await self._store.clear(telegram_user_id)
            comment_id = str(data.get("comment_id", ""))
            task_id = str(data.get("task_id", ""))
            if not comment_id or not task_id:
                return EngineOutcome("Комментарий потерялся, откройте задачу заново.")
            return await self._propose_payload(
                telegram_user_id,
                PendingOpType.UPDATE_COMMENT,
                {"comment_id": comment_id, "task_id": task_id, "content": text[:4000]},
            )

        # --- Stage 3: AI-агент ---
        # Весь текст уходит агенту (кроме пошаговых сценариев выше и ввода
        # запроса для кнопочного поиска — его ловит состояние SEARCH_WAIT).
        from jarvis.agent.routing import invokes_jarvis

        if (
            state is not None
            and state.flow == Flow.SEARCH_WAIT
            and state.step == "asked"
            and not (self._agent_enabled() and invokes_jarvis(text))
        ):
            return await self._do_search(telegram_user_id, text, page=0)

        if self._agent_enabled():
            return await self._run_agent(telegram_user_id, text)

        # Без AI — прежний детерминированный путь (деградированный режим).
        day = self._date_list_day(text)
        if day is not None:
            return await self._show_tasks_on_date(day)

        if looks_like_search(text):
            return await self._do_search(
                telegram_user_id, extract_search_query(text), page=0
            )

        try:
            context = await self._build_context(session_id=f"tg-{telegram_user_id}")
            interpretation = await self._ai.interpret(text, context)
        except AIUnavailableError:
            return await self._fallback_without_ai(telegram_user_id, text)
        except AIError:
            log.exception("ai provider failed on interpret")
            return EngineOutcome(
                "AI-сервис временно недоступен. Попробуйте позже, "
                "сохранить запись можно через /search или /tasks.",
                menu=self._menu_flag(),
            )

        await self._capture_hypothesis(interpretation)
        if interpretation.intent == "unknown" and self._agent_enabled():
            # Раньше «не понял» был тупиком; агент попробует реально помочь.
            return await self._run_agent(telegram_user_id, text)
        return await self._route(telegram_user_id, text, interpretation, context, source)

    async def search_page(self, telegram_user_id: int, page: int) -> EngineOutcome:
        state = await self._store.get_active(telegram_user_id)
        if state is None or state.flow != Flow.SEARCH_WAIT:
            return EngineOutcome("Запрос устарел. Повторите поиск заново.", menu=self._menu_flag())
        query = str(state.data.get("query", ""))
        return await self._do_search(telegram_user_id, query, page=max(0, page))

    # --- Маршрутизация интерпретации ---

    async def _route(
        self,
        telegram_user_id: int,
        text: str,
        interpretation: Interpretation,
        context: InterpreterContext,
        source: KnowledgeSource = KnowledgeSource.TEXT,
    ) -> EngineOutcome:
        intent = interpretation.intent

        if intent == "search":
            query = str(interpretation.entities.get("query") or text).strip()
            return await self._do_search(telegram_user_id, query, page=0)

        if intent == "document_upload":
            await self._store.set(telegram_user_id, Flow.AWAIT_DOCUMENT_UPLOAD)
            return EngineOutcome(
                "Хорошо. Пришлите файл одним сообщением — я проверю его, "
                "предложу название, тип и категорию, а сохраню только после подтверждения.",
                awaiting_document=True,
            )

        if intent == "smalltalk":
            reply = interpretation.reply
            if not reply:
                try:
                    reply = await self._ai.chat_reply(text, context)
                except AIError:
                    reply = "Привет! Готов помочь с задачами, заметками и поиском."
            return EngineOutcome(
                format_llm_text(reply),
                menu=self._menu_flag(),
                ui_actions=_ui_actions(interpretation),
            )

        if intent in _TASK_INTENTS:
            return await self._propose_task(telegram_user_id, text, interpretation, context)

        if intent == "add_comment":
            return await self._propose_comment(telegram_user_id, interpretation)

        if intent == "list_comments":
            return await self._show_comments(interpretation)

        if intent == "list_tasks":
            view = str(interpretation.entities.get("view") or "week").strip()
            return await self._show_task_list(view)

        if intent == "create_project":
            name = str(interpretation.entities.get("name") or "").strip()
            if not name:
                return EngineOutcome("Как назвать проект?")
            return await self._propose_payload(
                telegram_user_id,
                PendingOpType.CREATE_PROJECT,
                {
                    "name": name[:200],
                    "description": interpretation.entities.get("description"),
                },
            )

        if intent in _KNOWLEDGE_INTENTS:
            return await self._propose_knowledge(telegram_user_id, text, interpretation, source)

        if intent == "clarify":
            return await self._ask_clarify(telegram_user_id, text, interpretation, context)

        # unknown — просим уточнить, не угадываем.
        fallback = interpretation.reply or (
            "Не понял сообщение. Уточните, пожалуйста: сохранить как заметку, "
            "поставить задачу или найти что-то в базе?"
        )
        return EngineOutcome(
            format_llm_text(fallback),
            menu=self._menu_flag(),
            ui_actions=_ui_actions(interpretation),
        )

    # --- Предложение операций ---

    async def _propose_task(
        self,
        telegram_user_id: int,
        text: str,
        interpretation: Interpretation,
        context: InterpreterContext,
    ) -> EngineOutcome:
        entities = interpretation.entities
        title = str(entities.get("title") or "").strip()
        if not title:
            return await self._ask_clarify(
                telegram_user_id,
                text,
                interpretation,
                context,
                question="Как назвать задачу?",
            )

        due_at = self._parse_due_at(entities.get("due_at"))
        if entities.get("due_at") and due_at is None:
            return await self._ask_clarify(
                telegram_user_id,
                text,
                interpretation,
                context,
                question="Не смог разобрать срок. Уточните дату, пожалуйста "
                "(например, «22.10.2026» или «в пятницу»).",
            )

        category = await self._catalog.resolve_name(entities.get("category"))
        category_name = str(entities.get("category") or "").strip() or None
        project_name = str(entities.get("project") or "").strip() or None
        bucket = self._parse_bucket(entities.get("bucket"))
        priority = self._parse_priority(entities.get("priority"))

        draft = TaskDraft(
            title=title,
            description=entities.get("description"),
            original_text=text,
            due_at=due_at,
            bucket=bucket,
            priority=priority,
            category_id=category.id if category else None,
            # Категория названа, но её ещё нет: привяжем по имени — создастся при подтверждении.
            category_name=category_name if category is None else None,
            project_name=project_name,
        )
        return await self._propose(telegram_user_id, PendingOpType.CREATE_TASK, draft)

    async def _propose_comment(
        self, telegram_user_id: int, interpretation: Interpretation
    ) -> EngineOutcome:
        """Комментарий к задаче: находим кандидатов и предлагаем выбор или операцию."""
        entities = interpretation.entities
        content = str(entities.get("content") or "").strip()
        target = str(entities.get("target_task") or "").strip()
        if not content:
            await self._store.set(
                telegram_user_id,
                Flow.CLARIFY_DRAFT,
                data={
                    "original": "пользователь просит добавить комментарий",
                    "intent": "add_comment",
                },
            )
            return EngineOutcome(
                "Что именно добавить комментарием? Напишите текст комментария."
            )

        from jarvis.repositories.tasks import TaskRepository

        candidates = await TaskRepository(self._session).find_candidates(target or None)
        if not candidates:
            return EngineOutcome(
                "Не нашёл подходящих задач. Создайте задачу или уточните название.",
                menu=self._menu_flag(),
            )
        if len(candidates) == 1:
            from jarvis.services.drafts import CommentDraft

            draft = CommentDraft(task_id=candidates[0].id, content=content[:4000])
            return await self._propose(telegram_user_id, PendingOpType.CREATE_COMMENT, draft)

        await self._store.set(
            telegram_user_id,
            Flow.CHOOSE_TASK_FOR_COMMENT,
            data={"content": content[:4000]},
        )
        return EngineOutcome(
            "К какой задаче добавить комментарий?",
            task_choices=candidates,
        )

    async def choose_task_for_comment(
        self, telegram_user_id: int, task_id: str
    ) -> EngineOutcome:
        """Пользователь выбрал задачу из вариантов (кнопка)."""
        state = await self._store.get_active(telegram_user_id)
        if state is None or state.flow != Flow.CHOOSE_TASK_FOR_COMMENT:
            return EngineOutcome("Выбор устарел — повторите запрос.", menu=self._menu_flag())
        content = str(state.data.get("content", "")).strip()
        await self._store.clear(telegram_user_id)
        if not content:
            return EngineOutcome("Текст комментария потерялся — повторите запрос.")
        from jarvis.services.drafts import CommentDraft

        draft = CommentDraft(task_id=task_id, content=content)
        return await self._propose(telegram_user_id, PendingOpType.CREATE_COMMENT, draft)

    async def _propose_knowledge(
        self,
        telegram_user_id: int,
        text: str,
        interpretation: Interpretation,
        source: KnowledgeSource = KnowledgeSource.TEXT,
    ) -> EngineOutcome:
        entities = interpretation.entities
        hints = {hint.lower() for hint in interpretation.sensitivity_hints}

        if "secret" in hints or self._looks_like_secret(entities):
            await self._store.set(
                telegram_user_id,
                Flow.FORCE_SAVE_PENDING,
                data={
                    "text": text,
                    "project": str(entities.get("project") or "").strip() or None,
                },
            )
            return EngineOutcome(
                "⚠️ Похоже, в сообщении есть секрет (пароль, токен или ключ).\n\n"
                "• Надёжный вариант — отправить значение через /secret: оно "
                "зашифруется отдельным ключом и не попадёт в AI и MCP.\n"
                "• Либо нажмите кнопку ниже — сохраню заметкой с уровнем "
                "«чувствительная»: вы сможете её искать сами, MCP её не увидит.\n\n"
                "<i>В заметке текст хранится как есть — проверьте, что это ваш "
                "личный чат и в нём нет лишнего.</i>",
                force_save=True,
            )

        content = str(entities.get("content") or text).strip()
        title = str(entities.get("title") or first_line(text)).strip()
        if not content or not title:
            return EngineOutcome("Не могу сохранить пустую запись.", menu=self._menu_flag())

        kind = entities.get("type")
        knowledge_type = (
            KnowledgeType(kind) if kind in {t.value for t in KnowledgeType} else KnowledgeType.NOTE
        )
        sensitivity = Sensitivity.SENSITIVE if "sensitive" in hints else Sensitivity.NORMAL
        category = await self._catalog.resolve_name(entities.get("category"))
        project_name = str(entities.get("project") or "").strip() or None

        draft = KnowledgeDraft(
            type=knowledge_type,
            title=title,
            summary=entities.get("summary"),
            content=content,
            source_type=source,
            original_text=text,
            category_id=category.id if category else None,
            project_name=project_name,
            sensitivity=sensitivity,
        )
        return await self._propose(telegram_user_id, PendingOpType.CREATE_KNOWLEDGE, draft)

    async def _render_screen(
        self, telegram_user_id: int, name: str, params: dict
    ) -> EngineOutcome | None:
        """Экран, который попросил открыть агент (параметры уже провалидированы)."""
        if name == "tasks":
            if params.get("category"):
                return await self._show_tasks_in_category(str(params["category"]))
            if params.get("on_date"):
                return await self._show_tasks_on_date(params["on_date"])
            return await self._show_task_list(str(params.get("view") or "week"))
        if name == "search":
            return await self._do_search(telegram_user_id, str(params.get("query") or ""), page=0)
        if name == "documents":
            return await self._show_documents(params.get("category"))
        if name == "card":
            return EngineOutcome(
                "", card=(str(params.get("kind") or ""), str(params.get("id") or ""))
            )
        return None

    async def _show_task_list(self, view: str, page: int = 0) -> EngineOutcome:
        """Список задач по динамической группе («какие у меня задачи на неделю»)."""
        if view not in TASK_VIEWS:
            view = "week"
        tasks = await TaskService(self._session).list_view(
            view,
            now=datetime.now(UTC),
            tz=self._settings.tz,
            limit=TASK_LIST_LIMIT,
            offset=page * TASK_LIST_LIMIT,
        )
        title = TASK_VIEW_LABELS.get(view, "Задачи")
        return self._render_task_list(f"📋 <b>{title}</b>", tasks, view=view, page=page)

    async def _show_tasks_on_date(self, day: date, page: int = 0) -> EngineOutcome:
        """Список задач с дедлайном на конкретный день («задачи на 19 октября»)."""
        tasks = await TaskService(self._session).list_on_date(
            day,
            tz=self._settings.tz,
            limit=TASK_LIST_LIMIT,
            offset=page * TASK_LIST_LIMIT,
        )
        return self._render_task_list(
            f"📋 <b>Задачи на {day.strftime('%d.%m.%Y')}</b>",
            tasks,
            view="date",
            page=page,
            value=day.isoformat(),
        )

    async def _show_tasks_in_category(self, name: str, page: int = 0) -> EngineOutcome:
        """Список задач категории («покажи задачи в категории X»)."""
        category = await self._catalog.resolve_name(name)
        if category is None:
            return EngineOutcome(
                f"Категория «{html_escape(name)}» не найдена.", menu=self._menu_flag()
            )
        tasks = await TaskRepository(self._session).list_by_category(
            category.id, limit=TASK_LIST_LIMIT, offset=page * TASK_LIST_LIMIT
        )
        return self._render_task_list(
            f"📋 <b>Задачи в «{html_escape(category.name)}»</b>",
            tasks,
            view="category",
            page=page,
            value=str(category.id),
        )

    async def _show_documents(self, category_name: str | None) -> EngineOutcome:
        """Список документов («покажи список документов», экран агента documents)."""
        category = None
        if category_name:
            category = await self._catalog.resolve_name(str(category_name))
            if category is None:
                return EngineOutcome(
                    f"Категория «{html_escape(str(category_name))}» не найдена.",
                    menu=self._menu_flag(),
                )
        documents = await DocumentRepository(self._session).list_active(
            limit=DOCUMENT_LIST_LIMIT,
            category_id=category.id if category else None,
        )
        title = f"🗂 <b>{html_escape(category.name)}</b>" if category else "📄 <b>Документы</b>"
        if not documents:
            if category is None:
                return EngineOutcome(
                    "📄 Пока нет документов.\n\nОтправьте файл одним сообщением — "
                    "я зашифрую его и предложу сохранить.",
                    menu=self._menu_flag(),
                )
            return EngineOutcome(f"{title}\n\nПусто.", menu=self._menu_flag())
        lines = [title, ""]
        for index, document in enumerate(documents, start=1):
            lines.append(f"{index}. {html_escape(document.title)}")
        return EngineOutcome("\n".join(lines), documents=documents)

    def _render_task_list(
        self,
        title: str,
        tasks,
        *,
        view: str | None = None,
        page: int = 0,
        value: str = "",
    ) -> EngineOutcome:
        if not tasks:
            return EngineOutcome(f"{title}\n\nПусто.", menu=self._menu_flag())
        lines = [title, ""]
        for index, task in enumerate(tasks, start=1):
            suffix = ""
            if task.due_at is not None:
                suffix = f" — до {fmt_dt_local(task.due_at, self._settings.tz)}"
            lines.append(f"{index}. {html_escape(task.title)}{suffix}")
        return EngineOutcome(
            "\n".join(lines),
            task_choices=list(tasks),
            task_choices_action="view",
            task_choices_prompt="Нажмите на задачу, чтобы открыть карточку:",
            task_page=(view, page, value, len(tasks) >= TASK_LIST_LIMIT) if view else None,
        )

    async def _show_comments(self, interpretation: Interpretation) -> EngineOutcome:
        """Комментарии к задаче («какие комментарии у меня к задаче X»)."""
        target = str(interpretation.entities.get("target_task") or "").strip()
        candidates = await TaskRepository(self._session).find_candidates(target or None)
        if not candidates:
            return EngineOutcome(
                "Не нашёл подходящих задач. Уточните название.", menu=self._menu_flag()
            )
        if len(candidates) == 1:
            return await self._render_comments(candidates[0].id)
        return EngineOutcome(
            "К какой задаче показать комментарии?",
            task_choices=candidates,
            task_choices_action="comments",
        )

    async def _render_comments(self, task_id) -> EngineOutcome:
        task = await TaskRepository(self._session).get(task_id)
        if task is None:
            return EngineOutcome("Задача не найдена.", menu=self._menu_flag())
        comments = await TaskCommentRepository(self._session).list_for_task(task_id)
        if not comments:
            return EngineOutcome(
                f"💬 У задачи «{html_escape(task.title)}» комментариев пока нет.\n\n"
                "Откройте карточку задачи в /tasks и нажмите «💬 Комментарии», чтобы добавить."
            )
        lines = [f"💬 <b>Комментарии</b> — {html_escape(task.title)}", ""]
        for index, comment in enumerate(comments, start=1):
            stamp = fmt_dt_local(comment.created_at, self._settings.tz)
            lines.append(f"<b>{index}.</b> {html_escape(comment.content)}\n<i>{stamp}</i>")
        return EngineOutcome("\n".join(lines))

    async def _propose(
        self,
        telegram_user_id: int,
        operation_type: PendingOpType,
        draft: BaseModel,
        *,
        ask_due: bool = True,
    ) -> EngineOutcome:
        return await self._propose_payload(
            telegram_user_id, operation_type, draft.model_dump(mode="json"), ask_due=ask_due
        )

    async def _propose_payload(
        self,
        telegram_user_id: int,
        operation_type: PendingOpType,
        payload: dict,
        *,
        ask_due: bool = True,
    ) -> EngineOutcome:
        if (
            ask_due
            and operation_type == PendingOpType.CREATE_TASK
            and payload.get("due_at") is None
        ):
            return await self._ask_task_due(telegram_user_id, dict(payload))
        operation_id = await self._confirmation.propose(telegram_user_id, operation_type, payload)
        preview = await render_preview(
            self._session, operation_type, payload, self._settings.tz
        )
        return EngineOutcome("", pending_ops=[(operation_id, preview)])

    async def _ask_task_due(self, telegram_user_id: int, payload: dict) -> EngineOutcome:
        """Задача без срока: сначала уточняем срок, операцию пока не создаём."""
        await self._store.set(telegram_user_id, Flow.AWAIT_TASK_DUE, data={"draft": payload})
        title = html_escape(str(payload.get("title") or "задача"))
        return EngineOutcome(
            f"Задача «{title}» — без срока. Нужен ли срок? Напишите дату "
            "(например, «22.10.2026» или «в пятницу») или ответьте «без срока»."
        )

    async def _handle_task_due_answer(
        self, telegram_user_id: int, state, text: str
    ) -> EngineOutcome:
        draft = dict(state.data.get("draft") or {})
        if not draft:
            await self._store.clear(telegram_user_id)
            return EngineOutcome("Черновик задачи потерялся — повторите запрос.")
        if text.strip().lower().rstrip(".!") in NO_DUE_ANSWERS:
            await self._store.clear(telegram_user_id)
            return await self._propose_payload(
                telegram_user_id, PendingOpType.CREATE_TASK, draft, ask_due=False
            )
        parsed, matched = parse_human_date(text, datetime.now(UTC), self._settings.tz)
        if parsed is None or not matched:
            return EngineOutcome(
                "Не понял дату. Напишите «22.10.2026», «в пятницу» или «22.10 в 18:00», "
                "либо ответьте «без срока»."
            )
        await self._store.clear(telegram_user_id)
        draft["due_at"] = parsed.isoformat()
        return await self._propose_payload(
            telegram_user_id, PendingOpType.CREATE_TASK, draft, ask_due=False
        )

    # --- Stage 3: агент ---

    def _agent_enabled(self) -> bool:
        return self._settings.agent_enabled and getattr(self._ai, "name", "none") != "none"

    async def _run_agent(self, telegram_user_id: int, text: str) -> EngineOutcome:
        from jarvis.agent.orchestrator import AgentService

        service = AgentService(
            self._session,
            self._settings,
            self._ai,
            gate=self._gate,
            search_provider=self._search_provider,
            correlation_id=get_correlation_id(),
        )
        try:
            result = await service.run(telegram_user_id, text)
        except Exception:  # noqa: BLE001 - агент не должен ронять обработку апдейта
            log.exception("agent run failed")
            return EngineOutcome(
                "Не получилось выполнить агентский запрос. Попробуйте ещё раз позже.",
                menu=self._menu_flag(),
            )
        if result.screen is not None:
            outcome = await self._render_screen(
                telegram_user_id, result.screen[0], result.screen[1]
            )
            if outcome is not None:
                return outcome
        pending_ops, due_payload = await self._pending_previews(
            telegram_user_id, result.pending_operation_ids
        )
        if due_payload is not None:
            question = await self._ask_task_due(telegram_user_id, due_payload)
            question.pending_ops = pending_ops
            return question
        return EngineOutcome(
            result.text,
            pending_ops=pending_ops,
            ui_actions=result.quick_actions,
            entity_choices=[
                candidate.model_dump(mode="json") for candidate in result.entity_choices
            ],
            entity_choice_prompt=result.choice_prompt,
            files=list(result.files),
            menu=self._menu_flag() and not result.quick_actions and not pending_ops,
        )

    async def _pending_previews(
        self, telegram_user_id: int, operation_ids: list[str]
    ) -> tuple[list[tuple[str, str]], dict | None]:
        """Превью операций агента; задача без срока откладывается до ответа о сроке."""
        if not operation_ids:
            return [], None
        from jarvis.repositories.pending import PendingOperationRepository

        repo = PendingOperationRepository(self._session)
        previews: list[tuple[str, str]] = []
        due_payload: dict | None = None
        for operation_id in operation_ids:
            operation = await repo.get(uuid.UUID(str(operation_id)))
            if operation is None:
                continue
            payload = dict(operation.payload or {})
            if (
                operation.operation_type == PendingOpType.CREATE_TASK
                and payload.get("due_at") is None
            ):
                # Превью не показываем: сначала спросим про срок (операцию отменим,
                # после ответа предложим задачу заново).
                await self._confirmation.cancel(telegram_user_id, operation.id)
                if due_payload is None:
                    due_payload = payload
                continue
            preview = await render_preview(
                self._session,
                operation.operation_type,
                payload,
                self._settings.tz,
            )
            previews.append((str(operation_id), preview))
        return previews, due_payload

    # --- Improvement backlog ---

    async def _capture_hypothesis(self, interpretation: Interpretation) -> None:
        """Гипотеза LLM о проблеме Jarvis: в backlog, но не роняем диалог."""
        if interpretation.improvement_hypothesis is None:
            return
        try:
            await self._improvements.record_hypothesis(
                interpretation.improvement_hypothesis,
                correlation_id=get_correlation_id(),
            )
        except Exception:  # noqa: BLE001 - backlog не должен мешать диалогу
            log.exception("failed to record improvement hypothesis")

    # --- Уточнения и редактирование черновика ---

    async def _ask_clarify(
        self,
        telegram_user_id: int,
        text: str,
        interpretation: Interpretation,
        context: InterpreterContext,
        *,
        question: str | None = None,
    ) -> EngineOutcome:
        asked = question or interpretation.clarification_question or (
            "Уточните, пожалуйста, что нужно сделать?"
        )
        await self._store.set(
            telegram_user_id,
            Flow.CLARIFY_DRAFT,
            data={"original": text, "intent": interpretation.intent},
        )
        return EngineOutcome(format_llm_text(asked))

    async def _handle_clarify(self, telegram_user_id: int, state, text: str) -> EngineOutcome:
        original = str(state.data.get("original", ""))
        combined = f"{original}\nДополнение пользователя: {text}" if original else text
        try:
            context = await self._build_context(session_id=f"tg-{telegram_user_id}")
            interpretation = await self._ai.interpret(combined, context)
        except AIUnavailableError:
            await self._store.clear(telegram_user_id)
            return await self._fallback_without_ai(telegram_user_id, combined)
        except AIError:
            log.exception("ai provider failed during clarify")
            return EngineOutcome("AI-сервис временно недоступен. Попробуйте позже.")

        await self._capture_hypothesis(interpretation)
        if interpretation.intent in _TASK_INTENTS or interpretation.intent in _KNOWLEDGE_INTENTS:
            await self._store.clear(telegram_user_id)
            return await self._route(telegram_user_id, combined, interpretation, context)
        if interpretation.intent == "clarify" or not interpretation.clarification_question:
            await self._store.set(
                telegram_user_id,
                Flow.CLARIFY_DRAFT,
                data={"original": combined, "intent": interpretation.intent},
            )
            return EngineOutcome(
                format_llm_text(
                    interpretation.clarification_question or "Уточните, пожалуйста, детали."
                )
            )
        await self._store.clear(telegram_user_id)
        return await self._route(telegram_user_id, combined, interpretation, context)

    async def _handle_secret_value(
        self, telegram_user_id: int, state, text: str
    ) -> EngineOutcome:
        """Двухшаговый сценарий секрета: значение (шифруется сразу) → имя."""
        from jarvis.services.secrets import SecretService

        if state.step == "value":
            service = SecretService(self._session, self._settings)
            encrypted_b64, key_version = service.encrypt_value(text)
            await self._store.set(
                telegram_user_id,
                Flow.AWAIT_SECRET_VALUE,
                step="name",
                data={"encrypted_value_b64": encrypted_b64, "key_version": key_version},
            )
            return EngineOutcome(
                "Значение зашифровано. Как назвать секрет? "
                "Например: «сервер hetzner ssh ключ».\n\n"
                "<i>Совет: удалите сообщение со значением из чата.</i>"
            )
        data = dict(state.data)
        name = first_line(text, limit=200) or "Секрет"
        await self._store.clear(telegram_user_id)
        return await self._propose_payload(
            telegram_user_id,
            PendingOpType.CREATE_SECRET,
            {
                "name": name,
                "purpose": None,
                "secret_type": "other",
                "encrypted_value_b64": data.get("encrypted_value_b64", ""),
                "key_version": data.get("key_version", 1),
            },
        )

    async def start_secret_flow(self, telegram_user_id: int) -> EngineOutcome:
        await self._store.set(telegram_user_id, Flow.AWAIT_SECRET_VALUE, step="value")
        return EngineOutcome(
            "🔐 Пришлите значение секрета одним сообщением. Оно будет зашифровано "
            "и не попадёт в поиск, AI и MCP.\n\n"
            "<i>Совет: после отправки удалите сообщение со значением из чата.</i>",
            awaiting_secret=True,
        )

    async def _handle_edit(self, telegram_user_id: int, state, text: str) -> EngineOutcome:
        """Пользователь присылает исправленную формулировку вместо pending-операции."""
        old_op_id = state.pending_operation_id
        op_kind = str(state.data.get("op_kind", ""))
        draft = dict(state.data.get("draft") or {})

        try:
            context = await self._build_context(
                draft=draft, session_id=f"tg-{telegram_user_id}"
            )
            interpretation = await self._ai.interpret(text, context)
            entities = dict(interpretation.entities)
        except AIError:
            entities = {}

        if op_kind == PendingOpType.CREATE_KNOWLEDGE.value:
            if not entities.get("title"):
                draft["title"] = first_line(text) or draft.get("title", "Заметка")
            draft["content"] = text
            draft["original_text"] = text
        elif op_kind == PendingOpType.UPDATE_KNOWLEDGE.value:
            changes = dict(draft.get("changes") or {})
            if entities.get("title"):
                changes["title"] = str(entities["title"])[:300]
            if entities.get("summary"):
                changes["summary"] = str(entities["summary"])[:2000]
            if entities.get("content"):
                changes["content"] = str(entities["content"])[:8000]
            elif text.strip():
                changes["content"] = text[:8000]
            draft["changes"] = changes
        elif op_kind == PendingOpType.SAVE_DOCUMENT.value:
            draft["title"] = text.strip()[:300] or draft.get("title", "Документ")
        elif op_kind == PendingOpType.CREATE_SECRET.value:
            draft["name"] = first_line(text, limit=200) or draft.get("name", "Секрет")
        else:
            # «Изменить» может принести новое название: сначала берём то, что
            # разобрал ИИ (раньше заголовок из ответа ИИ игнорировался), и только
            # потом — текст целиком, как раньше.
            if entities.get("title"):
                draft["title"] = str(entities["title"])[:300]
            elif text.strip():
                draft["title"] = text.strip()[:300]
            if entities.get("due_at"):
                draft["due_at"] = entities["due_at"]
            if entities.get("bucket"):
                draft["bucket"] = entities["bucket"]

        if not draft:
            return EngineOutcome(
                "Не понял исправление. Пришлите новую формулировку коротким текстом."
            )

        if old_op_id is not None:
            try:
                await self._confirmation.cancel(telegram_user_id, uuid.UUID(str(old_op_id)))
            except Exception:  # noqa: BLE001 - старая операция могла уже истечь
                log.warning("failed to cancel previous pending operation %s", old_op_id)

        await self._store.clear(telegram_user_id)

        if op_kind == PendingOpType.CREATE_TASK.value:
            draft_model = TaskDraft.model_validate({**draft})
            return await self._propose(
                telegram_user_id, PendingOpType.CREATE_TASK, draft_model, ask_due=False
            )
        if op_kind == PendingOpType.CREATE_KNOWLEDGE.value:
            draft_model = KnowledgeDraft.model_validate({**draft})
            return await self._propose(
                telegram_user_id, PendingOpType.CREATE_KNOWLEDGE, draft_model
            )
        if op_kind == PendingOpType.UPDATE_KNOWLEDGE.value:
            payload = {"item_id": draft.get("item_id"), "changes": draft.get("changes") or {}}
            if not payload["item_id"] or not payload["changes"]:
                return EngineOutcome("Не удалось обновить операцию. Начните заново.")
            return await self._propose_payload(
                telegram_user_id, PendingOpType.UPDATE_KNOWLEDGE, payload
            )
        if op_kind == PendingOpType.SAVE_DOCUMENT.value:
            from jarvis.services.drafts import DocumentDraft

            draft_model = DocumentDraft.model_validate({**draft})
            return await self._propose(telegram_user_id, PendingOpType.SAVE_DOCUMENT, draft_model)
        if op_kind == PendingOpType.CREATE_SECRET.value:
            from jarvis.services.drafts import SecretCreateDraft

            draft_model = SecretCreateDraft.model_validate({**draft})
            return await self._propose(telegram_user_id, PendingOpType.CREATE_SECRET, draft_model)
        return EngineOutcome(
            "Отменил предыдущую операцию. Чтобы её изменить — "
            "сформулируйте запрос заново."
        )

    async def start_edit_pending(self, telegram_user_id: int, operation_id: str) -> None:
        """Запустить сценарий «Изменить» для pending-операции."""
        from jarvis.repositories.pending import PendingOperationRepository

        operation = await PendingOperationRepository(self._session).get(
            uuid.UUID(str(operation_id))
        )
        if operation is None:
            return
        op_kind = operation.operation_type.value
        draft = dict(operation.payload)
        summary = operation.operation_type.value
        await self._store.set(
            telegram_user_id,
            Flow.EDIT_PENDING,
            step=summary,
            data={"op_kind": op_kind, "draft": draft},
            pending_operation_id=operation.id,
        )

    # --- Поиск ---

    async def _do_search(self, telegram_user_id: int, query: str, *, page: int) -> EngineOutcome:
        query = query.strip()
        if not query:
            return EngineOutcome("Напишите, что искать.")
        offset = page * SEARCH_PAGE_SIZE
        hits = await self._search.search(query, limit=SEARCH_PAGE_SIZE, offset=offset)
        await self._store.set(
            telegram_user_id,
            Flow.SEARCH_WAIT,
            step="results",
            data={"query": query, "page": page},
        )
        return EngineOutcome("", search_hits=hits, search_query=query, search_page=page)

    # --- Вспомогательное ---

    async def _build_context(
        self,
        draft: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> InterpreterContext:
        categories = [category.name for category in await self._catalog.list_categories()]
        projects = [project.name for project in await self._projects.list_active(limit=50)]
        return InterpreterContext(
            now=datetime.now(UTC).astimezone(self._settings.tz),
            tz_name=self._settings.app_timezone,
            categories=categories,
            projects=projects,
            draft=draft,
            session_id=session_id,
        )

    async def _fallback_without_ai(self, telegram_user_id: int, text: str) -> EngineOutcome:
        """Ограниченный режим без AI: предлагаем сохранить как заметку."""
        if is_question(text):
            return EngineOutcome(
                "AI-провайдер не настроен, поэтому на вопросы я ответить не могу. "
                "Поиск по базе работает: /search. Настройте AI_PROVIDER, чтобы я "
                "понимал задачи, сроки и категории.",
                menu=self._menu_flag(),
            )
        draft = KnowledgeDraft(
            type=KnowledgeType.NOTE,
            title=first_line(text) or "Заметка",
            content=text,
            source_type=KnowledgeSource.TEXT,
            original_text=text,
        )
        outcome = await self._propose(telegram_user_id, PendingOpType.CREATE_KNOWLEDGE, draft)
        outcome.text += (
            "\n\n<i>AI-провайдер не настроен: сохраняю сообщение как заметку. "
            "После настройки AI я буду распознавать задачи и категории.</i>"
        )
        return outcome

    def _date_list_day(self, text: str) -> date | None:
        """«Задачи на <дата>» — детерминированный список, без AI."""
        if "задач" not in text.lower() or not _DATE_LIST_PREFIX.search(text):
            return None
        parsed, matched = parse_human_date(text, datetime.now(UTC), self._settings.tz)
        if not matched or parsed is None:
            return None
        return parsed.date()

    @staticmethod
    def _parse_due_at(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            return None
        return parsed

    @staticmethod
    def _parse_bucket(value: Any) -> TaskBucket | None:
        if not value:
            return None
        try:
            return TaskBucket(str(value))
        except ValueError:
            return None

    @staticmethod
    def _parse_priority(value: Any) -> TaskPriority:
        if not value:
            return TaskPriority.NORMAL
        try:
            return TaskPriority(str(value))
        except ValueError:
            return TaskPriority.NORMAL

    @staticmethod
    def _looks_like_secret(entities: dict[str, Any]) -> bool:
        haystack = " ".join(
            str(entities.get(key, "")) for key in ("title", "summary", "content")
        ).lower()
        markers = ("password=", "token=", "api_key", "apikey", "ssh-rsa", "private key")
        return any(marker in haystack for marker in markers)

    def _menu_flag(self) -> bool:
        return True
