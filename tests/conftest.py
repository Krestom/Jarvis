"""Общие фикстуры: юнит-настройки и интеграционная тестовая БД.

Тестовая БД создаётся на том же PostgreSQL, что описан в .env
(DATABASE_URL), отдельным именем и пересоздаётся на каждую сессию.
Без доступной БД интеграционные тесты скипаются с понятным сообщением.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import os
import subprocess
import sys
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from jarvis.config.settings import Settings
from jarvis.db.session import session_scope

ROOT = Path(__file__).resolve().parents[1]
TEST_DB_NAME = "jarvis_test"

# Детерминированные тестовые ключи (32 байта), очевидно не production-секреты.
TEST_KEY_B64 = base64.urlsafe_b64encode(bytes(range(32))).decode("ascii")
TEST_SECRETS_KEY_B64 = base64.urlsafe_b64encode(bytes(range(32, 64))).decode("ascii")


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="dev",
        telegram_allowed_user_ids=[111],
        document_encryption_key=TEST_KEY_B64,
        secrets_encryption_key=TEST_SECRETS_KEY_B64,
    )


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        app_env="dev",
        telegram_allowed_user_ids=[111],
        document_encryption_key=TEST_KEY_B64,
        secrets_encryption_key=TEST_SECRETS_KEY_B64,
        document_storage_path=tmp_path / "documents",
        database_url="postgresql+asyncpg://unused:unused@127.0.0.1:1/unused",
    )


# --- Интеграционная БД -------------------------------------------------------


def _database_urls() -> tuple[str, str]:
    """Вернуть (URL тестовой БД, DSN админской БД для CREATE/DROP)."""
    configured = Settings().database_url
    base, _, _ = configured.rpartition("/")
    test_url = f"{base}/{TEST_DB_NAME}"
    admin_dsn = configured.replace("postgresql+asyncpg://", "postgresql://")
    return test_url, admin_dsn


@pytest.fixture(scope="session")
def test_database_url() -> str:
    test_url, admin_dsn = _database_urls()

    async def _recreate() -> None:
        connection = await asyncpg.connect(admin_dsn)
        try:
            await connection.execute(f"DROP DATABASE IF EXISTS {TEST_DB_NAME} WITH (FORCE)")
            await connection.execute(f"CREATE DATABASE {TEST_DB_NAME}")
        finally:
            await connection.close()

    try:
        asyncio.run(_recreate())
    except (OSError, asyncpg.PostgresError) as error:
        pytest.skip(f"test database is unavailable: {error}")

    env = {**os.environ, "DATABASE_URL": test_url}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"alembic upgrade failed for test db:\n{result.stderr}")

    yield test_url

    async def _drop() -> None:
        connection = await asyncpg.connect(admin_dsn)
        try:
            await connection.execute(f"DROP DATABASE IF EXISTS {TEST_DB_NAME} WITH (FORCE)")
        finally:
            await connection.close()

    with contextlib.suppress(Exception):
        asyncio.run(_drop())


@pytest.fixture
async def db_engine(test_database_url):
    engine = create_async_engine(test_database_url, poolclass=NullPool)
    yield engine
    await engine.dispose()


async def _truncate_all(engine) -> None:
    async with engine.begin() as connection:
        result = await connection.execute(
            text(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
            )
        )
        tables = [row[0] for row in result]
        if tables:
            await connection.execute(
                text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE")
            )


@pytest.fixture
async def session_factory(db_engine):
    await _truncate_all(db_engine)
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture
async def session(session_factory):
    async with session_scope(session_factory) as active_session:
        yield active_session
