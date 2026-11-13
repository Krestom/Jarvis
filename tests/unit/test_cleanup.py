"""Уборка старых выгрузок backlog."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from jarvis.workers.cleanup import purge_export_files


def test_purge_export_files_removes_only_old(tmp_path):
    old = tmp_path / "old.md"
    new = tmp_path / "new.md"
    old.write_text("old", encoding="utf-8")
    new.write_text("new", encoding="utf-8")
    old_time = (datetime.now(UTC) - timedelta(days=8)).timestamp()
    os.utime(old, (old_time, old_time))

    purged = purge_export_files(
        SimpleNamespace(export_dir=tmp_path),
        datetime.now(UTC) - timedelta(days=7),
    )

    assert purged == 1
    assert not old.exists()
    assert new.exists()
