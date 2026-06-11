import time

from weather_mcp.cache import EventCache


def test_get_with_ttl_missing_returns_none(tmp_path):
    cache = EventCache(str(tmp_path / "c.sqlite"))
    cache.init_schema()
    assert cache.get_with_ttl("openmeteo:48.42:-123.37:0", 3600) is None
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


def test_immutable_api_is_gone(tmp_path):
    # The never-expire get/put pair was pruned (fleet conventions R4): nothing
    # used it, and a bare get() on a TTL key would skip the freshness check.
    cache = EventCache(str(tmp_path / "c.sqlite"))
    cache.init_schema()
    assert not hasattr(cache, "get")
    assert not hasattr(cache, "put")
    cache.close()
