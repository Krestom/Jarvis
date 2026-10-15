"""MCP-сервер Jarvis: read-only доступ к знаниям и задачам для OpenCode."""

from __future__ import annotations

import logging

from mcp.server import MCPServer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jarvis import __version__
from jarvis.config.settings import Settings
from jarvis.mcp.auth import BearerTokenMiddleware
from jarvis.mcp.tools import register_tools

log = logging.getLogger(__name__)

INSTRUCTIONS = """\
Jarvis — личная база знаний владельца. Инструменты работают только на чтение:
поиск заметок, инструкций, задач и контекста проектов. Секреты, пароли, ключи
и содержимое документов через MCP недоступны. Используй минимально
необходимый контекст и не пересказывай чувствительные данные без нужды.
"""


def create_mcp_server(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
) -> MCPServer:
    server = MCPServer(
        name="jarvis",
        title="Jarvis Knowledge OS",
        version=__version__,
        instructions=INSTRUCTIONS,
    )
    register_tools(server, session_factory, settings)
    return server


def create_mcp_http_app(server: MCPServer, settings: Settings):
    """ASGI-приложение MCP (streamable HTTP) за bearer-проверкой."""
    app = server.streamable_http_app(streamable_http_path="/mcp")
    return BearerTokenMiddleware(app, settings.mcp_token)
