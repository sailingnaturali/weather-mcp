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
