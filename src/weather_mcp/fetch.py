"""Orchestration: provider dispatch with cache lookup.

Each provider returns dataclass instances; here we cache via the dataclass
to_dict/from_dict round-trip.
"""

from __future__ import annotations

import time

from weather_mcp.cache import EventCache
from weather_mcp.client import RateLimitedClient
from weather_mcp.providers import ndbc, openmeteo, stormglass
from weather_mcp.providers.openmeteo import MarineForecastHour
from weather_mcp.quota import StormglassQuota

OPENMETEO_TTL = 60 * 60  # 1h
STORMGLASS_TTL = 6 * 60 * 60  # 6h
NDBC_OBS_TTL = 15 * 60  # 15min
NDBC_STATIONS_TTL = 24 * 60 * 60


def _bucket(value: float, places: int) -> str:
    return f"{round(value, places):.{places}f}"


def _hour_bucket() -> int:
    return int(time.time() // 3600)


def _six_hour_bucket() -> int:
    return int(time.time() // (6 * 3600))


async def get_openmeteo_forecast(
    client: RateLimitedClient, cache: EventCache, lat: float, lon: float, hours_ahead: int
) -> list[MarineForecastHour]:
    key = f"openmeteo:{_bucket(lat, 2)}:{_bucket(lon, 2)}:{_hour_bucket()}"
    cached = cache.get_with_ttl(key, OPENMETEO_TTL)
    if cached is not None:
        hours = [MarineForecastHour.from_dict(d) for d in cached]
        return hours[:hours_ahead]
    hours = await openmeteo.fetch_forecast(client, lat, lon, hours_ahead=hours_ahead)
    cache.put_with_ttl(key, [h.to_dict() for h in hours])
    return hours


async def get_stormglass_forecast(
    client: RateLimitedClient,
    cache: EventCache,
    quota: StormglassQuota,
    lat: float,
    lon: float,
    hours_ahead: int,
) -> list[MarineForecastHour]:
    key = f"stormglass:{_bucket(lat, 1)}:{_bucket(lon, 1)}:{_six_hour_bucket()}"
    cached = cache.get_with_ttl(key, STORMGLASS_TTL)
    if cached is not None:
        hours = [MarineForecastHour.from_dict(d) for d in cached]
        return hours[:hours_ahead]
    hours = await stormglass.fetch_forecast(client, quota, lat, lon, hours_ahead=hours_ahead)
    cache.put_with_ttl(key, [h.to_dict() for h in hours])
    return hours


async def get_ndbc_stations(
    client: RateLimitedClient, cache: EventCache
) -> list[ndbc.Station]:
    key = "ndbc:activestations"
    cached = cache.get_with_ttl(key, NDBC_STATIONS_TTL)
    if cached is not None:
        return [ndbc.Station(**d) for d in cached]
    stations = await ndbc.fetch_active_stations(client)
    cache.put_with_ttl(key, [s.__dict__ for s in stations])
    return stations


async def get_ndbc_observation(
    client: RateLimitedClient,
    cache: EventCache,
    station: ndbc.Station,
    ref_lat: float,
    ref_lon: float,
) -> ndbc.BuoyObservation | None:
    key = f"ndbc:obs:{station.id}"
    cached = cache.get_with_ttl(key, NDBC_OBS_TTL)
    if cached is not None:
        return _obs_from_dict(cached[0], station, ref_lat, ref_lon)
    obs = await ndbc.fetch_station_observation(client, station, ref_lat, ref_lon)
    if obs is not None:
        cache.put_with_ttl(key, [obs.to_dict()])
    return obs


def _obs_from_dict(
    d: dict, station: ndbc.Station, ref_lat: float, ref_lon: float
) -> ndbc.BuoyObservation:
    """Reconstruct an observation from cache, recomputing distance/bearing for the
    current reference point (the boat may have moved since the obs was cached)."""
    return ndbc.BuoyObservation(
        station=station,
        observed_utc=openmeteo._parse_dt(d["observed_utc"]),
        distance_nm=ndbc._haversine_nm(ref_lat, ref_lon, station.lat, station.lon),
        bearing_deg=ndbc._bearing_deg(ref_lat, ref_lon, station.lat, station.lon),
        wind_speed_kn=d.get("wind_speed_kn"),
        wind_dir_deg=d.get("wind_dir_deg"),
        wind_gust_kn=d.get("wind_gust_kn"),
        wave_height_m=d.get("wave_height_m"),
        wave_dom_period_s=d.get("wave_dom_period_s"),
        wave_avg_period_s=d.get("wave_avg_period_s"),
        wave_dir_deg=d.get("wave_dir_deg"),
        pressure_hpa=d.get("pressure_hpa"),
        pressure_tendency_hpa=d.get("pressure_tendency_hpa"),
    )


async def get_nearest_buoy_observations(
    client: RateLimitedClient,
    cache: EventCache,
    lat: float,
    lon: float,
    max_distance_nm: float,
    limit: int,
) -> list[ndbc.BuoyObservation]:
    stations = await get_ndbc_stations(client, cache)
    candidates = ndbc.nearest_stations(stations, lat, lon, max_distance_nm, limit * 3)
    results: list[ndbc.BuoyObservation] = []
    for station in candidates:
        obs = await get_ndbc_observation(client, cache, station, lat, lon)
        if obs is not None:
            results.append(obs)
        if len(results) >= limit:
            break
    results.sort(key=lambda o: o.distance_nm)
    return results[:limit]
