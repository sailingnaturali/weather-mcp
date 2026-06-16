# weather-mcp

MCP server for marine wind, swell, and buoy observations. Sibling to `currents-mcp` and `signalk-mcp` in the s/v Naturali navigator stack.

Why a marine-specific weather server (separated swell vs wind waves, buoys as
ground truth, the NDBC `.spec` format):
[the full story on the engineering blog](https://engineering.sailingnaturali.com/marine-weather-mcp-buoy-ground-truth-ndbc-spec-swell-wind-waves/).

## Tools

- `get_marine_forecast(lat, lon, hours_ahead?)` — wind/swell/seas forecast for a position. Routine; no quota. Reads the boat's **SignalK Weather API** (`/signalk/v2/api/weather`) when `SIGNALK_URL` is set, falling back to direct Open-Meteo otherwise; the response `source` reports which was used. (Both are Open-Meteo under the hood.)
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

- `SIGNALK_URL` — optional; the boat SignalK server (e.g. `http://naturalaspi.local:3000`). When set, `get_marine_forecast` reads the standard Weather API there first (needs a WeatherProvider plugin such as `@signalk/open-meteo-provider` installed), falling back to direct Open-Meteo. **Unset by default** — leave it empty when running off-boat to skip the SignalK round-trip.
- `STORMGLASS_API_KEY` — required for `get_marine_forecast_premium`.
- `WEATHER_CACHE_PATH` — optional; defaults to `~/.weather-mcp/cache.sqlite`.

## Tests

```
uv run pytest
```
