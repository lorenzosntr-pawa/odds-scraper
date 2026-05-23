import csv
import sqlite3
from pathlib import Path

import pytest

from odds_scraper.db_schema import init_schema
from odds_scraper.pricer import csv_export


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(str(tmp_path / "x.db"), isolation_level=None)
    init_schema(c)
    c.row_factory = sqlite3.Row
    return c


def _seed_one_run(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        "INSERT INTO pricer_runs (created_at, config_id, coverage, scope_json, "
        "n_events, n_rows, csv_path) "
        "VALUES ('2026-05-23T10:00:00Z', 1, 'all', '{}', 1, 1, 'sim/run_0001.csv')",
    )
    run_id = cur.lastrowid
    conn.execute(
        "INSERT INTO events (id, home, away, kickoff_utc) "
        "VALUES ('E1', 'Home FC', 'Away FC', '2026-05-22T18:30:00Z')",
    )
    cur = conn.execute(
        "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, fetch_status) "
        "VALUES ('2026-05-21T10:00:00Z', 'E1', 'betpawa', 'UPCOMING', 'ok')",
    )
    snap_id = cur.lastrowid
    conn.execute(
        "INSERT INTO pricer_results (run_id, snapshot_id, event_id, ts_utc, "
        "basis_used, our_p_home_2, our_2up_home_capped, bp_2up_home_odds) "
        "VALUES (?, ?, 'E1', '2026-05-21T10:00:00Z', 'bp', 0.65, 1.85, 1.83)",
        (run_id, snap_id),
    )
    return run_id


def test_write_run_csv_emits_header_and_rows(db, tmp_path):
    run_id = _seed_one_run(db)
    out = tmp_path / "run_0001.csv"
    csv_export.write_run_csv(db, run_id, out)

    with open(out, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert len(rows) == 1
    row = rows[0]
    assert row["event_id"] == "E1"
    assert row["home"] == "Home FC"
    assert row["away"] == "Away FC"
    assert row["basis_used"] == "bp"
    assert float(row["our_2up_home_capped"]) == 1.85
    assert float(row["bp_2up_home_odds"]) == 1.83


def test_write_run_csv_creates_dirs_and_handles_empty_run(db, tmp_path):
    """An empty run (n_rows=0) still produces a CSV with just headers."""
    cur = db.execute(
        "INSERT INTO pricer_runs (created_at, config_id, coverage, scope_json, "
        "n_events, n_rows, csv_path) "
        "VALUES ('2026-05-23T10:00:00Z', 1, 'all', '{}', 0, 0, 'sim/run_0002.csv')",
    )
    run_id = cur.lastrowid
    out = tmp_path / "sub" / "run_0002.csv"
    csv_export.write_run_csv(db, run_id, out)
    with open(out, encoding="utf-8") as f:
        lines = f.read().splitlines()
    assert len(lines) == 1  # header only
    assert "event_id" in lines[0]
