# weather-mcp

MCP server for marine wind, swell, and buoy observations. Sibling to `tide-mcp` and `signalk-mcp` in the s/v Naturali navigator stack.

## Tools

- `get_marine_forecast(lat, lon, hours_ahead?)` — Open-Meteo wind/swell/seas forecast for a position. Routine; no quota.
- `get_marine_forecast_premium(lat, lon, hours_ahead?)` — Stormglass blended model. Free tier caps at 10 requests per UTC day; cache hits do not count.
- `get_nearest_buoy_observations(lat, lon, max_distance_nm?, limit?)` — NDBC observed wind + waves. Reality check for forecasts. Where a station publishes `.spec` spectral data, swell and wind waves are reported separately; otherwise combined waves only.
- `get_stormglass_quota_status()` — used/remaining premium tokens for the current UTC day.

All tool outputs use `{value, display}` leaves: `display` is a pre-formatted,
TTS-safe string agents can speak verbatim — built for voice agents and small
local models that mangle unit formatting.

## Install

Runs anywhere `uv` is available (two runtime dependencies: `httpx`, `mcp`):

```bash
uvx --from git+https://github.com/sailingnaturali/weather-mcp weather-mcp
```

Claude Desktop / MCP client config:

```json
{
  "mcpServers": {
    "weather": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/sailingnaturali/weather-mcp", "weather-mcp"],
      "env": { "STORMGLASS_API_KEY": "optional — premium forecasts only" }
    }
  }
}
```

## Configuration

- `STORMGLASS_API_KEY` — required for `get_marine_forecast_premium`.
- `WEATHER_CACHE_PATH` — optional; defaults to `~/.weather-mcp/cache.sqlite`.

## Tests

```
uv run pytest
```
