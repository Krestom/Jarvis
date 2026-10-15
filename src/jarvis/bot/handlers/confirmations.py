"""Подтверждение, отмена и редактирование pending-операций."""

from __future__ import annotations

from aiogram import Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.ai import AIProvider
from jarvis.bot.callbacks import ConfirmCB
from jarvis.bot.texts import CALLBACK_STALE, CONFIRM_ALREADY, CONFIRM_APPLIED, CONFIRM_FAILED
from jarvis.config.settings import Settings
from jarvis.conversation.engine import ConversationEngine
from jarvis.services.confirmation import ConfirmationService

MESSAGE_LIMIT = 3900


async def _update_message(callback: CallbackQuery, suffix: str) -> None:
    message = callback.message
    base = message.text or ""
    updated = (base + suffix).strip()
    if len(updated) > MESSAGE_LIMIT:
        await message.answer(suffix.strip())
        return
    try:
        await message.edit_text(updated, reply_markup=None)
    except Exception:  # noqa: BLE001 - сообщение могло устареть
        await message.answer(suffix.strip())


async def confirm_action(
    callback: CallbackQuery,
    callback_data: ConfirmCB,
    session: AsyncSession,
    settings: Settings,
    ai: AIProvider,
) -> None:
    user_id = callback.from_user.id
    service = ConfirmationService(session, settings)

    if callback_data.action == "apply":
        result = await service.confirm(user_id, callback_data.op_id)
        await callback.answer()
        if result.ok:
            marker = CONFIRM_ALREADY if result.already else CONFIRM_APPLIED
            await _update_message(callback, f"\n\n{marker.format(summary=result.summary)}")
        else:
            await _update_message(callback, f"\n\n{CONFIRM_FAILED.format(summary=result.summary)}")
        return

    if callback_data.action == "cancel":
        result = await service.cancel(user_id, callback_data.op_id)
        await callback.answer("Отменено" if result.ok else result.summary)
        await _update_message(callback, f"\n\n❌ {result.summary}")
        return

    if callback_data.action == "edit":
        engine = ConversationEngine(session, settings, ai)
        await engine.start_edit_pending(user_id, callback_data.op_id)
        await callback.answer()
        await _update_message(
            callback,
            "\n\n✏️ Напишите исправленную формулировку одним сообщением — "
            "я предложу обновлённую операцию.",
        )
        return

    await callback.answer(CALLBACK_STALE, show_alert=True)


def build_router() -> Router:
    router = Router(name="confirmations")
    router.callback_query.register(confirm_action, ConfirmCB.filter())
    return router
