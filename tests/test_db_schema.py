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
