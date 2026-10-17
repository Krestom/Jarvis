"""Типизированные callback payload'ы (всегда короткие, серверные идентификаторы)."""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class MenuCB(CallbackData, prefix="mn"):
    # main|search|tasks|knowledge|documents|projects|settings|secrets|cancel_flow|
    # category_new|category_rename|project_new|secret_new|diagnostics
    action: str
    value: str = ""


class TaskListCB(CallbackData, prefix="tl"):
    # today | week | long_term | someday | no_group | inbox | all | done | date | category
    view: str
    page: int = 0
    value: str = ""  # id категории (view="category") или ISO-дата (view="date")


class TaskCB(CallbackData, prefix="tsk"):
    action: str  # view | due | bucket | done | delete | related | reopen
    task_id: str = ""  # uuid.hex
    value: str = ""


class KbListCB(CallbackData, prefix="kl"):
    page: int = 0


class KbCB(CallbackData, prefix="kb"):
    action: str  # view | full | similar | delete | related
    item_id: str = ""


class DocListCB(CallbackData, prefix="dl"):
    page: int = 0
    category: str = ""  # uuid.hex категории; "*" — показать список категорий


class DocCB(CallbackData, prefix="dc"):
    action: str  # view | download | delete | category | category_set | related
    doc_id: str = ""
    value: str = ""  # категория для category_set (uuid.hex или none)


class ConfirmCB(CallbackData, prefix="op"):
    action: str  # apply | edit | cancel
    op_id: str


class SearchCB(CallbackData, prefix="sr"):
    action: str  # page_next | page_prev | open
    page: int = 0
    kind: str = ""  # knowledge | task | document
    entity: str = ""  # uuid.hex


class SecretCB(CallbackData, prefix="sc"):
    action: str  # view | reveal | delete
    secret_id: str = ""


class SecretListCB(CallbackData, prefix="sl"):
    page: int = 0


class ImpListCB(CallbackData, prefix="il"):
    kind: str = "open"  # open | all | status | category
    value: str = ""  # статус или категория, когда kind их задаёт
    page: int = 0


class ImpCB(CallbackData, prefix="imp"):
    action: str  # view | status | delete | delete_yes | delete_no | export
    item_id: str = ""
    value: str = ""
    page: int = 0


class AgentPickCB(CallbackData, prefix="ap"):
    """Выбор сущности из кандидатов агента (индекс в сохранённом контексте)."""

    index: int


def uuid_hex(value) -> str:
    return str(value).replace("-", "")
