"""Fallback для callback-кнопок, которых больше нет в текущей версии."""

from __future__ import annotations

from aiogram import Router
from aiogram.types import CallbackQuery


async def stale_callback(callback: CallbackQuery) -> None:
    await callback.answer("Кнопка устарела — откройте меню заново.")


def build_router() -> Router:
    router = Router(name="stale_callbacks")
    router.callback_query.register(stale_callback)
    return router
