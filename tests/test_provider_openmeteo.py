import httpx
import respx

from weather_mcp.client import RateLimitedClient
from weather_mcp.providers.openmeteo import (
    MarineForecastHour,
    WaveObs,
    WindObs,
    fetch_forecast,
)


# 3 hours each, current hour forward
FORECAST_JSON = {
    "hourly": {
        "time": ["2099-01-01T00:00", "2099-01-01T01:00", "2099-01-01T02:00"],
        "wind_speed_10m": [12.0, 14.0, 16.0],
        "wind_direction_10m": [220, 225, 230],
        "wind_gusts_10m": [16.0, 18.0, 22.0],
        "pressure_msl": [1014.2, 1014.0, 1013.6],
    }
}

MARINE_JSON = {
    "hourly": {
        "time": ["2099-01-01T00:00", "2099-01-01T01:00", "2099-01-01T02:00"],
        "wave_height": [1.3, 1.4, 1.5],
        "wave_direction": [270, 275, 280],
        "wave_period": [8.0, 8.0, 8.0],
        "swell_wave_height": [1.2, 1.2, 1.2],
        "swell_wave_direction": [275, 275, 275],
        "swell_wave_period": [9.0, 9.0, 9.0],
        "wind_wave_height": [0.4, 0.5, 0.6],
        "wind_wave_direction": [220, 225, 230],
        "wind_wave_period": [4.0, 4.0, 4.0],
    }
}


@respx.mock
async def test_parses_full_marine_response():
    respx.get("https://api.open-meteo.com/v1/forecast").mock(
        return_value=httpx.Response(200, json=FORECAST_JSON)
    )
    respx.get("https://marine-api.open-meteo.com/v1/marine").mock(
        return_value=httpx.Response(200, json=MARINE_JSON)
    )
    client = RateLimitedClient()
    hours = await fetch_forecast(client, 48.42, -123.37, hours_ahead=12)
    await client.aclose()

    assert len(hours) == 3
    assert hours[0].wind == WindObs(speed_kn=12.0, dir_deg=220, gust_kn=16.0)
    assert hours[0].swell == WaveObs(height_m=1.2, dir_deg=275, period_s=9.0)
    assert hours[0].wind_wave == WaveObs(height_m=0.4, dir_deg=220, period_s=4.0)
    assert hours[0].combined_wave == WaveObs(height_m=1.3, dir_deg=270, period_s=8.0)
    assert hours[0].pressure_hpa == 1014.2


@respx.mock
async def test_marine_endpoint_failure_returns_wind_only():
    respx.get("https://api.open-meteo.com/v1/forecast").mock(
        return_value=httpx.Response(200, json=FORECAST_JSON)
    )
    respx.get("https://marine-api.open-meteo.com/v1/marine").mock(
        return_value=httpx.Response(500, text="server error")
    )
    client = RateLimitedClient()
    hours = await fetch_forecast(client, 48.42, -123.37, hours_ahead=12)
    await client.aclose()

    assert len(hours) == 3
    assert hours[0].wind.speed_kn == 12.0
    assert hours[0].swell.height_m is None
    assert hours[0].wind_wave.height_m is None
    assert hours[0].combined_wave.height_m is None
    assert hours[0].pressure_hpa == 1014.2


@respx.mock
async def test_handles_null_grid_cell():
    """Shore-near cells return None values from the marine endpoint."""
    null_marine = {
        "hourly": {
            "time": ["2099-01-01T00:00"],
            "wave_height": [None],
            "wave_direction": [None],
            "wave_period": [None],
            "swell_wave_height": [None],
            "swell_wave_direction": [None],
            "swell_wave_period": [None],
            "wind_wave_height": [None],
            "wind_wave_direction": [None],
            "wind_wave_period": [None],
        }
    }
    respx.get("https://api.open-meteo.com/v1/forecast").mock(
        return_value=httpx.Response(200, json={
            "hourly": {
                "time": ["2099-01-01T00:00"],
                "wind_speed_10m": [10.0],
                "wind_direction_10m": [180],
                "wind_gusts_10m": [12.0],
                "pressure_msl": [1015.0],
            }
        })
    )
    respx.get("https://marine-api.open-meteo.com/v1/marine").mock(
        return_value=httpx.Response(200, json=null_marine)
    )
    client = RateLimitedClient()
    hours = await fetch_forecast(client, 48.42, -123.37)
    await client.aclose()

    assert hours[0].wind.speed_kn == 10.0
    assert hours[0].swell.height_m is None


def test_marine_forecast_hour_roundtrips_via_dict():
    from datetime import datetime, timezone
    h = MarineForecastHour(
        utc=datetime(2099, 1, 1, 0, 0, tzinfo=timezone.utc),
        wind=WindObs(speed_kn=12.0, dir_deg=220, gust_kn=16.0),
        swell=WaveObs(height_m=1.2, dir_deg=275, period_s=9.0),
        wind_wave=WaveObs(height_m=0.4, dir_deg=220, period_s=4.0),
        combined_wave=WaveObs(height_m=1.3, dir_deg=270, period_s=8.0),
        pressure_hpa=1014.2,
    )
    assert MarineForecastHour.from_dict(h.to_dict()) == h


@respx.mock
async def test_hours_ahead_caps_returned_entries():
    long_hourly = {
        "time": [f"2099-01-01T{i:02d}:00" for i in range(24)],
        "wind_speed_10m": [10.0] * 24,
        "wind_direction_10m": [180] * 24,
        "wind_gusts_10m": [12.0] * 24,
        "pressure_msl": [1015.0] * 24,
    }
    long_marine = {
        "time": [f"2099-01-01T{i:02d}:00" for i in range(24)],
        "wave_height": [1.0] * 24,
        "wave_direction": [270] * 24,
        "wave_period": [8.0] * 24,
        "swell_wave_height": [1.0] * 24,
        "swell_wave_direction": [275] * 24,
        "swell_wave_period": [9.0] * 24,
        "wind_wave_height": [0.4] * 24,
        "wind_wave_direction": [180] * 24,
        "wind_wave_period": [4.0] * 24,
    }
    respx.get("https://api.open-meteo.com/v1/forecast").mock(
        return_value=httpx.Response(200, json={"hourly": long_hourly})
    )
    respx.get("https://marine-api.open-meteo.com/v1/marine").mock(
        return_value=httpx.Response(200, json={"hourly": long_marine})
    )
    client = RateLimitedClient()
    hours = await fetch_forecast(client, 48.42, -123.37, hours_ahead=6)
    await client.aclose()

    assert len(hours) == 6
