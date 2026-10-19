"""Типизированный UI агента: выбор реальной сущности кнопкой."""

from __future__ import annotations

from aiogram import Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.bot.callbacks import AgentPickCB
from jarvis.bot.formatting import esc
from jarvis.bot.ui_state import stale_message
from jarvis.config.settings import Settings
from jarvis.conversation.state import ConversationStore
from jarvis.repositories.conversation import ConversationRepository
from jarvis.repositories.knowledge import KnowledgeRepository
from jarvis.repositories.projects import ProjectRepository
from jarvis.repositories.tasks import TaskRepository


async def _load_candidate_title(session: AsyncSession, kind: str, raw_id: str) -> str | None:
    """Перечитать сущность из БД: id из контекста, заголовок — свежий."""
    import uuid

    try:
        entity_id = uuid.UUID(str(raw_id))
    except (ValueError, TypeError):
        return None
    if kind == "task":
        task = await TaskRepository(session).get(entity_id)
        return task.title if task is not None else None
    if kind == "knowledge":
        item = await KnowledgeRepository(session).get(entity_id)
        return item.title if item is not None else None
    if kind == "project":
        project = await ProjectRepository(session).get(entity_id)
        return project.name if project is not None else None
    return None


async def agent_pick(
    callback: CallbackQuery,
    callback_data: AgentPickCB,
    session: AsyncSession,
    settings: Settings,
) -> None:
    from jarvis.agent.routing import AGENT_FLOW

    user_id = callback.from_user.id
    store = ConversationStore(ConversationRepository(session), settings)
    state = await store.get_active(user_id)
    if state is None or state.flow != AGENT_FLOW:
        await callback.answer("Кнопка устарела — напишите запрос заново.", show_alert=True)
        return
    data = dict(state.data or {})
    candidates = list(data.get("agent_candidates") or [])
    index = callback_data.index
    if index < 0 or index >= len(candidates):
        await callback.answer("Вариант больше не актуален.", show_alert=True)
        return

    candidate = candidates[index]
    kind = str(candidate.get("kind") or "")
    fresh_title = await _load_candidate_title(session, kind, candidate.get("id"))
    if fresh_title is None:
        # Сущность исчезла — убираем из контекста и честно сообщаем.
        candidates.pop(index)
        data["agent_candidates"] = candidates
        await store.set(user_id, AGENT_FLOW, data=data)
        await callback.answer("Эта запись больше недоступна", show_alert=True)
        return

    data["agent_selected"] = {"kind": kind, "id": candidate.get("id"), "title": fresh_title}
    await store.set(user_id, AGENT_FLOW, data=data)
    await callback.answer("Выбрано")

    label = {"task": "задачу", "knowledge": "запись", "project": "проект"}.get(kind, "запись")
    text = f"✅ Выбрал {label}: <b>{esc(fresh_title)}</b>"
    if stale_message.get():
        await callback.message.answer(text)
    else:
        try:
            await callback.message.edit_text(text)
        except Exception:  # noqa: BLE001 - сообщение могло быть недоступно для правки
            await callback.message.answer(text)
    await callback.message.answer(
        "Что с ним сделать? Напишите естественно, например: "
        "«добавь комментарий: проверил конфигурацию», «отметь выполненной», "
        "«перенеси срок на пятницу»."
    )


def build_router() -> Router:
    router = Router(name="agent")
    router.callback_query.register(agent_pick, AgentPickCB.filter())
    return router


__all__ = ["build_router"]
