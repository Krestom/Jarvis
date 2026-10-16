"""Планировщик уведомлений: разбор времени сводки."""

from __future__ import annotations

from datetime import time

import pytest

from jarvis.workers.notifier import parse_digest_time


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("08:00", time(8, 0)),
        (" 23:59 ", time(23, 59)),
        ("0:05", time(0, 5)),
    ],
)
def test_parse_digest_time_valid(raw, expected):
    assert parse_digest_time(raw) == expected


@pytest.mark.parametrize("raw", ["", "abc", "25:00", "10:70", "10", None])
def test_parse_digest_time_invalid(raw):
    assert parse_digest_time(raw) is None
