"""Daily Stormglass request counter, persisted alongside the event cache.

Free-tier Stormglass allows 10 requests per UTC day. The counter resets
naturally per-row: querying today's date returns 0 used if no row exists yet.

Reserve+commit semantics: callers should `consume()` only after a successful
upstream response. Cache hits do not consume. Refunds are available for
transient (5xx) errors after a consume() was optimistically performed.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone


DEFAULT_DAILY_LIMIT = 10


class QuotaExhausted(Exception):
    """Raised by consume() when the daily limit has been reached."""


class StormglassQuota:
    """SQLite-backed daily counter. Shares its connection with EventCache."""

    def __init__(self, conn: sqlite3.Connection, daily_limit: int = DEFAULT_DAILY_LIMIT) -> None:
        self._conn = conn
        self.daily_limit = daily_limit

    def init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS stormglass_quota (
                date_utc TEXT PRIMARY KEY,
                used     INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        self._conn.commit()

    def _today(self) -> str:
        return datetime.now(timezone.utc).date().isoformat()

    def used_today(self) -> int:
        cur = self._conn.execute(
            "SELECT used FROM stormglass_quota WHERE date_utc = ?", (self._today(),)
        )
        row = cur.fetchone()
        return int(row[0]) if row else 0

    def remaining_today(self) -> int:
        return max(0, self.daily_limit - self.used_today())

    def consume(self) -> int:
        """Increment today's counter. Raises QuotaExhausted if already at limit."""
        today = self._today()
        cur = self._conn.execute(
            "SELECT used FROM stormglass_quota WHERE date_utc = ?", (today,)
        )
        row = cur.fetchone()
        used = int(row[0]) if row else 0
        if used >= self.daily_limit:
            raise QuotaExhausted(f"Stormglass quota exhausted ({used}/{self.daily_limit})")
        new_used = used + 1
        self._conn.execute(
            "INSERT OR REPLACE INTO stormglass_quota (date_utc, used) VALUES (?, ?)",
            (today, new_used),
        )
        self._conn.commit()
        return new_used

    def reset_seconds(self) -> int:
        """Seconds until next UTC midnight."""
        now = datetime.now(timezone.utc)
        tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return int((tomorrow - now).total_seconds())

    def reset_at_utc(self) -> str:
        """ISO8601 timestamp of next UTC midnight."""
        now = datetime.now(timezone.utc)
        tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return tomorrow.isoformat()
