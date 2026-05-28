import sqlite3

import pytest

from weather_mcp.cache import EventCache
from weather_mcp.client import RateLimitedClient
from weather_mcp.quota import StormglassQuota
from weather_mcp.server import TOOL_NAMES, build_server, dispatch


def _setup(tmp_path):
    cache = EventCache(str(tmp_path / "c.sqlite"))
    cache.init_schema()
    quota = StormglassQuota(cache.conn)
    quota.init_schema()
    client = RateLimitedClient()
    return cache, quota, client


def test_tool_names():
    assert TOOL_NAMES == [
        "get_marine_forecast",
        "get_marine_forecast_premium",
        "get_nearest_buoy_observations",
        "get_stormglass_quota_status",
    ]


async def test_build_server_names_it(tmp_path):
    cache, quota, client = _setup(tmp_path)
    server = build_server(client, cache, quota)
    assert server.name == "weather-mcp"
    await client.aclose()
    cache.close()


async def test_dispatch_quota_status_no_http(tmp_path):
    cache, quota, client = _setup(tmp_path)
    result = await dispatch(client, cache, quota, "get_stormglass_quota_status", {})
    await client.aclose()
    cache.close()
    assert result["used_today"] == 0
    assert result["remaining_today"] == 10
    assert "summary_display" in result


async def test_dispatch_premium_exhausted_returns_error_dict(tmp_path):
    cache, quota, client = _setup(tmp_path)
    # Manually exhaust without calling the API
    for _ in range(10):
        quota.consume()

    result = await dispatch(
        client, cache, quota,
        "get_marine_forecast_premium",
        {"lat": 48.42, "lon": -123.37},
    )
    await client.aclose()
    cache.close()

    assert "error" in result
    assert result["quota_remaining_today"] == 0


async def test_dispatch_unknown_tool(tmp_path):
    cache, quota, client = _setup(tmp_path)
    with pytest.raises(ValueError):
        await dispatch(client, cache, quota, "nope", {})
    await client.aclose()
    cache.close()
