"""HTML-виджет погоды для Bot API 10.4.

Статическая карточка: данные подставляет сервер, JS не нужен. HTML должен
укладываться в 4096 байт после zlib level 9 — это лимит Telegram.
Иконки — inline SVG (без <use>), чтобы не зависеть от поддержки ссылок
в песочнице виджетов.
"""

from __future__ import annotations

import html as html_lib
import zlib

from jarvis.bot.widgets.types import WidgetPayload
from jarvis.weather.codes import describe_weather_code
from jarvis.weather.provider import WeatherSnapshot

MAX_COMPRESSED_BYTES = 4096
HOURS_IN_WIDGET = 6
CAPTION_LIMIT = 1024


class WidgetTooLarge(RuntimeError):
    """HTML не влезает в лимит Telegram после сжатия."""


_ICONS: dict[str, str] = {
    "clear": (
        '<circle cx="16" cy="16" r="13" fill="currentColor" fill-opacity=".14"/>'
        '<circle cx="16" cy="16" r="6" fill="currentColor"/>'
        '<g stroke="currentColor" stroke-width="2" stroke-linecap="round">'
        '<path d="M16 3v4M16 25v4M3 16h4M25 16h4M6.5 6.5l2.8 2.8'
        'M22.7 22.7l2.8 2.8M25.5 6.5l-2.8 2.8M9.3 22.7l-2.8 2.8"/></g>'
    ),
    "night": '<path fill="currentColor" d="M21 3.5a11.5 11.5 0 1 0 7 20.5A13 13 0 0 1 21 3.5z"/>',
    "cloud": (
        '<g fill="currentColor"><circle cx="12" cy="18" r="6"/>'
        '<circle cx="21" cy="16" r="7"/><rect x="8" y="17" width="17" '
        'height="7" rx="3.5"/></g>'
    ),
    "rain": (
        '<g fill="currentColor"><circle cx="12" cy="15" r="5.5"/>'
        '<circle cx="20" cy="13.5" r="6.5"/><rect x="8.5" y="14" width="16" '
        'height="6.5" rx="3.25"/></g>'
        '<g stroke="#bfe4ff" stroke-width="2" stroke-linecap="round">'
        '<path d="M11 24l-1.5 3M17 24l-1.5 3M23 24l-1.5 3"/></g>'
    ),
    "snow": (
        '<g stroke="#dff2ff" stroke-width="2" stroke-linecap="round">'
        '<path d="M16 3v26M4.7 9.5l22.6 13M27.3 9.5L4.7 22.5"/>'
        '<path d="M16 3l-2.5 2.5M16 3l2.5 2.5M16 29l-2.5-2.5M16 29l2.5-2.5'
        'M4.7 9.5l.4 3.5M4.7 9.5l3.5-.4M27.3 22.5l-.4-3.5M27.3 22.5l-3.5.4'
        'M27.3 9.5l-3.5.4M27.3 9.5l-.4 3.5M4.7 22.5l3.5-.4M4.7 22.5l.4-3.5"/></g>'
    ),
    "fog": (
        '<g stroke="currentColor" stroke-width="2.4" stroke-linecap="round">'
        '<path d="M7 10h18M5 15h22M9 20h14M12 25h8"/></g>'
    ),
    "thunder": (
        '<g fill="currentColor"><circle cx="12" cy="13" r="5.5"/>'
        '<circle cx="20" cy="11.5" r="6.5"/><rect x="8.5" y="12" width="16" '
        'height="6.5" rx="3.25"/></g>'
        '<path fill="#ffd76a" d="M18 19l-5 7h3.2l-1 5 5.8-8h-3.2z"/>'
    ),
    "sunrise": (
        '<g fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
        'stroke-linejoin="round"><path d="M4 25h24M9 21a7 7 0 0 1 14 0'
        'M16 12V5M12.5 8.5L16 5l3.5 3.5"/></g>'
    ),
    "sunset": (
        '<g fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
        'stroke-linejoin="round"><path d="M4 25h24M9 21a7 7 0 0 1 14 0'
        'M16 5v7M12.5 8.5L16 12l3.5-3.5"/></g>'
    ),
}

_TEMPLATE = """<style>
*{box-sizing:border-box}
.w{--a:#2d7cf0;--b:#5ea9f7;--c:#8cc6f5;--n1:rgba(0,0,0,0);--n2:rgba(0,0,0,0);
width:100%;padding:18px;padding-bottom:16px;color:#fff;font-family:system-ui,sans-serif;
font-size:14px;line-height:1.25;
background:linear-gradient(180deg,rgba(0,0,0,0) 42%,rgba(7,20,38,.38)),
linear-gradient(180deg,var(--n1),var(--n2)),
radial-gradient(120% 70% at 82% 0%,rgba(255,255,255,.24),rgba(255,255,255,0) 55%),
linear-gradient(165deg,var(--a),var(--b) 52%,var(--c))}
.night{--n1:rgba(3,12,30,.55);--n2:rgba(3,12,30,.2)}
.cloud{--a:#4b6f8e;--b:#6f8ea4;--c:#91a9ba}
.rain{--a:#344b63;--b:#4f6a80;--c:#71889a}
.snow{--a:#54748e;--b:#7897ac;--c:#94aec0}
.fog{--a:#5c6c79;--b:#7f8f9a;--c:#9eacb6}
.storm{--a:#2b3446;--b:#4a586e;--c:#7c8999}
.hd{display:flex;align-items:center;justify-content:space-between;gap:10px;font-size:14px;font-weight:600}
.hd .city{max-width:78%;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
.hd .now{flex:none;font-size:9px;font-weight:500;letter-spacing:.12em;
text-transform:uppercase;color:rgba(255,255,255,.62)}
.hero{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-top:12px}
.temp{font-size:78px;font-weight:200;line-height:.9;letter-spacing:-.05em}
.deg{font-size:32px;font-weight:300;line-height:1;vertical-align:top;margin-left:2px}
.art{position:relative;width:84px;height:84px;flex:none}
.big{position:absolute;top:0;left:0;width:84px;height:84px;color:#fff;display:none}
.b-sun{display:block}
.night .b-sun{display:none}
.night .b-moon{display:block}
.cloud .b-sun,.cloud .b-moon{display:none}
.cloud .b-cloud{display:block}
.rain .b-sun,.rain .b-moon{display:none}
.rain .b-rain{display:block}
.snow .b-sun,.snow .b-moon{display:none}
.snow .b-snow{display:block}
.fog .b-sun,.fog .b-moon{display:none}
.fog .b-fog{display:block}
.storm .b-sun,.storm .b-moon{display:none}
.storm .b-storm{display:block}
.cond{margin-top:8px;font-size:15px;font-weight:500;color:rgba(255,255,255,.92)}
.hl{margin-top:3px;font-size:12px;color:rgba(255,255,255,.66)}
.hours{display:grid;grid-template-columns:repeat(6,1fr);gap:2px;margin-top:16px;
padding:10px 2px;border:1px solid rgba(255,255,255,.16);border-radius:15px;
background:rgba(255,255,255,.12)}
.h{display:flex;flex-direction:column;align-items:center;gap:6px;min-width:0}
.h span{font-size:10px;color:rgba(255,255,255,.68)}
.h b{font-size:13px;font-weight:600}
.ic{width:22px;height:22px;display:block;color:#fff}
.det{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin-top:14px}
.det div{min-width:0}
.det span{display:block;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;
font-size:9px;letter-spacing:.03em;text-transform:uppercase;color:rgba(255,255,255,.56)}
.det b{display:block;margin-top:3px;overflow:hidden;font-size:13px;
font-weight:600;white-space:nowrap;text-overflow:ellipsis}
.foot{display:flex;align-items:center;justify-content:space-between;gap:8px;
margin-top:13px;padding-top:11px;border-top:1px solid rgba(255,255,255,.16);
font-size:10px;color:rgba(255,255,255,.62)}
.foot div{display:flex;align-items:center;gap:6px}
.foot b{font-size:12px;font-weight:600;color:#fff}
.sm{width:15px;height:15px;color:rgba(255,255,255,.8);flex:none}
svg{display:block}
</style>
<div class="w {{sky}}">
<div class="hd"><div class="city">{{city}}</div><div class="now">Сейчас</div></div>
<div class="hero">
<div class="temp">{{temp}}<span class="deg">°</span></div>
<div class="art">{{hero}}</div>
</div>
<div class="cond">{{condition}}</div>
<div class="hl">Макс. {{high}}° · Мин. {{low}}°</div>
<div class="hours">{{hours}}</div>
<div class="det">
<div><span>Ощущается</span><b>{{feels}}°</b></div>
<div><span>Ветер</span><b>{{wind}}</b></div>
<div><span>Влажность</span><b>{{humidity}}%</b></div>
<div><span>Осадки</span><b>{{precip}}%</b></div>
</div>
<div class="foot">
<div>{{rise}}<span>Восход</span><b>{{sunrise}}</b></div>
<div>{{set}}<span>Закат</span><b>{{sunset}}</b></div>
</div>
</div>"""


def _icon(name: str, css_class: str = "ic") -> str:
    body = _ICONS.get(name, _ICONS["cloud"])
    return f'<svg class="{css_class}" viewBox="0 0 32 32">{body}</svg>'


def _hero() -> str:
    items = (
        ("b-sun", "clear"),
        ("b-moon", "night"),
        ("b-cloud", "cloud"),
        ("b-rain", "rain"),
        ("b-snow", "snow"),
        ("b-fog", "fog"),
        ("b-storm", "thunder"),
    )
    return "".join(_icon(name, f"big {css_class}") for css_class, name in items)


def _sky(snapshot: WeatherSnapshot) -> str:
    _, key = describe_weather_code(snapshot.current.weather_code)
    if key == "clear":
        return "" if snapshot.current.is_day else "night"
    return {
        "partly": "cloud",
        "cloudy": "cloud",
        "drizzle": "rain",
        "rain": "rain",
        "snow": "snow",
        "fog": "fog",
        "thunder": "storm",
    }.get(key, "cloud")


def _hours(snapshot: WeatherSnapshot) -> str:
    rows: list[str] = []
    for point in snapshot.hourly[:HOURS_IN_WIDGET]:
        _, key = describe_weather_code(point.weather_code)
        icon = "night" if key == "clear" and not point.is_day else key
        rows.append(
            f'<div class="h"><span>{point.time:%H:%M}</span>'
            f"{_icon(icon)}<b>{round(point.temperature_c)}°</b></div>"
        )
    return "".join(rows)


def _short_caption(snapshot: WeatherSnapshot) -> str:
    label, _ = describe_weather_code(snapshot.current.weather_code)
    return (
        f"Погода в {snapshot.city} · {round(snapshot.current.temperature_c)}°, "
        f"{label.lower()}"
    )


def _rich_message(snapshot: WeatherSnapshot, *, caption: str | None) -> str:
    """Нативная rich-карточка — fallback, если Telegram отклоняет виджет."""
    today = snapshot.daily[0]
    label, _ = describe_weather_code(snapshot.current.weather_code)
    hours = " · ".join(
        f"{point.time:%H:%M} {round(point.temperature_c)}°"
        for point in snapshot.hourly[:HOURS_IN_WIDGET]
    )
    details = (
        f"<p>Ощущается <b>{round(snapshot.current.feels_like_c)}°</b> · "
        f"Ветер <b>{round(snapshot.current.wind_speed_ms)} м/с</b> · "
        f"Влажность <b>{snapshot.current.humidity_pct}%</b> · "
        f"Осадки <b>{today.precipitation_probability_pct}%</b></p>"
    )
    parts = [
        f"<h3>Погода · {html_lib.escape(snapshot.city)}</h3>",
        f"<p><b>{round(snapshot.current.temperature_c)}°</b>, "
        f"{html_lib.escape(label).lower()}. Макс. {round(today.high_c)}°, "
        f"мин. {round(today.low_c)}°.</p>",
        details,
        f"<p>{hours}</p>",
        f"<p>Восход {today.sunrise:%H:%M} · Закат {today.sunset:%H:%M}</p>",
    ]
    if caption:
        parts.append(f"<p>{html_lib.escape(caption.strip())}</p>")
    return "".join(parts)


def render_weather_widget(
    snapshot: WeatherSnapshot, *, caption: str | None = None
) -> WidgetPayload:
    """Собрать HTML и подпись; при превышении лимита — WidgetTooLarge."""
    today = snapshot.daily[0]
    label, _ = describe_weather_code(snapshot.current.weather_code)
    values = {
        "sky": _sky(snapshot),
        "city": html_lib.escape(snapshot.city),
        "temp": f"{round(snapshot.current.temperature_c)}",
        "condition": html_lib.escape(label),
        "hero": _hero(),
        "high": f"{round(today.high_c)}",
        "low": f"{round(today.low_c)}",
        "hours": _hours(snapshot),
        "feels": f"{round(snapshot.current.feels_like_c)}",
        "wind": f"{round(snapshot.current.wind_speed_ms)} м/с",
        "humidity": f"{snapshot.current.humidity_pct}",
        "precip": f"{today.precipitation_probability_pct}",
        "rise": _icon("sunrise", "sm"),
        "set": _icon("sunset", "sm"),
        "sunrise": f"{today.sunrise:%H:%M}",
        "sunset": f"{today.sunset:%H:%M}",
    }
    html = _TEMPLATE
    for key, value in values.items():
        html = html.replace("{{" + key + "}}", value)

    compressed = len(zlib.compress(html.encode("utf-8"), 9))
    if compressed > MAX_COMPRESSED_BYTES:
        raise WidgetTooLarge(
            f"виджет погоды не влезает: {compressed} байт после сжатия "
            f"(лимит {MAX_COMPRESSED_BYTES})"
        )

    text = (caption or _short_caption(snapshot)).strip()
    if len(text) > CAPTION_LIMIT:
        text = text[: CAPTION_LIMIT - 8].rstrip() + "… /today"
    rich = _rich_message(snapshot, caption=text if caption is not None else None)
    return WidgetPayload(
        html=html, caption=text, width=360, rich_html=rich
    )


__all__ = ["WidgetTooLarge", "render_weather_widget"]
