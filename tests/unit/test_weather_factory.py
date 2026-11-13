"""Фабрика провайдера погоды."""

from __future__ import annotations

import pytest

from jarvis.weather import create_weather_provider
from jarvis.weather.none import NoneWeatherProvider
from jarvis.weather.open_meteo import OpenMeteoProvider
from jarvis.weather.provider import WeatherUnavailable


async def test_none_provider(settings):
    settings.weather_provider = "none"
    provider = create_weather_provider(settings)
    assert isinstance(provider, NoneWeatherProvider)
    with pytest.raises(WeatherUnavailable):
        await provider.fetch()


def test_open_meteo_provider(settings):
    settings.weather_provider = "open-meteo"
    provider = create_weather_provider(settings)
    assert isinstance(provider, OpenMeteoProvider)


def test_unknown_provider(settings):
    settings.weather_provider = "weatherkit"
    with pytest.raises(ValueError):
        create_weather_provider(settings)
