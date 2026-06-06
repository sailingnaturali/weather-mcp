import httpx
import respx

from weather_mcp.client import RateLimitedClient
from weather_mcp.providers.ndbc import (
    Station,
    fetch_active_stations,
    fetch_station_observation,
    nearest_stations,
    parse_activestations,
    parse_realtime2,
    parse_spec,
)


STATIONS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<stations>
  <station id="46087" lat="48.494" lon="-124.728" name="Neah Bay" met="y" type="buoy" owner="NDBC"/>
  <station id="46041" lat="47.353" lon="-124.731" name="Cape Elizabeth" met="y" type="buoy" owner="NDBC"/>
  <station id="X" lat="48.0" lon="-124.0" name="Currents Only" met="n" type="buoy" owner="NDBC"/>
  <station id="46060" lat="60.564" lon="-146.838" name="Far Buoy" met="y" type="buoy" owner="NDBC"/>
</stations>"""

REALTIME_FULL = """#YY  MM DD hh mm WDIR WSPD GST  WVHT   DPD   APD MWD   PRES  ATMP  WTMP  DEWP  VIS PTDY  TIDE
#yr  mo dy hr mn degT m/s  m/s     m   sec   sec degT   hPa  degC  degC  degC  nmi  hPa    ft
2099 01 01 00 50  230  7.2  9.8   1.5   8.0   6.0 265  1013.5  12.0  10.5  11.0   MM -0.6    MM
2099 01 01 00 40  225  6.8  9.0   1.4   8.0   6.0 265  1014.0  12.0  10.5  11.0   MM -0.5    MM"""

REALTIME_NO_WAVES = """#YY  MM DD hh mm WDIR WSPD GST  WVHT   DPD   APD MWD   PRES  ATMP  WTMP  DEWP  VIS PTDY  TIDE
#yr  mo dy hr mn degT m/s  m/s     m   sec   sec degT   hPa  degC  degC  degC  nmi  hPa    ft
2099 01 01 00 50  230  7.2  9.8    MM    MM    MM  MM  1013.5  12.0  10.5  11.0   MM -0.6    MM"""

REALTIME_ALL_MM = """#YY  MM DD hh mm WDIR WSPD GST  WVHT   DPD   APD MWD   PRES  ATMP  WTMP  DEWP  VIS PTDY  TIDE
#yr  mo dy hr mn degT m/s  m/s     m   sec   sec degT   hPa  degC  degC  degC  nmi  hPa    ft
2099 01 01 00 50   MM   MM   MM    MM    MM    MM  MM      MM    MM    MM    MM   MM    MM    MM"""


def test_parse_activestations_filters_to_met_y():
    stations = parse_activestations(STATIONS_XML)
    ids = [s.id for s in stations]
    assert "46087" in ids
    assert "46041" in ids
    assert "X" not in ids, "met=n stations are excluded"
    assert "46060" in ids


def test_parse_realtime2_full_observation():
    station = Station(id="46087", name="Neah Bay", lat=48.494, lon=-124.728)
    obs = parse_realtime2(REALTIME_FULL, station, ref_lat=48.5, ref_lon=-124.7)
    assert obs is not None
    # 7.2 m/s * 1.94384 → 14.0 kn
    assert round(obs.wind_speed_kn, 1) == 14.0
    assert obs.wind_dir_deg == 230
    assert round(obs.wind_gust_kn, 1) == 19.0
    assert obs.wave_height_m == 1.5
    assert obs.wave_dom_period_s == 8.0
    assert obs.wave_dir_deg == 265
    assert obs.pressure_hpa == 1013.5
    assert obs.pressure_tendency_hpa == -0.6
    assert set(obs.available_fields()) == {"wind", "wave", "pressure"}


def test_parse_realtime2_wave_fields_missing():
    station = Station(id="46087", name="Neah Bay", lat=48.494, lon=-124.728)
    obs = parse_realtime2(REALTIME_NO_WAVES, station, ref_lat=48.5, ref_lon=-124.7)
    assert obs is not None
    assert obs.wave_height_m is None
    assert obs.wind_speed_kn is not None
    assert obs.available_fields() == ["wind", "pressure"]


def test_parse_realtime2_all_mm_returns_none():
    station = Station(id="X", name="Dead", lat=48.0, lon=-124.0)
    obs = parse_realtime2(REALTIME_ALL_MM, station, ref_lat=48.0, ref_lon=-124.0)
    assert obs is None, "station with no usable data must be dropped"


def test_parse_realtime2_no_data_rows():
    text = """#YY  MM DD hh mm WDIR WSPD GST  WVHT   DPD   APD MWD   PRES  ATMP  WTMP  DEWP  VIS PTDY  TIDE
#yr  mo dy hr mn degT m/s  m/s     m   sec   sec degT   hPa  degC  degC  degC  nmi  hPa    ft"""
    station = Station(id="X", name="X", lat=0.0, lon=0.0)
    assert parse_realtime2(text, station, 0.0, 0.0) is None


def test_nearest_stations_orders_by_distance():
    stations = parse_activestations(STATIONS_XML)
    # Reference: Cape Flattery ~ 48.4, -124.7. Neah Bay is closer than Cape Elizabeth.
    nearest = nearest_stations(stations, 48.4, -124.7, max_distance_nm=200, limit=2)
    assert [s.id for s in nearest] == ["46087", "46041"]


def test_nearest_stations_respects_max_distance():
    stations = parse_activestations(STATIONS_XML)
    nearest = nearest_stations(stations, 48.4, -124.7, max_distance_nm=20, limit=10)
    assert [s.id for s in nearest] == ["46087"]


@respx.mock
async def test_fetch_active_stations_parses_xml():
    respx.get("https://www.ndbc.noaa.gov/activestations.xml").mock(
        return_value=httpx.Response(200, text=STATIONS_XML)
    )
    client = RateLimitedClient()
    stations = await fetch_active_stations(client)
    await client.aclose()
    assert len(stations) == 3  # met=y only


@respx.mock
async def test_fetch_station_observation_returns_none_on_404():
    respx.get("https://www.ndbc.noaa.gov/data/realtime2/9999.txt").mock(
        return_value=httpx.Response(404)
    )
    client = RateLimitedClient()
    station = Station(id="9999", name="Gone", lat=0.0, lon=0.0)
    obs = await fetch_station_observation(client, station, 0.0, 0.0)
    await client.aclose()
    assert obs is None


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
