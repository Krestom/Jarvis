"""Типизированный реестр внутренних инструментов агента.

Только явно описанные операции: чтение отделено от мутаций, каждая мутация
помечена уровнем риска. Все данные — через существующие сервисы/репозитории;
удаление — через pending-операции Stage 1 (подтверждение кнопкой).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.agent.schemas import EntityCandidate, OutgoingFile
from jarvis.config.settings import Settings
from jarvis.domain.buckets import DONE_STATUSES, local_day_start
from jarvis.domain.enums import (
    ImprovementCategory,
    ImprovementStatus,
    KnowledgeSource,
    KnowledgeType,
    PendingOpType,
    Sensitivity,
    TaskBucket,
    TaskPriority,
    TaskStatus,
)
from jarvis.repositories.audit import AuditRepository
from jarvis.repositories.categories import CategoryRepository
from jarvis.repositories.comments import TaskCommentRepository
from jarvis.repositories.improvements import OPEN_STATUSES, ImprovementRepository
from jarvis.repositories.knowledge import KnowledgeRepository
from jarvis.repositories.projects import ProjectRepository
from jarvis.repositories.tasks import TaskRepository
from jarvis.research.provider import WebSearchProvider, WebSearchUnavailable
from jarvis.security.redaction import looks_like_secret
from jarvis.services.confirmation import ConfirmationService
from jarvis.services.drafts import CommentDraft, KnowledgeDraft, TaskDraft
from jarvis.services.errors import NotFoundError, OperationError
from jarvis.services.improvements import ImprovementService
from jarvis.services.search import SearchService
from jarvis.services.tasks import TASK_VIEWS, TaskService

log = logging.getLogger(__name__)

ErrorKind = Literal[
    "validation", "not_found", "ambiguous", "permission", "unsupported", "transient", "internal"
]

MAX_SURFACED = 12
COMMENTS_IN_CARD = 5


@dataclass
class ToolResult:
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error_kind: str | None = None
    error_message: str | None = None
    mutating: bool = False

    def payload(self) -> dict[str, Any]:
        """Формат результата для LLM (компактный и стабильный)."""
        if self.ok:
            return {"ok": True, "data": self.data}
        return {
            "ok": False,
            "error": {"kind": self.error_kind or "internal", "message": self.error_message or ""},
        }

    @classmethod
    def failure(cls, kind: ErrorKind, message: str, *, mutating: bool = False) -> ToolResult:
        return cls(ok=False, error_kind=kind, error_message=message, mutating=mutating)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    mutating: bool
    risk: Literal["low", "medium", "high"]


# --- Аргументы инструментов (JSON Schema уходит в промпт) ---


class ListTasksArgs(BaseModel):
    view: str = Field(
        default="week",
        description="today|week|month|long_term|someday|no_group|inbox|all|done",
    )
    limit: int = Field(default=10, ge=1, le=30)
    offset: int = Field(default=0, ge=0)
    on_date: date | None = Field(
        default=None,
        description="День (YYYY-MM-DD): задачи с дедлайном на эту дату; "
        "используй вместо view для вопросов «задачи на 19 октября»",
    )
    category: str | None = Field(
        default=None,
        max_length=120,
        description="Название категории-папки (например «Работа»); указывается вместо view",
    )

    @field_validator("view")
    @classmethod
    def _known_view(cls, value: str) -> str:
        if value not in TASK_VIEWS:
            raise ValueError(f"view должен быть одним из: {', '.join(TASK_VIEWS)}")
        return value


class ListProjectTasksArgs(BaseModel):
    project: str = Field(min_length=1, max_length=200, description="Название проекта")
    include_done: bool = False
    limit: int = Field(default=10, ge=1, le=30)


class SearchTasksArgs(BaseModel):
    query: str = Field(min_length=2, max_length=200)
    limit: int = Field(default=10, ge=1, le=30)


class GetTaskArgs(BaseModel):
    task_id: uuid.UUID


class CreateTaskArgs(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=2000)
    due_at: datetime | None = Field(default=None, description="ISO 8601 с часовым поясом")
    bucket: TaskBucket | None = None
    priority: TaskPriority | None = None
    project: str | None = Field(default=None, max_length=200)
    category: str | None = Field(
        default=None, max_length=120, description="Категория-папка (если названа пользователем)"
    )


class CreateCategoryArgs(BaseModel):
    name: str = Field(min_length=1, max_length=120, description="Название категории-папки")


class CreateProjectArgs(BaseModel):
    name: str = Field(min_length=1, max_length=200, description="Название проекта")
    description: str | None = Field(default=None, max_length=2000)


class ListCommentsArgs(BaseModel):
    task: str = Field(min_length=1, max_length=300, description="Название задачи")


class UpdateTaskArgs(BaseModel):
    task_id: uuid.UUID
    title: str | None = Field(default=None, min_length=1, max_length=300)
    status: TaskStatus | None = None
    priority: TaskPriority | None = None
    bucket: TaskBucket | None = None
    # Явный null в аргументах означает «убрать срок» (см. _update_task).
    due_at: datetime | None = Field(
        default=None, description="ISO 8601 с часовым поясом; null — убрать срок"
    )
    project: str | None = Field(
        default=None,
        max_length=200,
        description="Название проекта (создаётся, если его ещё нет); "
        "«нет»/«без проекта» — убрать проект",
    )


class AddCommentArgs(BaseModel):
    task_id: uuid.UUID
    content: str = Field(min_length=1, max_length=4000)


class UpdateCommentArgs(BaseModel):
    comment_id: uuid.UUID = Field(description="id комментария (из get_task)")
    content: str = Field(min_length=1, max_length=4000, description="Новый текст комментария")


class DeleteTaskArgs(BaseModel):
    task_id: uuid.UUID


class DeleteKnowledgeArgs(BaseModel):
    item_id: uuid.UUID = Field(description="id записи знаний (kind=knowledge из search_all)")


class DeleteDocumentArgs(BaseModel):
    document_id: uuid.UUID = Field(description="id документа (kind=document из search_all)")


class RenameCategoryArgs(BaseModel):
    name: str = Field(
        min_length=1, max_length=120, description="Текущее название категории-папки"
    )
    new_name: str = Field(min_length=1, max_length=120, description="Новое название")


class ListProjectsArgs(BaseModel):
    limit: int = Field(default=20, ge=1, le=50)


class SearchAllArgs(BaseModel):
    query: str = Field(min_length=2, max_length=200)
    limit: int = Field(default=6, ge=1, le=12)


class GetKnowledgeArgs(BaseModel):
    item_id: uuid.UUID


class WebSearchArgs(BaseModel):
    query: str = Field(min_length=2, max_length=300)
    limit: int = Field(default=5, ge=1, le=10)


class ReportGapArgs(BaseModel):
    title: str = Field(min_length=3, max_length=300, description="Короткий заголовок пробела")
    summary: str = Field(
        min_length=3, max_length=2000, description="Что именно невозможно и почему"
    )
    evidence: str | None = Field(default=None, max_length=2000)


class ListImprovementsArgs(BaseModel):
    status: str = Field(
        default="open",
        description="open (new+reviewed+planned) | new | reviewed | planned | "
        "implemented | wont_fix | archived | all",
    )
    category: str | None = Field(
        default=None,
        description="failure | missing_capability | ux_friction | improvement_idea",
    )
    limit: int = Field(default=10, ge=1, le=20)


class UpdateImprovementArgs(BaseModel):
    item_id: uuid.UUID = Field(description="id записи бэклога (из list_improvements)")
    status: ImprovementStatus = Field(
        description="new | reviewed | planned | implemented | wont_fix | archived"
    )


class DailyStatsArgs(BaseModel):
    day: date | None = Field(
        default=None,
        description="День (YYYY-MM-DD); по умолчанию — сегодня",
    )


class ExportChatArgs(BaseModel):
    day: date | None = Field(
        default=None, description="День (YYYY-MM-DD); по умолчанию — сегодня"
    )


class ExportIssuesArgs(BaseModel):
    status: str = Field(
        default="open",
        description="open (new+reviewed+planned) | all | new,reviewed,planned,"
        "implemented,wont_fix,archived (через запятую)",
    )
    category: str | None = Field(
        default=None,
        description="failure | missing_capability | ux_friction | improvement_idea",
    )


class CreateKnowledgeArgs(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1, max_length=8000, description="Суть записи, дословно")
    project: str | None = Field(default=None, max_length=200, description="Название проекта")
    knowledge_type: KnowledgeType = KnowledgeType.NOTE
    sensitive: bool = Field(
        default=False,
        description="true для кредов, паролей, персональных данных и финансов",
    )


class UpdateKnowledgeArgs(BaseModel):
    item_id: uuid.UUID
    title: str | None = Field(default=None, min_length=1, max_length=300)
    summary: str | None = Field(default=None, max_length=2000)
    content: str | None = Field(default=None, min_length=1, max_length=8000)
    category: str | None = Field(
        default=None, max_length=120, description="Название категории-папки"
    )
    project: str | None = Field(default=None, max_length=200, description="Название проекта")
    # None — не трогать уровень; true/false — выставить явно.
    sensitive: bool | None = Field(
        default=None,
        description="true — пометить чувствительной, false — обычной; не указывай, чтобы не менять",
    )


class SendDocumentArgs(BaseModel):
    document_id: uuid.UUID = Field(description="id документа (kind=document из search_all)")


class UpdateDocumentArgs(BaseModel):
    document_id: uuid.UUID = Field(description="id документа (kind=document из search_all)")
    title: str | None = Field(
        default=None, min_length=1, max_length=300, description="Новое название документа"
    )
    category: str | None = Field(
        default=None,
        max_length=120,
        description="Название категории-папки; «нет»/«без категории» — убрать категорию",
    )
    # None — не трогать уровень; true/false — выставить явно.
    sensitive: bool | None = Field(
        default=None,
        description="true — пометить чувствительным, false — обычным (снимает защиту); "
        "не указывай, чтобы не менять",
    )


@dataclass(frozen=True)
class ToolMeta:
    args_model: type[BaseModel]
    description: str
    mutating: bool
    risk: Literal["low", "medium", "high"]


_TOOLS: dict[str, ToolMeta] = {
    "list_tasks": ToolMeta(
        ListTasksArgs,
        "Список задач по динамическому представлению (today — включая просроченные, "
        "week, month — до конца текущего месяца, long_term, someday, no_group, "
        "inbox, all — все незавершённые, done). Вместо view можно указать on_date "
        "(задачи на конкретный день) или category (категория-папка). Данные из базы.",
        mutating=False,
        risk="low",
    ),
    "list_project_tasks": ToolMeta(
        ListProjectTasksArgs,
        "Незавершённые (по умолчанию) задачи конкретного проекта по его названию.",
        mutating=False,
        risk="low",
    ),
    "search_tasks": ToolMeta(
        SearchTasksArgs,
        "Поиск задач по тексту (FTS + подстрока). Используй, когда пользователь "
        "называет тему, а не список.",
        mutating=False,
        risk="low",
    ),
    "get_task": ToolMeta(
        GetTaskArgs,
        "Карточка задачи: поля, проект и последние комментарии.",
        mutating=False,
        risk="low",
    ),
    "list_comments": ToolMeta(
        ListCommentsArgs,
        "Комментарии задачи по её названию (данные из базы).",
        mutating=False,
        risk="low",
    ),
    "create_task": ToolMeta(
        CreateTaskArgs,
        "Создать задачу. Инструмент ОТПРАВЛЯЕТ превью с кнопкой подтверждения — "
        "задача создастся только после нажатия пользователем (обратимо). "
        "Вызывай только когда пользователь явно просит поставить задачу; "
        "срок — ISO 8601, если он назван.",
        mutating=True,
        risk="low",
    ),
    "create_category": ToolMeta(
        CreateCategoryArgs,
        "Создать категорию-папку для группировки задач (например «Рабочие», «Дом»). "
        "Отправляет превью с кнопкой подтверждения. Если такая категория уже есть — "
        "вернётся существующая без операции. Обратимо.",
        mutating=True,
        risk="low",
    ),
    "create_project": ToolMeta(
        CreateProjectArgs,
        "Создать проект по названию. Отправляет превью с кнопкой подтверждения. "
        "Если такой проект уже есть — вернётся существующий без операции. Обратимо.",
        mutating=True,
        risk="low",
    ),
    "update_task": ToolMeta(
        UpdateTaskArgs,
        "Изменить разрешённые поля задачи: статус, приоритет, группу, срок, заголовок, "
        "проект. Отправляет превью с кнопкой подтверждения (изменение применится после "
        "нажатия). due_at=null — убрать срок; project=«нет» — убрать проект.",
        mutating=True,
        risk="low",
    ),
    "add_comment": ToolMeta(
        AddCommentArgs,
        "Добавить комментарий к задаче. Отправляет превью с кнопкой подтверждения "
        "(комментарий появится после нажатия). Текст — дословно от пользователя.",
        mutating=True,
        risk="low",
    ),
    "delete_task": ToolMeta(
        DeleteTaskArgs,
        "Удалить задачу. Высокий риск: инструмент только ОТПРАВЛЯЕТ подтверждение, "
        "удаление произойдёт после кнопки пользователя.",
        mutating=True,
        risk="high",
    ),
    "delete_knowledge": ToolMeta(
        DeleteKnowledgeArgs,
        "Удалить запись знаний. Высокий риск: инструмент только ОТПРАВЛЯЕТ "
        "подтверждение, удаление произойдёт после кнопки пользователя.",
        mutating=True,
        risk="high",
    ),
    "delete_document": ToolMeta(
        DeleteDocumentArgs,
        "Удалить документ. Высокий риск: инструмент только ОТПРАВЛЯЕТ подтверждение, "
        "удаление произойдёт после кнопки пользователя.",
        mutating=True,
        risk="high",
    ),
    "update_comment": ToolMeta(
        UpdateCommentArgs,
        "Изменить текст комментария задачи (id комментария — из get_task). Отправляет "
        "превью с кнопкой подтверждения.",
        mutating=True,
        risk="low",
    ),
    "rename_category": ToolMeta(
        RenameCategoryArgs,
        "Переименовать категорию-папку по текущему названию. Отправляет превью с "
        "кнопкой подтверждения.",
        mutating=True,
        risk="low",
    ),
    "list_projects": ToolMeta(
        ListProjectsArgs,
        "Актуальные проекты (id и названия) — чтобы сопоставить название из речи.",
        mutating=False,
        risk="low",
    ),
    "search_all": ToolMeta(
        SearchAllArgs,
        "Поиск по знаниям, задачам и заголовкам документов — сначала проверь "
        "внутреннюю базу, и только потом внешний интернет.",
        mutating=False,
        risk="low",
    ),
    "get_knowledge": ToolMeta(
        GetKnowledgeArgs,
        "Полный текст записи знаний по id (из search_all).",
        mutating=False,
        risk="low",
    ),
    "send_document": ToolMeta(
        SendDocumentArgs,
        "Отправить пользователю файл документа вложением (расшифровывается на время "
        "отправки). Вызывай, когда просят выдать/прислать/вывести найденный документ "
        "файлом. Файл уйдёт в чат сразу вместе с ответом.",
        mutating=False,
        risk="low",
    ),
    "web_search": ToolMeta(
        WebSearchArgs,
        "Внешний веб-поиск. Только когда нужны внешние факты (документация, "
        "сравнение подходов), не для данных приложения.",
        mutating=False,
        risk="low",
    ),
    "report_capability_gap": ToolMeta(
        ReportGapArgs,
        "Записать в improvement backlog объективно отсутствующую возможность Jarvis. "
        "Перед вызовом посмотри list_improvements (category=missing_capability): если "
        "похожий пробел уже есть — используй ТОЧНО его заголовок, чтобы увеличить "
        "счётчик, а не создать дубликат. Вызывай только когда запрос невозможен "
        "из-за пробела в продукте, а не из-за ошибки пользователя, пустого поиска "
        "или сетевого сбоя.",
        mutating=True,
        risk="low",
    ),
    "list_improvements": ToolMeta(
        ListImprovementsArgs,
        "Improvement backlog: записи о сбоях, пробелах возможностей и идеях "
        "(id, заголовок, категория, статус, источник, сколько раз встречалось). "
        "Используй, когда просят показать backlog/проблемы/что записано на улучшение.",
        mutating=False,
        risk="low",
    ),
    "update_improvement": ToolMeta(
        UpdateImprovementArgs,
        "Сменить статус записи improvement backlog (как кнопки статусов в "
        "/diagnostics). Применяется сразу, без кнопки подтверждения — вызывай только "
        "по явной просьбе пользователя. id бери из list_improvements.",
        mutating=True,
        risk="medium",
    ),
    "daily_stats": ToolMeta(
        DailyStatsArgs,
        "Дневная статистика: успешные действия по типам (создано/изменено/удалено), "
        "закрытые задачи и новые записи improvement backlog за день. Используй для "
        "вопросов «что сделано за день», «сколько закрыто», «что нового в бэклоге».",
        mutating=False,
        risk="low",
    ),
    "export_chat": ToolMeta(        ExportChatArgs,
        "Выгрузить журнал диалога за день двумя файлами (Markdown + JSON) — они "
        "уйдут в чат вместе с ответом. Текст уже с маскировкой секретов. Используй, "
        "когда просят экспортировать/выгрузить переписку.",
        mutating=False,
        risk="low",
    ),
    "export_issues": ToolMeta(
        ExportIssuesArgs,
        "Выгрузить improvement backlog (диагностику) двумя файлами Markdown + JSON — "
        "уйдут в чат вместе с ответом. По умолчанию — только открытые записи. "
        "Используй, когда просят экспортировать backlog/проблемы/диагностику файлом.",
        mutating=False,
        risk="low",
    ),
    "create_knowledge": ToolMeta(
        CreateKnowledgeArgs,
        "Сохранить запись знаний (заметку/инструкцию/решение) в базу, при желании — "
        "в проект. Создаёт pending-операцию: запись сохранится после кнопки "
        "подтверждения. Для кредов/паролей/токенов обязательно sensitive=true.",
        mutating=True,
        risk="medium",
    ),
    "update_knowledge": ToolMeta(
        UpdateKnowledgeArgs,
        "Изменить заметку знаний: заголовок, сводку, текст, категорию, проект, "
        "уровень чувствительности. Отправляет превью с кнопкой подтверждения — "
        "изменение применится после нажатия. Работает и с чувствительными "
        "заметками; указывай только те поля, которые меняешь.",
        mutating=True,
        risk="medium",
    ),
    "update_document": ToolMeta(
        UpdateDocumentArgs,
        "Изменить название и/или уровень чувствительности документа по id "
        "(из search_all). Отправляет превью с кнопкой подтверждения — изменение "
        "применится после нажатия.",
        mutating=True,
        risk="medium",
    ),
}


class ToolRegistry:
    """Реестр, привязанный к сессии БД, пользователю и контексту запроса."""

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        telegram_user_id: int,
        search_provider: WebSearchProvider,
        correlation_id: str | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._user_id = telegram_user_id
        self._search_provider = search_provider
        self._correlation_id = correlation_id
        self.surfaced: list[EntityCandidate] = []
        # Все pending-операции, предложенные за этот запрос (каждой нужна кнопка).
        self.pending_operation_ids: list[str] = []
        # Файлы к отправке в чат (документ, экспорт чата) — уйдут с ответом.
        self.outgoing_files: list[OutgoingFile] = []

    def catalog(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name=name,
                description=meta.description,
                parameters=meta.args_model.model_json_schema(),
                mutating=meta.mutating,
                risk=meta.risk,
            )
            for name, meta in _TOOLS.items()
        ]

    async def call(self, name: str, args: dict[str, Any] | None) -> ToolResult:
        meta = _TOOLS.get(name)
        if meta is None:
            return ToolResult.failure("validation", f"неизвестный инструмент: {name}")
        try:
            parsed = meta.args_model.model_validate(args or {})
        except ValidationError as error:
            first = error.errors()[0]
            location = ".".join(str(part) for part in first.get("loc", ()))
            return ToolResult.failure(
                "validation", f"некорректные аргументы ({location}): {first.get('msg')}"
            )
        try:
            return await getattr(self, f"_{name}")(parsed)
        except NotFoundError as error:
            return ToolResult.failure("not_found", str(error), mutating=meta.mutating)
        except OperationError as error:
            return ToolResult.failure("validation", str(error), mutating=meta.mutating)
        except WebSearchUnavailable as error:
            return ToolResult.failure("unsupported", str(error))
        except (OperationalError, InterfaceError):
            log.warning("agent tool %s hit a transient database error", name)
            return ToolResult.failure(
                "transient", "временная ошибка базы данных, повторите вызов", mutating=meta.mutating
            )
        except Exception:  # noqa: BLE001 - внутрь LLM не должны утекать детали
            log.exception("agent tool %s failed", name)
            return ToolResult.failure(
                "internal", "внутренняя ошибка инструмента", mutating=meta.mutating
            )

    # --- Чтение ---

    async def _list_tasks(self, args: ListTasksArgs) -> ToolResult:
        service = TaskService(self._session)
        if args.on_date is not None:
            tasks = await service.list_on_date(
                args.on_date, tz=self._settings.tz, limit=args.limit, offset=args.offset
            )
            await self._surface_tasks(tasks)
            return ToolResult(
                ok=True,
                data={
                    "date": args.on_date.isoformat(),
                    "items": await self._task_briefs(tasks),
                    "offset": args.offset,
                    "limit": args.limit,
                },
            )
        if args.category:
            category = await CategoryRepository(self._session).find_by_name(args.category)
            if category is None:
                return ToolResult.failure(
                    "not_found",
                    f"категория «{args.category}» не найдена; список категорий — в /settings",
                )
            tasks = await TaskRepository(self._session).list_by_category(
                category.id, limit=args.limit, offset=args.offset
            )
            await self._surface_tasks(tasks)
            return ToolResult(
                ok=True,
                data={"category": category.name, "items": await self._task_briefs(tasks)},
            )
        now = datetime.now(self._settings.tz)
        tasks = await service.list_view(
            args.view,
            now=now,
            tz=self._settings.tz,
            limit=args.limit,
            offset=args.offset,
        )
        await self._surface_tasks(tasks)
        return ToolResult(
            ok=True,
            data={
                "view": args.view,
                "items": await self._task_briefs(tasks),
                "offset": args.offset,
                "limit": args.limit,
            },
        )

    async def _list_project_tasks(self, args: ListProjectTasksArgs) -> ToolResult:
        project = await ProjectRepository(self._session).find_by_name(args.project)
        if project is None:
            return ToolResult.failure(
                "not_found", f"проект «{args.project}» не найден; посмотри list_projects"
            )
        tasks = await TaskRepository(self._session).list_for_project(
            project.id, limit=min(args.limit * 2, 60)
        )
        if not args.include_done:
            tasks = [task for task in tasks if task.status not in DONE_STATUSES]
        tasks = tasks[: args.limit]
        await self._surface_tasks(tasks)
        return ToolResult(
            ok=True,
            data={
                "project": project.name,
                "project_id": str(project.id),
                "items": await self._task_briefs(tasks),
                "include_done": args.include_done,
            },
        )

    async def _search_tasks(self, args: SearchTasksArgs) -> ToolResult:
        repo = TaskRepository(self._session)
        tasks = await repo.search(args.query, limit=args.limit)
        if not tasks:
            tasks = await repo.search_like(args.query, limit=args.limit)
        await self._surface_tasks(tasks)
        return ToolResult(
            ok=True,
            data={"query": args.query, "items": await self._task_briefs(tasks)},
        )

    async def _get_task(self, args: GetTaskArgs) -> ToolResult:
        task = await TaskRepository(self._session).get(args.task_id)
        if task is None:
            return ToolResult.failure("not_found", "задача не найдена или удалена")
        comments = await TaskCommentRepository(self._session).list_for_task(
            task.id, limit=COMMENTS_IN_CARD
        )
        await self._surface_task(task)
        brief = (await self._task_briefs([task]))[0]
        brief["description"] = task.description
        brief["updated_at"] = task.updated_at.isoformat()
        brief["comments"] = [
            {
                "id": str(comment.id),
                "content": comment.content[:500],
                "created_at": comment.created_at.isoformat(),
            }
            for comment in comments
        ]
        return ToolResult(ok=True, data={"task": brief})

    async def _list_comments(self, args: ListCommentsArgs) -> ToolResult:
        repo = TaskRepository(self._session)
        candidates = await repo.search(args.task, limit=5)
        if not candidates:
            candidates = await repo.search_like(args.task, limit=5)
        if not candidates:
            return ToolResult.failure("not_found", f"задача «{args.task}» не найдена")
        if len(candidates) > 1:
            for task in candidates:
                await self._surface_task(task)
            return ToolResult(
                ok=True,
                data={
                    "status": "multiple_matches",
                    "candidates": [
                        {"task_id": str(task.id), "title": task.title}
                        for task in candidates
                    ],
                    "note": "подходит несколько задач — уточни у пользователя, к какой",
                },
            )
        task = candidates[0]
        await self._surface_task(task)
        comments = await TaskCommentRepository(self._session).list_for_task(task.id)
        return ToolResult(
            ok=True,
            data={
                "task_id": str(task.id),
                "title": task.title,
                "comments": [
                    {
                        "content": comment.content[:500],
                        "created_at": comment.created_at.isoformat(),
                    }
                    for comment in comments
                ],
            },
        )

    async def _list_improvements(self, args: ListImprovementsArgs) -> ToolResult:
        repo = ImprovementRepository(self._session)
        statuses: tuple[ImprovementStatus, ...] | None = None
        status = (args.status or "open").strip().lower()
        if status == "open":
            statuses = (
                ImprovementStatus.NEW,
                ImprovementStatus.REVIEWED,
                ImprovementStatus.PLANNED,
            )
        elif status != "all":
            try:
                statuses = (ImprovementStatus(status),)
            except ValueError:
                return ToolResult.failure(
                    "validation",
                    "status должен быть один из: open, all, new, reviewed, planned, "
                    "implemented, wont_fix, archived",
                )
        category: ImprovementCategory | None = None
        if args.category:
            try:
                category = ImprovementCategory(args.category.strip().lower())
            except ValueError:
                return ToolResult.failure(
                    "validation",
                    "category должен быть failure|missing_capability|ux_friction|improvement_idea",
                )
        items = await repo.list_items(
            statuses=statuses, category=category, limit=args.limit
        )
        counts = await repo.counts_by_status()
        return ToolResult(
            ok=True,
            data={
                "filter": {"status": status, "category": args.category},
                "counts_by_status": {
                    key.value: value for key, value in counts.items()
                },
                "items": [
                    {
                        "id": str(item.id),
                        "title": item.title,
                        "category": item.category.value,
                        "status": item.status.value,
                        "source": item.source.value,
                        "occurrence_count": item.occurrence_count,
                        "summary": item.summary[:400],
                        "last_seen_at": item.last_seen_at.astimezone(
                            self._settings.tz
                        ).isoformat(),
                    }
                    for item in items
                ],
                "note": "статусы записей меняются в /diagnostics кнопками",
            },
        )

    async def _list_projects(self, args: ListProjectsArgs) -> ToolResult:
        projects = await ProjectRepository(self._session).list_active(limit=args.limit)
        self._surface(
            [
                EntityCandidate(kind="project", id=project.id, title=project.name)
                for project in projects
            ]
        )
        return ToolResult(
            ok=True,
            data={
                "items": [
                    {"id": str(project.id), "name": project.name, "status": project.status.value}
                    for project in projects
                ]
            },
        )

    async def _search_all(self, args: SearchAllArgs) -> ToolResult:
        hits = await SearchService(self._session).search(args.query, limit=args.limit)
        items = []
        for hit in hits:
            items.append(
                {
                    "kind": hit.kind,
                    "id": str(hit.entity_id),
                    "title": hit.title,
                    "snippet": hit.snippet,
                }
            )
            if hit.kind in ("task", "knowledge"):
                self._surface([EntityCandidate(kind=hit.kind, id=hit.entity_id, title=hit.title)])
        return ToolResult(ok=True, data={"query": args.query, "items": items})

    async def _get_knowledge(self, args: GetKnowledgeArgs) -> ToolResult:
        item = await KnowledgeRepository(self._session).get(args.item_id)
        if item is None:
            return ToolResult.failure("not_found", "запись знаний не найдена")
        self._surface([EntityCandidate(kind="knowledge", id=item.id, title=item.title)])
        return ToolResult(
            ok=True,
            data={
                "id": str(item.id),
                "title": item.title,
                "type": item.type.value,
                "content": item.content[:2000],
                "updated_at": item.updated_at.isoformat(),
            },
        )

    async def _send_document(self, args: SendDocumentArgs) -> ToolResult:
        from jarvis.documents.storage import EncryptedFileStorage
        from jarvis.services.documents import TELEGRAM_SEND_LIMIT, DocumentService

        storage = EncryptedFileStorage(
            self._settings.document_storage_path, self._settings.document_keyring()
        )
        document, data = await DocumentService(
            self._session, self._settings, storage
        ).read_file(args.document_id)
        if len(data) > TELEGRAM_SEND_LIMIT:
            return ToolResult.failure(
                "unsupported", "файл слишком большой для отправки через Telegram"
            )
        filename = (
            PurePosixPath(document.original_filename).name
            if document.original_filename
            else f"{document.title}.bin"
        )
        self.outgoing_files.append(
            OutgoingFile(
                filename=filename,
                data=data,
                caption="📤 Файл расшифрован для отправки. Удалите сообщение после использования.",
            )
        )
        return ToolResult(
            ok=True,
            data={
                "title": document.title,
                "filename": filename,
                "size_bytes": len(data),
                "note": "файл отправится в чат вместе с ответом",
            },
        )

    async def _daily_stats(self, args: DailyStatsArgs) -> ToolResult:
        day = args.day or datetime.now(self._settings.tz).date()
        start = local_day_start(day, self._settings.tz)
        end = local_day_start(day + timedelta(days=1), self._settings.tz)
        actions = await AuditRepository(self._session).count_actions_between(start, end)
        tasks = TaskRepository(self._session)
        completed = await tasks.list_completed_between(start, end)
        completed_count = await tasks.count_completed_between(start, end)
        improvements = ImprovementRepository(self._session)
        new_items = await improvements.list_first_seen_between(start, end)
        new_count = await improvements.count_first_seen_between(start, end)
        return ToolResult(
            ok=True,
            data={
                "date": day.isoformat(),
                "actions": actions,
                "tasks_completed": {
                    "count": completed_count,
                    "items": [task.title for task in completed],
                },
                "new_improvements": {
                    "count": new_count,
                    "items": [
                        {"title": item.title, "category": item.category.value}
                        for item in new_items
                    ],
                },
            },
        )

    async def _export_chat(self, args: ExportChatArgs) -> ToolResult:
        from jarvis.services.chatlog import export_chat_day

        day = args.day or datetime.now(self._settings.tz).date()
        result = await export_chat_day(self._session, self._settings, day)
        stamp = day.strftime("%Y%m%d")
        self.outgoing_files.append(
            OutgoingFile(filename=f"jarvis_chat_{stamp}.md", data=result.markdown.encode("utf-8"))
        )
        self.outgoing_files.append(
            OutgoingFile(
                filename=f"jarvis_chat_{stamp}.json", data=result.json_text.encode("utf-8")
            )
        )
        return ToolResult(
            ok=True,
            data={
                "date": day.isoformat(),
                "message_count": result.message_count,
                "truncated": result.truncated,
                "note": "два файла (MD и JSON) отправятся в чат вместе с ответом",
            },
        )

    async def _export_issues(self, args: ExportIssuesArgs) -> ToolResult:
        from jarvis.services.improvement_export import export_backlog

        status = args.status.strip()
        statuses: tuple[ImprovementStatus, ...] | None = OPEN_STATUSES
        if status == "all":
            statuses = None
        elif status != "open":
            try:
                statuses = tuple(
                    ImprovementStatus(part.strip())
                    for part in status.split(",")
                    if part.strip()
                )
            except ValueError:
                return ToolResult.failure("validation", "неизвестный статус")
        category = None
        if args.category:
            try:
                category = ImprovementCategory(args.category)
            except ValueError:
                return ToolResult.failure("validation", "неизвестная категория")
        result = await export_backlog(
            ImprovementRepository(self._session),
            statuses=statuses,
            category=category,
            tz=self._settings.tz,
        )
        stamp = datetime.now(self._settings.tz).strftime("%Y%m%d_%H%M%S")
        self.outgoing_files.append(
            OutgoingFile(
                filename=f"jarvis_issues_{stamp}.md", data=result.markdown.encode("utf-8")
            )
        )
        self.outgoing_files.append(
            OutgoingFile(
                filename=f"jarvis_issues_{stamp}.json",
                data=result.json_text.encode("utf-8"),
            )
        )
        return ToolResult(
            ok=True,
            data={
                "item_count": result.item_count,
                "note": "два файла (MD и JSON) отправятся в чат вместе с ответом",
            },
        )

    async def _web_search(self, args: WebSearchArgs) -> ToolResult:
        limit = min(args.limit, self._settings.web_search_max_results)
        results = await self._search_provider.search(args.query, limit=limit)
        return ToolResult(
            ok=True,
            data={
                "query": args.query,
                "provider": self._search_provider.name,
                "results": [
                    {"title": result.title, "url": result.url, "snippet": result.snippet}
                    for result in results
                ],
            },
        )

    # --- Мутации ---

    async def _propose(
        self, operation_type: PendingOpType, payload: dict, *, summary: str
    ) -> ToolResult:
        """Мутация предлагается пользователю: pending-операция + кнопка подтверждения."""
        operation_id = await ConfirmationService(self._session, self._settings).propose(
            self._user_id, operation_type, payload
        )
        self.pending_operation_ids.append(operation_id)
        return ToolResult(
            ok=True,
            mutating=True,
            data={
                "status": "confirmation_required",
                "operation_id": operation_id,
                "note": (
                    "операция НЕ выполнена: пользователю отправлено превью с кнопкой "
                    "«Подтвердить»; применяется только после нажатия"
                ),
                "summary": summary,
            },
        )

    async def _create_knowledge(self, args: CreateKnowledgeArgs) -> ToolResult:
        sensitive = args.sensitive or looks_like_secret(args.content) or looks_like_secret(
            args.title
        )
        draft = KnowledgeDraft(
            type=args.knowledge_type,
            title=args.title,
            content=args.content,
            source_type=KnowledgeSource.TEXT,
            original_text=args.content,
            project_name=args.project,
            sensitivity=Sensitivity.SENSITIVE if sensitive else Sensitivity.NORMAL,
        )
        result = await self._propose(
            PendingOpType.CREATE_KNOWLEDGE,
            draft.model_dump(mode="json"),
            summary=f"запись «{args.title}»",
        )
        result.data["title"] = args.title
        result.data["project"] = args.project
        result.data["sensitivity"] = draft.sensitivity.value
        return result

    async def _create_task(self, args: CreateTaskArgs) -> ToolResult:
        category_id = None
        category_name = None
        if args.category:
            category = await CategoryRepository(self._session).find_by_name(args.category)
            if category is not None:
                category_id = category.id
            else:
                # Категории нет: предлагаем её создать (если ещё не предложена) и
                # привязываем задачу по имени — id разрешится при подтверждении.
                proposed = await self._create_category(CreateCategoryArgs(name=args.category))
                created_id = proposed.data.get("category_id")
                if created_id:
                    category_id = uuid.UUID(str(created_id))
                else:
                    category_name = args.category
        draft = TaskDraft(
            title=args.title,
            description=args.description,
            due_at=args.due_at,
            bucket=args.bucket,
            project_name=args.project,
            category_id=category_id,
            category_name=category_name,
            **({"priority": args.priority} if args.priority else {}),
        )
        result = await self._propose(
            PendingOpType.CREATE_TASK,
            draft.model_dump(mode="json"),
            summary=f"задача «{args.title}»",
        )
        result.data["title"] = args.title
        if category_name:
            result.data["category"] = category_name
            result.data["note"] = (
                f"категории «{category_name}» ещё нет: отправлены две кнопки — "
                "подтверждение создания категории и подтверждение задачи "
                "(задача встанет в эту категорию)"
            )
        return result

    async def _create_category(self, args: CreateCategoryArgs) -> ToolResult:
        name = args.name.strip()[:120]
        existing = await CategoryRepository(self._session).find_by_name(name)
        if existing is not None:
            return ToolResult(
                ok=True,
                mutating=True,
                data={
                    "category_id": str(existing.id),
                    "name": existing.name,
                    "note": "категория уже существует",
                },
            )
        if await self._category_already_proposed(name):
            return ToolResult(
                ok=True,
                mutating=True,
                data={
                    "name": name,
                    "status": "confirmation_required",
                    "note": "категория уже предложена на подтверждение ранее в этом диалоге",
                },
            )
        return await self._propose(
            PendingOpType.CREATE_CATEGORY,
            {"name": name},
            summary=f"категория «{name}»",
        )

    async def _rename_category(self, args: RenameCategoryArgs) -> ToolResult:
        category = await CategoryRepository(self._session).find_by_name(args.name)
        if category is None:
            return ToolResult.failure(
                "not_found", f"категория «{args.name}» не найдена", mutating=True
            )
        new_name = args.new_name.strip()[:120]
        result = await self._propose(
            PendingOpType.RENAME_CATEGORY,
            {"category_id": str(category.id), "new_name": new_name},
            summary=f"категория «{category.name}» → «{new_name}»",
        )
        result.data["category_id"] = str(category.id)
        return result

    async def _category_already_proposed(self, name: str) -> bool:
        """Уже висит неподтверждённое предложение создать такую категорию."""
        from sqlalchemy import select

        from jarvis.db.models import PendingOperation
        from jarvis.domain.enums import PendingStatus

        result = await self._session.execute(
            select(PendingOperation).where(
                PendingOperation.telegram_user_id == self._user_id,
                PendingOperation.operation_type == PendingOpType.CREATE_CATEGORY,
                PendingOperation.status == PendingStatus.PENDING,
            )
        )
        for operation in result.scalars():
            if str(operation.payload.get("name", "")).strip().lower() == name.lower():
                return True
        return False

    async def _create_project(self, args: CreateProjectArgs) -> ToolResult:
        name = args.name.strip()[:200]
        existing = await ProjectRepository(self._session).find_by_name(name)
        if existing is not None:
            return ToolResult(
                ok=True,
                mutating=True,
                data={
                    "project_id": str(existing.id),
                    "name": existing.name,
                    "note": "проект уже существует",
                },
            )
        return await self._propose(
            PendingOpType.CREATE_PROJECT,
            {"name": name, "description": args.description},
            summary=f"проект «{name}»",
        )

    async def _update_task(self, args: UpdateTaskArgs) -> ToolResult:
        # exclude_unset: меняем только явно переданные поля; mode="json" — payload уйдёт в JSONB.
        raw = args.model_dump(exclude={"task_id"}, exclude_unset=True, mode="json")
        changes = {
            key: value for key, value in raw.items() if value is not None and key != "project"
        }
        if "due_at" in raw and raw["due_at"] is None:
            changes["due_at"] = None  # явный null — убрать срок
        if raw.get("project") is not None:
            name = str(raw["project"]).strip()
            if name.lower() in ("нет", "без проекта", "none", "-"):
                changes["project_id"] = None
            else:
                project = await ProjectRepository(self._session).find_by_name(name)
                if project is not None:
                    changes["project_id"] = str(project.id)
                else:
                    changes["project_name"] = name  # создастся при подтверждении
        if not changes:
            return ToolResult.failure("validation", "не указаны изменения", mutating=True)
        task = await TaskRepository(self._session).get(args.task_id)
        if task is None:
            return ToolResult.failure("not_found", "задача не найдена", mutating=True)
        result = await self._propose(
            PendingOpType.UPDATE_TASK,
            {"task_id": str(args.task_id), "changes": changes},
            summary=f"задача «{task.title}»",
        )
        result.data["changes"] = sorted(changes)
        return result

    async def _add_comment(self, args: AddCommentArgs) -> ToolResult:
        task = await TaskRepository(self._session).get(args.task_id)
        if task is None:
            return ToolResult.failure("not_found", "задача не найдена", mutating=True)
        result = await self._propose(
            PendingOpType.CREATE_COMMENT,
            CommentDraft(task_id=args.task_id, content=args.content).model_dump(
                mode="json"
            ),
            summary=f"комментарий к «{task.title}»",
        )
        result.data["task_title"] = task.title
        return result

    async def _update_comment(self, args: UpdateCommentArgs) -> ToolResult:
        comment = await TaskCommentRepository(self._session).get(args.comment_id)
        if comment is None:
            return ToolResult.failure("not_found", "комментарий не найден", mutating=True)
        result = await self._propose(
            PendingOpType.UPDATE_COMMENT,
            {
                "comment_id": str(comment.id),
                "task_id": str(comment.task_id),
                "content": args.content,
            },
            summary="комментарий",
        )
        result.data["comment_id"] = str(comment.id)
        return result

    async def _update_knowledge(self, args: UpdateKnowledgeArgs) -> ToolResult:
        repo = KnowledgeRepository(self._session)
        item = await repo.get(args.item_id)
        if item is None:
            return ToolResult.failure("not_found", "запись не найдена", mutating=True)
        changes: dict = {}
        if args.title is not None:
            changes["title"] = args.title
        if args.summary is not None:
            changes["summary"] = args.summary
        if args.content is not None:
            changes["content"] = args.content
        if args.category is not None:
            category = await CategoryRepository(self._session).find_by_name(args.category)
            if category is None:
                return ToolResult.failure(
                    "not_found",
                    f"категория «{args.category}» не найдена (создаётся в /settings)",
                    mutating=True,
                )
            changes["category_id"] = str(category.id)
        if args.project is not None:
            project = await ProjectRepository(self._session).find_by_name(args.project)
            if project is None:
                return ToolResult.failure(
                    "not_found",
                    f"проект «{args.project}» не найден (посмотри list_projects)",
                    mutating=True,
                )
            changes["project_id"] = str(project.id)
        if args.sensitive is not None:
            changes["sensitivity"] = (
                Sensitivity.SENSITIVE.value if args.sensitive else Sensitivity.NORMAL.value
            )
        if not changes:
            return ToolResult.failure("validation", "не указаны изменения", mutating=True)
        result = await self._propose(
            PendingOpType.UPDATE_KNOWLEDGE,
            {"item_id": str(args.item_id), "changes": changes},
            summary=f"запись «{item.title}»",
        )
        result.data["changes"] = sorted(changes)
        return result

    async def _update_document(self, args: UpdateDocumentArgs) -> ToolResult:
        from jarvis.repositories.documents import DocumentRepository

        changes: dict = {}
        if args.title is not None:
            changes["title"] = args.title.strip()
        if args.category is not None:
            name = args.category.strip()
            if name.lower() in ("нет", "без категории", "none", "-", ""):
                changes["category_id"] = None
            else:
                category = await CategoryRepository(self._session).find_by_name(name)
                if category is None:
                    return ToolResult.failure(
                        "not_found",
                        f"категория «{name}» не найдена (создаётся в /settings)",
                        mutating=True,
                    )
                changes["category_id"] = str(category.id)
        if args.sensitive is not None:
            changes["sensitivity"] = (
                Sensitivity.SENSITIVE if args.sensitive else Sensitivity.NORMAL
            ).value
        if not changes:
            return ToolResult.failure("validation", "не указаны изменения", mutating=True)
        document = await DocumentRepository(self._session).get(args.document_id)
        if document is None:
            return ToolResult.failure("not_found", "документ не найден", mutating=True)
        result = await self._propose(
            PendingOpType.UPDATE_DOCUMENT,
            {"document_id": str(args.document_id), **changes},
            summary=f"документ «{document.title}»",
        )
        result.data["changes"] = sorted(changes)
        return result

    async def _update_improvement(self, args: UpdateImprovementArgs) -> ToolResult:
        repo = ImprovementRepository(self._session)
        item = await repo.get(args.item_id)
        if item is None:
            return ToolResult.failure("not_found", "запись бэклога не найдена", mutating=True)
        await repo.update_status(item, args.status)
        return ToolResult(
            ok=True,
            mutating=True,
            data={
                "item_id": str(item.id),
                "title": item.title,
                "status": args.status.value,
            },
        )

    async def _delete_task(self, args: DeleteTaskArgs) -> ToolResult:
        task = await TaskRepository(self._session).get(args.task_id)
        if task is None:
            return ToolResult.failure("not_found", "задача не найдена", mutating=True)
        result = await self._propose(
            PendingOpType.DELETE_TASK,
            {"entity_id": str(task.id)},
            summary=f"удаление задачи «{task.title}»",
        )
        result.data["task_title"] = task.title
        return result

    async def _delete_knowledge(self, args: DeleteKnowledgeArgs) -> ToolResult:
        item = await KnowledgeRepository(self._session).get(args.item_id)
        if item is None:
            return ToolResult.failure("not_found", "запись не найдена", mutating=True)
        result = await self._propose(
            PendingOpType.DELETE_KNOWLEDGE,
            {"entity_id": str(item.id)},
            summary=f"удаление записи «{item.title}»",
        )
        result.data["title"] = item.title
        return result

    async def _delete_document(self, args: DeleteDocumentArgs) -> ToolResult:
        from jarvis.repositories.documents import DocumentRepository

        document = await DocumentRepository(self._session).get(args.document_id)
        if document is None:
            return ToolResult.failure("not_found", "документ не найден", mutating=True)
        result = await self._propose(
            PendingOpType.DELETE_DOCUMENT,
            {"entity_id": str(document.id)},
            summary=f"удаление документа «{document.title}»",
        )
        result.data["title"] = document.title
        return result

    async def _report_capability_gap(self, args: ReportGapArgs) -> ToolResult:
        item = await ImprovementService(self._session, self._settings).record_gap(
            title=args.title,
            summary=args.summary,
            evidence=args.evidence,
            correlation_id=self._correlation_id,
        )
        return ToolResult(
            ok=True,
            mutating=True,
            data={"item_id": str(item.id), "occurrence_count": item.occurrence_count},
        )

    # --- Вспомогательное ---

    def _surface(self, candidates: list[EntityCandidate]) -> None:
        for candidate in candidates:
            if candidate not in self.surfaced:
                self.surfaced.append(candidate)
        del self.surfaced[:-MAX_SURFACED]

    async def _surface_task(self, task: Any) -> None:
        if task is not None:
            self._surface([EntityCandidate(kind="task", id=task.id, title=task.title)])

    async def _surface_tasks(self, tasks: list) -> None:
        self._surface(
            [EntityCandidate(kind="task", id=task.id, title=task.title) for task in tasks]
        )

    async def _task_briefs(self, tasks: list) -> list[dict[str, Any]]:
        projects = await self._project_names(tasks)
        return [
            task_brief(task, projects, tz=self._settings.tz) for task in tasks
        ]

    async def _project_names(self, tasks: list) -> dict[uuid.UUID, str]:
        project_ids = {task.project_id for task in tasks if task.project_id}
        if not project_ids:
            return {}
        repo = ProjectRepository(self._session)
        names: dict[uuid.UUID, str] = {}
        for project_id in project_ids:
            project = await repo.get(project_id)
            if project is not None:
                names[project_id] = project.name
        return names


def task_brief(
    task, projects: dict[uuid.UUID, str] | None = None, tz=None
) -> dict[str, Any]:
    due_local = None
    if task.due_at is not None and tz is not None:
        due_local = task.due_at.astimezone(tz).isoformat()
    return {
        "id": str(task.id),
        "title": task.title,
        "status": task.status.value,
        "bucket": task.bucket.value if task.bucket else None,
        "priority": task.priority.value,
        "due_at": task.due_at.isoformat() if task.due_at else None,
        "due_at_local": due_local,
        "project": (projects or {}).get(task.project_id),
    }


__all__ = ["ToolRegistry", "ToolResult", "ToolSpec", "task_brief"]
