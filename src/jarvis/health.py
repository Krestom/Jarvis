"""FastAPI-приложение для health-проверок (без публичного доступа)."""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jarvis import __version__

log = logging.getLogger(__name__)


def create_health_app(
    session_factory: async_sessionmaker[AsyncSession],
    extra_checks: dict[str, bool] | None = None,
) -> FastAPI:
    app = FastAPI(title="jarvis-health", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        db_ok = await _check_db(session_factory)
        components = {"db": db_ok, **(extra_checks or {})}
        ok = all(components.values())
        payload = {
            "status": "ok" if ok else "degraded",
            "version": __version__,
            "components": components,
        }
        return JSONResponse(payload, status_code=200 if ok else 503)

    return app


async def _check_db(session_factory: async_sessionmaker[AsyncSession]) -> bool:
    try:
        async with session_factory() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001 - health-чек не должен падать наружу
        log.exception("database health check failed")
        return False
