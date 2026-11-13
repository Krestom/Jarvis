"""Провайдер погоды Open-Meteo (без ключа).

Один GET на точку; единицы — метрика, ветер сразу в м/с (`wind_speed_unit=ms`).
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx

from jarvis.weather.provider import (
    CurrentWeather,
    DailyWeather,
    HourlyPoint,
    WeatherSnapshot,
    WeatherUnavailable,
)

log = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
HOURLY_POINTS = 8
DAILY_POINTS = 2


def _number(value: object, default: float = 0.0) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _integer(value: object, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


class OpenMeteoProvider:
    name = "open-meteo"

    def __init__(
        self,
        *,
        latitude: float,
        longitude: float,
        city: str,
        timezone: str = "Europe/Moscow",
        timeout_seconds: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._latitude = latitude
        self._longitude = longitude
        self._city = city
        self._timezone = timezone
        self._tzinfo = ZoneInfo(timezone)
        self._timeout = timeout_seconds
        self._transport = transport

    async def fetch(self) -> WeatherSnapshot:
        params = {
            "latitude": self._latitude,
            "longitude": self._longitude,
            "current": (
                "temperature_2m,relative_humidity_2m,apparent_temperature,"
                "is_day,precipitation,weather_code,pressure_msl,"
                "wind_speed_10m,wind_direction_10m,wind_gusts_10m"
            ),
            "hourly": (
                "temperature_2m,precipitation_probability,precipitation,"
                "weather_code,wind_speed_10m,is_day"
            ),
            "daily": (
                "weather_code,temperature_2m_max,temperature_2m_min,sunrise,"
                "sunset,precipitation_probability_max,wind_speed_10m_max"
            ),
            "timezone": self._timezone,
            "forecast_days": DAILY_POINTS,
            "wind_speed_unit": "ms",
        }
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.get(OPEN_METEO_URL, params=params)
        except httpx.HTTPError as error:
            log.warning("open-meteo request failed: %s", error)
            raise WeatherUnavailable("сервис погоды недоступен") from error
        if response.status_code >= 400:
            log.warning("open-meteo: HTTP %s", response.status_code)
            raise WeatherUnavailable(
                f"сервис погоды вернул ошибку (HTTP {response.status_code})"
            )
        try:
            payload = response.json()
        except ValueError as error:
            raise WeatherUnavailable("некорректный ответ сервиса погоды") from error
        try:
            return self._parse(payload)
        except (KeyError, TypeError, ValueError, IndexError) as error:
            log.warning("open-meteo: unexpected payload: %s", error)
            raise WeatherUnavailable("некорректный ответ сервиса погоды") from error

    def _parse(self, payload: dict) -> WeatherSnapshot:
        current_raw = payload["current"]
        hourly_raw = payload["hourly"]
        daily_raw = payload["daily"]

        current = CurrentWeather(
            temperature_c=_number(current_raw["temperature_2m"]),
            feels_like_c=_number(current_raw["apparent_temperature"]),
            humidity_pct=_integer(current_raw["relative_humidity_2m"]),
            precipitation_mm=_number(current_raw["precipitation"]),
            weather_code=_integer(current_raw["weather_code"]),
            wind_speed_ms=_number(current_raw["wind_speed_10m"]),
            wind_gust_ms=_number(current_raw["wind_gusts_10m"]),
            wind_direction_deg=_integer(current_raw["wind_direction_10m"]),
            pressure_hpa=_number(current_raw["pressure_msl"]),
            is_day=bool(current_raw["is_day"]),
        )
        current_time = self._aware(current_raw["time"])

        hourly: list[HourlyPoint] = []
        times = hourly_raw["time"]
        for index, raw_time in enumerate(times):
            moment = self._aware(raw_time)
            if moment < current_time:
                continue
            hourly.append(
                HourlyPoint(
                    time=moment,
                    temperature_c=_number(hourly_raw["temperature_2m"][index]),
                    precipitation_probability_pct=_integer(
                        hourly_raw["precipitation_probability"][index]
                    ),
                    precipitation_mm=_number(hourly_raw["precipitation"][index]),
                    weather_code=_integer(hourly_raw["weather_code"][index]),
                    wind_speed_ms=_number(hourly_raw["wind_speed_10m"][index]),
                    is_day=bool(hourly_raw["is_day"][index]),
                )
            )
            if len(hourly) >= HOURLY_POINTS:
                break
        if not hourly:
            raise ValueError("hourly forecast is empty")

        daily: list[DailyWeather] = []
        for index, raw_date in enumerate(daily_raw["time"]):
            daily.append(
                DailyWeather(
                    date=date.fromisoformat(raw_date),
                    weather_code=_integer(daily_raw["weather_code"][index]),
                    high_c=_number(daily_raw["temperature_2m_max"][index]),
                    low_c=_number(daily_raw["temperature_2m_min"][index]),
                    sunrise=self._aware(daily_raw["sunrise"][index]),
                    sunset=self._aware(daily_raw["sunset"][index]),
                    precipitation_probability_pct=_integer(
                        daily_raw["precipitation_probability_max"][index]
                    ),
                    wind_speed_max_ms=_number(daily_raw["wind_speed_10m_max"][index]),
                )
            )
        if not daily:
            raise ValueError("daily forecast is empty")

        return WeatherSnapshot(
            city=self._city,
            current=current,
            hourly=tuple(hourly),
            daily=tuple(daily),
            fetched_at=datetime.now(self._tzinfo),
        )

    def _aware(self, value: str) -> datetime:
        moment = datetime.fromisoformat(str(value))
        if moment.tzinfo is None:
            return moment.replace(tzinfo=self._tzinfo)
        return moment.astimezone(self._tzinfo)


__all__ = ["OpenMeteoProvider"]
