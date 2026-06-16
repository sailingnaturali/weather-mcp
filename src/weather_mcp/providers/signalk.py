"""SignalK Weather API provider.

Reads the boat's standard Weather API (``/signalk/v2/api/weather/forecasts/point``,
populated by a WeatherProvider plugin such as ``@signalk/open-meteo-provider``) and
maps the SI ``WeatherData`` shape to ``MarineForecastHour``. This lets the MCP read
the boat's canonical weather surface instead of hitting the internet directly;
callers fall back to direct Open-Meteo when SignalK or a provider is unavailable.

Note: SignalK's ``water`` spec carries combined-significant wave height + swell but
no separate wind-wave component, so ``wind_wave`` is ``None`` when sourced here
(``combined_wave`` and ``swell`` are still populated).
"""

from __future__ import annotations

import math

from marine_forecast.client import RateLimitedClient
from marine_forecast.openmeteo import (
    MarineForecastHour,
    WaveObs,
    WindObs,
    _parse_dt,
)

_MS_TO_KN = 1.943844


def _kn(ms: float | None) -> float | None:
    return None if ms is None else round(ms * _MS_TO_KN, 1)


def _deg(rad: float | None) -> int | None:
    return None if rad is None else int(round(math.degrees(rad))) % 360


def _hpa(pa: float | None) -> float | None:
    return None if pa is None else round(pa / 100.0, 1)


def _wave(water: dict, height: str, direction: str, period: str) -> WaveObs:
    return WaveObs(
        height_m=water.get(height),
        dir_deg=_deg(water.get(direction)),
        period_s=water.get(period),
    )


def weather_data_to_hour(wd: dict) -> MarineForecastHour:
    """Map one SignalK ``WeatherData`` entry (SI units) to a ``MarineForecastHour``.

    SignalK is SI: wind m/s, directions radians, pressure Pa, wave heights m,
    periods s. The model uses knots / degrees / hPa, so convert accordingly.
    """
    wind = wd.get("wind") or {}
    water = wd.get("water") or {}
    outside = wd.get("outside") or {}
    return MarineForecastHour(
        utc=_parse_dt(wd["date"]),
        wind=WindObs(
            speed_kn=_kn(wind.get("speedTrue")),
            dir_deg=_deg(wind.get("directionTrue")),
            gust_kn=_kn(wind.get("gust")),
        ),
        swell=_wave(water, "swellHeight", "swellDirection", "swellPeriod"),
        combined_wave=_wave(
            water, "waveSignificantHeight", "waveDirection", "wavePeriod"
        ),
        wind_wave=WaveObs(None, None, None),
        pressure_hpa=_hpa(outside.get("pressure")),
    )


async def fetch_forecast(
    client: RateLimitedClient,
    base_url: str,
    lat: float,
    lon: float,
    hours_ahead: int,
) -> list[MarineForecastHour]:
    """GET the SignalK point forecast and map it.

    Raises on any failure (server unreachable, no provider configured -> 404,
    empty body) so the caller can fall back to a direct provider.
    """
    url = f"{base_url.rstrip('/')}/signalk/v2/api/weather/forecasts/point"
    resp = await client.get(url, params={"lat": lat, "lon": lon, "count": hours_ahead})
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list) or not data:
        raise ValueError("SignalK weather forecast empty or unavailable")
    return [weather_data_to_hour(wd) for wd in data][:hours_ahead]
