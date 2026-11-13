"""Статический виджет погоды: размер, тема, экранирование."""

from __future__ import annotations

import zlib
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from jarvis.bot.widgets.weather import MAX_COMPRESSED_BYTES, render_weather_widget
from jarvis.weather.provider import CurrentWeather, DailyWeather, HourlyPoint, WeatherSnapshot

TZ = ZoneInfo("Europe/Moscow")
NOW = datetime(2026, 11, 13, 8, 0, tzinfo=TZ)


def _snapshot(code: int = 71, *, is_day: bool = True, city: str = "Москва") -> WeatherSnapshot:
    return WeatherSnapshot(
        city=city,
        current=CurrentWeather(-3.4, -8.1, 78, 0.0, code, 5.0, 9.0, 240, 1012.5, is_day),
        hourly=tuple(
            HourlyPoint(
                NOW + timedelta(hours=i),
                -3.4 - i * 0.3,
                20 + i * 5,
                0.0,
                71 if i < 3 else 3,
                5.0,
                True,
            )
            for i in range(8)
        ),
        daily=(
            DailyWeather(
                date(2026, 11, 13),
                71,
                -1.0,
                -6.2,
                NOW.replace(hour=7, minute=58),
                NOW.replace(hour=16, minute=20),
                70,
                6.0,
            ),
            DailyWeather(
                date(2026, 11, 14),
                3,
                0.5,
                -3.0,
                NOW.replace(hour=8, minute=0),
                NOW.replace(hour=16, minute=18),
                20,
                4.0,
            ),
        ),
        fetched_at=NOW,
    )


def test_widget_fits_compressed_limit():
    payload = render_weather_widget(_snapshot())
    compressed = len(zlib.compress(payload.html.encode("utf-8"), 9))
    assert compressed <= MAX_COMPRESSED_BYTES
    assert "{{" not in payload.html


def test_caption_and_theme():
    payload = render_weather_widget(_snapshot(code=0, is_day=False), caption="Доброе утро")
    assert payload.caption == "Доброе утро"
    assert 'class="w night"' in payload.html


def test_city_is_escaped():
    payload = render_weather_widget(_snapshot(city="<Москва & Co>"))
    assert "<Москва & Co>" not in payload.html
    assert "&lt;Москва &amp; Co&gt;" in payload.html


def test_caption_is_truncated():
    payload = render_weather_widget(_snapshot(), caption="я" * 2000)
    assert len(payload.caption) <= 1024
    assert payload.caption.endswith("/today")
