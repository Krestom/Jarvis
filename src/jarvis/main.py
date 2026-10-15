"""Точка входа приложения: конфигурация, БД, health, бот, уборка, shutdown."""

from __future__ import annotations

import asyncio
import logging
import signal

import uvicorn
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from jarvis import __version__
from jarvis.ai import NoneProvider, create_ai_provider
from jarvis.bot.app import create_bot, create_dispatcher, setup_bot_commands
from jarvis.config.settings import Settings, get_settings
from jarvis.db.session import create_engine, create_session_factory, session_scope
from jarvis.health import create_health_app
from jarvis.logging_setup import setup_logging
from jarvis.services.catalog import CatalogService
from jarvis.transcription import create_transcription_provider
from jarvis.workers.cleanup import cleanup_loop

log = logging.getLogger(__name__)

_DB_WAIT_ATTEMPTS = 15
_DB_WAIT_DELAY_SECONDS = 2.0


async def wait_for_db(engine: AsyncEngine) -> None:
    last_error: Exception | None = None
    for attempt in range(1, _DB_WAIT_ATTEMPTS + 1):
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            log.info("database is reachable")
            return
        except Exception as error:  # noqa: BLE001 - ждём недоступную БД
            last_error = error
            log.warning("database not ready (attempt %s/%s)", attempt, _DB_WAIT_ATTEMPTS)
            await asyncio.sleep(_DB_WAIT_DELAY_SECONDS)
    raise RuntimeError("database is not reachable") from last_error


def _build_ai(settings: Settings):
    try:
        return create_ai_provider(settings)
    except ValueError as error:
        log.error("AI provider is misconfigured, running without AI: %s", error)
        return NoneProvider()


def _build_transcription(settings: Settings):
    try:
        return create_transcription_provider(settings)
    except ValueError as error:
        log.error("transcription is misconfigured, voice is disabled: %s", error)
        return None


async def amain() -> int:
    settings: Settings = get_settings()
    setup_logging(settings.log_level)
    log.info("starting jarvis app env=%s version=%s", settings.app_env, __version__)

    problems = settings.bot_config_problems()
    for problem in problems:
        log.warning("config problem: %s", problem)
    if settings.app_env == "prod" and problems and settings.bot_token:
        log.error("refusing to start the bot in prod with invalid config")
        return 1

    ai = _build_ai(settings)
    transcription = _build_transcription(settings)

    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    await wait_for_db(engine)

    async with session_scope(session_factory) as session:
        created = await CatalogService(session).ensure_defaults()
        if created:
            log.info("seeded %s default categories", created)

    health_app = create_health_app(session_factory)
    server = uvicorn.Server(
        uvicorn.Config(
            health_app,
            host=settings.health_host,
            port=settings.health_port,
            log_config=None,
            access_log=False,
        )
    )
    health_task = asyncio.create_task(server.serve(), name="health-server")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGTERM, stop.set)
        loop.add_signal_handler(signal.SIGINT, stop.set)
    except NotImplementedError:
        pass  # Windows: Ctrl+C прервёт asyncio.run

    cleanup_task = asyncio.create_task(
        cleanup_loop(session_factory, settings, stop), name="cleanup"
    )

    bot = None
    dispatcher = None
    polling_task = None
    if settings.bot_token and settings.telegram_allowed_user_ids:
        bot = create_bot(settings)
        dispatcher = create_dispatcher(settings, session_factory, ai, transcription)
        await setup_bot_commands(bot)
        polling_task = asyncio.create_task(
            dispatcher.start_polling(bot, handle_signals=False), name="telegram-polling"
        )
        log.info("telegram bot is polling (ai=%s)", ai.name)
    else:
        log.warning(
            "telegram bot is disabled (token set: %s, allowed users: %s)",
            bool(settings.bot_token),
            len(settings.telegram_allowed_user_ids),
        )

    log.info(
        "jarvis is running (health on %s:%s)", settings.health_host, settings.health_port
    )
    await stop.wait()

    log.info("shutting down")
    stop.set()
    server.should_exit = True
    await health_task

    if dispatcher is not None:
        await dispatcher.stop_polling()
    if polling_task is not None:
        await polling_task
    if bot is not None:
        await bot.session.close()

    await cleanup_task
    await engine.dispose()
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
