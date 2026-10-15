"""Диалоговый движок: маршрутизация сообщений, черновики, предложения операций."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.ai import AIError, AIProvider, AIUnavailableError, InterpreterContext
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
from jarvis.conversation.preview import render_preview
from jarvis.conversation.state import ConversationStore
from jarvis.domain.enums import (
    KnowledgeSource,
    KnowledgeType,
    PendingOpType,
    Sensitivity,
    TaskBucket,
    TaskPriority,
)
from jarvis.repositories.conversation import ConversationRepository
from jarvis.repositories.projects import ProjectRepository
from jarvis.services.catalog import CatalogService
from jarvis.services.confirmation import ConfirmationService
from jarvis.services.drafts import KnowledgeDraft, TaskDraft
from jarvis.services.search import SearchHit, SearchService

log = logging.getLogger(__name__)

SEARCH_PAGE_SIZE = 7

_TASK_INTENTS = ("create_task",)
_KNOWLEDGE_INTENTS = ("create_knowledge",)


@dataclass
class EngineOutcome:
    text: str
    pending_op_id: str | None = None
    search_hits: list[SearchHit] = field(default_factory=list)
    search_query: str | None = None
    search_page: int = 0
    menu: bool = False
    awaiting_document: bool = False
    awaiting_secret: bool = False


class ConversationEngine:
    def __init__(self, session: AsyncSession, settings: Settings, ai: AIProvider) -> None:
        self._session = session
        self._settings = settings
        self._ai = ai
        self._store = ConversationStore(ConversationRepository(session), settings)
        self._confirmation = ConfirmationService(session, settings)
        self._catalog = CatalogService(session)
        self._projects = ProjectRepository(session)
        self._search = SearchService(session)

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

        if state is not None and state.flow == Flow.SEARCH_WAIT:
            return await self._do_search(telegram_user_id, text, page=0)

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
            return EngineOutcome(reply, menu=self._menu_flag())

        if intent in _TASK_INTENTS:
            return await self._propose_task(telegram_user_id, text, interpretation, context)

        if intent in _KNOWLEDGE_INTENTS:
            return await self._propose_knowledge(telegram_user_id, text, interpretation, source)

        if intent == "clarify":
            return await self._ask_clarify(telegram_user_id, text, interpretation, context)

        # unknown — просим уточнить, не угадываем.
        fallback = interpretation.reply or (
            "Не понял сообщение. Уточните, пожалуйста: сохранить как заметку, "
            "поставить задачу или найти что-то в базе?"
        )
        return EngineOutcome(fallback, menu=self._menu_flag())

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
        project = await self._resolve_project(entities.get("project"))
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
            project_id=project.id if project else None,
        )
        return await self._propose(telegram_user_id, PendingOpType.CREATE_TASK, draft)

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
            return EngineOutcome(
                "Похоже, в этом сообщении секрет (пароль, токен или ключ). "
                "Я не сохраняю секреты в обычные заметки — используйте команду /secret. "
                "Если секрета здесь нет, переформулируйте без него.",
                menu=self._menu_flag(),
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
        project = await self._resolve_project(entities.get("project"))

        draft = KnowledgeDraft(
            type=knowledge_type,
            title=title,
            summary=entities.get("summary"),
            content=content,
            source_type=source,
            original_text=text,
            category_id=category.id if category else None,
            project_id=project.id if project else None,
            sensitivity=sensitivity,
        )
        return await self._propose(telegram_user_id, PendingOpType.CREATE_KNOWLEDGE, draft)

    async def _resolve_project(self, name):
        if not name:
            return None
        return await self._projects.find_by_name(str(name))

    async def _propose(
        self,
        telegram_user_id: int,
        operation_type: PendingOpType,
        draft: BaseModel,
    ) -> EngineOutcome:
        return await self._propose_payload(
            telegram_user_id, operation_type, draft.model_dump(mode="json")
        )

    async def _propose_payload(
        self,
        telegram_user_id: int,
        operation_type: PendingOpType,
        payload: dict,
    ) -> EngineOutcome:
        operation_id = await self._confirmation.propose(telegram_user_id, operation_type, payload)
        preview = await render_preview(
            self._session, operation_type, payload, self._settings.tz
        )
        return EngineOutcome(preview, pending_op_id=operation_id)

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
        return EngineOutcome(asked)

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
                interpretation.clarification_question or "Уточните, пожалуйста, детали."
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
        elif op_kind == PendingOpType.SAVE_DOCUMENT.value:
            draft["title"] = text.strip()[:300] or draft.get("title", "Документ")
        elif op_kind == PendingOpType.CREATE_SECRET.value:
            draft["name"] = first_line(text, limit=200) or draft.get("name", "Секрет")
        else:
            if not entities.get("title"):
                draft["title"] = text.strip()[:300] or draft.get("title", "")
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
            return await self._propose(telegram_user_id, PendingOpType.CREATE_TASK, draft_model)
        if op_kind == PendingOpType.CREATE_KNOWLEDGE.value:
            draft_model = KnowledgeDraft.model_validate({**draft})
            return await self._propose(
                telegram_user_id, PendingOpType.CREATE_KNOWLEDGE, draft_model
            )
        if op_kind == PendingOpType.SAVE_DOCUMENT.value:
            from jarvis.services.drafts import DocumentDraft

            draft_model = DocumentDraft.model_validate({**draft})
            return await self._propose(telegram_user_id, PendingOpType.SAVE_DOCUMENT, draft_model)
        if op_kind == PendingOpType.CREATE_SECRET.value:
            from jarvis.services.drafts import SecretCreateDraft

            draft_model = SecretCreateDraft.model_validate({**draft})
            return await self._propose(telegram_user_id, PendingOpType.CREATE_SECRET, draft_model)
        return EngineOutcome("Не удалось обновить операцию. Начните заново.")

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
            telegram_user_id, Flow.SEARCH_WAIT, data={"query": query, "page": page}
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
