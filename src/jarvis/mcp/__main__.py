"""Запуск MCP-сервера: streamable HTTP (по умолчанию) или stdio (локально)."""

from __future__ import annotations

import argparse
import asyncio
import logging

import uvicorn

from jarvis.config.settings import get_settings
from jarvis.db.session import create_engine, create_session_factory
from jarvis.logging_setup import setup_logging
from jarvis.mcp.server import create_mcp_http_app, create_mcp_server

log = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Jarvis MCP server (read-only)")
    parser.add_argument(
        "--stdio",
        action="store_true",
        help="запустить в режиме stdio (для локального OpenCode), по умолчанию HTTP",
    )
    args = parser.parse_args()

    settings = get_settings()
    setup_logging(settings.log_level)

    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    server = create_mcp_server(settings, session_factory)

    if args.stdio:
        log.info("starting jarvis MCP over stdio (read-only)")
        asyncio.run(_run_stdio(server, engine))
        return

    problems = settings.mcp_config_problems()
    if problems:
        raise SystemExit("MCP config problems: " + "; ".join(problems))

    app = create_mcp_http_app(server, settings)
    log.info(
        "starting jarvis MCP on %s:%s/mcp (read-only, bearer auth)",
        settings.mcp_host,
        settings.mcp_port,
    )
    uvicorn.run(
        app,
        host=settings.mcp_host,
        port=settings.mcp_port,
        log_level=settings.log_level.lower(),
        access_log=False,
    )


async def _run_stdio(server, engine) -> None:
    try:
        await server.run_stdio_async()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    main()
