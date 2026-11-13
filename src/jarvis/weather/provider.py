"""Доменные модели погоды и интерфейс провайдера."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol


class WeatherUnavailable(RuntimeError):
    """Провайдер погоды недоступен или вернул некорректные данные."""


@dataclass(frozen=True)
class CurrentWeather:
    temperature_c: float
    feels_like_c: float
    humidity_pct: int
    precipitation_mm: float
    weather_code: int
    wind_speed_ms: float
    wind_gust_ms: float
    wind_direction_deg: int
    pressure_hpa: float
    is_day: bool


@dataclass(frozen=True)
class HourlyPoint:
    time: datetime
    temperature_c: float
    precipitation_probability_pct: int
    precipitation_mm: float
    weather_code: int
    wind_speed_ms: float
    is_day: bool


@dataclass(frozen=True)
class DailyWeather:
    date: date
    weather_code: int
    high_c: float
    low_c: float
    sunrise: datetime
    sunset: datetime
    precipitation_probability_pct: int
    wind_speed_max_ms: float


@dataclass(frozen=True)
class WeatherSnapshot:
    """Снимок погоды для одной точки; часовая нарезка — ближайшие часы."""

    city: str
    current: CurrentWeather
    hourly: tuple[HourlyPoint, ...]
    daily: tuple[DailyWeather, ...]
    fetched_at: datetime


class WeatherProvider(Protocol):
    name: str

    async def fetch(self) -> WeatherSnapshot: ...


__all__ = [
    "CurrentWeather",
    "DailyWeather",
    "HourlyPoint",
    "WeatherProvider",
    "WeatherSnapshot",
    "WeatherUnavailable",
]
