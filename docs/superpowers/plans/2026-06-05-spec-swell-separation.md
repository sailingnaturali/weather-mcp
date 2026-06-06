# NDBC `.spec` Swell Separation + Ecosystem Play Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `get_nearest_buoy_observations` returns swell-separated wave data (swell vs wind waves) for NDBC stations that publish `.spec` files, with zero regression for stations that don't; then publish the adopt-vs-keep story via Scribe and list the server on MCP registries.

**Architecture:** NDBC publishes `realtime2/{id}.spec` next to the `realtime2/{id}.txt` we already parse — same whitespace-table format, swell/wind-wave separated. We add a `parse_spec()` parser, fetch both files concurrently in `fetch_station_observation`, and merge `.spec` fields into `BuoyObservation` when timestamps are within 1 h. Missing/stale/404 `.spec` degrades to today's behavior. The merged observation rides the existing `ndbc:obs:{id}` cache (15 min). The tool layer adds `swell`/`wind_wave` `{value, display}` blocks and prefers swell phrasing in summaries.

**Tech Stack:** Python 3.12, httpx (via existing `RateLimitedClient`), pytest + respx, no new dependencies. Spec: `docs/superpowers/specs/2026-06-05-adopt-vs-keep-and-spec-waves-design.md`.

**Repo:** all code tasks in `~/src/sailingnaturali/weather-mcp`. Run all commands from the repo root. Test runner: `uv run pytest`.

**`.spec` file format** (verified live 2026-06-05, station 46088):

```
#YY  MM DD hh mm WVHT  SwH  SwP  WWH  WWP SwD WWD  STEEPNESS  APD MWD
#yr  mo dy hr mn    m    m  sec    m  sec  -  degT     -      sec degT
2026 06 06 04 10  0.4  0.1  6.2  0.4  2.9 WSW   W        N/A  2.9 273
```

Columns after `split()`: 0–4 = timestamp, 5 = WVHT, 6 = SwH (swell height m), 7 = SwP (swell period s), 8 = WWH (wind-wave height m), 9 = WWP (wind-wave period s), 10 = SwD (swell direction, **compass string** e.g. `WSW`), 11 = WWD (wind-wave direction, compass string), 12 = STEEPNESS (`SWELL`/`AVERAGE`/`STEEP`/`N/A`), 13 = APD, 14 = MWD. Missing values are `MM` (and `N/A` for steepness). Header lines start with `#`.

---

### Task 1: `parse_spec()` parser and `SpecWaves` dataclass

**Files:**
- Modify: `src/weather_mcp/providers/ndbc.py`
- Test: `tests/test_provider_ndbc.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_provider_ndbc.py`. Also extend the import block at the top of the file to add `parse_spec`:

```python
from weather_mcp.providers.ndbc import (
    Station,
    fetch_active_stations,
    fetch_station_observation,
    nearest_stations,
    parse_activestations,
    parse_realtime2,
    parse_spec,
)
```

Append fixtures + tests:

```python
SPEC_FULL = """#YY  MM DD hh mm WVHT  SwH  SwP  WWH  WWP SwD WWD  STEEPNESS  APD MWD
#yr  mo dy hr mn    m    m  sec    m  sec  -  degT     -      sec degT
2099 01 01 00 40  1.5  1.1 10.0  0.6  3.4   W  SW    AVERAGE  5.0 270
2099 01 01 00 10  1.4  1.0 10.0  0.6  3.2   W  SW    AVERAGE  5.0 268"""

SPEC_NA_STEEPNESS = """#YY  MM DD hh mm WVHT  SwH  SwP  WWH  WWP SwD WWD  STEEPNESS  APD MWD
#yr  mo dy hr mn    m    m  sec    m  sec  -  degT     -      sec degT
2099 01 01 00 40  0.4  0.1  6.2  0.4  2.9 WSW   W        N/A  2.9 273"""

SPEC_ALL_MM = """#YY  MM DD hh mm WVHT  SwH  SwP  WWH  WWP SwD WWD  STEEPNESS  APD MWD
#yr  mo dy hr mn    m    m  sec    m  sec  -  degT     -      sec degT
2099 01 01 00 40   MM   MM   MM   MM   MM  MM  MM         MM   MM  MM"""

SPEC_HEADER_ONLY = """#YY  MM DD hh mm WVHT  SwH  SwP  WWH  WWP SwD WWD  STEEPNESS  APD MWD
#yr  mo dy hr mn    m    m  sec    m  sec  -  degT     -      sec degT"""


def test_parse_spec_full_row():
    spec = parse_spec(SPEC_FULL)
    assert spec is not None
    assert spec.observed_utc.isoformat() == "2099-01-01T00:40:00+00:00"
    assert spec.swell_height_m == 1.1
    assert spec.swell_period_s == 10.0
    assert spec.swell_dir_compass == "W"
    assert spec.wind_wave_height_m == 0.6
    assert spec.wind_wave_period_s == 3.4
    assert spec.wind_wave_dir_compass == "SW"
    assert spec.steepness == "AVERAGE"


def test_parse_spec_na_steepness_is_none():
    spec = parse_spec(SPEC_NA_STEEPNESS)
    assert spec is not None
    assert spec.steepness is None
    assert spec.swell_dir_compass == "WSW"


def test_parse_spec_all_mm_returns_none():
    assert parse_spec(SPEC_ALL_MM) is None


def test_parse_spec_header_only_returns_none():
    assert parse_spec(SPEC_HEADER_ONLY) is None


def test_parse_spec_empty_returns_none():
    assert parse_spec("") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_provider_ndbc.py -v -k parse_spec`
Expected: FAIL at import time — `ImportError: cannot import name 'parse_spec'`

- [ ] **Step 3: Implement `SpecWaves` and `parse_spec`**

In `src/weather_mcp/providers/ndbc.py`, add below the `REALTIME2_URL` constant:

```python
SPEC_URL = "https://www.ndbc.noaa.gov/data/realtime2/{station_id}.spec"
```

Add below the `Station` dataclass (before `BuoyObservation`):

```python
@dataclass(frozen=True)
class SpecWaves:
    """One row of a realtime2 .spec file: swell vs wind-wave separation."""

    observed_utc: datetime
    swell_height_m: float | None
    swell_period_s: float | None
    swell_dir_compass: str | None
    wind_wave_height_m: float | None
    wind_wave_period_s: float | None
    wind_wave_dir_compass: str | None
    steepness: str | None
```

Add below `parse_realtime2` (it reuses the existing `_maybe_float` helper):

```python
def _maybe_str(token: str) -> str | None:
    return None if token in (MM, "N/A") else token


def parse_spec(text: str) -> SpecWaves | None:
    """Parse the first data row of a realtime2 .spec response.

    Directions arrive as compass strings (e.g. 'WSW') and are kept as-is.
    Returns None if there are no data rows or every wave field is missing.
    """
    rows = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    if not rows:
        return None
    cols = rows[0].split()
    if len(cols) < 13:
        return None

    yyyy, mm, dd, hh, mn = cols[0:5]
    try:
        observed = datetime(int(yyyy), int(mm), int(dd), int(hh), int(mn), tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None

    spec = SpecWaves(
        observed_utc=observed,
        swell_height_m=_maybe_float(cols[6]),
        swell_period_s=_maybe_float(cols[7]),
        swell_dir_compass=_maybe_str(cols[10]),
        wind_wave_height_m=_maybe_float(cols[8]),
        wind_wave_period_s=_maybe_float(cols[9]),
        wind_wave_dir_compass=_maybe_str(cols[11]),
        steepness=_maybe_str(cols[12]),
    )
    if spec.swell_height_m is None and spec.wind_wave_height_m is None:
        return None
    return spec
```

Also update the module docstring's endpoint list (lines 3–8) to:

```python
"""NDBC buoy provider. Free, no API key.

Three endpoints:
- activestations.xml: full station list (cached 24h)
- realtime2/{id}.txt: latest observations per station (cached 15min, merged)
- realtime2/{id}.spec: swell vs wind-wave separation, where published (merged
  into the observation when its timestamp is within 1h of the .txt row)
"""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_provider_ndbc.py -v -k parse_spec`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add src/weather_mcp/providers/ndbc.py tests/test_provider_ndbc.py
git commit -m "feat(ndbc): parse realtime2 .spec swell/wind-wave separation"
```

---

### Task 2: `BuoyObservation` spec fields + `merge_spec()`

**Files:**
- Modify: `src/weather_mcp/providers/ndbc.py`
- Test: `tests/test_provider_ndbc.py`

- [ ] **Step 1: Write the failing tests**

Extend the import block in `tests/test_provider_ndbc.py` to add `merge_spec`. Append:

```python
SPEC_STALE = """#YY  MM DD hh mm WVHT  SwH  SwP  WWH  WWP SwD WWD  STEEPNESS  APD MWD
#yr  mo dy hr mn    m    m  sec    m  sec  -  degT     -      sec degT
2098 12 31 20 00  1.5  1.1 10.0  0.6  3.4   W  SW    AVERAGE  5.0 270"""


def _full_obs():
    station = Station(id="46087", name="Neah Bay", lat=48.494, lon=-124.728)
    return parse_realtime2(REALTIME_FULL, station, ref_lat=48.5, ref_lon=-124.7)


def test_merge_spec_attaches_fields():
    # .txt row is 2099-01-01 00:50, .spec row 00:40 — 10 min skew, merges
    obs = merge_spec(_full_obs(), parse_spec(SPEC_FULL))
    assert obs.swell_height_m == 1.1
    assert obs.swell_period_s == 10.0
    assert obs.swell_dir_compass == "W"
    assert obs.wind_wave_height_m == 0.6
    assert obs.wind_wave_dir_compass == "SW"
    assert obs.steepness == "AVERAGE"
    assert "swell" in obs.available_fields()


def test_merge_spec_none_is_noop():
    obs = merge_spec(_full_obs(), None)
    assert obs.swell_height_m is None
    assert "swell" not in obs.available_fields()


def test_merge_spec_stale_is_dropped():
    # .spec row is >1h older than the .txt row — must not merge
    obs = merge_spec(_full_obs(), parse_spec(SPEC_STALE))
    assert obs.swell_height_m is None


def test_merged_obs_survives_to_dict():
    obs = merge_spec(_full_obs(), parse_spec(SPEC_FULL))
    d = obs.to_dict()
    assert d["swell_height_m"] == 1.1
    assert d["wind_wave_dir_compass"] == "SW"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_provider_ndbc.py -v -k merge`
Expected: FAIL at import time — `ImportError: cannot import name 'merge_spec'`

- [ ] **Step 3: Implement**

In `src/weather_mcp/providers/ndbc.py`:

1. Change the dataclasses import to include `replace`:

```python
from dataclasses import asdict, dataclass, field, replace
```

2. Add to `BuoyObservation`, after `pressure_tendency_hpa: float | None` (defaults keep every existing constructor call site working):

```python
    # From realtime2 .spec, when published and fresh (see merge_spec)
    swell_height_m: float | None = None
    swell_period_s: float | None = None
    swell_dir_compass: str | None = None
    wind_wave_height_m: float | None = None
    wind_wave_period_s: float | None = None
    wind_wave_dir_compass: str | None = None
    steepness: str | None = None
```

3. In `BuoyObservation.available_fields`, after the `wave` check:

```python
        if self.swell_height_m is not None or self.wind_wave_height_m is not None:
            out.append("swell")
```

4. Add below `parse_spec`:

```python
MAX_SPEC_SKEW_S = 3600  # .spec older/newer than the .txt row by more than 1h → stale


def merge_spec(obs: BuoyObservation, spec: SpecWaves | None) -> BuoyObservation:
    """Attach .spec wave separation to an observation when timestamps align."""
    if spec is None:
        return obs
    if abs((obs.observed_utc - spec.observed_utc).total_seconds()) > MAX_SPEC_SKEW_S:
        return obs
    return replace(
        obs,
        swell_height_m=spec.swell_height_m,
        swell_period_s=spec.swell_period_s,
        swell_dir_compass=spec.swell_dir_compass,
        wind_wave_height_m=spec.wind_wave_height_m,
        wind_wave_period_s=spec.wind_wave_period_s,
        wind_wave_dir_compass=spec.wind_wave_dir_compass,
        steepness=spec.steepness,
    )
```

- [ ] **Step 4: Run the full provider test file**

Run: `uv run pytest tests/test_provider_ndbc.py -v`
Expected: all PASS (new merge tests + every pre-existing test — the defaulted fields must not break `parse_realtime2` construction)

- [ ] **Step 5: Commit**

```bash
git add src/weather_mcp/providers/ndbc.py tests/test_provider_ndbc.py
git commit -m "feat(ndbc): merge .spec swell separation into BuoyObservation"
```

---

### Task 3: fetch `.txt` + `.spec` concurrently in `fetch_station_observation`

**Files:**
- Modify: `src/weather_mcp/providers/ndbc.py:175-185` (current `fetch_station_observation`)
- Test: `tests/test_provider_ndbc.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_provider_ndbc.py` (respx style matches the existing fetch tests; `REALTIME_FULL`'s row is 2099-01-01 00:50, `SPEC_FULL`'s is 00:40, so they merge):

```python
@respx.mock
async def test_fetch_station_observation_merges_spec():
    respx.get("https://www.ndbc.noaa.gov/data/realtime2/46087.txt").mock(
        return_value=httpx.Response(200, text=REALTIME_FULL)
    )
    respx.get("https://www.ndbc.noaa.gov/data/realtime2/46087.spec").mock(
        return_value=httpx.Response(200, text=SPEC_FULL)
    )
    client = RateLimitedClient()
    station = Station(id="46087", name="Neah Bay", lat=48.494, lon=-124.728)
    obs = await fetch_station_observation(client, station, 48.5, -124.7)
    await client.aclose()
    assert obs is not None
    assert obs.swell_height_m == 1.1
    assert obs.wave_height_m == 1.5, ".txt fields unchanged"


@respx.mock
async def test_fetch_station_observation_spec_404_degrades():
    respx.get("https://www.ndbc.noaa.gov/data/realtime2/46087.txt").mock(
        return_value=httpx.Response(200, text=REALTIME_FULL)
    )
    respx.get("https://www.ndbc.noaa.gov/data/realtime2/46087.spec").mock(
        return_value=httpx.Response(404)
    )
    client = RateLimitedClient()
    station = Station(id="46087", name="Neah Bay", lat=48.494, lon=-124.728)
    obs = await fetch_station_observation(client, station, 48.5, -124.7)
    await client.aclose()
    assert obs is not None
    assert obs.swell_height_m is None
    assert obs.wave_height_m == 1.5
    assert "swell" not in obs.available_fields()


@respx.mock
async def test_fetch_station_observation_txt_404_still_none():
    respx.get("https://www.ndbc.noaa.gov/data/realtime2/46087.txt").mock(
        return_value=httpx.Response(404)
    )
    respx.get("https://www.ndbc.noaa.gov/data/realtime2/46087.spec").mock(
        return_value=httpx.Response(200, text=SPEC_FULL)
    )
    client = RateLimitedClient()
    station = Station(id="46087", name="Neah Bay", lat=48.494, lon=-124.728)
    obs = await fetch_station_observation(client, station, 48.5, -124.7)
    await client.aclose()
    assert obs is None, ".spec alone is not an observation"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_provider_ndbc.py -v -k fetch_station`
Expected: the two new merge/degrade tests FAIL (no `.spec` request is made, `swell_height_m` is None / unexpected request error from respx for the unrequested mock — either failure mode is fine); `txt_404` may already pass.

Note: if respx raises "unmocked request" errors on pre-existing tests because the new code now requests `.spec` URLs, that is expected — fix by mocking the `.spec` URL with a 404 in those pre-existing tests (`test_fetch_station_observation_returns_none_on_404` needs `respx.get(".../9999.spec").mock(return_value=httpx.Response(404))` added).

- [ ] **Step 3: Implement**

Replace `fetch_station_observation` in `src/weather_mcp/providers/ndbc.py` with:

```python
async def _get_text_or_none(client: RateLimitedClient, url: str) -> str | None:
    try:
        resp = await client.get(url)
        resp.raise_for_status()
    except Exception:
        return None
    return resp.text


async def fetch_station_observation(
    client: RateLimitedClient, station: Station, ref_lat: float, ref_lon: float
) -> BuoyObservation | None:
    """Fetch + parse the latest observation for one station.

    .txt and .spec are fetched concurrently; a missing or stale .spec degrades
    to the combined-waves-only observation.
    """
    txt_url = REALTIME2_URL.format(station_id=station.id)
    spec_url = SPEC_URL.format(station_id=station.id)
    txt_text, spec_text = await asyncio.gather(
        _get_text_or_none(client, txt_url),
        _get_text_or_none(client, spec_url),
    )
    if txt_text is None:
        return None
    obs = parse_realtime2(txt_text, station, ref_lat, ref_lon)
    if obs is None:
        return None
    spec = parse_spec(spec_text) if spec_text is not None else None
    return merge_spec(obs, spec)
```

- [ ] **Step 4: Run the full provider test file**

Run: `uv run pytest tests/test_provider_ndbc.py -v`
Expected: all PASS (including the pre-existing 404 test, with its `.spec` mock added per Step 2's note)

- [ ] **Step 5: Commit**

```bash
git add src/weather_mcp/providers/ndbc.py tests/test_provider_ndbc.py
git commit -m "feat(ndbc): fetch .txt and .spec concurrently, merge when fresh"
```

---

### Task 4: cache round-trip for the new fields

**Files:**
- Modify: `src/weather_mcp/fetch.py:95-114` (`_obs_from_dict`)
- Test: `tests/test_fetch.py`

The merged observation is cached as a dict under `ndbc:obs:{id}` (15 min TTL); `_obs_from_dict` reconstructs it field-by-field and would silently drop the new fields.

- [ ] **Step 1: Write the failing test**

Look at the top of `tests/test_fetch.py` first and reuse its existing fixtures/helpers for `EventCache` setup if present (it tests `get_ndbc_observation` already — match its style). Append a test along these lines, adapting helper names to that file:

```python
async def test_ndbc_observation_cache_roundtrips_spec_fields(tmp_path):
    from weather_mcp.cache import EventCache
    from weather_mcp.fetch import get_ndbc_observation
    from weather_mcp.providers.ndbc import Station

    import httpx
    import respx

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

    with respx.mock:
        respx.get("https://www.ndbc.noaa.gov/data/realtime2/46087.txt").mock(
            return_value=httpx.Response(200, text=txt)
        )
        respx.get("https://www.ndbc.noaa.gov/data/realtime2/46087.spec").mock(
            return_value=httpx.Response(200, text=spec)
        )
        from weather_mcp.client import RateLimitedClient

        client = RateLimitedClient()
        first = await get_ndbc_observation(client, cache, station, 48.5, -124.7)
        # Second call must hit the cache (respx would error on a new request
        # only if we cleared the mocks; instead assert equality of spec fields)
        second = await get_ndbc_observation(client, cache, station, 48.5, -124.7)
        await client.aclose()

    assert first.swell_height_m == 1.1
    assert second.swell_height_m == 1.1, "spec fields must survive the cache round-trip"
    assert second.wind_wave_dir_compass == "SW"
    assert second.steepness == "AVERAGE"
    cache.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_fetch.py -v -k roundtrip`
Expected: FAIL — `second.swell_height_m` is None (`_obs_from_dict` drops the new keys)

- [ ] **Step 3: Implement**

In `src/weather_mcp/fetch.py`, `_obs_from_dict`, add after `pressure_tendency_hpa=d.get("pressure_tendency_hpa"),`:

```python
        swell_height_m=d.get("swell_height_m"),
        swell_period_s=d.get("swell_period_s"),
        swell_dir_compass=d.get("swell_dir_compass"),
        wind_wave_height_m=d.get("wind_wave_height_m"),
        wind_wave_period_s=d.get("wind_wave_period_s"),
        wind_wave_dir_compass=d.get("wind_wave_dir_compass"),
        steepness=d.get("steepness"),
```

(`.get()` returns None for dicts cached before this release — old cache entries stay valid.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_fetch.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/weather_mcp/fetch.py tests/test_fetch.py
git commit -m "feat(ndbc): round-trip spec wave fields through the obs cache"
```

---

### Task 5: tool layer — `swell`/`wind_wave` blocks, conditional note, summary preference

**Files:**
- Modify: `src/weather_mcp/tools.py` (`_buoy_dict`, `_buoy_summary`)
- Create: `tests/test_tools_buoy.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tools_buoy.py`:

```python
"""Display-layer tests for buoy swell separation. Builds observations directly."""

from datetime import datetime, timezone

from weather_mcp.providers.ndbc import BuoyObservation, Station
from weather_mcp.tools import _buoy_dict, _buoy_summary

STATION = Station(id="46087", name="Neah Bay", lat=48.494, lon=-124.728)


def _obs(**overrides) -> BuoyObservation:
    base = dict(
        station=STATION,
        observed_utc=datetime(2099, 1, 1, 0, 50, tzinfo=timezone.utc),
        distance_nm=12.0,
        bearing_deg=270,
        wind_speed_kn=14.0,
        wind_dir_deg=230,
        wind_gust_kn=19.0,
        wave_height_m=1.5,
        wave_dom_period_s=8.0,
        wave_avg_period_s=6.0,
        wave_dir_deg=265,
        pressure_hpa=1013.5,
        pressure_tendency_hpa=-0.6,
    )
    base.update(overrides)
    return BuoyObservation(**base)


SPEC_FIELDS = dict(
    swell_height_m=1.1,
    swell_period_s=10.0,
    swell_dir_compass="W",
    wind_wave_height_m=0.6,
    wind_wave_period_s=3.4,
    wind_wave_dir_compass="SW",
    steepness="AVERAGE",
)


def test_buoy_dict_with_spec_has_swell_blocks_and_no_note():
    d = _buoy_dict(_obs(**SPEC_FIELDS))
    assert d["swell"]["value"]["height_m"] == 1.1
    assert d["swell"]["display"] == "1.1 m from W at 10 s"
    assert d["wind_wave"]["value"]["dir_compass"] == "SW"
    assert d["wind_wave"]["display"] == "0.6 m from SW at 3 s"
    assert "note" not in d["wave"], "combined-only caveat must vanish when swell is separated"
    assert "swell" in d["available_fields"]


def test_buoy_dict_without_spec_keeps_note_and_omits_blocks():
    d = _buoy_dict(_obs())
    assert "swell" not in d
    assert "wind_wave" not in d
    assert "combined waves only" in d["wave"]["note"]


def test_buoy_summary_prefers_swell_over_combined():
    s = _buoy_summary(_obs(**SPEC_FIELDS))
    assert "swell 1.1 m from W at 10 s" in s
    assert "seas 1.5 m" not in s


def test_buoy_summary_falls_back_to_combined():
    s = _buoy_summary(_obs())
    assert "seas 1.5 m" in s
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_tools_buoy.py -v`
Expected: FAIL — `KeyError: 'swell'`, note assertions fail

- [ ] **Step 3: Implement**

In `src/weather_mcp/tools.py`:

1. Add a display helper next to `_buoy_wave_display`:

```python
def _spec_wave_display(height_m: float | None, dir_compass: str | None, period_s: float | None) -> str:
    if height_m is None:
        return "unavailable"
    s = f"{height_m:.1f} m"
    if dir_compass:
        s += f" from {dir_compass}"
    if period_s is not None:
        s += f" at {period_s:.0f} s"
    return s
```

2. In `_buoy_summary`, replace the wave branch (currently `if obs.wave_height_m is not None: ...`) with:

```python
    if obs.swell_height_m is not None:
        bits.append(
            "swell "
            + _spec_wave_display(obs.swell_height_m, obs.swell_dir_compass, obs.swell_period_s)
        )
    elif obs.wave_height_m is not None:
        wave_str = f"{obs.wave_height_m:.1f} m"
        if obs.wave_dir_deg is not None:
            wave_str += f" from {_compass(obs.wave_dir_deg)}"
        if obs.wave_dom_period_s is not None:
            wave_str += f" at {obs.wave_dom_period_s:.0f} s"
        bits.append(f"seas {wave_str}")
```

3. In `_buoy_dict`, the `"wave"` entry currently hardcodes the note. Restructure the function body:

```python
def _buoy_dict(obs: BuoyObservation) -> dict:
    has_spec = obs.swell_height_m is not None or obs.wind_wave_height_m is not None
    wave_block: dict = {
        "value": {
            "height_m": obs.wave_height_m,
            "dom_period_s": obs.wave_dom_period_s,
            "avg_period_s": obs.wave_avg_period_s,
            "dir_deg": obs.wave_dir_deg,
        },
        "display": _buoy_wave_display(obs),
    }
    if not has_spec:
        wave_block["note"] = (
            "combined waves only — this station's .spec swell separation is "
            "unavailable or stale"
        )
    d = {
        "station_id": obs.station.id,
        "name": obs.station.name,
        "distance_nm": round(obs.distance_nm, 1),
        "bearing_from_position_deg": obs.bearing_deg,
        "bearing_display": _compass(obs.bearing_deg),
        "observed_utc": obs.observed_utc.isoformat(),
        "age_display": _age_display(obs.observed_utc),
        "wind": {
            "value": {
                "speed_kn": obs.wind_speed_kn,
                "dir_deg": obs.wind_dir_deg,
                "gust_kn": obs.wind_gust_kn,
            },
            "display": _wind_display(WindObs(obs.wind_speed_kn, obs.wind_dir_deg, obs.wind_gust_kn))
            if obs.wind_speed_kn is not None
            else "unavailable",
        },
        "wave": wave_block,
        "pressure": {
            "value": {
                "hpa": obs.pressure_hpa,
                "tendency_hpa": obs.pressure_tendency_hpa,
            },
            "display": _buoy_pressure_display(obs),
        },
        "available_fields": obs.available_fields(),
        "summary_display": (
            f"{obs.station.name} ({obs.station.id}), "
            f"{obs.distance_nm:.0f} nm {_compass(obs.bearing_deg)} "
            f"({_age_display(obs.observed_utc)}): {_buoy_summary(obs)}"
        ),
    }
    if has_spec:
        d["swell"] = {
            "value": {
                "height_m": obs.swell_height_m,
                "period_s": obs.swell_period_s,
                "dir_compass": obs.swell_dir_compass,
            },
            "display": _spec_wave_display(obs.swell_height_m, obs.swell_dir_compass, obs.swell_period_s),
        }
        d["wind_wave"] = {
            "value": {
                "height_m": obs.wind_wave_height_m,
                "period_s": obs.wind_wave_period_s,
                "dir_compass": obs.wind_wave_dir_compass,
            },
            "display": _spec_wave_display(obs.wind_wave_height_m, obs.wind_wave_dir_compass, obs.wind_wave_period_s),
        }
        if obs.steepness is not None:
            d["steepness"] = obs.steepness
    return d
```

(Keep the existing top-of-function summary ordering; only the structure shown changes. `_buoy_summary` already produced the wind bit — that part is untouched.)

- [ ] **Step 4: Run the new tests, then the whole suite**

Run: `uv run pytest tests/test_tools_buoy.py -v && uv run pytest`
Expected: all PASS — server-level tests must not regress

- [ ] **Step 5: Commit**

```bash
git add src/weather_mcp/tools.py tests/test_tools_buoy.py
git commit -m "feat(tools): surface swell/wind-wave blocks, prefer swell in summaries"
```

---

### Task 6: docs — README, tool description, navigator prompts

**Files:**
- Modify: `README.md` (weather-mcp)
- Modify: `src/weather_mcp/server.py` (tool description for `get_nearest_buoy_observations`, if it repeats the combined-waves caveat — check with `grep -n "combined" src/weather_mcp/server.py`)
- Modify: `~/src/sailingnaturali/naturali-agents/prompts/navigator.md:27`
- Modify: `~/src/sailingnaturali/naturali-agents/skills/navigator/body.md:27`

- [ ] **Step 1: Update weather-mcp README**

In `README.md`, replace the `get_nearest_buoy_observations` bullet with:

```markdown
- `get_nearest_buoy_observations(lat, lon, max_distance_nm?, limit?)` — NDBC observed wind + waves. Reality check for forecasts. Where a station publishes `.spec` spectral data, swell and wind waves are reported separately; otherwise combined waves only.
```

- [ ] **Step 2: Update server.py tool description if it repeats the caveat**

Run `grep -n "combined\|swell" src/weather_mcp/server.py`. If the `get_nearest_buoy_observations` tool description mentions combined-waves-only, reword to match the README bullet above (swell-separated where `.spec` is published). If it doesn't mention waves, no change.

- [ ] **Step 3: Update the navigator prompt + skill (naturali-agents repo)**

Both files have the identical line 27. Replace in **both** `prompts/navigator.md` and `skills/navigator/body.md`:

Old:
```
- `mcp_weather_get_nearest_buoy_observations(lat, lon, max_distance_nm?, limit?)` — NDBC observed wind + combined waves from nearby buoys; the reality check against the forecast (combined waves only, not swell-separated)
```

New:
```
- `mcp_weather_get_nearest_buoy_observations(lat, lon, max_distance_nm?, limit?)` — NDBC observed wind + waves from nearby buoys; the reality check against the forecast. Swell-separated (`swell`, `wind_wave` blocks) where the buoy publishes spectral data; combined waves otherwise — check for the `note` field.
```

- [ ] **Step 4: Run weather-mcp suite once more**

Run: `uv run pytest`
Expected: all PASS

- [ ] **Step 5: Commit both repos**

```bash
cd ~/src/sailingnaturali/weather-mcp
git add README.md src/weather_mcp/server.py
git commit -m "docs: swell separation in buoy obs where .spec is published"
cd ~/src/sailingnaturali/naturali-agents
git add prompts/navigator.md skills/navigator/body.md
git commit -m "docs(navigator): buoy obs now swell-separated where available"
```

---

### Task 7: live smoke test + push

**Files:** none (verification only)

- [ ] **Step 1: Live verification against real NDBC**

Run from the weather-mcp repo root:

```bash
uv run python -c "
import asyncio
from weather_mcp.client import RateLimitedClient
from weather_mcp.providers.ndbc import Station, fetch_station_observation

async def main():
    client = RateLimitedClient()
    # 46088 = New Dungeness, publishes .spec (verified 2026-06-05)
    st = Station(id='46088', name='New Dungeness', lat=48.334, lon=-123.165)
    obs = await fetch_station_observation(client, st, 48.76, -123.0)
    await client.aclose()
    assert obs is not None, 'no observation returned'
    print('combined :', obs.wave_height_m, 'm')
    print('swell    :', obs.swell_height_m, 'm', obs.swell_dir_compass, obs.swell_period_s, 's')
    print('wind wave:', obs.wind_wave_height_m, 'm', obs.wind_wave_dir_compass)
    print('fields   :', obs.available_fields())

asyncio.run(main())
"
```

Expected: prints real numbers; `fields` includes `swell` (if 46088's `.spec` is temporarily stale, swell may be None — try 46087 before treating it as a bug).

- [ ] **Step 2: Full suite, then push both repos**

```bash
cd ~/src/sailingnaturali/weather-mcp && uv run pytest && git push
cd ~/src/sailingnaturali/naturali-agents && git push
```

Expected: all tests PASS, both pushes succeed.

---

### Task 8: Scribe blog post (adopt-vs-keep story)

**Files:**
- Read: `~/src/sailingnaturali/planning/.claude/agents/scribe.md` (the Scribe persona/workflow — follow it exactly)
- Create: a post in `~/src/sailingnaturali/engineering/_posts/` via Scribe's PR workflow

- [ ] **Step 1: Read the Scribe agent definition and an example post**

Read `planning/.claude/agents/scribe.md` and `engineering/_posts/2026-06-05-signalk-mcp-named-tools-vs-execute-code-token-efficiency-voice-agent.md` (the existing adopt-vs-keep post — match its shape: evaluation framework, receipts, decision, reusable lesson).

- [ ] **Step 2: Dispatch Scribe with this source material**

Dispatch a subagent with the Scribe persona (per its definition file) and this brief:

> Topic: "Why generic weather MCPs don't work for marine navigation — an adopt-vs-keep audit."
> Source spec: `weather-mcp/docs/superpowers/specs/2026-06-05-adopt-vs-keep-and-spec-waves-design.md` (contains the full comparison table and per-alternative rejection rationale).
> Key beats: (1) prime directive — adopt before build, with receipts; (2) the audit table vs cmer81/open-meteo-mcp, weather-mcp/weather-mcp, RyanCardin15/NOAA-TidesAndCurrents; (3) why each failed: no buoy ground-truthing, no quota-aware premium tool design, no TTS-safe `{value, display}` contract, tool-count bloat on small models; (4) the ndbc-api dependency-weight rejection (2 deps → ~9 incl. scipy/xarray for ~220 lines of text parsing); (5) what we built instead of switching: `.spec` swell separation — closing our own biggest documented gap; (6) reusable lesson for MCP authors: an MCP server's value is the tool *design* (quota awareness, absence handling, display contracts), not API coverage.
> Workflow: follow Scribe's branch + PR process against the `engineering/` repo. Do not merge — leave the PR for Bryan's review.

- [ ] **Step 3: Verify the PR exists**

Run: `cd ~/src/sailingnaturali/engineering && gh pr list`
Expected: one open PR from Scribe with the post.

---

### Task 9: registry listings

**Files:**
- Possibly modify: `weather-mcp/README.md` (only if a registry requires missing metadata)

- [ ] **Step 1: Verify the repo presents well**

Check `weather-mcp/pyproject.toml` has a `description` and the README states install (`uvx weather-mcp` / `uv run weather-mcp`) and required env (`STORMGLASS_API_KEY` optional). Fix gaps if any.

- [ ] **Step 2: Submit / verify listing on each registry**

- **Glama** — auto-indexes public GitHub MCP repos; verify at `https://glama.ai/mcp/servers?query=weather-mcp` (search for `sailingnaturali/weather-mcp`). If absent, submit via the "Add server" flow on glama.ai.
- **PulseMCP** — submit at `https://www.pulsemcp.com/submit` with the GitHub URL.
- **mcp.so** — submit via the site's "Submit" form with the GitHub URL.

These are web forms — if a form can't be driven from this environment, compile the three URLs + a one-paragraph blurb (use the README's first paragraph) and hand them to Bryan as the deliverable for this task.

- [ ] **Step 3: Record completion**

Note submission status (done / handed to Bryan) in the final report. No commit needed unless README/pyproject changed; if changed:

```bash
cd ~/src/sailingnaturali/weather-mcp
git add README.md pyproject.toml
git commit -m "docs: registry-facing metadata" && git push
```
