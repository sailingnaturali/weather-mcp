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
