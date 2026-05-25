"""Tests for the dual-engine V1+V2 runner."""

import csv
import sqlite3
from pathlib import Path

import pytest

from odds_scraper.db_schema import init_schema
from odds_scraper.pricer import configs, runner, runner_v2


def _seed_event_with_priced_snapshot(conn, event_id):
    conn.execute(
        "INSERT INTO events (id, home, away, kickoff_utc) "
        "VALUES (?, 'Home FC', 'Away FC', '2026-05-22T18:30:00Z')",
        (event_id,),
    )
    cur = conn.execute(
        "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, fetch_status) "
        "VALUES ('2026-05-21T10:00:00Z', ?, 'betpawa', 'UPCOMING', 'ok')",
        (event_id,),
    )
    snap_id = cur.lastrowid
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
        conn.execute(
            "INSERT INTO prices (snapshot_id, event_id, ts_utc, bookmaker, "
            "market_id, line, side, odds, probability) "
            "VALUES (?, ?, '2026-05-21T10:00:00Z', 'betpawa', ?, ?, ?, ?, ?)",
            (snap_id, event_id, mid, line, side, odds, prob),
        )


@pytest.fixture
def db(tmp_path):
    c = sqlite3.connect(str(tmp_path / "odds.db"), isolation_level=None)
    init_schema(c)
    c.row_factory = sqlite3.Row
    return c


def _read_csv(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


_BASE_SCOPE = {"country": "", "league": "", "date": "", "search": ""}


def test_dual_runner_v1_only_matches_existing_runner(db, tmp_path):
    """`run_simulation_dual(engines=('v1',))` must produce a CSV with
    the same V1 cell values that `run_simulation` produces."""
    _seed_event_with_priced_snapshot(db, "E")
    default = configs.load_default(db)
    p_dual = tmp_path / "sim" / "dual_v1.csv"
    p_old  = tmp_path / "sim" / "old_v1.csv"
    runner_v2.run_simulation_dual(
        db, config=default, regime="any", density="all",
        scope=_BASE_SCOPE,
        csv_path=p_dual, engines=("v1",),
    )
    runner.run_simulation(
        db, config=default, regime="any", density="all",
        scope=_BASE_SCOPE,
        csv_path=p_old,
    )
    r_dual = _read_csv(p_dual)
    r_old  = _read_csv(p_old)
    assert len(r_dual) == len(r_old) == 1
    for col in ("our_p_home_1", "our_1up_home_capped", "bp_p_1up_home"):
        assert r_dual[0][col] == r_old[0][col]
    assert r_dual[0]["engines"] == "v1"
    # v2 cells stay blank when only v1 selected.
    assert r_dual[0]["v2_p_home_1"] == ""


def test_dual_runner_v2_only_fills_v2_blanks_v1(db, tmp_path):
    _seed_event_with_priced_snapshot(db, "E")
    default = configs.load_default(db)
    p = tmp_path / "sim" / "v2_only.csv"
    runner_v2.run_simulation_dual(
        db, config=default, regime="any", density="all",
        scope=_BASE_SCOPE,
        csv_path=p, engines=("v2",),
    )
    rows = _read_csv(p)
    assert len(rows) == 1
    assert rows[0]["engines"] == "v2"
    assert rows[0]["v2_p_home_1"] != ""
    # V1 cells blank.
    assert rows[0]["our_p_home_1"] == ""
    assert rows[0]["our_1up_home_capped"] == ""


def test_dual_runner_both_fills_v1_and_v2_blocks(db, tmp_path):
    _seed_event_with_priced_snapshot(db, "E")
    default = configs.load_default(db)
    p = tmp_path / "sim" / "both.csv"
    runner_v2.run_simulation_dual(
        db, config=default, regime="any", density="all",
        scope=_BASE_SCOPE,
        csv_path=p, engines=("v1", "v2"),
    )
    rows = _read_csv(p)
    assert len(rows) == 1
    assert rows[0]["engines"] == "v1,v2"
    assert rows[0]["our_p_home_1"] != ""
    assert rows[0]["v2_p_home_1"] != ""


def test_dual_runner_progress_callback_fires_start_and_end(db, tmp_path):
    _seed_event_with_priced_snapshot(db, "E")
    default = configs.load_default(db)
    calls = []
    runner_v2.run_simulation_dual(
        db, config=default, regime="any", density="all",
        scope=_BASE_SCOPE,
        csv_path=tmp_path / "sim" / "p.csv",
        engines=("v1", "v2"),
        on_progress=lambda d, t: calls.append((d, t)),
    )
    assert calls[0] == (0, 1)
    assert calls[-1] == (1, 1)


def test_dual_runner_rejects_empty_engines(db, tmp_path):
    default = configs.load_default(db)
    with pytest.raises(ValueError, match="at least one engine"):
        runner_v2.run_simulation_dual(
            db, config=default, regime="any", density="all",
            scope=_BASE_SCOPE,
            csv_path=tmp_path / "sim" / "x.csv",
            engines=(),
        )


def test_dual_runner_rejects_unknown_engine(db, tmp_path):
    default = configs.load_default(db)
    with pytest.raises(ValueError, match="unknown engine"):
        runner_v2.run_simulation_dual(
            db, config=default, regime="any", density="all",
            scope=_BASE_SCOPE,
            csv_path=tmp_path / "sim" / "x.csv",
            engines=("v3",),
        )
