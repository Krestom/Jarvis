"""WMO-коды: подписи и иконки."""

from __future__ import annotations

import pytest

from jarvis.weather.codes import WMO_CODES, describe_weather_code


@pytest.mark.parametrize("code", sorted(WMO_CODES))
def test_every_code_has_label_and_icon(code):
    label, icon = describe_weather_code(code)
    assert label
    assert icon in {"clear", "partly", "cloudy", "fog", "drizzle", "rain", "snow", "thunder"}


@pytest.mark.parametrize(
    ("code", "icon"),
    [
        (0, "clear"),
        (3, "cloudy"),
        (45, "fog"),
        (51, "drizzle"),
        (63, "rain"),
        (73, "snow"),
        (95, "thunder"),
    ],
)
def test_spot_checks(code, icon):
    assert describe_weather_code(code)[1] == icon


def test_unknown_code_is_neutral():
    assert describe_weather_code(42) == ("нет данных", "cloudy")
