import time

from weather_mcp.cache import EventCache


def test_get_missing_returns_none(tmp_path):
    cache = EventCache(str(tmp_path / "c.sqlite"))
    cache.init_schema()
    assert cache.get("openmeteo:48.42:-123.37:0") is None
    cache.close()


def test_put_then_get_roundtrips(tmp_path):
    cache = EventCache(str(tmp_path / "c.sqlite"))
    cache.init_schema()
    payload = [{"utc": "2026-05-28T12:00:00+00:00", "wind_kn": 12.0}]
    cache.put("openmeteo:48.42:-123.37:0", payload)
    assert cache.get("openmeteo:48.42:-123.37:0") == payload
    cache.close()


def test_put_with_ttl_roundtrips_within_ttl(tmp_path):
    cache = EventCache(str(tmp_path / "c.sqlite"))
    cache.init_schema()
    cache.put_with_ttl("ndbc:activestations", [{"id": "46087"}])
    assert cache.get_with_ttl("ndbc:activestations", 3600) == [{"id": "46087"}]
    cache.close()


def test_get_with_ttl_expires(tmp_path):
    cache = EventCache(str(tmp_path / "c.sqlite"))
    cache.init_schema()
    cache.put_with_ttl("k", [{"x": 1}])
    time.sleep(0.01)
    assert cache.get_with_ttl("k", 0) is None
    cache.close()


def test_immutable_get_put_not_returned_by_ttl(tmp_path):
    cache = EventCache(str(tmp_path / "c.sqlite"))
    cache.init_schema()
    cache.put("frozen-key", [{"kind": "fixed"}])
    assert cache.get("frozen-key") == [{"kind": "fixed"}]
    assert cache.get_with_ttl("frozen-key", 3600) is None
    cache.close()
