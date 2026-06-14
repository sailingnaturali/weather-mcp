"""Re-export the shared Open-Meteo provider (moved to marine-forecast).

Kept as a shim so weather_mcp's existing import paths (and the private
_parse_dt helper used by fetch.py) keep working with no call-site churn."""
from marine_forecast.openmeteo import (  # noqa: F401
    MarineForecastHour,
    WaveObs,
    WindObs,
    fetch_forecast,
    _parse_dt,
)
