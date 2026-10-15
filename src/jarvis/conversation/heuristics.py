"""Простые эвристики для работы без AI и очевидных случаев."""

from __future__ import annotations

_SEARCH_MARKERS = (
    "найди",
    "напомни",
    "поиск",
    "что я знаю о",
    "что я знал про",
    "что я знаю про",
    "покажи записи",
    "записи про",
    "инструкция по",
)


def looks_like_search(text: str) -> bool:
    lowered = text.strip().lower()
    return any(lowered.startswith(marker) for marker in _SEARCH_MARKERS)


def extract_search_query(text: str) -> str:
    """Убрать вводную формулировку («найди …», «поиск …») из запроса."""
    stripped = text.strip()
    lowered = stripped.lower()
    for marker in sorted(_SEARCH_MARKERS, key=len, reverse=True):
        if lowered.startswith(marker):
            query = stripped[len(marker):].lstrip(" ,:—-")
            return query or stripped
    return stripped


def is_question(text: str) -> bool:
    return text.strip().endswith("?")


def first_line(text: str, *, limit: int = 80) -> str:
    line = text.strip().splitlines()[0] if text.strip() else ""
    line = line.strip()
    if len(line) <= limit:
        return line
    return line[: limit - 1] + "…"
