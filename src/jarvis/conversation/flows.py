"""Сценарии (flows) краткосрочного контекста диалога."""

from __future__ import annotations

from enum import StrEnum


class Flow(StrEnum):
    SEARCH_WAIT = "search_wait"
    CLARIFY_DRAFT = "clarify_draft"
    EDIT_PENDING = "edit_pending"
    AWAIT_DOCUMENT_UPLOAD = "await_document_upload"
    AWAIT_SECRET_VALUE = "await_secret_value"
    CREATE_CATEGORY = "create_category"
    EDIT_CATEGORY = "edit_category"
    CREATE_PROJECT = "create_project"
    EDIT_TASK_DUE = "edit_task_due"
    AWAIT_COMMENT = "await_comment"
    EDIT_COMMENT = "edit_comment"
    CHOOSE_TASK_FOR_COMMENT = "choose_task_for_comment"
    CHOOSE_DOC_CATEGORY = "choose_doc_category"
    FORCE_SAVE_PENDING = "force_save_pending"
