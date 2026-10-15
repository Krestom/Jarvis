"""Парсинг фильтров /export_issues (без БД)."""

from __future__ import annotations

import pytest

from jarvis.bot.handlers.diagnostics import OPEN_STATUSES, parse_export_filters
from jarvis.domain.enums import ImprovementCategory, ImprovementStatus


def test_parse_export_filters():
    assert parse_export_filters("/export_issues") == (OPEN_STATUSES, None)
    assert parse_export_filters("/export_issues status=all") == (None, None)
    assert parse_export_filters("/export_issues status=new,wont_fix") == (
        (ImprovementStatus.NEW, ImprovementStatus.WONT_FIX),
        None,
    )
    assert parse_export_filters("/export_issues category=ux_friction") == (
        OPEN_STATUSES,
        ImprovementCategory.UX_FRICTION,
    )
    with pytest.raises(ValueError):
        parse_export_filters("/export_issues status=nonsense")
    with pytest.raises(ValueError):
        parse_export_filters("/export_issues category=nonsense")
