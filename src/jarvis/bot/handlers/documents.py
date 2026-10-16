"""Документы: список, карточка, загрузка файла, выдача, удаление."""

from __future__ import annotations

import logging
from io import BytesIO
from pathlib import PurePosixPath

from aiogram import F, Router
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.bot.callbacks import DocCB, DocListCB, uuid_hex
from jarvis.bot.formatting import esc, split_message
from jarvis.bot.handlers.open_entity import parse_hex_uuid, render_document_card
from jarvis.bot.handlers.render import propose_and_send
from jarvis.bot.keyboards import (
    PAGE_SIZE,
    document_categories_keyboard,
    document_category_set_keyboard,
    documents_list_keyboard,
    main_menu,
)
from jarvis.config.settings import Settings
from jarvis.conversation.flows import Flow
from jarvis.conversation.state import ConversationStore
from jarvis.documents.storage import EncryptedFileStorage
from jarvis.domain.enums import EntityType, PendingOpType
from jarvis.repositories.categories import CategoryRepository
from jarvis.repositories.conversation import ConversationRepository
from jarvis.repositories.documents import DocumentRepository
from jarvis.repositories.links import LinkRepository
from jarvis.services.catalog import CatalogService
from jarvis.services.documents import DocumentService
from jarvis.services.errors import OperationError

log = logging.getLogger(__name__)


TELEGRAM_SEND_LIMIT = 48 * 1024 * 1024  # запас под 50 МБ лимит Bot API


def build_storage(settings: Settings) -> EncryptedFileStorage:
    return EncryptedFileStorage(settings.document_storage_path, settings.document_keyring())


async def send_documents_list(
    message: Message, session: AsyncSession, *, page: int, category: str = ""
) -> None:
    category_id = parse_hex_uuid(category)
    category_obj = (
        await CategoryRepository(session).get(category_id) if category_id else None
    )
    if category and category_obj is None:
        await message.answer("🗂 Категория не найдена — откройте список заново.")
        return
    documents = await DocumentRepository(session).list_active(
        limit=PAGE_SIZE,
        offset=page * PAGE_SIZE,
        category_id=category_obj.id if category_obj else None,
    )
    title = f"🗂 <b>{esc(category_obj.name)}</b>" if category_obj else "📄 <b>Документы</b>"
    if not documents and page == 0:
        if category_obj is not None:
            await message.answer(f"{title}\n\nПусто.")
            return
        await message.answer(
            "📄 Пока нет документов.\n\nОтправьте файл одним сообщением — "
            "я зашифрую его и предложу сохранить.",
            reply_markup=main_menu(),
        )
        return
    lines = [title, ""]
    for index, document in enumerate(documents, start=1):
        lines.append(f"{index}. {esc(document.title)}")
    markup = documents_list_keyboard(
        documents, page, has_next=len(documents) >= PAGE_SIZE, category=category
    )
    for chunk in split_message("\n".join(lines)):
        await message.answer(chunk)
    await message.answer("Выберите документ:", reply_markup=markup)


async def documents_page(
    callback: CallbackQuery, callback_data: DocListCB, session: AsyncSession
) -> None:
    await callback.answer()
    if callback_data.category == "*":
        categories = await CatalogService(session).list_categories()
        await callback.message.answer(
            "🗂 <b>Документы по категориям</b>",
            reply_markup=document_categories_keyboard(categories),
        )
        return
    await send_documents_list(
        callback.message,
        session,
        page=callback_data.page,
        category=callback_data.category,
    )


async def document_action(
    callback: CallbackQuery,
    callback_data: DocCB,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if callback_data.action == "category_set":
        user_id = callback.from_user.id
        store = ConversationStore(ConversationRepository(session), settings)
        state = await store.get_active(user_id)
        if state is None or state.flow != Flow.CHOOSE_DOC_CATEGORY:
            await callback.answer("Выбор устарел — откройте карточку заново", show_alert=True)
            return
        document_id = parse_hex_uuid(str(state.data.get("document_id", "")))
        await store.clear(user_id)
        await callback.answer()
        if document_id is None:
            await callback.message.answer("Документ потерялся — откройте карточку заново.")
            return
        if callback_data.value == "none":
            category_id = None
        else:
            category_id = parse_hex_uuid(callback_data.value)
            if category_id is None:
                await callback.message.answer("Категория не распознана.")
                return
        await propose_and_send(
            callback.message,
            session,
            settings,
            user_id,
            PendingOpType.UPDATE_DOCUMENT,
            {
                "document_id": str(document_id),
                "category_id": str(category_id) if category_id else None,
            },
        )
        return

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
        for chunk in split_message(text):
            await callback.message.answer(chunk)
        await callback.message.answer("Действия:", reply_markup=markup)
        return

    if callback_data.action == "category":
        store = ConversationStore(ConversationRepository(session), settings)
        await store.set(
            callback.from_user.id,
            Flow.CHOOSE_DOC_CATEGORY,
            data={"document_id": str(document_id)},
        )
        categories = await CatalogService(session).list_categories()
        await callback.answer()
        await callback.message.answer(
            "Выберите категорию для документа:",
            reply_markup=document_category_set_keyboard(categories, uuid_hex(document_id)),
        )
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

    if callback_data.action == "related":
        await callback.answer()
        links = await LinkRepository(session).list_for(EntityType.DOCUMENT, document_id)
        if not links:
            await callback.message.answer("Связанных записей нет.")
            return
        lines = ["🔗 <b>Связанные записи</b>"]
        for link in links:
            lines.append(
                f"• {link.entity_type.value}: <code>{link.entity_id}</code> ({link.relation.value})"
            )
        await callback.message.answer("\n".join(lines))
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
            "category_id": None,
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
