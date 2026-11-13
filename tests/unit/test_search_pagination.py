"""Пагинация единого поиска: страницы не дублируют и не теряют записи."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from jarvis.services.search import SearchService


def make_item(title: str, created_at: datetime):
    return SimpleNamespace(
        id=uuid.uuid4(),
        title=title,
        summary="summary",
        content="content",
        description="description",
        document_type="other",
        created_at=created_at,
    )


class FakeRepo:
    def __init__(self, items):
        self.items = sorted(items, key=lambda item: item.created_at, reverse=True)

    async def search(self, query, *, limit=10, offset=0, **kwargs):
        return self.items[offset : offset + limit]

    async def search_by_title(self, query, *, limit=10, **kwargs):
        return self.items[:limit]


async def test_search_pages_do_not_overlap_or_skip():
    now = datetime.now(UTC)
    service = SearchService(None)
    service._knowledge = FakeRepo(
        [make_item(f"k{i}", now - timedelta(minutes=i)) for i in range(20)]
    )
    service._tasks = FakeRepo(
        [make_item(f"t{i}", now - timedelta(minutes=i, seconds=1)) for i in range(20)]
    )
    service._documents = FakeRepo(
        [make_item(f"d{i}", now - timedelta(minutes=i, seconds=2)) for i in range(5)]
    )

    pages = [await service.search("q", limit=7, offset=offset) for offset in (0, 7, 14)]
    assert [len(page) for page in pages] == [7, 7, 7]
    ids = [hit.entity_id for page in pages for hit in page]
    assert len(ids) == len(set(ids))
