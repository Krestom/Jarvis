import httpx

from jarvis.health import create_health_app


class FakeSession:
    async def execute(self, *args, **kwargs):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


class BrokenSession(FakeSession):
    async def execute(self, *args, **kwargs):
        raise RuntimeError("db is down")


async def _get_healthz(app) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/healthz")


async def test_health_ok() -> None:
    app = create_health_app(lambda: FakeSession())
    response = await _get_healthz(app)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["components"]["db"] is True


async def test_health_degraded_when_db_down() -> None:
    app = create_health_app(lambda: BrokenSession())
    response = await _get_healthz(app)
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["components"]["db"] is False
