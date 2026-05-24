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
        regime="any", density="all",
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
        db, config=default, regime="any", density="latest",
        scope={"status": "upcoming", "country": "", "league": "", "date": "", "search": ""},
        csv_dir=tmp_path / "sim",
    )
    rows = db.execute(
        "SELECT ts_utc FROM pricer_results WHERE run_id = ?", (run_id,),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["ts_utc"] == "2026-05-21T11:00:00Z"


def test_run_simulation_skips_snapshot_with_zero_odds(db, tmp_path):
    """Live snapshots sometimes carry odds=0 for suspended selections.
    The engine's cap step would crash (1.0 / source_odds → ZeroDivisionError).
    The runner must drop those snapshots and keep going so one bad row
    doesn't kill an entire run."""
    # Seed one good event so we know the runner produces output.
    _seed_event_with_priced_snapshot(db, "GOOD")
    # Seed a second event whose 1x2 has odds=0 — engine input filter
    # should drop it OR the runner's try/except should catch it.
    db.execute(
        "INSERT INTO events (id, home, away, kickoff_utc) "
        "VALUES ('BAD', 'B-Home', 'B-Away', '2026-05-22T18:30:00Z')",
    )
    cur = db.execute(
        "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, "
        "match_minute, score_home, score_away, fetch_status) "
        "VALUES ('2026-05-21T12:00:00Z', 'BAD', 'betpawa', 'STARTED', "
        "1, 0, 0, 'ok')",
    )
    snap_id = cur.lastrowid
    bad_rows = [
        # All three 1x2 sides — one has odds=0 (suspended), so this whole
        # snapshot's 1x2 input is invalid and gets dropped.
        ("1x2_ft", 0.0, "home", 0.00, 0.54),
        ("1x2_ft", 0.0, "draw", 3.40, 0.29),
        ("1x2_ft", 0.0, "away", 4.20, 0.17),
        ("over_under_ft", 2.5, "over",  1.85, 0.55),
        ("over_under_ft", 2.5, "under", 1.95, 0.45),
        ("next_goal_ft", 1.0, "home", 1.85, 0.54),
        ("next_goal_ft", 1.0, "none", 8.50, 0.12),
        ("next_goal_ft", 1.0, "away", 3.50, 0.34),
    ]
    for mid, line, side, odds, prob in bad_rows:
        db.execute(
            "INSERT INTO prices (snapshot_id, event_id, ts_utc, bookmaker, "
            "market_id, line, side, odds, probability) "
            "VALUES (?, 'BAD', '2026-05-21T12:00:00Z', 'betpawa', ?, ?, ?, ?, ?)",
            (snap_id, mid, line, side, odds, prob),
        )

    default = configs.load_default(db)
    run_id = runner.run_simulation(
        db, config=default, regime="any", density="all",
        scope={"status": "", "country": "", "league": "", "date": "", "search": ""},
        csv_dir=tmp_path / "sim",
    )
    rows = db.execute(
        "SELECT event_id FROM pricer_results WHERE run_id = ?", (run_id,),
    ).fetchall()
    event_ids = {r["event_id"] for r in rows}
    # Run survived; only the good event produced a result.
    assert "GOOD" in event_ids
    assert "BAD" not in event_ids


def test_run_simulation_handles_many_snapshots_without_in_clause_limit(db, tmp_path):
    """SQLite caps the number of `?` placeholders in a single query
    (`SQLITE_LIMIT_VARIABLE_NUMBER`, 999 in older builds). The previous
    runner used `WHERE id IN (?, ?, ...)` across all selected snapshots
    which broke once Lorenzo ran the simulator across his ~17k-snapshot
    DB. Seed enough snapshots to be well over the limit and prove the
    run completes."""
    # Seed one event with 1500 snapshots (well over the 999 default).
    db.execute(
        "INSERT INTO events (id, home, away, kickoff_utc) "
        "VALUES ('BIG', 'A', 'B', '2026-05-22T18:30:00Z')",
    )
    from datetime import datetime, timedelta, timezone
    base = datetime(2026, 5, 21, 0, 0, tzinfo=timezone.utc)
    rows = []
    for i in range(1500):
        ts = (base + timedelta(seconds=i * 90)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows.append((ts,))
    db.executemany(
        "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, fetch_status) "
        "VALUES (?, 'BIG', 'betpawa', 'UPCOMING', 'ok')",
        rows,
    )

    default = configs.load_default(db)
    # Should NOT raise "too many SQL variables".
    run_id = runner.run_simulation(
        db, config=default, regime="any", density="all",
        scope={"status": "upcoming", "country": "", "league": "",
               "date": "", "search": ""},
        csv_dir=tmp_path / "sim",
    )
    summary = db.execute(
        "SELECT n_events, n_rows FROM pricer_runs WHERE id = ?", (run_id,),
    ).fetchone()
    # No prices were seeded so engine inputs are missing — n_rows=0
    # but the run completes (no exception).
    assert summary["n_rows"] == 0


def test_run_simulation_regime_density_orthogonal(db, tmp_path):
    """prematch + latest must pick the most recent UPCOMING tick per
    event, not the global head — which might be ENDED."""
    # Event with 3 snapshots: 2 prematch + 1 live (head).
    db.execute(
        "INSERT INTO events (id, home, away, kickoff_utc) "
        "VALUES ('M', 'A', 'B', '2026-05-22T18:30:00Z')",
    )
    inserts = [
        ("2026-05-22T17:00:00Z", "UPCOMING"),
        ("2026-05-22T17:30:00Z", "UPCOMING"),  # latest prematch
        ("2026-05-22T18:35:00Z", "STARTED"),
    ]
    snap_ids = {}
    for ts, status in inserts:
        cur = db.execute(
            "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, "
            "score_home, score_away, fetch_status) "
            "VALUES (?, 'M', 'betpawa', ?, 0, 0, 'ok')",
            (ts, status),
        )
        snap_ids[ts] = cur.lastrowid
        # Seed minimum engine inputs at each ts so the engine produces output.
        for mid, line, side, odds, prob in [
            ("1x2_ft", 0.0, "home", 1.85, 0.54),
            ("1x2_ft", 0.0, "draw", 3.40, 0.29),
            ("1x2_ft", 0.0, "away", 4.20, 0.17),
            ("over_under_ft", 2.5, "over",  1.85, 0.55),
            ("over_under_ft", 2.5, "under", 1.95, 0.45),
            ("next_goal_ft", 1.0, "home", 1.85, 0.54),
            ("next_goal_ft", 1.0, "none", 8.50, 0.12),
            ("next_goal_ft", 1.0, "away", 3.50, 0.34),
        ]:
            db.execute(
                "INSERT INTO prices (snapshot_id, event_id, ts_utc, bookmaker, "
                "market_id, line, side, odds, probability) "
                "VALUES (?, 'M', ?, 'betpawa', ?, ?, ?, ?, ?)",
                (snap_ids[ts], ts, mid, line, side, odds, prob),
            )

    default = configs.load_default(db)
    run_id = runner.run_simulation(
        db, config=default, regime="prematch", density="latest",
        scope={"country": "", "league": "", "date": "", "search": ""},
        csv_dir=tmp_path / "sim",
    )
    rows = db.execute(
        "SELECT ts_utc FROM pricer_results WHERE run_id = ?", (run_id,),
    ).fetchall()
    # Should pick exactly the latest UPCOMING tick.
    assert len(rows) == 1
    assert rows[0]["ts_utc"] == "2026-05-22T17:30:00Z"


def test_count_scope_returns_event_and_snapshot_counts(db):
    """count_scope powers the simulator page's live preview counter."""
    db.execute(
        "INSERT INTO events (id, home, away, kickoff_utc) "
        "VALUES ('CS', 'A', 'B', '2026-05-22T18:30:00Z')",
    )
    for ts, status in [
        ("2026-05-22T16:00:00Z", "UPCOMING"),
        ("2026-05-22T17:00:00Z", "UPCOMING"),
        ("2026-05-22T18:35:00Z", "STARTED"),
    ]:
        db.execute(
            "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, fetch_status) "
            "VALUES (?, 'CS', 'betpawa', ?, 'ok')",
            (ts, status),
        )
    n_ev, n_snap = runner.count_scope(
        db, "any", "all",
        {"country": "", "league": "", "date": "", "search": ""},
    )
    assert n_ev == 1 and n_snap == 3
    n_ev2, n_snap2 = runner.count_scope(
        db, "prematch", "latest",
        {"country": "", "league": "", "date": "", "search": ""},
    )
    assert n_ev2 == 1 and n_snap2 == 1
