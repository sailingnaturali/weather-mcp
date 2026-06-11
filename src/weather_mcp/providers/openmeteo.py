"""Open-Meteo provider. Free, no API key.

Two endpoints fetched in parallel:
- forecast: wind + pressure
- marine: combined waves, swell, wind waves (separated)

If marine fails, return wind+pressure with wave fields as None — graceful
degradation; the briefing already worked this way (briefing.py:112-123).
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from weather_mcp.client import RateLimitedClient

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"


@dataclass(frozen=True)
class WindObs:
    speed_kn: float | None
    dir_deg: int | None
    gust_kn: float | None


@dataclass(frozen=True)
class WaveObs:
    height_m: float | None
    dir_deg: int | None
    period_s: float | None


@dataclass(frozen=True)
class MarineForecastHour:
    utc: datetime
    wind: WindObs
    swell: WaveObs
    wind_wave: WaveObs
    combined_wave: WaveObs
    pressure_hpa: float | None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["utc"] = self.utc.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "MarineForecastHour":
        return cls(
            utc=_parse_dt(d["utc"]),
            wind=WindObs(**d["wind"]),
            swell=WaveObs(**d["swell"]),
            wind_wave=WaveObs(**d["wind_wave"]),
            combined_wave=WaveObs(**d["combined_wave"]),
            pressure_hpa=d.get("pressure_hpa"),
        )


def _parse_dt(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _safe(arr: list | None, i: int) -> Any | None:
    if arr is None or i >= len(arr):
        return None
    v = arr[i]
    return v if v is not None else None


def _int_or_none(v: Any) -> int | None:
    return None if v is None else int(round(v))


async def fetch_forecast(
    client: RateLimitedClient, lat: float, lon: float, hours_ahead: int = 12
) -> list[MarineForecastHour]:
    """Fetch wind + marine in parallel, zip into MarineForecastHour entries.

    Returns up to `hours_ahead` hours starting at the current UTC hour. If the
    marine endpoint fails (shore-near cell, transient error), wave fields are
    None but wind/pressure are still returned. Raises if the forecast endpoint
    itself fails.
    """
    forecast_task = _fetch_json(
        client,
        FORECAST_URL,
        {
            "latitude": lat,
            "longitude": lon,
            "hourly": "wind_speed_10m,wind_direction_10m,wind_gusts_10m,pressure_msl",
            "wind_speed_unit": "kn",
            "forecast_days": 2,
            "timezone": "UTC",
        },
    )
    marine_task = _fetch_json(
        client,
        MARINE_URL,
        {
            "latitude": lat,
            "longitude": lon,
            "hourly": (
                "wave_height,wave_direction,wave_period,"
                "swell_wave_height,swell_wave_direction,swell_wave_period,"
                "wind_wave_height,wind_wave_direction,wind_wave_period"
            ),
            "forecast_days": 2,
            "timezone": "UTC",
        },
        optional=True,
    )

    forecast_json, marine_json = await asyncio.gather(forecast_task, marine_task)

    fh = forecast_json["hourly"]
    times = fh["time"]
    wind_speed = fh.get("wind_speed_10m", [])
    wind_dir = fh.get("wind_direction_10m", [])
    wind_gust = fh.get("wind_gusts_10m", [])
    pressure = fh.get("pressure_msl", [])

    if marine_json is not None:
        mh = marine_json.get("hourly", {})
        wave_h = mh.get("wave_height", [])
        wave_d = mh.get("wave_direction", [])
        wave_p = mh.get("wave_period", [])
        swell_h = mh.get("swell_wave_height", [])
        swell_d = mh.get("swell_wave_direction", [])
        swell_p = mh.get("swell_wave_period", [])
        wwave_h = mh.get("wind_wave_height", [])
        wwave_d = mh.get("wind_wave_direction", [])
        wwave_p = mh.get("wind_wave_period", [])
    else:
        wave_h = wave_d = wave_p = None
        swell_h = swell_d = swell_p = None
        wwave_h = wwave_d = wwave_p = None

    now = datetime.now(timezone.utc)
    out: list[MarineForecastHour] = []
    for i, t in enumerate(times):
        utc = _parse_dt(t)
        # Strictly-before the truncated current hour keeps the in-progress
        # hour (the 14:00 row at 14:59) and drops completed ones.
        if utc < now.replace(minute=0, second=0, microsecond=0):
            continue
        out.append(
            MarineForecastHour(
                utc=utc,
                wind=WindObs(
                    speed_kn=_safe(wind_speed, i),
                    dir_deg=_int_or_none(_safe(wind_dir, i)),
                    gust_kn=_safe(wind_gust, i),
                ),
                swell=WaveObs(
                    height_m=_safe(swell_h, i),
                    dir_deg=_int_or_none(_safe(swell_d, i)),
                    period_s=_safe(swell_p, i),
                ),
                wind_wave=WaveObs(
                    height_m=_safe(wwave_h, i),
                    dir_deg=_int_or_none(_safe(wwave_d, i)),
                    period_s=_safe(wwave_p, i),
                ),
                combined_wave=WaveObs(
                    height_m=_safe(wave_h, i),
                    dir_deg=_int_or_none(_safe(wave_d, i)),
                    period_s=_safe(wave_p, i),
                ),
                pressure_hpa=_safe(pressure, i),
            )
        )
        if len(out) >= hours_ahead:
            break
    return out


async def _fetch_json(
    client: RateLimitedClient, url: str, params: dict, optional: bool = False
) -> dict | None:
    """GET + parse JSON. If optional=True, swallow exceptions and return None."""
    try:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        if optional:
            return None
        raise
