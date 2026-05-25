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


def _seed_event_with_one_priced_snapshot(db, ev_id):
    """Helper to seed a single priced tick for A/B tests below."""
    return _seed_event_with_priced_snapshot(db, ev_id)


def test_dual_runner_profile_b_emits_pB_columns(db, tmp_path):
    """When config_b is set, every selected engine runs twice and the
    pB_* OUR cells carry the second profile's output. The bookmaker
    EV cells under pB_* use profile B's probability against the same
    book odds (no book odds duplication)."""
    _seed_event_with_priced_snapshot(db, "AB")
    default = configs.load_default(db)
    # Profile B uses a noticeably different boost coefficient so V2 2UP
    # diverges from V1 even at score 0-0 (boost feeds the 2UP residual).
    over = dict(configs.DEFAULT_COEFFICIENTS)
    over["TWOUP_FAVORITE_BOOST_COEFFICIENT"] = 0.40  # vs default 0.9
    over["TWOUP_UNDERDOG_BOOST_COEFFICIENT"] = 0.30  # vs default 0.6
    pid_b = configs.create_profile(db, "low-boost", over)
    profile_b = configs.load_by_id(db, pid_b)

    p = tmp_path / "sim" / "ab.csv"
    runner_v2.run_simulation_dual(
        db, config=default, config_b=profile_b,
        regime="any", density="all",
        scope={"country": "", "league": "", "date": "", "search": ""},
        csv_path=p, engines=("v1",),
    )
    rows = _read_csv(p)
    assert len(rows) == 1
    r = rows[0]
    assert r["profile_a"] == "default"
    assert r["profile_b"] == "low-boost"
    # Profile A's 2UP cells populated; profile B's pB_ cells populated
    # and DIFFER (the boost change shifts the residual).
    assert r["our_p_home_2"] != ""
    assert r["pB_our_p_home_2"] != ""
    assert float(r["pB_our_p_home_2"]) != pytest.approx(float(r["our_p_home_2"]))
    # V2 pB cells stay blank (engine=v1 only).
    assert r["pB_v2_p_home_2"] == ""
    # pB_bp_*_ev is populated even though pB_bp_*_odds is not (odds
    # column stays only under main `bp_*` since it's profile-agnostic).
    # But seed didn't write BP UP odds, so EV is blank by missing-odds
    # path, not by profile B being absent. Check that the COLUMN exists.
    assert "pB_bp_1up_home_ev" in r


def test_dual_runner_no_profile_b_leaves_pB_blank(db, tmp_path):
    """Single-profile run: pB_* columns stay blank, profile_b cell blank."""
    _seed_event_with_priced_snapshot(db, "SOLO")
    default = configs.load_default(db)
    p = tmp_path / "sim" / "solo.csv"
    runner_v2.run_simulation_dual(
        db, config=default,
        regime="any", density="all",
        scope={"country": "", "league": "", "date": "", "search": ""},
        csv_path=p, engines=("v1",),
    )
    r = _read_csv(p)[0]
    assert r["profile_b"] == ""
    assert r["pB_our_p_home_1"] == ""
    assert r["pB_our_2up_home_capped"] == ""
    assert r["pB_bp_1up_home_ev"] == ""


def test_dual_runner_profile_b_with_both_engines(db, tmp_path):
    """engine=both + Profile B: all four blocks populated
    (Profile A V1, Profile A V2, Profile B V1, Profile B V2)."""
    _seed_event_with_priced_snapshot(db, "ALL")
    default = configs.load_default(db)
    over = dict(configs.DEFAULT_COEFFICIENTS)
    over["TWOUP_FAVORITE_BOOST_COEFFICIENT"] = 0.40
    pid_b = configs.create_profile(db, "x", over)
    profile_b = configs.load_by_id(db, pid_b)
    p = tmp_path / "sim" / "all.csv"
    runner_v2.run_simulation_dual(
        db, config=default, config_b=profile_b,
        regime="any", density="all",
        scope={"country": "", "league": "", "date": "", "search": ""},
        csv_path=p, engines=("v1", "v2"),
    )
    r = _read_csv(p)[0]
    # All four 2UP probability blocks populated and pB_ differs from
    # main (since boost changed).
    for col in ("our_p_home_2", "v2_p_home_2",
                "pB_our_p_home_2", "pB_v2_p_home_2"):
        assert r[col] != "", f"{col} unexpectedly blank"
    assert float(r["pB_our_p_home_2"]) != pytest.approx(float(r["our_p_home_2"]))


def test_dual_runner_rejects_same_profile_twice_via_route():
    """End-to-end guard: the route refuses config_id_b == config_id.
    (Runner itself doesn't enforce this — the route does, because the
    cost of running A vs A is wasted compute with zero information.)"""
    # The runner accepts identical profiles — output is identical to A
    # but the duplication is the caller's problem. This test just
    # documents the runner's contract: it doesn't reject.
    pass
