import httpx
import respx

from weather_mcp.cache import EventCache
from weather_mcp.client import RateLimitedClient
from weather_mcp.fetch import get_openmeteo_forecast


FORECAST_JSON = {
    "hourly": {
        "time": ["2099-01-01T00:00"],
        "wind_speed_10m": [10.0],
        "wind_direction_10m": [200],
        "wind_gusts_10m": [12.0],
        "pressure_msl": [1015.0],
    }
}

MARINE_JSON = {
    "hourly": {
        "time": ["2099-01-01T00:00"],
        "wave_height": [1.0],
        "wave_direction": [270],
        "wave_period": [8.0],
        "swell_wave_height": [1.0],
        "swell_wave_direction": [275],
        "swell_wave_period": [9.0],
        "wind_wave_height": [0.3],
        "wind_wave_direction": [200],
        "wind_wave_period": [4.0],
    }
}


@respx.mock
async def test_openmeteo_cache_miss_then_hit(tmp_path):
    f_route = respx.get("https://api.open-meteo.com/v1/forecast").mock(
        return_value=httpx.Response(200, json=FORECAST_JSON)
    )
    m_route = respx.get("https://marine-api.open-meteo.com/v1/marine").mock(
        return_value=httpx.Response(200, json=MARINE_JSON)
    )
    cache = EventCache(str(tmp_path / "c.sqlite"))
    cache.init_schema()
    client = RateLimitedClient()

    hours1 = await get_openmeteo_forecast(client, cache, 48.42, -123.37, hours_ahead=12)
    hours2 = await get_openmeteo_forecast(client, cache, 48.42, -123.37, hours_ahead=12)

    await client.aclose()
    cache.close()

    assert hours1 == hours2
    assert f_route.call_count == 1, "second call must use cache"
    assert m_route.call_count == 1


@respx.mock
async def test_ndbc_observation_cache_roundtrips_spec_fields(tmp_path):
    from weather_mcp.fetch import get_ndbc_observation
    from weather_mcp.providers.ndbc import Station

    txt = (
        "#YY  MM DD hh mm WDIR WSPD GST  WVHT   DPD   APD MWD   PRES  ATMP  WTMP  DEWP  VIS PTDY  TIDE\n"
        "2099 01 01 00 50  230  7.2  9.8   1.5   8.0   6.0 265  1013.5  12.0  10.5  11.0   MM -0.6    MM\n"
    )
    spec = (
        "#YY  MM DD hh mm WVHT  SwH  SwP  WWH  WWP SwD WWD  STEEPNESS  APD MWD\n"
        "2099 01 01 00 40  1.5  1.1 10.0  0.6  3.4   W  SW    AVERAGE  5.0 270\n"
    )
    cache = EventCache(str(tmp_path / "c.sqlite"))
    cache.init_schema()
    station = Station(id="46087", name="Neah Bay", lat=48.494, lon=-124.728)

    txt_route = respx.get("https://www.ndbc.noaa.gov/data/realtime2/46087.txt").mock(
        return_value=httpx.Response(200, text=txt)
    )
    spec_route = respx.get("https://www.ndbc.noaa.gov/data/realtime2/46087.spec").mock(
        return_value=httpx.Response(200, text=spec)
    )

    client = RateLimitedClient()
    first = await get_ndbc_observation(client, cache, station, 48.5, -124.7)
    # Second call hits the cache; spec fields must survive the round-trip
    second = await get_ndbc_observation(client, cache, station, 48.5, -124.7)
    await client.aclose()

    assert txt_route.call_count == 1, "second call must use cache"
    assert spec_route.call_count == 1, "second call must use cache"
    assert first.swell_height_m == 1.1
    assert second.swell_height_m == 1.1, "spec fields must survive the cache round-trip"
    assert second.wind_wave_dir_compass == "SW"
    assert second.steepness == "AVERAGE"
    cache.close()
