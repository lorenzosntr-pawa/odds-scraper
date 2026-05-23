import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from odds_scraper.db_schema import init_schema
from odds_scraper.pricer import configs, engine, runner


def _seed_event_with_priced_snapshot(conn: sqlite3.Connection, event_id: str):
    conn.execute(
        "INSERT INTO events (id, home, away, kickoff_utc) "
        "VALUES (?, 'Home FC', 'Away FC', '2026-05-22T18:30:00Z')",
        (event_id,),
    )
    cur = conn.execute(
        "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, "
        "match_minute, score_home, score_away, fetch_status) "
        "VALUES ('2026-05-21T10:00:00Z', ?, 'betpawa', 'UPCOMING', "
        "NULL, NULL, NULL, 'ok')",
        (event_id,),
    )
    snap_id = cur.lastrowid
    base = [
        ("1x2_ft", 0.0, "home", 1.85, 0.54),
        ("1x2_ft", 0.0, "draw", 3.40, 0.29),
        ("1x2_ft", 0.0, "away", 4.20, 0.17),
        ("over_under_ft", 2.5, "over",  1.85, 0.55),
        ("over_under_ft", 2.5, "under", 1.95, 0.45),
        ("next_goal_ft", 1.0, "home", 1.85, 0.54),
        ("next_goal_ft", 1.0, "none", 8.50, 0.12),
        ("next_goal_ft", 1.0, "away", 3.50, 0.34),
    ]
    for mid, line, side, odds, prob in base:
        conn.execute(
            "INSERT INTO prices (snapshot_id, event_id, ts_utc, bookmaker, "
            "market_id, line, side, odds, probability) "
            "VALUES (?, ?, '2026-05-21T10:00:00Z', 'betpawa', ?, ?, ?, ?, ?)",
            (snap_id, event_id, mid, line, side, odds, prob),
        )
    return snap_id


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(str(tmp_path / "odds.db"), isolation_level=None)
    init_schema(c)
    c.row_factory = sqlite3.Row
    return c


def test_with_coefficients_mutates_and_restores(db):
    original_margin = engine.ONEUP_FAVORITE_MARGIN
    with runner.with_coefficients({"ONEUP_FAVORITE_MARGIN": (0.9, 0.05)}):
        assert engine.ONEUP_FAVORITE_MARGIN == (0.9, 0.05)
    assert engine.ONEUP_FAVORITE_MARGIN == original_margin


def test_with_coefficients_restores_on_exception(db):
    original_margin = engine.ONEUP_FAVORITE_MARGIN
    with pytest.raises(RuntimeError):
        with runner.with_coefficients({"ONEUP_FAVORITE_MARGIN": (0.9, 0.05)}):
            raise RuntimeError("boom")
    assert engine.ONEUP_FAVORITE_MARGIN == original_margin


def test_run_simulation_writes_results_for_priced_snapshot(db, tmp_path):
    _seed_event_with_priced_snapshot(db, "E1")
    default = configs.load_default(db)
    run_id = runner.run_simulation(
        db, config=default,
        coverage="all",
        scope={"status": "upcoming", "country": "", "league": "", "date": "", "search": ""},
        csv_dir=tmp_path / "sim",
    )
    row = db.execute(
        "SELECT n_events, n_rows, csv_path FROM pricer_runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    assert row["n_events"] == 1
    assert row["n_rows"] == 1
    results = db.execute(
        "SELECT event_id, basis_used, our_p_home_2, our_2up_home_capped "
        "FROM pricer_results WHERE run_id = ?",
        (run_id,),
    ).fetchall()
    assert len(results) == 1
    assert results[0]["event_id"] == "E1"
    assert results[0]["basis_used"] == "bp"
    # Engine succeeded — non-null capped 2UP odds.
    assert results[0]["our_2up_home_capped"] is not None


def test_run_simulation_coverage_latest_emits_one_row_per_event(db, tmp_path):
    _seed_event_with_priced_snapshot(db, "E1")
    # Add a second snapshot for the same event so 'all' would emit 2 rows
    # and 'latest' must emit 1.
    cur = db.execute(
        "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, "
        "match_minute, score_home, score_away, fetch_status) "
        "VALUES ('2026-05-21T11:00:00Z', 'E1', 'betpawa', 'UPCOMING', "
        "NULL, NULL, NULL, 'ok')",
    )
    snap2 = cur.lastrowid
    for mid, line, side, odds, prob in [
        ("1x2_ft", 0.0, "home", 1.85, 0.54),
        ("1x2_ft", 0.0, "draw", 3.40, 0.29),
        ("1x2_ft", 0.0, "away", 4.20, 0.17),
        ("over_under_ft", 2.5, "over",  1.85, 0.55),
        ("over_under_ft", 2.5, "under", 1.95, 0.45),
    ]:
        db.execute(
            "INSERT INTO prices (snapshot_id, event_id, ts_utc, bookmaker, "
            "market_id, line, side, odds, probability) "
            "VALUES (?, 'E1', '2026-05-21T11:00:00Z', 'betpawa', ?, ?, ?, ?, ?)",
            (snap2, mid, line, side, odds, prob),
        )

    default = configs.load_default(db)
    run_id = runner.run_simulation(
        db, config=default, coverage="latest",
        scope={"status": "upcoming", "country": "", "league": "", "date": "", "search": ""},
        csv_dir=tmp_path / "sim",
    )
    rows = db.execute(
        "SELECT ts_utc FROM pricer_results WHERE run_id = ?", (run_id,),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["ts_utc"] == "2026-05-21T11:00:00Z"
