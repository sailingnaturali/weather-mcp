"""MCP tool implementations.

Returns shapes use {value, display} leaves like tide-mcp. The display field is
pre-formatted and TTS-safe — agents always report `display` verbatim. The model
never sees raw SI values to reformat.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from weather_mcp.cache import EventCache
from weather_mcp.client import RateLimitedClient
from weather_mcp.fetch import (
    get_nearest_buoy_observations,
    get_openmeteo_forecast,
    get_stormglass_forecast,
)
from weather_mcp.providers.ndbc import BuoyObservation
from weather_mcp.providers.openmeteo import MarineForecastHour, WaveObs, WindObs
from weather_mcp.quota import QuotaExhausted, StormglassQuota
from weather_mcp.providers.stormglass import StormglassAuthError, StormglassError

DISPLAY_TZ = ZoneInfo("America/Vancouver")

_COMPASS = [
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
]


def _compass(deg: int | float | None) -> str:
    if deg is None:
        return ""
    return _COMPASS[int(round(deg / 22.5)) % 16]


def _fmt_local(utc: datetime) -> str:
    local = utc.astimezone(DISPLAY_TZ)
    return f"{local:%a %H:%M} {local:%Z}"


def _wind_display(w: WindObs) -> str:
    if w.speed_kn is None:
        return "unavailable"
    s = f"{w.speed_kn:.0f} kn"
    if w.dir_deg is not None:
        s += f" {_compass(w.dir_deg)}"
    if w.gust_kn is not None and w.speed_kn is not None and w.gust_kn >= w.speed_kn + 5:
        s += f", gusting {w.gust_kn:.0f}"
    return s


def _wave_display(label: str, w: WaveObs) -> str:
    if w.height_m is None:
        return f"{label} unavailable"
    s = f"{w.height_m:.1f} m"
    if w.dir_deg is not None:
        s += f" from {_compass(w.dir_deg)}"
    if w.period_s is not None:
        s += f" at {w.period_s:.0f} s"
    return s


def _pressure_display(hpa: float | None) -> str:
    return "unavailable" if hpa is None else f"{hpa:.0f} hPa"


def _hour_dict(h: MarineForecastHour) -> dict:
    return {
        "utc": h.utc.isoformat(),
        "local_display": _fmt_local(h.utc),
        "wind": {
            "value": {
                "speed_kn": h.wind.speed_kn,
                "dir_deg": h.wind.dir_deg,
                "gust_kn": h.wind.gust_kn,
            },
            "display": _wind_display(h.wind),
        },
        "swell": {
            "value": {
                "height_m": h.swell.height_m,
                "dir_deg": h.swell.dir_deg,
                "period_s": h.swell.period_s,
            },
            "display": _wave_display("Swell", h.swell),
        },
        "wind_wave": {
            "value": {
                "height_m": h.wind_wave.height_m,
                "dir_deg": h.wind_wave.dir_deg,
                "period_s": h.wind_wave.period_s,
            },
            "display": _wave_display("Wind waves", h.wind_wave),
        },
        "combined_wave": {
            "value": {
                "height_m": h.combined_wave.height_m,
                "dir_deg": h.combined_wave.dir_deg,
                "period_s": h.combined_wave.period_s,
            },
            "display": _wave_display("Combined seas", h.combined_wave),
        },
        "pressure": {
            "value": {"hpa": h.pressure_hpa},
            "display": _pressure_display(h.pressure_hpa),
        },
    }


def _trend_label(values: list[float | None]) -> str:
    """Wind-speed trend across the forecast window."""
    nums = [v for v in values if v is not None]
    if len(nums) < 2:
        return "steady"
    first, last = nums[0], nums[-1]
    if last - first > 5:
        return "building"
    if first - last > 5:
        return "easing"
    return "steady"


def _summary(hours: list[MarineForecastHour], source: str) -> str:
    if not hours:
        return f"No forecast hours returned ({source})."
    first = hours[0]
    wind_now = _wind_display(first.wind)
    trend = _trend_label([h.wind.speed_kn for h in hours])
    later = next((h for h in hours if h.wind.speed_kn is not None and abs(
        (h.wind.speed_kn or 0) - (first.wind.speed_kn or 0)) > 5), None)
    trend_clause = ""
    if trend != "steady" and later is not None:
        trend_clause = f", {trend} to {later.wind.speed_kn:.0f} kn by {_fmt_local(later.utc)}"
    swell_clause = ""
    if first.swell.height_m is not None:
        swell_clause = f". {_wave_display('Swell', first.swell)}"
    return f"Wind {wind_now}{trend_clause}{swell_clause}."


def _forecast_response(
    source: str, lat: float, lon: float, hours: list[MarineForecastHour]
) -> dict:
    return {
        "source": source,
        "position": {"lat": lat, "lon": lon},
        "issued_utc": datetime.now(timezone.utc).isoformat(),
        "summary_display": _summary(hours, source),
        "hourly": [_hour_dict(h) for h in hours],
    }


async def get_marine_forecast(
    client: RateLimitedClient,
    cache: EventCache,
    lat: float,
    lon: float,
    hours_ahead: int = 12,
) -> dict:
    """Open-Meteo wind + separated swell/wind-waves + pressure."""
    try:
        hours = await get_openmeteo_forecast(client, cache, lat, lon, hours_ahead)
    except Exception as e:
        return {
            "source": "open-meteo",
            "position": {"lat": lat, "lon": lon},
            "error": str(e),
            "summary_display": (
                f"Open-Meteo forecast unavailable: {e}. "
                f"Try again, or use get_marine_forecast_premium (costs 1 Stormglass token)."
            ),
        }
    return _forecast_response("open-meteo", lat, lon, hours)


async def get_marine_forecast_premium(
    client: RateLimitedClient,
    cache: EventCache,
    quota: StormglassQuota,
    lat: float,
    lon: float,
    hours_ahead: int = 24,
) -> dict:
    """Stormglass blended forecast. Costs 1 quota token on cache miss."""
    try:
        hours = await get_stormglass_forecast(client, cache, quota, lat, lon, hours_ahead)
    except QuotaExhausted as e:
        return {
            "source": "stormglass",
            "position": {"lat": lat, "lon": lon},
            "quota_remaining_today": 0,
            "error": str(e),
            "summary_display": (
                f"Stormglass quota exhausted (0/{quota.daily_limit}). "
                f"Resets at UTC midnight ({quota.reset_at_utc()}). "
                f"Use get_marine_forecast for Open-Meteo data."
            ),
        }
    except StormglassAuthError as e:
        return {
            "source": "stormglass",
            "position": {"lat": lat, "lon": lon},
            "quota_remaining_today": quota.remaining_today(),
            "error": str(e),
            "summary_display": f"Stormglass auth failed: {e}",
        }
    except StormglassError as e:
        return {
            "source": "stormglass",
            "position": {"lat": lat, "lon": lon},
            "quota_remaining_today": quota.remaining_today(),
            "error": str(e),
            "summary_display": f"Stormglass error: {e}",
        }
    resp = _forecast_response("stormglass", lat, lon, hours)
    resp["quota_remaining_today"] = quota.remaining_today()
    return resp


def _age_display(observed_utc: datetime) -> str:
    delta = datetime.now(timezone.utc) - observed_utc
    minutes = int(delta.total_seconds() // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    return f"{hours} h ago"


def _buoy_summary(obs: BuoyObservation) -> str:
    bits: list[str] = []
    if obs.wind_speed_kn is not None:
        bits.append(_wind_display(WindObs(obs.wind_speed_kn, obs.wind_dir_deg, obs.wind_gust_kn)))
    if obs.wave_height_m is not None:
        wave_str = f"{obs.wave_height_m:.1f} m"
        if obs.wave_dir_deg is not None:
            wave_str += f" from {_compass(obs.wave_dir_deg)}"
        if obs.wave_dom_period_s is not None:
            wave_str += f" at {obs.wave_dom_period_s:.0f} s"
        bits.append(f"seas {wave_str}")
    return ", ".join(bits) if bits else "no current observations"


def _buoy_dict(obs: BuoyObservation) -> dict:
    return {
        "station_id": obs.station.id,
        "name": obs.station.name,
        "distance_nm": round(obs.distance_nm, 1),
        "bearing_from_position_deg": obs.bearing_deg,
        "bearing_display": _compass(obs.bearing_deg),
        "observed_utc": obs.observed_utc.isoformat(),
        "age_display": _age_display(obs.observed_utc),
        "wind": {
            "value": {
                "speed_kn": obs.wind_speed_kn,
                "dir_deg": obs.wind_dir_deg,
                "gust_kn": obs.wind_gust_kn,
            },
            "display": _wind_display(WindObs(obs.wind_speed_kn, obs.wind_dir_deg, obs.wind_gust_kn))
            if obs.wind_speed_kn is not None
            else "unavailable",
        },
        "wave": {
            "value": {
                "height_m": obs.wave_height_m,
                "dom_period_s": obs.wave_dom_period_s,
                "avg_period_s": obs.wave_avg_period_s,
                "dir_deg": obs.wave_dir_deg,
            },
            "display": _buoy_wave_display(obs),
            "note": "combined waves only — NDBC standard files do not separate swell from wind waves",
        },
        "pressure": {
            "value": {
                "hpa": obs.pressure_hpa,
                "tendency_hpa": obs.pressure_tendency_hpa,
            },
            "display": _buoy_pressure_display(obs),
        },
        "available_fields": obs.available_fields(),
        "summary_display": (
            f"{obs.station.name} ({obs.station.id}), "
            f"{obs.distance_nm:.0f} nm {_compass(obs.bearing_deg)} "
            f"({_age_display(obs.observed_utc)}): {_buoy_summary(obs)}"
        ),
    }


def _buoy_wave_display(obs: BuoyObservation) -> str:
    if obs.wave_height_m is None:
        return "unavailable"
    s = f"{obs.wave_height_m:.1f} m"
    if obs.wave_dir_deg is not None:
        s += f" from {_compass(obs.wave_dir_deg)}"
    if obs.wave_dom_period_s is not None:
        s += f", dominant period {obs.wave_dom_period_s:.0f} s"
    return s


def _buoy_pressure_display(obs: BuoyObservation) -> str:
    if obs.pressure_hpa is None:
        return "unavailable"
    s = f"{obs.pressure_hpa:.0f} hPa"
    if obs.pressure_tendency_hpa is not None:
        trend = "rising" if obs.pressure_tendency_hpa > 0.5 else "falling" if obs.pressure_tendency_hpa < -0.5 else "steady"
        s += f", {trend}"
    return s


async def get_nearest_buoy_observations_tool(
    client: RateLimitedClient,
    cache: EventCache,
    lat: float,
    lon: float,
    max_distance_nm: float = 50.0,
    limit: int = 3,
) -> dict:
    try:
        obs_list = await get_nearest_buoy_observations(
            client, cache, lat, lon, max_distance_nm, limit
        )
    except Exception as e:
        return {
            "source": "ndbc",
            "position": {"lat": lat, "lon": lon},
            "error": str(e),
            "summary_display": f"NDBC station list unavailable: {e}",
        }
    if not obs_list:
        return {
            "source": "ndbc",
            "position": {"lat": lat, "lon": lon},
            "stations": [],
            "summary_display": f"No reporting NDBC buoys within {max_distance_nm:.0f} nm.",
        }
    first = obs_list[0]
    summary = (
        f"Nearest buoy: {first.station.name}, "
        f"{first.distance_nm:.0f} nm {_compass(first.bearing_deg)} "
        f"({_age_display(first.observed_utc)}). {_buoy_summary(first)}."
    )
    return {
        "source": "ndbc",
        "position": {"lat": lat, "lon": lon},
        "stations": [_buoy_dict(o) for o in obs_list],
        "summary_display": summary,
    }


def get_stormglass_quota_status(quota: StormglassQuota) -> dict:
    """Pure SQLite read; no network call."""
    used = quota.used_today()
    remaining = quota.remaining_today()
    return {
        "used_today": used,
        "remaining_today": remaining,
        "daily_limit": quota.daily_limit,
        "resets_at_utc": quota.reset_at_utc(),
        "resets_in_display": _reset_display(quota.reset_seconds()),
        "summary_display": (
            f"Stormglass: {used}/{quota.daily_limit} used today, "
            f"{remaining} remaining. Resets in {_reset_display(quota.reset_seconds())}."
        ),
    }


def _reset_display(seconds: int) -> str:
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"
