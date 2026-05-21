import sqlite3

import pytest

from odds_scraper.db_schema import SCHEMA_VERSION, init_schema


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:", isolation_level=None)
    c.execute("PRAGMA foreign_keys = ON")
    yield c
    c.close()


def test_init_schema_creates_all_tables(conn):
    init_schema(conn)
    tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {"schema_version", "events", "snapshots", "prices"} <= tables


def test_init_schema_creates_all_indexes(conn):
    init_schema(conn)
    indexes = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name NOT LIKE 'sqlite_autoindex_%'"
        ).fetchall()
    }
    assert {
        "idx_snapshots_event_ts",
        "idx_snapshots_ts",
        "idx_prices_event_market_outcome",
        "idx_prices_ts",
    } <= indexes


def test_init_schema_records_current_version(conn):
    init_schema(conn)
    row = conn.execute(
        "SELECT version FROM schema_version WHERE rowid = 1"
    ).fetchone()
    assert row[0] == SCHEMA_VERSION


def test_init_schema_is_idempotent(conn):
    init_schema(conn)
    init_schema(conn)
    rows = conn.execute("SELECT version FROM schema_version").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == SCHEMA_VERSION


def test_v2_adds_country_and_league_columns(conn):
    init_schema(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(events)").fetchall()}
    assert {"country_id", "country_name", "league_id", "league_name"} <= cols


def test_v2_schema_version_recorded(conn):
    init_schema(conn)
    row = conn.execute(
        "SELECT version FROM schema_version WHERE rowid = 1"
    ).fetchone()
    assert row[0] == 2


def test_v2_upgrades_a_v1_database(conn):
    # Simulate a v1 database: run only the v1 base DDL (no country/league
    # columns) and pin schema_version to 1.
    conn.executescript("""
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
        INSERT OR REPLACE INTO schema_version (rowid, version) VALUES (1, 1);
    """)
    # Insert a pre-existing event so we can verify the migration didn't drop data
    conn.execute(
        "INSERT INTO events (id, home, away, kickoff_utc) "
        "VALUES ('E_OLD', 'Old Home', 'Old Away', '2026-05-01T00:00:00Z')",
    )

    # Run the migration
    init_schema(conn)

    # New columns exist
    cols = {r[1] for r in conn.execute("PRAGMA table_info(events)").fetchall()}
    assert {"country_id", "country_name", "league_id", "league_name"} <= cols
    # Existing data preserved, new columns NULL
    row = conn.execute(
        "SELECT id, home, country_id, country_name, league_id, league_name "
        "FROM events WHERE id = 'E_OLD'"
    ).fetchone()
    assert row[0] == "E_OLD"
    assert row[1] == "Old Home"
    assert row[2] is None and row[3] is None and row[4] is None and row[5] is None
    # Version bumped to 2
    v = conn.execute(
        "SELECT version FROM schema_version WHERE rowid = 1"
    ).fetchone()
    assert v[0] == 2


def test_v2_migration_is_idempotent_after_partial_failure(conn):
    # Simulate the scenario where the v2 ALTER TABLE statements have already
    # been applied to some columns but the schema_version was never bumped
    # (a crash between the ALTERs and the version write). Re-running
    # init_schema must NOT raise "duplicate column name".
    conn.executescript("""
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
            country_name TEXT
        );
        INSERT OR REPLACE INTO schema_version (rowid, version) VALUES (1, 1);
    """)
    # Some country cols already added; v2 should add only the missing two
    init_schema(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(events)").fetchall()}
    assert {"country_id", "country_name", "league_id", "league_name"} <= cols
    v = conn.execute(
        "SELECT version FROM schema_version WHERE rowid = 1"
    ).fetchone()
    assert v[0] == 2
