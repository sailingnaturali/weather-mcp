"""Disk-backed cache for marine weather data.

Originally copied from tide-mcp's cache; diverged 2026-06 — weather-mcp uses
only TTL entries (forecasts, buoy observations, station lists) keyed by
source + spatial bucket via get_with_ttl/put_with_ttl. The immutable
(never-expire) get/put pair was pruned: nothing here called it, and a future
caller using bare get() on a TTL key would read stale data with no freshness
check (fleet conventions R4).
"""

from __future__ import annotations

import json
import sqlite3
import time


class EventCache:
    """SQLite key->JSON store. Call init_schema() once before get/put.

    All access is synchronous and expected on a single thread (the MCP server's
    event-loop thread).
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self._conn = sqlite3.connect(path)

    def init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS events_cache (
                key        TEXT PRIMARY KEY,
                payload    TEXT NOT NULL,
                written_at REAL
            );
            """
        )
        cols = {row[1] for row in self._conn.execute("PRAGMA table_info(events_cache)")}
        if "written_at" not in cols:
            self._conn.execute("ALTER TABLE events_cache ADD COLUMN written_at REAL")
        self._conn.commit()

    def get_with_ttl(self, key: str, ttl_seconds: float) -> list[dict] | None:
        """Return the payload only if written within ttl_seconds; else None."""
        cur = self._conn.execute(
            "SELECT payload, written_at FROM events_cache WHERE key = ?", (key,)
        )
        row = cur.fetchone()
        if row is None or row[1] is None:
            return None
        if time.time() - row[1] >= ttl_seconds:
            return None
        return json.loads(row[0])

    def put_with_ttl(self, key: str, payload: list[dict]) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO events_cache (key, payload, written_at) VALUES (?, ?, ?)",
            (key, json.dumps(payload), time.time()),
        )
        self._conn.commit()

    @property
    def conn(self) -> sqlite3.Connection:
        """Underlying connection. Quota module uses it to share the DB file."""
        return self._conn

    def close(self) -> None:
        self._conn.close()
