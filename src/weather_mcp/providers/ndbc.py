"""NDBC buoy provider. Free, no API key.

Three endpoints:
- activestations.xml: full station list (cached 24h)
- realtime2/{id}.txt: latest observations per station (cached 15min)
- realtime2/{id}.spec: swell vs wind-wave separation, where published (merged
  into the observation when its timestamp is within 1h of the .txt row)
"""

from __future__ import annotations

import asyncio
import math
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from weather_mcp.client import RateLimitedClient

STATIONS_URL = "https://www.ndbc.noaa.gov/activestations.xml"
REALTIME2_URL = "https://www.ndbc.noaa.gov/data/realtime2/{station_id}.txt"
SPEC_URL = "https://www.ndbc.noaa.gov/data/realtime2/{station_id}.spec"

MS_TO_KN = 1.94384
MM = "MM"  # NDBC missing-value sentinel


@dataclass(frozen=True)
class Station:
    id: str
    name: str
    lat: float
    lon: float


@dataclass(frozen=True)
class SpecWaves:
    """One row of a realtime2 .spec file: swell vs wind-wave separation."""

    observed_utc: datetime
    swell_height_m: float | None
    swell_period_s: float | None
    swell_dir_compass: str | None
    wind_wave_height_m: float | None
    wind_wave_period_s: float | None
    wind_wave_dir_compass: str | None
    steepness: str | None


@dataclass(frozen=True)
class BuoyObservation:
    station: Station
    observed_utc: datetime
    distance_nm: float
    bearing_deg: int
    wind_speed_kn: float | None
    wind_dir_deg: int | None
    wind_gust_kn: float | None
    wave_height_m: float | None
    wave_dom_period_s: float | None
    wave_avg_period_s: float | None
    wave_dir_deg: int | None
    pressure_hpa: float | None
    pressure_tendency_hpa: float | None

    def available_fields(self) -> list[str]:
        out = []
        if self.wind_speed_kn is not None or self.wind_dir_deg is not None:
            out.append("wind")
        if self.wave_height_m is not None or self.wave_dir_deg is not None:
            out.append("wave")
        if self.pressure_hpa is not None:
            out.append("pressure")
        return out

    def to_dict(self) -> dict:
        d = asdict(self)
        d["observed_utc"] = self.observed_utc.isoformat()
        return d


def parse_activestations(xml_text: str) -> list[Station]:
    """Parse activestations.xml, return met=y stations."""
    root = ET.fromstring(xml_text)
    out: list[Station] = []
    for s in root.findall("station"):
        if s.get("met") != "y":
            continue
        try:
            out.append(
                Station(
                    id=s.get("id", ""),
                    name=s.get("name", ""),
                    lat=float(s.get("lat", "0")),
                    lon=float(s.get("lon", "0")),
                )
            )
        except (TypeError, ValueError):
            continue
    return out


def _maybe_float(token: str) -> float | None:
    return None if token == MM else float(token)


def _maybe_int(token: str) -> int | None:
    return None if token == MM else int(round(float(token)))


def _maybe_str(token: str) -> str | None:
    return None if token in (MM, "N/A") else token


def parse_realtime2(text: str, station: Station, ref_lat: float, ref_lon: float) -> BuoyObservation | None:
    """Parse the first non-header data row of a realtime2 .txt response.

    Header rows start with '#'. Returns None if the file has no data rows or
    every observed field is missing.
    """
    rows = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    if not rows:
        return None
    cols = rows[0].split()
    if len(cols) < 13:
        return None

    yyyy, mm, dd, hh, mn = cols[0:5]
    try:
        observed = datetime(int(yyyy), int(mm), int(dd), int(hh), int(mn), tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None

    wdir = _maybe_int(cols[5])
    wspd_ms = _maybe_float(cols[6])
    gst_ms = _maybe_float(cols[7])
    wvht = _maybe_float(cols[8])
    dpd = _maybe_float(cols[9])
    apd = _maybe_float(cols[10])
    mwd = _maybe_int(cols[11])
    pres = _maybe_float(cols[12])
    ptdy = _maybe_float(cols[17]) if len(cols) > 17 else None

    wspd_kn = wspd_ms * MS_TO_KN if wspd_ms is not None else None
    gst_kn = gst_ms * MS_TO_KN if gst_ms is not None else None

    obs = BuoyObservation(
        station=station,
        observed_utc=observed,
        distance_nm=_haversine_nm(ref_lat, ref_lon, station.lat, station.lon),
        bearing_deg=_bearing_deg(ref_lat, ref_lon, station.lat, station.lon),
        wind_speed_kn=wspd_kn,
        wind_dir_deg=wdir,
        wind_gust_kn=gst_kn,
        wave_height_m=wvht,
        wave_dom_period_s=dpd,
        wave_avg_period_s=apd,
        wave_dir_deg=mwd,
        pressure_hpa=pres,
        pressure_tendency_hpa=ptdy,
    )
    if not obs.available_fields():
        return None
    return obs


def parse_spec(text: str) -> SpecWaves | None:
    """Parse the first data row of a realtime2 .spec response.

    Directions arrive as compass strings (e.g. 'WSW') and are kept as-is.
    Returns None if there are no data rows or every wave field is missing.
    """
    rows = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    if not rows:
        return None
    cols = rows[0].split()
    if len(cols) < 13:
        return None

    yyyy, mm, dd, hh, mn = cols[0:5]
    try:
        observed = datetime(int(yyyy), int(mm), int(dd), int(hh), int(mn), tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None

    spec = SpecWaves(
        observed_utc=observed,
        swell_height_m=_maybe_float(cols[6]),
        swell_period_s=_maybe_float(cols[7]),
        swell_dir_compass=_maybe_str(cols[10]),
        wind_wave_height_m=_maybe_float(cols[8]),
        wind_wave_period_s=_maybe_float(cols[9]),
        wind_wave_dir_compass=_maybe_str(cols[11]),
        steepness=_maybe_str(cols[12]),
    )
    if spec.swell_height_m is None and spec.wind_wave_height_m is None:
        return None
    return spec


def _haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r_nm = 3440.065
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r_nm * math.asin(math.sqrt(a))


def _bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> int:
    """Initial bearing from (lat1,lon1) toward (lat2,lon2), in degrees [0,360)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    brng = math.degrees(math.atan2(x, y))
    return int(round((brng + 360) % 360))


async def fetch_active_stations(client: RateLimitedClient) -> list[Station]:
    """Fetch + parse the active stations XML. No caching here — caller decides."""
    resp = await client.get(STATIONS_URL)
    resp.raise_for_status()
    return parse_activestations(resp.text)


async def fetch_station_observation(
    client: RateLimitedClient, station: Station, ref_lat: float, ref_lon: float
) -> BuoyObservation | None:
    """Fetch + parse the latest observation for one station."""
    url = REALTIME2_URL.format(station_id=station.id)
    try:
        resp = await client.get(url)
        resp.raise_for_status()
    except Exception:
        return None
    return parse_realtime2(resp.text, station, ref_lat, ref_lon)


def nearest_stations(
    stations: list[Station], lat: float, lon: float, max_distance_nm: float, limit: int
) -> list[Station]:
    """Return up to `limit` stations within `max_distance_nm`, sorted nearest first."""
    with_dist = [(s, _haversine_nm(lat, lon, s.lat, s.lon)) for s in stations]
    in_range = [(s, d) for s, d in with_dist if d <= max_distance_nm]
    in_range.sort(key=lambda sd: sd[1])
    return [s for s, _ in in_range[:limit]]


async def fetch_nearest_observations(
    client: RateLimitedClient,
    stations: list[Station],
    lat: float,
    lon: float,
    max_distance_nm: float,
    limit: int,
    candidate_multiplier: int = 3,
) -> list[BuoyObservation]:
    """Try the closest stations, drop any with no usable data, return up to `limit`.

    We over-fetch candidates because not every nearby station reports waves or
    might be temporarily offline.
    """
    candidates = nearest_stations(stations, lat, lon, max_distance_nm, limit * candidate_multiplier)
    results = await asyncio.gather(
        *(fetch_station_observation(client, s, lat, lon) for s in candidates)
    )
    usable = [r for r in results if r is not None]
    usable.sort(key=lambda o: o.distance_nm)
    return usable[:limit]
