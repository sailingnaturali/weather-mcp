"""Stormglass provider. Blended marine forecast (model 'sg').

Free tier: 10 requests per UTC day. Each request returns up to 10 days of
hourly data for a point, so cache aggressively.

Quota discipline: check remaining_today() before any HTTP call; consume only
after a 200 response. Cache hits do not consume. Callers convert the raised
QuotaExhausted / StormglassError into a clean error dict at the tool layer.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from weather_mcp.client import RateLimitedClient
from weather_mcp.providers.openmeteo import (
    MarineForecastHour,
    WaveObs,
    WindObs,
    _parse_dt,
)
from weather_mcp.quota import QuotaExhausted, StormglassQuota

ENDPOINT = "https://api.stormglass.io/v2/weather/point"
SOURCE = "sg"
PARAM_LIST = [
    "windSpeed",
    "windDirection",
    "gust",
    "waveHeight",
    "waveDirection",
    "wavePeriod",
    "swellHeight",
    "swellDirection",
    "swellPeriod",
    "windWaveHeight",
    "windWaveDirection",
    "windWavePeriod",
    "pressure",
]

MS_TO_KN = 1.94384


class StormglassError(Exception):
    """Wraps HTTP/auth/parsing errors from Stormglass."""


class StormglassAuthError(StormglassError):
    """Missing API key, or upstream returned 401/403."""


def _api_key() -> str:
    key = os.environ.get("STORMGLASS_API_KEY", "").strip()
    if not key:
        raise StormglassAuthError("STORMGLASS_API_KEY is not set")
    return key


def _pick_sg(obj: dict | None, key: str) -> float | None:
    if not obj or key not in obj:
        return None
    v = obj[key]
    if isinstance(v, dict):
        return v.get(SOURCE)
    return v


def _int_or_none(v: float | None) -> int | None:
    return None if v is None else int(round(v))


def parse_hours(payload: dict) -> list[MarineForecastHour]:
    """Parse the `hours` array from a Stormglass response."""
    out: list[MarineForecastHour] = []
    for row in payload.get("hours", []):
        wind_kn = _pick_sg(row, "windSpeed")
        wind_kn = wind_kn * MS_TO_KN if wind_kn is not None else None
        gust_kn = _pick_sg(row, "gust")
        gust_kn = gust_kn * MS_TO_KN if gust_kn is not None else None
        out.append(
            MarineForecastHour(
                utc=_parse_dt(row["time"]),
                wind=WindObs(
                    speed_kn=wind_kn,
                    dir_deg=_int_or_none(_pick_sg(row, "windDirection")),
                    gust_kn=gust_kn,
                ),
                swell=WaveObs(
                    height_m=_pick_sg(row, "swellHeight"),
                    dir_deg=_int_or_none(_pick_sg(row, "swellDirection")),
                    period_s=_pick_sg(row, "swellPeriod"),
                ),
                wind_wave=WaveObs(
                    height_m=_pick_sg(row, "windWaveHeight"),
                    dir_deg=_int_or_none(_pick_sg(row, "windWaveDirection")),
                    period_s=_pick_sg(row, "windWavePeriod"),
                ),
                combined_wave=WaveObs(
                    height_m=_pick_sg(row, "waveHeight"),
                    dir_deg=_int_or_none(_pick_sg(row, "waveDirection")),
                    period_s=_pick_sg(row, "wavePeriod"),
                ),
                pressure_hpa=_pick_sg(row, "pressure"),
            )
        )
    return out


async def fetch_forecast(
    client: RateLimitedClient,
    quota: StormglassQuota,
    lat: float,
    lon: float,
    hours_ahead: int = 24,
) -> list[MarineForecastHour]:
    """Stormglass premium forecast. Consumes one quota token on 200 response only."""
    if quota.remaining_today() <= 0:
        raise QuotaExhausted(
            f"Stormglass quota exhausted (0/{quota.daily_limit}); resets at UTC midnight"
        )

    key = _api_key()
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    end = now + timedelta(hours=hours_ahead)
    params = {
        "lat": lat,
        "lng": lon,
        "params": ",".join(PARAM_LIST),
        "source": SOURCE,
        "start": int(now.timestamp()),
        "end": int(end.timestamp()),
    }
    resp = await client.get(ENDPOINT, params=params, headers={"Authorization": key})

    if resp.status_code == 401 or resp.status_code == 403:
        raise StormglassAuthError(f"Stormglass auth failed: {resp.status_code}")
    if 500 <= resp.status_code < 600:
        raise StormglassError(f"Stormglass server error: {resp.status_code}")
    if resp.status_code >= 400:
        raise StormglassError(f"Stormglass error: {resp.status_code} {resp.text[:200]}")

    payload = resp.json()
    quota.consume()
    return parse_hours(payload)
