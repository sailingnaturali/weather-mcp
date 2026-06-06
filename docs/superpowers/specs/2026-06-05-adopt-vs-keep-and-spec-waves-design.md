# weather-mcp: adopt-vs-keep decision + swell-separated buoy observations

**Date:** 2026-06-05
**Status:** Approved (Bryan, 2026-06-05)

## Background

Workspace prime directive: improve an existing usable tool before building our own;
build only as a last resort, and record why existing options failed. This spec is the
audit of `weather-mcp` against the weather MCP ecosystem, the resulting decision, and
the one capability change that falls out of it.

## Audit findings

### What ours is

Python MCP server, ~1,500 lines, **2 runtime dependencies** (httpx, mcp), SQLite
caching + Stormglass quota ledger, published MIT. Four tools:

- `get_marine_forecast` — Open-Meteo wind / swell / wind-wave / combined seas / pressure
- `get_marine_forecast_premium` — Stormglass blend, 10 tokens/UTC-day, cache hits free
- `get_nearest_buoy_observations` — NDBC realtime obs by lat/lon with distance, bearing, age
- `get_stormglass_quota_status` — token ledger read, no network

All outputs use the `{value, display}` contract: pre-formatted TTS-safe display strings
the agent reports verbatim. Usage data (cache + quota DB, checked 2026-06-05):
Open-Meteo and NDBC in active use by the daily briefing; **Stormglass never called**
(expected — boat ashore, all vessel data mocked).

### Ecosystem candidates

| Capability | ours | cmer81/open-meteo-mcp | weather-mcp/weather-mcp | RyanCardin15/NOAA-TidesAndCurrents |
|---|---|---|---|---|
| Open-Meteo marine (swell/wind-wave split) | ✅ | ✅ raw JSON | ✅ own format | ❌ |
| NDBC nearest-buoy obs by lat/lon | ✅ | ❌ | ❌ ("NOT suitable for coastal navigation") | ❌ (CO-OPS stations, not buoys) |
| Premium blend + quota-aware tool design | ✅ | ❌ | ❌ | ❌ |
| `{value, display}` TTS-safe contract | ✅ | ❌ | ❌ | ❌ |
| Tools exposed to the agent | 4 | 13 | 16 | 25+ |

No dedicated NDBC MCP server exists; the closest thing is
[CDJellen/ndbc-api](https://github.com/CDJellen/ndbc-api), a Python *library*
(deps: pandas, numpy, scipy, xarray, h5netcdf, beautifulsoup4 — see below).

## Decision: keep, extend, promote

**Keep `weather-mcp`.** Justification per alternative, as the prime directive requires:

1. **cmer81/open-meteo-mcp** — covers only the Open-Meteo leg (~330 of our lines),
   returns raw API JSON (breaks the display contract, a hard requirement for the
   local-model briefing Navigator and future offline voice), and exposes 13 tools where
   we expose 4 (tool-selection load on small models). No buoys, no premium.
2. **weather-mcp/weather-mcp** — same gaps, 16 tools, and self-describes its marine
   data as not suitable for coastal navigation. The buoy reality-check is the core of
   the Navigator's weather doctrine (`prompts/navigator.md`): forecast vs. observed,
   lead with the observation. Nothing in the ecosystem does it.
3. **RyanCardin15/NOAA-TidesAndCurrents** — CO-OPS water levels/currents, overlaps
   tide-mcp's domain more than ours; no wave observations, no forecasts.
4. **Splitting** (their forecast + our buoys) — rejected: doubles config surface,
   breaks the single-source `summary_display`, and the most-called tool loses the
   display contract.
5. **Adopting ndbc-api as a dependency** — rejected: it would take the server from 2
   dependencies to ~9, including a scientific-computing stack (scipy, xarray, pandas,
   h5netcdf), to replace ~220 lines of whitespace-table parsing. For a server people
   install via `uvx`, install weight is an adoption barrier; we are optimizing the
   public artifact, not just our own runtime.

**Stormglass stays** (Bryan, 2026-06-05): it is insurance for the passage-making
phase; unused-while-ashore is expected, not a removal signal.

## Change 1: swell-separated buoy observations (`.spec` files)

Our biggest documented limitation: standard NDBC `.txt` files report combined waves
only. NDBC publishes `realtime2/{station}.spec` alongside `realtime2/{station}.txt`,
same whitespace-table format, with the separation we want (verified live 2026-06-05,
station 46088):

```
#YY  MM DD hh mm WVHT  SwH  SwP  WWH  WWP SwD WWD  STEEPNESS  APD MWD
2026 06 06 04 10  0.4  0.1  6.2  0.4  2.9 WSW   W        N/A  2.9 273
```

- `SwH`/`SwP`/`SwD` — swell height (m), period (s), direction (compass string)
- `WWH`/`WWP`/`WWD` — wind-wave height, period, direction (compass string)
- `STEEPNESS` — qualitative (may be `N/A`); `MM` marks missing values, as in `.txt`

### Design

- `providers/ndbc.py` gains `parse_spec()` mirroring `parse_realtime2()` (same
  first-data-row, `MM`-as-None conventions; directions arrive as compass strings, kept
  as-is for display, no degree round-trip).
- `BuoyObservation` gains optional swell/wind-wave fields (heights, periods,
  compass-string directions, steepness).
- Fetch `.spec` concurrently with `.txt` per station; a missing/stale `.spec` (404 or
  obs time differing from the `.txt` row by more than 1 h) degrades gracefully —
  fields stay `None`, tool output unchanged from today.
- The merged observation is cached under the existing `ndbc:obs:{station}` key
  (15 min); `.spec` is never fetched independently, so it needs no cache key of
  its own.
- `tools.py` `_buoy_dict()` adds `swell` and `wind_wave` blocks in the existing
  `{value, display}` shape; the `"combined waves only"` note is emitted only when
  `.spec` data is absent. `summary_display` prefers "swell X m at Y s from Z" over the
  combined figure when available.

### Testing

- Parser unit tests from fixture text: normal row, `MM` missing values, `N/A`
  steepness, empty file, header-only file.
- Merge tests: `.txt` + `.spec` aligned, `.spec` stale (>1 h skew → dropped), `.spec`
  404 (output identical to current behavior).
- Display tests: swell-present and swell-absent summaries; note only when absent.

## Change 2: ecosystem play (no code)

- **Blog post** — "why generic weather MCPs don't work for marine navigation": buoy
  ground-truthing, quota-aware premium tool design, TTS-safe display contracts; uses
  this audit's comparison table. **Drafted and published via the Scribe agent as a PR
  to the `engineering/` repo** once Change 1 lands (the post should reference the
  swell-separated capability, not the old limitation).
- **Registry listings** — submit weather-mcp to PulseMCP, Glama, and mcp.so.

## Out of scope

- Removing or replacing Stormglass (explicitly kept).
- Adopting `ndbc-api` (rejected above).
- Any change to tide-mcp despite the CO-OPS overlap with RyanCardin's server — that is
  a separate audit.
- Historical/spectral-density (`swden`) buoy data.

## Success criteria

- `get_nearest_buoy_observations` returns swell/wind-wave separation for stations that
  publish `.spec`, with zero regression for stations that don't.
- `uv run pytest` green; dependency count unchanged (httpx, mcp).
- Blog post PR opened by Scribe on `engineering/`; registry submissions done.
