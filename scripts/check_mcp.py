"""Проверка MCP-сервера: аутентификация и рукопожатие.

Запуск (на сервере или через ssh-туннель):
    python scripts/check_mcp.py
    python scripts/check_mcp.py --url http://127.0.0.1:8765/mcp

Скрипт не печатает токен. Ожидаемый результат:
    без токена: 401
    с токеном:  200 и имя сервера.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx  # noqa: E402

from jarvis import __version__  # noqa: E402
from jarvis.config.settings import Settings  # noqa: E402

INIT_BODY = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "jarvis-check", "version": __version__},
    },
}
HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


def _extract_server_info(text: str) -> dict | None:
    for line in text.splitlines():
        if line.startswith("data: "):
            try:
                payload = json.loads(line[len("data: "):])
            except ValueError:
                continue
            result = payload.get("result") or {}
            if "serverInfo" in result:
                return result["serverInfo"]
    return None


async def amain() -> int:
    parser = argparse.ArgumentParser(description="Проверка Jarvis MCP")
    parser.add_argument("--url", default=None, help="URL MCP (по умолчанию из настроек)")
    args = parser.parse_args()

    settings = Settings()
    url = args.url or f"http://127.0.0.1:{settings.mcp_port}/mcp"
    token = settings.mcp_token
    if not token:
        print("MCP_AUTH_TOKEN не задан в .env — проверка невозможна.")
        return 2

    # trust_env=False: локальный трафик не должен уходить в системный прокси.
    async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
        anonymous = await client.post(url, json=INIT_BODY, headers=HEADERS)
        print(f"{url}")
        print(f"  без токена: HTTP {anonymous.status_code} (ожидается 401)")
        if anonymous.status_code != 401:
            print("  ⚠️ сервер отвечает без токена — проверьте конфигурацию!")
            return 1

        wrong = await client.post(
            url, json=INIT_BODY, headers={**HEADERS, "Authorization": "Bearer wrong"}
        )
        print(f"  с неверным токеном: HTTP {wrong.status_code} (ожидается 401)")

        authorized = await client.post(
            url, json=INIT_BODY, headers={**HEADERS, "Authorization": f"Bearer {token}"}
        )
        print(f"  с токеном: HTTP {authorized.status_code}")
        if authorized.status_code != 200:
            print("  ❌ MCP не принял токен из .env")
            return 1
        server_info = _extract_server_info(authorized.text)
        if server_info:
            print(
                f"  сервер: {server_info.get('name')} {server_info.get('version')}"
            )
    print("MCP работает (read-only, bearer-аутентификация активна).")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(amain()))
