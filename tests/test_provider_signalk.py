import httpx
import pytest
import respx

from weather_mcp.cache import EventCache
from weather_mcp.client import RateLimitedClient
from weather_mcp.providers import signalk
from weather_mcp.tools import get_marine_forecast
from tests.test_fetch import FORECAST_JSON, MARINE_JSON

SK_URL = "http://sk.test"
SK_POINT_PATH = f"{SK_URL}/signalk/v2/api/weather/forecasts/point"

# One SignalK WeatherData 'point' entry in SI units (m/s, radians, Pa, m, s).
SK_POINT = {
    "date": "2026-06-16T18:00:00.000Z",
    "type": "point",
    "outside": {"temperature": 290.15, "pressure": 101500.0},
    "wind": {"speedTrue": 5.0, "directionTrue": 1.5707963, "gust": 7.5},  # ~90deg
    "water": {
        "waveSignificantHeight": 1.2,
        "waveDirection": 3.1415927,  # 180deg
        "wavePeriod": 6.0,
        "swellHeight": 0.8,
        "swellDirection": 0.0,
        "swellPeriod": 9.0,
    },
}

_KN = 1.943844


def test_weather_data_to_hour_converts_si_units():
    h = signalk.weather_data_to_hour(SK_POINT)
    assert h.utc.isoformat() == "2026-06-16T18:00:00+00:00"
    assert h.wind.speed_kn == round(5.0 * _KN, 1)
    assert h.wind.dir_deg == 90
    assert h.wind.gust_kn == round(7.5 * _KN, 1)
    assert h.pressure_hpa == 1015.0  # 101500 Pa -> hPa
    assert (h.combined_wave.height_m, h.combined_wave.dir_deg, h.combined_wave.period_s) == (1.2, 180, 6.0)
    assert (h.swell.height_m, h.swell.dir_deg, h.swell.period_s) == (0.8, 0, 9.0)
    # SignalK's water spec has no separate wind-wave component.
    assert h.wind_wave.height_m is None


def test_weather_data_to_hour_tolerates_missing_water():
    # A provider without waves (e.g. before the upstream marine PR) -> wind only.
    wd = {"date": SK_POINT["date"], "wind": {"speedTrue": 3.0, "directionTrue": 0.0, "gust": None}}
    h = signalk.weather_data_to_hour(wd)
    assert h.wind.speed_kn == round(3.0 * _KN, 1)
    assert h.combined_wave.height_m is None
    assert h.swell.height_m is None
    assert h.pressure_hpa is None


@respx.mock
async def test_fetch_forecast_maps_list():
    respx.get(SK_POINT_PATH).mock(return_value=httpx.Response(200, json=[SK_POINT, SK_POINT]))
    client = RateLimitedClient()
    hours = await signalk.fetch_forecast(client, SK_URL, 48.6, -123.2, 6)
    await client.aclose()
    assert len(hours) == 2
    assert hours[0].combined_wave.height_m == 1.2


@respx.mock
async def test_fetch_forecast_raises_on_empty_or_error():
    client = RateLimitedClient()
    respx.get(SK_POINT_PATH).mock(return_value=httpx.Response(200, json=[]))
    with pytest.raises(Exception):
        await signalk.fetch_forecast(client, SK_URL, 48.6, -123.2, 6)
    respx.get(SK_POINT_PATH).mock(return_value=httpx.Response(404))
    with pytest.raises(Exception):
        await signalk.fetch_forecast(client, SK_URL, 48.6, -123.2, 6)
    await client.aclose()


@respx.mock
async def test_get_marine_forecast_prefers_signalk(monkeypatch, tmp_path):
    monkeypatch.setenv("SIGNALK_URL", SK_URL)
    sk = respx.get(SK_POINT_PATH).mock(return_value=httpx.Response(200, json=[SK_POINT, SK_POINT]))
    om = respx.get("https://api.open-meteo.com/v1/forecast").mock(return_value=httpx.Response(200, json=FORECAST_JSON))
    cache = EventCache(str(tmp_path / "c.sqlite"))
    cache.init_schema()
    client = RateLimitedClient()
    out = await get_marine_forecast(client, cache, 48.6, -123.2, hours_ahead=2)
    await client.aclose()
    cache.close()
    assert out["source"] == "signalk"
    assert out["hourly"]
    assert sk.called and not om.called  # Open-Meteo not hit when SignalK serves


@respx.mock
async def test_get_marine_forecast_falls_back_when_signalk_down(monkeypatch, tmp_path):
    monkeypatch.setenv("SIGNALK_URL", SK_URL)
    respx.get(SK_POINT_PATH).mock(return_value=httpx.Response(503))
    respx.get("https://api.open-meteo.com/v1/forecast").mock(return_value=httpx.Response(200, json=FORECAST_JSON))
    respx.get("https://marine-api.open-meteo.com/v1/marine").mock(return_value=httpx.Response(200, json=MARINE_JSON))
    cache = EventCache(str(tmp_path / "c.sqlite"))
    cache.init_schema()
    client = RateLimitedClient()
    out = await get_marine_forecast(client, cache, 48.6, -123.2, hours_ahead=2)
    await client.aclose()
    cache.close()
    assert out["source"] == "open-meteo"


@respx.mock
async def test_get_marine_forecast_skips_signalk_when_unset(monkeypatch, tmp_path):
    monkeypatch.delenv("SIGNALK_URL", raising=False)
    sk = respx.get(SK_POINT_PATH).mock(return_value=httpx.Response(200, json=[SK_POINT]))
    respx.get("https://api.open-meteo.com/v1/forecast").mock(return_value=httpx.Response(200, json=FORECAST_JSON))
    respx.get("https://marine-api.open-meteo.com/v1/marine").mock(return_value=httpx.Response(200, json=MARINE_JSON))
    cache = EventCache(str(tmp_path / "c.sqlite"))
    cache.init_schema()
    client = RateLimitedClient()
    out = await get_marine_forecast(client, cache, 48.6, -123.2, hours_ahead=2)
    await client.aclose()
    cache.close()
    assert out["source"] == "open-meteo"
    assert not sk.called  # SignalK never queried when SIGNALK_URL is unset
