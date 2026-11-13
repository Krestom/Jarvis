"""Провайдер Open-Meteo: параметры, парсинг, ошибки (без сети)."""

from __future__ import annotations

from zoneinfo import ZoneInfo

import httpx
import pytest

from jarvis.weather.open_meteo import OpenMeteoProvider
from jarvis.weather.provider import WeatherUnavailable

TZ = "Europe/Moscow"


def _payload() -> dict:
    return {
        "timezone": TZ,
        "current": {
            "time": "2026-11-13T15:00",
            "temperature_2m": -3.4,
            "relative_humidity_2m": 78,
            "apparent_temperature": -8.1,
            "is_day": 1,
            "precipitation": 0.2,
            "weather_code": 71,
            "pressure_msl": 1012.5,
            "wind_speed_10m": 5.0,
            "wind_direction_10m": 240,
            "wind_gusts_10m": 9.0,
        },
        "hourly": {
            "time": [
                "2026-11-13T14:00",
                "2026-11-13T15:00",
                "2026-11-13T16:00",
                "2026-11-13T17:00",
                "2026-11-13T18:00",
                "2026-11-13T19:00",
                "2026-11-13T20:00",
                "2026-11-13T21:00",
                "2026-11-13T22:00",
                "2026-11-13T23:00",
            ],
            "temperature_2m": [-3.8, -3.4, -3.1, -2.9, -3.0, -3.4, -3.8, -4.1, -4.4, -4.7],
            "precipitation_probability": [10, 20, 30, 40, 50, 60, 70, 80, 90, 100],
            "precipitation": [0.0, 0.1, 0.2, 0.2, 0.1, 0.0, 0.0, 0.0, 0.0, 0.0],
            "weather_code": [3, 71, 71, 71, 3, 3, 3, 3, 3, 3],
            "wind_speed_10m": [4.0, 5.0, 5.5, 6.0, 5.5, 5.0, 4.5, 4.0, 3.5, 3.0],
            "is_day": [0, 1, 1, 1, 0, 0, 0, 0, 0, 0],
        },
        "daily": {
            "time": ["2026-11-13", "2026-11-14"],
            "weather_code": [71, 3],
            "temperature_2m_max": [-1.0, 0.5],
            "temperature_2m_min": [-6.2, -3.0],
            "sunrise": ["2026-11-13T07:58", "2026-11-14T08:00"],
            "sunset": ["2026-11-13T16:20", "2026-11-14T16:18"],
            "precipitation_probability_max": [70, 20],
            "wind_speed_10m_max": [6.0, 4.0],
        },
    }


def _provider(handler) -> OpenMeteoProvider:
    return OpenMeteoProvider(
        latitude=55.7558,
        longitude=37.6173,
        city="Москва",
        timezone=TZ,
        transport=httpx.MockTransport(handler),
    )


async def test_sends_expected_params_and_parses():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json=_payload())

    snapshot = await _provider(handler).fetch()

    assert seen["latitude"] == "55.7558"
    assert seen["longitude"] == "37.6173"
    assert seen["timezone"] == TZ
    assert seen["forecast_days"] == "2"
    assert seen["wind_speed_unit"] == "ms"
    assert "wind_gusts_10m" in seen["current"]
    assert "precipitation_probability_max" in seen["daily"]

    assert snapshot.city == "Москва"
    assert snapshot.current.temperature_c == pytest.approx(-3.4)
    assert snapshot.current.feels_like_c == pytest.approx(-8.1)
    assert snapshot.current.humidity_pct == 78
    assert snapshot.current.wind_speed_ms == pytest.approx(5.0)
    assert snapshot.current.wind_gust_ms == pytest.approx(9.0)
    assert snapshot.current.is_day is True
    assert len(snapshot.hourly) == 8
    assert snapshot.hourly[0].time.hour == 15  # 14:00 уже прошёл
    assert snapshot.hourly[0].time.tzinfo == ZoneInfo(TZ)
    assert snapshot.hourly[0].is_day is True
    assert snapshot.daily[0].high_c == pytest.approx(-1.0)
    assert snapshot.daily[0].low_c == pytest.approx(-6.2)
    assert snapshot.daily[0].sunrise.hour == 7
    assert snapshot.fetched_at.tzinfo == ZoneInfo(TZ)


async def test_http_error_maps_to_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={})

    with pytest.raises(WeatherUnavailable):
        await _provider(handler).fetch()


async def test_timeout_maps_to_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    with pytest.raises(WeatherUnavailable):
        await _provider(handler).fetch()


async def test_broken_json_maps_to_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not-json")

    with pytest.raises(WeatherUnavailable):
        await _provider(handler).fetch()


async def test_missing_sections_maps_to_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    with pytest.raises(WeatherUnavailable):
        await _provider(handler).fetch()
