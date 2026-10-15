"""Типизированные callback payload'ы (всегда короткие, серверные идентификаторы)."""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class MenuCB(CallbackData, prefix="mn"):
    # main|search|tasks|knowledge|documents|projects|settings|secrets|cancel_flow|
    # category_new|category_rename|project_new|secret_new
    action: str
    value: str = ""


class TaskListCB(CallbackData, prefix="tl"):
    view: str  # today | week | long_term | someday | no_group | inbox | done
    page: int = 0


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


class DocCB(CallbackData, prefix="dc"):
    action: str  # view | download | delete | category | related
    doc_id: str = ""


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


def uuid_hex(value) -> str:
    return str(value).replace("-", "")
