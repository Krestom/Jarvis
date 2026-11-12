"""Документы: список, карточка, загрузка файла, выдача, удаление."""

from __future__ import annotations

import logging
from io import BytesIO
from pathlib import PurePosixPath

from aiogram import F, Router
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.bot.callbacks import DocCB, DocListCB
from jarvis.bot.formatting import esc
from jarvis.bot.handlers.open_entity import parse_hex_uuid, render_document_card
from jarvis.bot.handlers.render import propose_and_send, send_or_edit
from jarvis.bot.keyboards import (
    PAGE_SIZE,
    documents_list_keyboard,
    main_menu,
)
from jarvis.config.settings import Settings
from jarvis.conversation.state import ConversationStore
from jarvis.documents.storage import EncryptedFileStorage
from jarvis.domain.enums import PendingOpType
from jarvis.repositories.conversation import ConversationRepository
from jarvis.repositories.documents import DocumentRepository
from jarvis.services.documents import TELEGRAM_SEND_LIMIT, DocumentService
from jarvis.services.errors import OperationError

log = logging.getLogger(__name__)


def build_storage(settings: Settings) -> EncryptedFileStorage:
    return EncryptedFileStorage(settings.document_storage_path, settings.document_keyring())


async def send_documents_list(
    message: Message,
    session: AsyncSession,
    *,
    page: int,
    edit: bool = False,
) -> None:
    documents = await DocumentRepository(session).list_active(
        limit=PAGE_SIZE,
        offset=page * PAGE_SIZE,
    )
    title = "📄 <b>Документы</b>"
    if not documents and page == 0:
        await send_or_edit(
            message,
            "📄 Пока нет документов.\n\nОтправьте файл одним сообщением — "
            "я зашифрую его и предложу сохранить.",
            markup=main_menu(),
            edit=edit,
        )
        return
    lines = [title, ""]
    for index, document in enumerate(documents, start=1):
        lines.append(f"{index}. {esc(document.title)}")
    markup = documents_list_keyboard(
        documents, page, has_next=len(documents) >= PAGE_SIZE
    )
    await send_or_edit(
        message, "\n".join(lines) + "\n\nВыберите документ:", markup=markup, edit=edit
    )


async def documents_page(
    callback: CallbackQuery, callback_data: DocListCB, session: AsyncSession
) -> None:
    await callback.answer()
    await send_documents_list(
        callback.message,
        session,
        page=callback_data.page,
        edit=True,
    )


async def document_action(
    callback: CallbackQuery,
    callback_data: DocCB,
    session: AsyncSession,
    settings: Settings,
) -> None:
    document_id = parse_hex_uuid(callback_data.doc_id)
    if document_id is None:
        await callback.answer("Некорректный документ", show_alert=True)
        return

    if callback_data.action == "view":
        text, markup = await render_document_card(session, document_id)
        await callback.answer()
        if text is None:
            await callback.message.answer("Документ не найден.", reply_markup=main_menu())
            return
        # Карточка правит сообщение, из которого открыта (список/поиск).
        await send_or_edit(callback.message, f"{text}\n\nДействия:", markup=markup, edit=True)
        return

    if callback_data.action == "download":
        try:
            _, data = await DocumentService(session, settings, build_storage(settings)).read_file(
                document_id
            )
        except OperationError as error:
            await callback.answer(str(error), show_alert=True)
            return
        if len(data) > TELEGRAM_SEND_LIMIT:
            await callback.answer(
                "Файл слишком большой для отправки через Telegram", show_alert=True
            )
            return
        document = await DocumentRepository(session).get(document_id)
        await callback.answer()
        filename = (
            PurePosixPath(document.original_filename).name
            if document and document.original_filename
            else f"{document.title if document else 'document'}.bin"
        )
        await callback.message.answer_document(
            BufferedInputFile(data, filename=filename),
            caption="📤 Файл расшифрован для отправки. Удалите сообщение после использования.",
        )
        return

    if callback_data.action == "delete":
        await callback.answer()
        await propose_and_send(
            callback.message,
            session,
            settings,
            callback.from_user.id,
            PendingOpType.DELETE_DOCUMENT,
            {"entity_id": str(document_id)},
        )
        return

    await callback.answer("Неизвестное действие", show_alert=True)


async def document_upload(
    message: Message, session: AsyncSession, settings: Settings
) -> None:
    telegram_document = message.document
    if telegram_document is None:
        return

    service = DocumentService(session, settings, build_storage(settings))
    try:
        service.validate_upload(
            telegram_document.file_size or 0, telegram_document.mime_type
        )
    except OperationError as error:
        await message.answer(f"⚠️ {error}")
        return

    buffer = BytesIO()
    try:
        await message.bot.download(telegram_document, destination=buffer)
    except Exception:  # noqa: BLE001 - сеть Telegram может отвалиться
        log.exception("failed to download document from telegram")
        await message.answer("Не удалось скачать файл из Telegram. Попробуйте ещё раз.")
        return
    data = buffer.getvalue()

    try:
        document = await service.stage_upload(
            data=data,
            original_filename=telegram_document.file_name,
            mime_type=telegram_document.mime_type,
        )
    except OperationError as error:
        await message.answer(f"⚠️ {error}")
        return

    store = ConversationStore(ConversationRepository(session), settings)
    await store.clear(message.from_user.id)

    await propose_and_send(
        message,
        session,
        settings,
        message.from_user.id,
        PendingOpType.SAVE_DOCUMENT,
        {
            "document_id": str(document.id),
            "title": document.title,
            "document_type": "other",
            "sensitivity": "sensitive",
        },
    )
    await message.answer(
        "🔒 Файл уже зашифрован и ждёт подтверждения. "
        "Нажмите «Изменить», чтобы задать другое название."
    )


def build_router() -> Router:
    router = Router(name="documents")
    router.callback_query.register(documents_page, DocListCB.filter())
    router.callback_query.register(document_action, DocCB.filter())
    router.message.register(document_upload, F.document)
    return router
