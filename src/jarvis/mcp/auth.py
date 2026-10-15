"""Bearer-аутентификация для MCP-приложения (стататический токен из конфигурации)."""

from __future__ import annotations

import hmac
import json

UNAUTHORIZED_BODY = json.dumps({"error": "unauthorized"}).encode("utf-8")


class BearerTokenMiddleware:
    """Обёртка ASGI: пропускает запросы с корректным Authorization: Bearer <token>.

    MCP слушает только loopback (внешний доступ — через ssh-туннель), но
    токен всё равно обязателен: без него любой локальный процесс на сервере
    мог бы читать базу знаний.
    """

    def __init__(self, app, token: str) -> None:
        if not token:
            raise ValueError("MCP_AUTH_TOKEN must be set for the HTTP transport")
        self._app = app
        self._expected = f"Bearer {token}".encode()

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        provided = b""
        for name, value in scope.get("headers") or []:
            if name.lower() == b"authorization":
                provided = value
                break
        if not hmac.compare_digest(provided, self._expected):
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"www-authenticate", b"Bearer"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": UNAUTHORIZED_BODY})
            return
        await self._app(scope, receive, send)
