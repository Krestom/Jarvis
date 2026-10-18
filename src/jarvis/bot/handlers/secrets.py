"""Секреты: список, карточка, показ значения по явному запросу, удаление."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.ai import AIProvider
from jarvis.bot.callbacks import SecretCB, SecretListCB
from jarvis.bot.formatting import esc, secret_card_text
from jarvis.bot.handlers.open_entity import parse_hex_uuid
from jarvis.bot.handlers.render import answer_safe, propose_and_send, send_outcome
from jarvis.bot.keyboards import (
    PAGE_SIZE,
    main_menu,
    secret_card_keyboard,
    secrets_list_keyboard,
)
from jarvis.config.settings import Settings
from jarvis.conversation.engine import ConversationEngine
from jarvis.domain.enums import PendingOpType
from jarvis.services.errors import OperationError
from jarvis.services.secrets import SecretService

log = logging.getLogger(__name__)


REVEAL_LIFETIME_SECONDS = 90


async def start_secret_flow(
    message: Message, session: AsyncSession, settings: Settings, ai: AIProvider
) -> None:
    engine = ConversationEngine(session, settings, ai)
    outcome = await engine.start_secret_flow(message.from_user.id)
    await send_outcome(message, outcome, session, settings)


async def send_secrets_list(
    message: Message, session: AsyncSession, settings: Settings, *, page: int, edit: bool = False
) -> None:
    from jarvis.bot.handlers.render import send_or_edit

    service = SecretService(session, settings)
    secrets = await service.list_metadata(limit=PAGE_SIZE, offset=page * PAGE_SIZE)
    if not secrets and page == 0:
        await send_or_edit(
            message,
            "🔐 Секретов пока нет.\n\nКоманда /secret добавляет секрет: значение "
            "шифруется и не попадает в поиск, AI и MCP.",
            markup=main_menu(),
            edit=edit,
        )
        return
    lines = ["🔐 <b>Секреты</b>", ""]
    for index, secret in enumerate(secrets, start=1):
        lines.append(f"{index}. {secret.name}")
    lines.append("\n<i>Значения не показываются в списках и поиске.</i>")
    markup = secrets_list_keyboard(secrets, page, has_next=len(secrets) >= PAGE_SIZE)
    await send_or_edit(
        message, "\n".join(lines) + "\n\nВыберите секрет:", markup=markup, edit=edit
    )


async def secrets_page(
    callback: CallbackQuery,
    callback_data: SecretListCB,
    session: AsyncSession,
    settings: Settings,
) -> None:
    await callback.answer()
    await send_secrets_list(
        callback.message, session, settings, page=callback_data.page, edit=True
    )


async def secret_action(
    callback: CallbackQuery,
    callback_data: SecretCB,
    session: AsyncSession,
    settings: Settings,
) -> None:
    secret_id = parse_hex_uuid(callback_data.secret_id)
    if secret_id is None:
        await callback.answer("Некорректный секрет", show_alert=True)
        return

    service = SecretService(session, settings)
    secret = await service.get(secret_id)
    if secret is None:
        await callback.answer("Секрет не найден", show_alert=True)
        return

    if callback_data.action == "view":
        await callback.answer()
        await callback.message.answer(
            secret_card_text(secret), reply_markup=secret_card_keyboard(callback_data.secret_id)
        )
        return

    if callback_data.action == "reveal":
        try:
            value = service.decrypt_value(secret.encrypted_value)
        except OperationError as error:
            await callback.answer(str(error), show_alert=True)
            return
        await callback.answer("Показываю на 90 секунд")
        message = await answer_safe(
            callback.message,
            f"👁 <b>{esc(secret.name)}</b>\n<code>{esc(value)}</code>\n\n"
            "<i>Сообщение будет удалено через 90 секунд. Не пересылайте его.</i>",
        )
        asyncio.create_task(_delete_later(message, REVEAL_LIFETIME_SECONDS))
        return

    if callback_data.action == "delete":
        await callback.answer()
        await propose_and_send(
            callback.message,
            session,
            settings,
            callback.from_user.id,
            PendingOpType.DELETE_SECRET,
            {"entity_id": str(secret_id)},
        )
        return

    await callback.answer("Неизвестное действие", show_alert=True)


async def _delete_later(message: Message, delay_seconds: int) -> None:
    import contextlib

    await asyncio.sleep(delay_seconds)
    with contextlib.suppress(Exception):  # сообщение могло быть уже удалено
        await message.delete()


def build_router() -> Router:
    router = Router(name="secrets")
    router.callback_query.register(secrets_page, SecretListCB.filter())
    router.callback_query.register(secret_action, SecretCB.filter())
    return router
