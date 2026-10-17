"""Планировщик уведомлений: разбор времён сводок."""

from __future__ import annotations

from datetime import time

import pytest

from jarvis.workers.notifier import parse_digest_time, parse_digest_times


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


def test_parse_digest_times_sorted_and_deduplicated():
    result = parse_digest_times("22:00, 08:00,12:00,08:00")
    assert result == [time(8, 0), time(12, 0), time(22, 0)]


def test_parse_digest_times_skips_garbage():
    assert parse_digest_times("08:00,abc, ,25:00,09:30") == [time(8, 0), time(9, 30)]


def test_parse_digest_times_empty():
    assert parse_digest_times("") == []
    assert parse_digest_times(None) == []
