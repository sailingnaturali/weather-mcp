import sqlite3

import pytest

from weather_mcp.quota import QuotaExhausted, StormglassQuota


def _quota(daily_limit: int = 10) -> StormglassQuota:
    conn = sqlite3.connect(":memory:")
    q = StormglassQuota(conn, daily_limit=daily_limit)
    q.init_schema()
    return q


def test_used_today_starts_zero():
    q = _quota()
    assert q.used_today() == 0
    assert q.remaining_today() == 10


def test_consume_increments():
    q = _quota()
    assert q.consume() == 1
    assert q.consume() == 2
    assert q.used_today() == 2
    assert q.remaining_today() == 8


def test_consume_raises_at_limit():
    q = _quota(daily_limit=2)
    q.consume()
    q.consume()
    with pytest.raises(QuotaExhausted):
        q.consume()
    assert q.used_today() == 2


def test_refund_decrements_floor_zero():
    q = _quota()
    q.consume()
    q.refund()
    assert q.used_today() == 0
    q.refund()
    assert q.used_today() == 0


def test_yesterday_does_not_count_against_today(monkeypatch):
    """If only yesterday's row exists, today's used_today() returns 0."""
    q = _quota()
    q._conn.execute(
        "INSERT INTO stormglass_quota (date_utc, used) VALUES (?, ?)",
        ("1999-01-01", 999),
    )
    q._conn.commit()
    assert q.used_today() == 0
    assert q.remaining_today() == 10


def test_reset_seconds_positive():
    q = _quota()
    assert 0 < q.reset_seconds() <= 86400


def test_reset_at_utc_is_isoformat():
    q = _quota()
    s = q.reset_at_utc()
    assert s.endswith("+00:00")
    assert "T00:00:00" in s
