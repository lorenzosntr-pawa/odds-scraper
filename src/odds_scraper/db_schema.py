from __future__ import annotations

import sqlite3
from typing import Callable

SCHEMA_VERSION = 2

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
    kickoff_utc  TEXT NOT NULL,
    country_id   TEXT,
    country_name TEXT,
    league_id    TEXT,
    league_name  TEXT
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

def _add_columns_if_missing(
    conn: sqlite3.Connection, table: str, columns: list[tuple[str, str]],
) -> None:
    """Idempotently ALTER TABLE to add columns that don't already exist.

    columns is a list of (col_name, col_type) pairs, e.g.,
    [("country_id", "TEXT"), ("country_name", "TEXT")].

    SQLite's `ALTER TABLE ADD COLUMN` has no IF NOT EXISTS form, so we
    inspect PRAGMA table_info and only emit the ALTER for missing names.
    Makes migrations safe against partial-completion crashes where the
    ALTER succeeded but the schema_version bump didn't.
    """
    existing = {
        row[1]  # PRAGMA table_info columns: cid, name, type, notnull, dflt, pk
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    for col_name, col_type in columns:
        if col_name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}")


_MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {
    1: lambda conn: None,
    2: lambda conn: _add_columns_if_missing(conn, "events", [
        ("country_id",   "TEXT"),
        ("country_name", "TEXT"),
        ("league_id",    "TEXT"),
        ("league_name",  "TEXT"),
    ]),
}


def init_schema(conn: sqlite3.Connection) -> None:
    """Idempotent: apply base DDL, then any pending migrations.

    Note: calls executescript(), which issues an implicit COMMIT before
    running. Must be called outside any explicit transaction.
    """
    conn.executescript(_BASE_DDL)
    current = _current_version(conn)
    for v in range(current + 1, SCHEMA_VERSION + 1):
        # Each migration step is its own transaction so a crash between
        # the schema change (e.g. ALTER TABLE) and the version bump can't
        # leave the DB with the new shape but the old version recorded —
        # which would brick the next open by trying to re-run the migration.
        conn.execute("BEGIN")
        try:
            _MIGRATIONS[v](conn)
            conn.execute(
                "INSERT OR REPLACE INTO schema_version (rowid, version) "
                "VALUES (1, ?)",
                (v,),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


def _current_version(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT version FROM schema_version WHERE rowid = 1"
    ).fetchone()
    return row[0] if row else 0
