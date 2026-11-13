"""Отключённый провайдер погоды (WEATHER_PROVIDER=none)."""

from __future__ import annotations

from jarvis.weather.provider import WeatherSnapshot, WeatherUnavailable


class NoneWeatherProvider:
    name = "none"

    async def fetch(self) -> WeatherSnapshot:
        raise WeatherUnavailable(
            "погода не настроена: задайте WEATHER_PROVIDER=open-meteo"
        )


__all__ = ["NoneWeatherProvider"]
