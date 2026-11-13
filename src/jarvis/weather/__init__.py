"""Фабрика провайдера погоды по конфигурации (WEATHER_PROVIDER)."""

from __future__ import annotations

from jarvis.config.settings import Settings
from jarvis.weather.none import NoneWeatherProvider
from jarvis.weather.provider import WeatherProvider


def create_weather_provider(settings: Settings) -> WeatherProvider:
    provider = settings.weather_provider.strip().lower()
    if provider in ("", "none", "off", "disabled"):
        return NoneWeatherProvider()
    if provider in ("open-meteo", "open_meteo"):
        from jarvis.weather.open_meteo import OpenMeteoProvider

        return OpenMeteoProvider(
            latitude=settings.weather_latitude,
            longitude=settings.weather_longitude,
            city=settings.weather_city,
            timezone=settings.weather_timezone,
            timeout_seconds=settings.weather_timeout_seconds,
        )
    raise ValueError(
        f"unknown weather provider: {settings.weather_provider!r} "
        "(доступны: none, open-meteo)"
    )


__all__ = ["create_weather_provider"]
