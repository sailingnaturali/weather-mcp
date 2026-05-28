import os
import sqlite3

import httpx
import pytest
import respx

from weather_mcp.client import RateLimitedClient
from weather_mcp.providers.stormglass import (
    StormglassAuthError,
    StormglassError,
    fetch_forecast,
    parse_hours,
)
from weather_mcp.quota import QuotaExhausted, StormglassQuota


SG_PAYLOAD = {
    "hours": [
        {
            "time": "2099-01-01T00:00:00+00:00",
            "windSpeed": {"sg": 6.0, "noaa": 5.5},
            "windDirection": {"sg": 220},
            "gust": {"sg": 8.0},
            "waveHeight": {"sg": 1.3},
            "waveDirection": {"sg": 270},
            "wavePeriod": {"sg": 8.0},
            "swellHeight": {"sg": 1.2},
            "swellDirection": {"sg": 275},
            "swellPeriod": {"sg": 9.0},
            "windWaveHeight": {"sg": 0.4},
            "windWaveDirection": {"sg": 220},
            "windWavePeriod": {"sg": 4.0},
            "pressure": {"sg": 1014.0},
        }
    ]
}


def _quota(limit: int = 10) -> StormglassQuota:
    conn = sqlite3.connect(":memory:")
    q = StormglassQuota(conn, daily_limit=limit)
    q.init_schema()
    return q


def test_parse_hours_picks_sg_source():
    hours = parse_hours(SG_PAYLOAD)
    assert len(hours) == 1
    h = hours[0]
    # 6.0 m/s * 1.94384 → 11.66 kn
    assert round(h.wind.speed_kn, 1) == 11.7
    assert h.wind.dir_deg == 220
    assert h.swell.height_m == 1.2
    assert h.swell.dir_deg == 275
    assert h.swell.period_s == 9.0
    assert h.wind_wave.height_m == 0.4
    assert h.combined_wave.height_m == 1.3
    assert h.pressure_hpa == 1014.0


@respx.mock
async def test_consumes_token_on_200(monkeypatch):
    monkeypatch.setenv("STORMGLASS_API_KEY", "test-key")
    respx.get("https://api.stormglass.io/v2/weather/point").mock(
        return_value=httpx.Response(200, json=SG_PAYLOAD)
    )
    client = RateLimitedClient()
    q = _quota()
    hours = await fetch_forecast(client, q, 48.42, -123.37, hours_ahead=24)
    await client.aclose()

    assert len(hours) == 1
    assert q.used_today() == 1


@respx.mock
async def test_refuses_when_quota_zero(monkeypatch):
    monkeypatch.setenv("STORMGLASS_API_KEY", "test-key")
    route = respx.get("https://api.stormglass.io/v2/weather/point").mock(
        return_value=httpx.Response(200, json=SG_PAYLOAD)
    )
    client = RateLimitedClient()
    q = _quota(limit=1)
    q.consume()  # exhaust

    with pytest.raises(QuotaExhausted):
        await fetch_forecast(client, q, 48.42, -123.37)
    await client.aclose()

    assert not route.called, "no HTTP call when quota exhausted"


@respx.mock
async def test_missing_api_key_raises_no_http(monkeypatch):
    monkeypatch.delenv("STORMGLASS_API_KEY", raising=False)
    route = respx.get("https://api.stormglass.io/v2/weather/point").mock(
        return_value=httpx.Response(200, json=SG_PAYLOAD)
    )
    client = RateLimitedClient()
    q = _quota()
    with pytest.raises(StormglassAuthError):
        await fetch_forecast(client, q, 48.42, -123.37)
    await client.aclose()

    assert not route.called
    assert q.used_today() == 0


@respx.mock
async def test_5xx_does_not_consume(monkeypatch):
    monkeypatch.setenv("STORMGLASS_API_KEY", "test-key")
    respx.get("https://api.stormglass.io/v2/weather/point").mock(
        return_value=httpx.Response(503, text="upstream busy")
    )
    client = RateLimitedClient()
    q = _quota()
    with pytest.raises(StormglassError):
        await fetch_forecast(client, q, 48.42, -123.37)
    await client.aclose()

    assert q.used_today() == 0, "5xx must not consume a token"


@respx.mock
async def test_401_raises_auth_error(monkeypatch):
    monkeypatch.setenv("STORMGLASS_API_KEY", "bad-key")
    respx.get("https://api.stormglass.io/v2/weather/point").mock(
        return_value=httpx.Response(401, text="unauthorized")
    )
    client = RateLimitedClient()
    q = _quota()
    with pytest.raises(StormglassAuthError):
        await fetch_forecast(client, q, 48.42, -123.37)
    await client.aclose()
    assert q.used_today() == 0


@respx.mock
async def test_authorization_header_is_raw_key(monkeypatch):
    """Stormglass docs: Authorization header carries the raw key (no 'Bearer ')."""
    monkeypatch.setenv("STORMGLASS_API_KEY", "raw-key-xyz")
    seen = {}

    def capture(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=SG_PAYLOAD)

    respx.get("https://api.stormglass.io/v2/weather/point").mock(side_effect=capture)
    client = RateLimitedClient()
    q = _quota()
    await fetch_forecast(client, q, 48.42, -123.37)
    await client.aclose()

    assert seen["auth"] == "raw-key-xyz"
