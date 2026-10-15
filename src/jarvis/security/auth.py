"""Allow-list авторизация по Telegram user id."""

from __future__ import annotations

import logging
from collections.abc import Iterable

log = logging.getLogger(__name__)


class AccessDenied(Exception):
    """Пользователь не входит в allow-list."""


class Authorizer:
    """Проверка доступа для всех точек входа (сообщения, callbacks, файлы).

    Пустой allow-list запрещает доступ всем — отсутствие настройки
    никогда не означает "разрешить всем".
    """

    def __init__(self, allowed_user_ids: Iterable[int]) -> None:
        self._allowed = frozenset(int(user_id) for user_id in allowed_user_ids)

    @property
    def allowed_ids(self) -> frozenset[int]:
        return self._allowed

    def is_allowed(self, user_id: int | None) -> bool:
        return user_id is not None and int(user_id) in self._allowed

    def ensure_allowed(self, user_id: int | None) -> None:
        if not self.is_allowed(user_id):
            log.warning("access denied for telegram user_id=%s", user_id)
            raise AccessDenied("access denied")
