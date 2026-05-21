from __future__ import annotations

import sqlite3
from typing import Callable

SCHEMA_VERSION = 1

_BASE_DDL = """
CREATE TABLE IF NOT EXISTS schema_version (
    rowid    INTEGER PRIMARY KEY CHECK (rowid = 1),
    version  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id           TEXT PRIMARY KEY,
    sr_id        TEXT,
    genius_id    TEXT,
    home         TEXT NOT NULL,
    away         TEXT NOT NULL,
    kickoff_utc  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc        TEXT NOT NULL,
    event_id      TEXT NOT NULL REFERENCES events(id),
    bookmaker     TEXT NOT NULL,
    status        TEXT NOT NULL,
    match_minute  INTEGER,
    score_home    INTEGER,
    score_away    INTEGER,
    fetch_status  TEXT NOT NULL,
    fetch_error   TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS prices (
    snapshot_id  INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    event_id     TEXT NOT NULL,
    ts_utc       TEXT NOT NULL,
    bookmaker    TEXT NOT NULL,
    market_id    TEXT NOT NULL,
    line         REAL NOT NULL DEFAULT 0.0,
    side         TEXT NOT NULL,
    odds         REAL,
    probability  REAL,
    PRIMARY KEY (snapshot_id, market_id, line, side)
);

CREATE INDEX IF NOT EXISTS idx_snapshots_event_ts
    ON snapshots(event_id, ts_utc);
CREATE INDEX IF NOT EXISTS idx_snapshots_ts
    ON snapshots(ts_utc);
CREATE INDEX IF NOT EXISTS idx_prices_event_market_outcome
    ON prices(event_id, market_id, line, side, ts_utc);
CREATE INDEX IF NOT EXISTS idx_prices_ts
    ON prices(ts_utc);
"""

_MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {
    1: lambda conn: None,
}


def init_schema(conn: sqlite3.Connection) -> None:
    """Idempotent: apply base DDL, then any pending migrations.

    Note: calls executescript(), which issues an implicit COMMIT before
    running. Must be called outside any explicit transaction.
    """
    conn.executescript(_BASE_DDL)
    current = _current_version(conn)
    for v in range(current + 1, SCHEMA_VERSION + 1):
        _MIGRATIONS[v](conn)
        conn.execute(
            "INSERT OR REPLACE INTO schema_version (rowid, version) "
            "VALUES (1, ?)",
            (v,),
        )


def _current_version(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT version FROM schema_version WHERE rowid = 1"
    ).fetchone()
    return row[0] if row else 0
