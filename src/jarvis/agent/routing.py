"""Детерминированный роутинг: обращение по имени, профиль выполнения."""

from __future__ import annotations

import re

from jarvis.agent.budget import (
    PROFILE_ORDINARY,
    PROFILE_PLANNING,
    PROFILE_RESEARCH,
    ExecutionProfile,
)
from jarvis.config.settings import Settings

# Обращение к ассистенту: отдельный токен «jarvis»/«джарвис» (любой регистр).
# Границы не включают дефис и буквы/цифры: «jarvis-обёртка» не срабатывает.
INVOCATION_PATTERN = re.compile(r"(?<![\w-])(jarvis|джарвис)(?![\w-])", re.IGNORECASE)

# Flow персистентного контекста агента (выбранные сущности, кандидаты, last run).
AGENT_FLOW = "agent_context"


def invokes_jarvis(text: str) -> bool:
    return bool(INVOCATION_PATTERN.search(text or ""))


def detect_profile(text: str, settings: Settings) -> str:
    lowered = (text or "").lower()
    if any(marker in lowered for marker in _markers(settings.agent_research_markers)):
        return PROFILE_RESEARCH
    if any(marker in lowered for marker in _markers(settings.agent_planning_markers)):
        return PROFILE_PLANNING
    return PROFILE_ORDINARY


def _markers(raw: str) -> list[str]:
    return [part.strip().lower() for part in raw.split(",") if part.strip()]


def build_profile(name: str, settings: Settings) -> ExecutionProfile:
    if name == PROFILE_RESEARCH:
        return ExecutionProfile(
            name, settings.agent_research_seconds, settings.agent_research_steps,
            settings.agent_web_queries_research,
        )
    if name == PROFILE_PLANNING:
        return ExecutionProfile(
            name, settings.agent_planning_seconds, settings.agent_planning_steps,
            settings.agent_web_queries_planning,
        )
    return ExecutionProfile(
        name, settings.agent_ordinary_seconds, settings.agent_ordinary_steps,
        settings.agent_web_queries_ordinary,
    )


__all__ = [
    "AGENT_FLOW",
    "build_profile",
    "detect_profile",
    "invokes_jarvis",
]
