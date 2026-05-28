# weather-mcp

MCP server for marine wind, swell, and buoy observations. Sibling to `tide-mcp` and `signalk-mcp` in the s/v Naturali navigator stack.

## Tools

- `get_marine_forecast(lat, lon, hours_ahead?)` — Open-Meteo wind/swell/seas forecast for a position. Routine; no quota.
- `get_marine_forecast_premium(lat, lon, hours_ahead?)` — Stormglass blended model. Free tier caps at 10 requests per UTC day; cache hits do not count.
- `get_nearest_buoy_observations(lat, lon, max_distance_nm?, limit?)` — NDBC observed wind + combined waves. Reality check for forecasts. Note: standard NDBC files do not separate swell from wind waves.
- `get_stormglass_quota_status()` — used/remaining premium tokens for the current UTC day.

## Configuration

- `STORMGLASS_API_KEY` — required for `get_marine_forecast_premium`.
- `WEATHER_CACHE_PATH` — optional; defaults to `~/.weather-mcp/cache.sqlite`.

## Tests

```
uv run pytest
```
