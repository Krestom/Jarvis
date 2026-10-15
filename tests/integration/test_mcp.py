"""Интеграционные тесты MCP: политика read-only и bearer-аутентификация."""

from __future__ import annotations

import asyncio
import json
import socket

import httpx
import pytest
import uvicorn
from pydantic import SecretStr
from sqlalchemy import func, select

from jarvis.config.settings import Settings
from jarvis.db.models import AuditLog
from jarvis.domain.enums import EntityType, KnowledgeType, Relation, Sensitivity
from jarvis.mcp.server import create_mcp_http_app, create_mcp_server
from jarvis.repositories.knowledge import KnowledgeRepository
from jarvis.repositories.links import LinkRepository
from jarvis.repositories.projects import ProjectRepository
from jarvis.repositories.tasks import TaskRepository
from jarvis.services.drafts import SecretCreateDraft
from jarvis.services.secrets import SecretService

pytestmark = pytest.mark.integration

MCP_TOKEN = "test-mcp-token"  # noqa: S105 - тестовое значение


def make_mcp_settings(test_settings: Settings, *, include_sensitive: bool = False) -> Settings:
    return test_settings.model_copy(
        update={
            "mcp_auth_token": SecretStr(MCP_TOKEN),
            "mcp_include_sensitive": include_sensitive,
        }
    )


async def call_tool(server, name: str, arguments: dict) -> dict:
    result = await server.call_tool(name, arguments)
    structured = result.structured_content or {}
    return structured.get("result", structured)


async def test_mcp_tools_respect_policy(test_settings, session_factory, session):
    knowledge = KnowledgeRepository(session)
    instruction = await knowledge.create(
        type=KnowledgeType.INSTRUCTION,
        title="Инструкция по деплою",
        content="Как выкатить релиз на сервер",
        summary="Пошаговый деплой",
    )
    sensitive = await knowledge.create(
        title="Личная переписка",
        content="чувствительные детали",
        sensitivity=Sensitivity.SENSITIVE,
    )
    project = await ProjectRepository(session).create(name="Jarvis", description="Личная ОС")
    task = await TaskRepository(session).create(title="Настроить MCP", project_id=project.id)

    secrets_service = SecretService(session, test_settings)
    encrypted_b64, key_version = secrets_service.encrypt_value("super-secret-token")
    secret_id = await secrets_service.create_from_draft(
        SecretCreateDraft(
            name="Токен MCP",
            secret_type="api_token",
            encrypted_value_b64=encrypted_b64,
            key_version=key_version,
        )
    )

    links = LinkRepository(session)
    await links.add(
        EntityType.PROJECT, project.id, EntityType.TASK, task.id, Relation.RELATES_TO
    )
    await links.add(
        EntityType.SECRET, secret_id, EntityType.PROJECT, project.id, Relation.RELATES_TO
    )
    await session.commit()

    default_settings = make_mcp_settings(test_settings)
    server = create_mcp_server(default_settings, session_factory)

    # 1. Поиск не отдаёт чувствительные записи.
    result = await call_tool(server, "search_knowledge", {"query": "деплою"})
    titles = [item["title"] for item in result["results"]]
    assert "Инструкция по деплою" in titles

    # 2. Чувствительная запись не выдаётся и по прямому id.
    result = await call_tool(server, "get_knowledge_item", {"item_id": str(sensitive.id)})
    assert result.get("error") == "not_found"

    # 3. Разрешённая запись отдаётся полностью.
    result = await call_tool(server, "get_knowledge_item", {"item_id": str(instruction.id)})
    assert result["content"] == "Как выкатить релиз на сервер"

    # 4. Секреты недоступны: в поиске и в связях.
    result = await call_tool(server, "search_knowledge", {"query": "super-secret-token"})
    assert result["count"] == 0

    result = await call_tool(server, "find_related", {
        "entity_type": "project", "entity_id": str(project.id)
    })
    related_types = {item["type"] for item in result["results"]}
    assert "task" in related_types
    assert "secret" not in related_types
    assert "super-secret-token" not in json.dumps(result)

    # 5. Контекст проекта.
    result = await call_tool(server, "get_project_context", {"project_name": "jarvis"})
    assert result["project"]["name"] == "Jarvis"
    assert any(item["title"] == "Настроить MCP" for item in result["tasks"])

    # 6. Список задач по динамическому списку.
    result = await call_tool(server, "search_tasks", {"view": "no_group"})
    assert any(item["title"] == "Настроить MCP" for item in result["results"])

    # 7. Аудит MCP пишется без содержимого.
    audit_count = await session.scalar(
        select(func.count()).select_from(AuditLog).where(AuditLog.actor == "mcp")
    )
    assert audit_count >= 5

    # 8. С MCP_INCLUDE_SENSITIVE=true чувствительная запись доступна, секрет — нет.
    open_settings = make_mcp_settings(test_settings, include_sensitive=True)
    open_server = create_mcp_server(open_settings, session_factory)
    result = await call_tool(open_server, "get_knowledge_item", {"item_id": str(sensitive.id)})
    assert result["title"] == "Личная переписка"

    result = await call_tool(open_server, "find_related", {
        "entity_type": "project", "entity_id": str(project.id)
    })
    assert "secret" not in {item["type"] for item in result["results"]}


async def test_mcp_http_requires_bearer_token(test_settings, session_factory):
    settings = make_mcp_settings(test_settings)
    server = create_mcp_server(settings, session_factory)
    app = create_mcp_http_app(server, settings)

    port = _free_port()
    uvicorn_server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    )
    serving = asyncio.create_task(uvicorn_server.serve())
    await _wait_port(port)
    try:
        init_body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        }
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", trust_env=False
        ) as client:
            anonymous = await client.post("/mcp", json=init_body, headers=headers)
            assert anonymous.status_code == 401

            wrong = await client.post(
                "/mcp",
                json=init_body,
                headers={**headers, "Authorization": "Bearer wrong-token"},
            )
            assert wrong.status_code == 401

            authorized = await client.post(
                "/mcp",
                json=init_body,
                headers={**headers, "Authorization": f"Bearer {MCP_TOKEN}"},
            )
            assert authorized.status_code == 200
            assert "serverInfo" in authorized.text or '"result"' in authorized.text
    finally:
        uvicorn_server.should_exit = True
        await serving


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def _wait_port(port: int, timeout: float = 10.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.close()
            await writer.wait_closed()
            return
        except OSError:
            await asyncio.sleep(0.1)
    raise RuntimeError("mcp test server did not start")
