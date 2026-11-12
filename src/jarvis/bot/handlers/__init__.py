"""Сборка списка роутеров. Порядок важен: конкретные хендлеры раньше fallback'ов."""

from __future__ import annotations

from aiogram import Router


def build_routers() -> list[Router]:
    from jarvis.bot.handlers import (
        agent,
        commands,
        confirmations,
        diagnostics,
        documents,
        free_text,
        knowledge,
        menu,
        search,
        secrets,
        tasks,
        voice,
    )

    return [
        commands.build_router(),
        diagnostics.build_router(),
        menu.build_router(),
        confirmations.build_router(),
        tasks.build_router(),
        knowledge.build_router(),
        documents.build_router(),
        secrets.build_router(),
        search.build_router(),
        voice.build_router(),
        agent.build_router(),
        free_text.build_router(),  # всегда последний
    ]
