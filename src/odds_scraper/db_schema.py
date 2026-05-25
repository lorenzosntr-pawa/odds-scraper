from __future__ import annotations

import json
import sqlite3
from typing import Callable

SCHEMA_VERSION = 8

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
CREATE INDEX IF NOT EXISTS idx_snapshots_event_bm_ts
    ON snapshots(event_id, bookmaker, ts_utc);
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


def _apply_v4_pricer_tables(conn: sqlite3.Connection) -> None:
    """v4: pricer integration — configs, runs, results + seed default profile.

    The "default" config is the read-only baseline pinned to the engine
    source's FeatureProperties.java values. Tests and the /simulator
    page rely on it being present exactly once with is_default=1.

    Note: we use individual conn.execute() calls (not executescript) because
    init_schema wraps each migration in BEGIN/COMMIT, and executescript
    issues an implicit COMMIT before running, which would end that tx.
    """
    conn.execute(
        """
        CREATE TABLE pricer_configs (
            id           INTEGER PRIMARY KEY,
            name         TEXT NOT NULL UNIQUE,
            created_at   TEXT NOT NULL,
            is_default   INTEGER NOT NULL DEFAULT 0,
            coefficients TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE pricer_runs (
            id          INTEGER PRIMARY KEY,
            created_at  TEXT NOT NULL,
            config_id   INTEGER NOT NULL REFERENCES pricer_configs(id),
            coverage    TEXT NOT NULL,
            scope_json  TEXT NOT NULL,
            n_events    INTEGER NOT NULL,
            n_rows      INTEGER NOT NULL,
            csv_path    TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX idx_pricer_runs_created ON pricer_runs(created_at DESC)"
    )
    conn.execute(
        """
        CREATE TABLE pricer_results (
            run_id              INTEGER NOT NULL REFERENCES pricer_runs(id),
            snapshot_id         INTEGER NOT NULL REFERENCES snapshots(id),
            event_id            TEXT    NOT NULL,
            ts_utc              TEXT    NOT NULL,
            basis_used          TEXT    NOT NULL,
            lambda_home         REAL,
            lambda_away         REAL,
            our_p_home_1        REAL,
            our_p_away_1        REAL,
            our_1up_home_fair   REAL,
            our_1up_home_capped REAL,
            our_1up_away_fair   REAL,
            our_1up_away_capped REAL,
            our_p_home_2        REAL,
            our_p_away_2        REAL,
            our_2up_home_fair   REAL,
            our_2up_home_capped REAL,
            our_2up_away_fair   REAL,
            our_2up_away_capped REAL,
            bp_1up_home_odds    REAL, bp_1up_away_odds  REAL,
            bp_2up_home_odds    REAL, bp_2up_away_odds  REAL,
            sb_1up_home_odds    REAL, sb_1up_away_odds  REAL,
            sb_2up_home_odds    REAL, sb_2up_away_odds  REAL,
            b9j_1up_home_odds   REAL, b9j_1up_away_odds REAL,
            b9j_2up_home_odds   REAL, b9j_2up_away_odds REAL,
            bw_1up_home_odds    REAL, bw_1up_away_odds  REAL,
            bw_2up_home_odds    REAL, bw_2up_away_odds  REAL
        )
        """
    )
    conn.execute(
        "CREATE INDEX idx_pricer_results_run   ON pricer_results(run_id)"
    )
    conn.execute(
        "CREATE INDEX idx_pricer_results_event ON pricer_results(event_id, ts_utc)"
    )
    default_coeffs = {
        "ONEUP_FAVORITE_MARGIN": [0.9969, 0.0313],
        "ONEUP_UNDERDOG_MARGIN": [0.9799, 0.0400],
        "ONEUP_MIN_GUARANTEED_REDUCTION": 0.02,
        "ONEUP_TRAILING_MIN_REDUCTION": 0.05,
        "ONEUP_TRAILING_MAX_REDUCTION": 0.25,
        "TWOUP_FAVORITE_MARGIN": [0.998, 0.010],
        "TWOUP_UNDERDOG_MARGIN": [0.994, 0.014],
        "TWOUP_FAVORITE_BOOST_COEFFICIENT": 0.9,
        "TWOUP_UNDERDOG_BOOST_COEFFICIENT": 0.6,
        "TWOUP_FAVORITE_MIN_GUARANTEED_REDUCTION": 0.02,
        "TWOUP_UNDERDOG_MIN_GUARANTEED_REDUCTION": 0.005,
        "TWOUP_TRAILING_MIN_REDUCTION": 0.05,
        "TWOUP_TRAILING_MAX_REDUCTION": 0.25,
    }
    conn.execute(
        "INSERT INTO pricer_configs (name, created_at, is_default, coefficients) "
        "VALUES ('default', datetime('now'), 1, ?)",
        (json.dumps(default_coeffs),),
    )


_MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {
    1: lambda conn: None,
    2: lambda conn: _add_columns_if_missing(conn, "events", [
        ("country_id",   "TEXT"),
        ("country_name", "TEXT"),
        ("league_id",    "TEXT"),
        ("league_name",  "TEXT"),
    ]),
    # v3: covering index for the batched latest-prices query. Without it,
    # the home page does GROUP BY (event_id, bookmaker, ts_utc) over the
    # prices table (~1M rows after a few days) and takes ~6s; with it,
    # the query is index-only and drops to ~75ms.
    3: lambda conn: conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_snapshots_event_bm_ts "
        "ON snapshots(event_id, bookmaker, ts_utc)"
    ),
    4: lambda conn: _apply_v4_pricer_tables(conn),
    # v5: per-tick OUR engine output, written by the scraper alongside
    # scraped snapshots so the detail page can show OUR in the
    # historical timeline without re-running a simulator.
    5: lambda conn: _apply_v5_pricer_live_results(conn),
    # v6: progress tracking + single-flight on pricer_runs.
    #   state: 'running' | 'done' | 'failed' — the simulator UI uses it
    #          to show a progress bar while a run is in flight and to
    #          refuse a second POST while one is already running.
    #   n_done / n_total: per-tick progress counters; the runner
    #          updates n_done every batch_size ticks so the polling
    #          status endpoint can show a percentage.
    #   started_at / finished_at: wall-clock timestamps so the UI can
    #          show "running for 12m" / "finished 3s ago".
    6: lambda conn: _add_columns_if_missing(conn, "pricer_runs", [
        ("state",       "TEXT NOT NULL DEFAULT 'done'"),
        ("n_done",      "INTEGER NOT NULL DEFAULT 0"),
        ("n_total",     "INTEGER NOT NULL DEFAULT 0"),
        ("started_at",  "TEXT"),
        ("finished_at", "TEXT"),
    ]),
    # v7: split the old one-shot "coverage" enum into two orthogonal
    # dimensions — `coverage` now stores the regime (any/prematch/live,
    # snapshot status filter); new `density` stores the per-event
    # sampling (all / latest). Lets the user combine e.g. "prematch
    # only, latest per event" which the previous single-radio shape
    # couldn't express. Backfills existing rows so history stays
    # readable: old 'all'/'latest' → regime='any' + density inferred;
    # 'prematch'/'live' → keep as regime, density='all'.
    7: lambda conn: _apply_v7_split_coverage(conn),
    # v8: drop pricer_runs and pricer_results. The simulator now writes
    # CSV files only — per-tick OUR is already in pricer_live_results
    # (the authoritative on-tick record written by the scraper), so the
    # run+results tables were duplicating that work and bloating the DB
    # with rows the user actively didn't want there.
    8: lambda conn: _apply_v8_drop_sim_tables(conn),
}


def _apply_v8_drop_sim_tables(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE IF EXISTS pricer_results")
    conn.execute("DROP TABLE IF EXISTS pricer_runs")


def _apply_v7_split_coverage(conn: sqlite3.Connection) -> None:
    _add_columns_if_missing(conn, "pricer_runs", [
        ("density", "TEXT NOT NULL DEFAULT 'all'"),
    ])
    conn.execute("UPDATE pricer_runs SET density = 'latest' WHERE coverage = 'latest'")
    conn.execute("UPDATE pricer_runs SET coverage = 'any' WHERE coverage IN ('all', 'latest')")


def _apply_v5_pricer_live_results(conn: sqlite3.Connection) -> None:
    """v5: pricer_live_results — one row per (event_id, ts_utc) tick."""
    conn.execute(
        """
        CREATE TABLE pricer_live_results (
            event_id            TEXT NOT NULL,
            ts_utc              TEXT NOT NULL,
            basis_used          TEXT NOT NULL,
            lambda_home         REAL,
            lambda_away         REAL,
            our_p_home_1        REAL,
            our_p_away_1        REAL,
            our_1up_home_fair   REAL,
            our_1up_home_capped REAL,
            our_1up_away_fair   REAL,
            our_1up_away_capped REAL,
            our_p_home_2        REAL,
            our_p_away_2        REAL,
            our_2up_home_fair   REAL,
            our_2up_home_capped REAL,
            our_2up_away_fair   REAL,
            our_2up_away_capped REAL,
            PRIMARY KEY (event_id, ts_utc)
        )
        """
    )
    conn.execute(
        "CREATE INDEX idx_pricer_live_event ON pricer_live_results(event_id, ts_utc)"
    )


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
