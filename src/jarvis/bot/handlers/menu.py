"""Навигация по меню (inline-кнопки)."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.ai import AIProvider
from jarvis.bot.callbacks import MenuCB
from jarvis.config.settings import Settings
from jarvis.conversation.flows import Flow
from jarvis.conversation.state import ConversationStore
from jarvis.repositories.conversation import ConversationRepository
from jarvis.repositories.projects import ProjectRepository


def MenuCBFilter(action: str):  # noqa: N802 - читаемо на месте использования
    return MenuCB.filter(F.action == action)


async def _edit_or_send(callback: CallbackQuery, text: str, markup, *, with_menu: bool = False):
    from jarvis.bot.keyboards import main_menu

    reply_markup = main_menu() if with_menu else markup
    message = callback.message
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except Exception:  # noqa: BLE001 - старое сообщение могло быть недоступно для правки
        await message.answer(text, reply_markup=reply_markup)


async def send_settings(
    message: Message, session: AsyncSession, settings: Settings
) -> None:
    from jarvis.bot.keyboards import settings_keyboard
    from jarvis.services.catalog import CatalogService

    categories = await CatalogService(session).list_categories()
    lines = [
        "⚙️ <b>Настройки</b>",
        f"Часовой пояс: <code>{settings.app_timezone}</code>",
        f"Срок подтверждения операций: {settings.pending_operation_ttl_minutes} мин",
        f"Состояние сценариев живёт: {settings.conversation_state_ttl_hours} ч",
        "\n<b>Категории</b> (нажмите, чтобы переименовать):",
    ]
    await message.answer("\n".join(lines), reply_markup=settings_keyboard(categories))


async def menu_main(callback: CallbackQuery) -> None:
    await _edit_or_send(callback, "Главное меню:", markup=None, with_menu=True)


async def menu_search(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    store = ConversationStore(ConversationRepository(session), settings)
    await store.set(callback.from_user.id, Flow.SEARCH_WAIT)
    await callback.answer()
    await callback.message.answer("🔎 Напишите, что искать.")


async def menu_tasks(callback: CallbackQuery) -> None:
    from jarvis.bot.keyboards import tasks_menu_keyboard

    await callback.answer()
    await callback.message.answer(
        "📋 <b>Задачи</b>\nВыберите список:", reply_markup=tasks_menu_keyboard()
    )


async def menu_knowledge(callback: CallbackQuery, session: AsyncSession) -> None:
    from jarvis.bot.handlers.knowledge import send_knowledge_list

    await callback.answer()
    await send_knowledge_list(callback.message, session, page=0)


async def menu_documents(callback: CallbackQuery, session: AsyncSession) -> None:
    from jarvis.bot.handlers.documents import send_documents_list

    await callback.answer()
    await send_documents_list(callback.message, session, page=0)


async def menu_secrets(
    callback: CallbackQuery, session: AsyncSession, settings: Settings
) -> None:
    from jarvis.bot.handlers.secrets import send_secrets_list

    await callback.answer()
    await send_secrets_list(callback.message, session, settings, page=0)


async def menu_secret_new(
    callback: CallbackQuery, session: AsyncSession, settings: Settings, ai: AIProvider
) -> None:
    from jarvis.bot.handlers.secrets import start_secret_flow

    await callback.answer()
    await start_secret_flow(callback.message, session, settings, ai)


async def menu_projects(callback: CallbackQuery, session: AsyncSession) -> None:
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    projects = await ProjectRepository(session).list_active(limit=30)
    lines = ["🗂 <b>Проекты</b>"]
    if projects:
        lines.extend(f"• {project.name}" for project in projects)
    else:
        lines.append("Пока нет проектов. Создайте первый кнопкой ниже.")
    builder = [
        [
            InlineKeyboardButton(
                text="➕ Новый проект",
                callback_data=MenuCB(action="project_new").pack(),
            )
        ],
        [InlineKeyboardButton(text="🏠 В меню", callback_data=MenuCB(action="main").pack())],
    ]
    await callback.answer()
    await callback.message.answer(
        "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=builder)
    )


async def menu_settings(
    callback: CallbackQuery, session: AsyncSession, settings: Settings
) -> None:
    await callback.answer()
    await send_settings(callback.message, session, settings)


async def menu_categories(
    callback: CallbackQuery, session: AsyncSession, settings: Settings
) -> None:
    await callback.answer()
    await send_settings(callback.message, session, settings)


async def menu_cancel_flow(
    callback: CallbackQuery, session: AsyncSession, settings: Settings
) -> None:
    store = ConversationStore(ConversationRepository(session), settings)
    await store.clear(callback.from_user.id)
    await callback.answer("Сценарий отменён")
    await _edit_or_send(callback, "Сценарий отменён.", markup=None, with_menu=True)


async def menu_category_new(
    callback: CallbackQuery, session: AsyncSession, settings: Settings
) -> None:
    store = ConversationStore(ConversationRepository(session), settings)
    await store.set(callback.from_user.id, Flow.CREATE_CATEGORY)
    await callback.answer()
    await callback.message.answer("Как назвать новую категорию?")


async def menu_category_rename(
    callback: CallbackQuery, session: AsyncSession, settings: Settings, callback_data: MenuCB
) -> None:
    if not callback_data.value:
        await callback.answer("Не понял, какую категорию менять", show_alert=True)
        return
    store = ConversationStore(ConversationRepository(session), settings)
    await store.set(
        callback.from_user.id, Flow.EDIT_CATEGORY, data={"category_id": callback_data.value}
    )
    await callback.answer()
    await callback.message.answer("Напишите новое название категории.")


async def menu_project_new(
    callback: CallbackQuery, session: AsyncSession, settings: Settings
) -> None:
    store = ConversationStore(ConversationRepository(session), settings)
    await store.set(callback.from_user.id, Flow.CREATE_PROJECT)
    await callback.answer()
    await callback.message.answer("Как назвать проект?")


async def menu_force_save(
    callback: CallbackQuery, session: AsyncSession, settings: Settings
) -> None:
    """Сознательное сохранение сообщения с секретом как заметки."""
    from jarvis.bot.handlers.render import propose_and_send
    from jarvis.conversation.heuristics import first_line
    from jarvis.domain.enums import (
        KnowledgeSource,
        KnowledgeType,
        PendingOpType,
        Sensitivity,
    )
    from jarvis.services.drafts import KnowledgeDraft

    store = ConversationStore(ConversationRepository(session), settings)
    state = await store.get_active(callback.from_user.id)
    if (
        state is None
        or state.flow != Flow.FORCE_SAVE_PENDING
        or not state.data.get("text")
    ):
        await callback.answer("Нечего сохранять — напишите сообщение заново.", show_alert=True)
        return
    text = str(state.data["text"])
    await store.clear(callback.from_user.id)
    await callback.answer()

    draft = KnowledgeDraft(
        type=KnowledgeType.NOTE,
        title=first_line(text) or "Заметка",
        content=text,
        source_type=KnowledgeSource.TEXT,
        original_text=text,
        project_name=str(state.data.get("project") or "").strip() or None,
        sensitivity=Sensitivity.SENSITIVE,
    )
    await propose_and_send(
        callback.message,
        session,
        settings,
        callback.from_user.id,
        PendingOpType.CREATE_KNOWLEDGE,
        draft.model_dump(mode="json"),
    )
    await callback.message.answer(
        "<i>Сохраню с уровнем «чувствительная»: поиск и AI видят её только по "
        "вашему запросу, MCP — не видит.</i>"
    )


def build_router() -> Router:
    router = Router(name="menu")
    router.callback_query.register(menu_main, MenuCBFilter("main"))
    router.callback_query.register(menu_search, MenuCBFilter("search"))
    router.callback_query.register(menu_tasks, MenuCBFilter("tasks"))
    router.callback_query.register(menu_knowledge, MenuCBFilter("knowledge"))
    router.callback_query.register(menu_documents, MenuCBFilter("documents"))
    router.callback_query.register(menu_secrets, MenuCBFilter("secrets"))
    router.callback_query.register(menu_secret_new, MenuCBFilter("secret_new"))
    router.callback_query.register(menu_projects, MenuCBFilter("projects"))
    router.callback_query.register(menu_settings, MenuCBFilter("settings"))
    router.callback_query.register(menu_categories, MenuCBFilter("categories"))
    router.callback_query.register(menu_cancel_flow, MenuCBFilter("cancel_flow"))
    router.callback_query.register(menu_category_new, MenuCBFilter("category_new"))
    router.callback_query.register(menu_category_rename, MenuCBFilter("category_rename"))
    router.callback_query.register(menu_project_new, MenuCBFilter("project_new"))
    router.callback_query.register(menu_force_save, MenuCBFilter("force_save"))
    return router
