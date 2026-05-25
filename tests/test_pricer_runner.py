import csv
import sqlite3
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


def _read_csv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


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


def test_run_simulation_writes_csv_for_priced_snapshot(db, tmp_path):
    _seed_event_with_priced_snapshot(db, "E1")
    default = configs.load_default(db)
    csv_path = tmp_path / "sim" / "run_0001.csv"
    n_events, n_rows = runner.run_simulation(
        db, config=default, regime="any", density="all",
        scope={"country": "", "league": "", "date": "", "search": ""},
        csv_path=csv_path,
    )
    assert n_events == 1
    assert n_rows == 1
    rows = _read_csv(csv_path)
    assert len(rows) == 1
    r = rows[0]
    assert r["event_id"] == "E1"
    assert r["home"] == "Home FC"
    assert r["basis_used"] == "bp"
    assert r["our_2up_home_capped"] not in ("", None)


def test_run_simulation_density_latest_emits_one_row_per_event(db, tmp_path):
    _seed_event_with_priced_snapshot(db, "E1")
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
    csv_path = tmp_path / "sim" / "run.csv"
    _, n_rows = runner.run_simulation(
        db, config=default, regime="any", density="latest",
        scope={"country": "", "league": "", "date": "", "search": ""},
        csv_path=csv_path,
    )
    assert n_rows == 1
    rows = _read_csv(csv_path)
    assert rows[0]["ts_utc"] == "2026-05-21T11:00:00Z"


def test_run_simulation_skips_snapshot_with_zero_odds(db, tmp_path):
    _seed_event_with_priced_snapshot(db, "GOOD")
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
    csv_path = tmp_path / "sim" / "run.csv"
    runner.run_simulation(
        db, config=default, regime="any", density="all",
        scope={"country": "", "league": "", "date": "", "search": ""},
        csv_path=csv_path,
    )
    event_ids = {r["event_id"] for r in _read_csv(csv_path)}
    assert "GOOD" in event_ids
    assert "BAD" not in event_ids


def test_run_simulation_handles_many_snapshots_without_in_clause_limit(db, tmp_path):
    """Regression: the old runner re-queried snapshots via `WHERE id IN
    (?, ?, …)` and blew SQLite's 999-variable limit on big scopes. The
    refactor inlines event meta directly into _select_ticks so no
    follow-up IN clause exists."""
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
    csv_path = tmp_path / "sim" / "big.csv"
    # No exception: handles 1500 ticks fine. No prices were seeded so
    # the engine deactivates and n_rows is 0 — what matters is no
    # OperationalError.
    n_ev, n_rows = runner.run_simulation(
        db, config=default, regime="any", density="all",
        scope={"country": "", "league": "", "date": "", "search": ""},
        csv_path=csv_path,
    )
    assert n_rows == 0


def test_run_simulation_regime_density_orthogonal(db, tmp_path):
    db.execute(
        "INSERT INTO events (id, home, away, kickoff_utc) "
        "VALUES ('M', 'A', 'B', '2026-05-22T18:30:00Z')",
    )
    inserts = [
        ("2026-05-22T17:00:00Z", "UPCOMING"),
        ("2026-05-22T17:30:00Z", "UPCOMING"),  # latest prematch
        ("2026-05-22T18:35:00Z", "STARTED"),
    ]
    for ts, status in inserts:
        cur = db.execute(
            "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, "
            "score_home, score_away, fetch_status) "
            "VALUES (?, 'M', 'betpawa', ?, 0, 0, 'ok')",
            (ts, status),
        )
        snap = cur.lastrowid
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
                (snap, ts, mid, line, side, odds, prob),
            )

    default = configs.load_default(db)
    csv_path = tmp_path / "sim" / "rd.csv"
    runner.run_simulation(
        db, config=default, regime="prematch", density="latest",
        scope={"country": "", "league": "", "date": "", "search": ""},
        csv_path=csv_path,
    )
    rows = _read_csv(csv_path)
    assert len(rows) == 1
    assert rows[0]["ts_utc"] == "2026-05-22T17:30:00Z"


def test_count_scope_returns_event_and_tick_counts(db):
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


def test_count_scope_filters_by_event_id_date_and_search(db):
    """The page's event picker (and the date/search inputs that feed it)
    narrow the run to a single event without touching the other UI
    state — verify the scope plumbing through count_scope."""
    db.execute(
        "INSERT INTO events (id, home, away, kickoff_utc) "
        "VALUES ('A', 'Liverpool', 'Arsenal', '2026-05-22T18:30:00Z')",
    )
    db.execute(
        "INSERT INTO events (id, home, away, kickoff_utc) "
        "VALUES ('B', 'Chelsea', 'Spurs', '2026-05-23T18:30:00Z')",
    )
    for ev_id in ("A", "B"):
        db.execute(
            "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, fetch_status) "
            "VALUES ('2026-05-22T16:00:00Z', ?, 'betpawa', 'UPCOMING', 'ok')",
            (ev_id,),
        )
    base = {"country": "", "league": "", "date": "", "search": ""}

    # event_id narrows to one match
    n_ev, _ = runner.count_scope(db, "any", "all", {**base, "event_id": "A"})
    assert n_ev == 1
    # date narrows by kickoff date
    n_ev, _ = runner.count_scope(db, "any", "all", {**base, "date": "2026-05-23"})
    assert n_ev == 1
    # search matches either home or away (case-insensitive)
    n_ev, _ = runner.count_scope(db, "any", "all", {**base, "search": "liverpool"})
    assert n_ev == 1
    n_ev, _ = runner.count_scope(db, "any", "all", {**base, "search": "SPURS"})
    assert n_ev == 1
    n_ev, _ = runner.count_scope(db, "any", "all", {**base, "search": "nope"})
    assert n_ev == 0


def test_run_simulation_calls_progress_callback(db, tmp_path):
    """on_progress fires at least once at start and once at end so the
    in-memory registry sees the run begin + finish."""
    _seed_event_with_priced_snapshot(db, "E1")
    default = configs.load_default(db)
    calls: list[tuple[int, int]] = []
    runner.run_simulation(
        db, config=default, regime="any", density="all",
        scope={"country": "", "league": "", "date": "", "search": ""},
        csv_path=tmp_path / "sim" / "p.csv",
        on_progress=lambda d, t: calls.append((d, t)),
    )
    # First call at (0, n_total), final call at (n_total, n_total).
    assert calls[0] == (0, 1)
    assert calls[-1] == (1, 1)


def test_run_simulation_dedupes_ticks_across_bookmakers(db, tmp_path):
    """Each (event_id, ts_utc) tick has up to 4 snapshot rows (one per
    bookmaker). The runner must produce ONE CSV row per tick, not one
    per snapshot — the engine output is per tick, not per book."""
    # Seed 4 per-book snapshots at the SAME (event, ts) tick. Only BP
    # gets a full set of priced inputs; the others are empty.
    db.execute(
        "INSERT INTO events (id, home, away, kickoff_utc) "
        "VALUES ('D', 'A', 'B', '2026-05-22T18:30:00Z')",
    )
    bp_snap = None
    for bm in ("betpawa", "sportybet", "bet9ja", "betway"):
        cur = db.execute(
            "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, "
            "score_home, score_away, fetch_status) "
            "VALUES ('2026-05-22T18:00:00Z', 'D', ?, 'UPCOMING', 0, 0, 'ok')",
            (bm,),
        )
        if bm == "betpawa":
            bp_snap = cur.lastrowid
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
            "VALUES (?, 'D', '2026-05-22T18:00:00Z', 'betpawa', ?, ?, ?, ?, ?)",
            (bp_snap, mid, line, side, odds, prob),
        )

    default = configs.load_default(db)
    csv_path = tmp_path / "sim" / "dd.csv"
    _, n_rows = runner.run_simulation(
        db, config=default, regime="any", density="all",
        scope={"country": "", "league": "", "date": "", "search": ""},
        csv_path=csv_path,
    )
    # One tick → one CSV row, NOT four (one per bookmaker).
    assert n_rows == 1
    assert len(_read_csv(csv_path)) == 1


def test_ev_helper_blank_when_either_factor_missing():
    """`_ev` must yield None (→ blank CSV cell) when either the engine
    probability or the bookmaker odds is missing, NOT a misleading
    `-1.0` from `None * 0 - 1`."""
    assert runner._ev(None, 2.0) is None
    assert runner._ev(0.5, None) is None
    assert runner._ev(None, None) is None


def test_ev_helper_computes_expected_value():
    assert runner._ev(0.5, 2.0) == pytest.approx(0.0)
    assert runner._ev(0.6, 2.0) == pytest.approx(0.2)
    assert runner._ev(0.4, 2.0) == pytest.approx(-0.2)


def test_run_simulation_emits_ev_for_bp_and_sb(db, tmp_path):
    """Each (BP, SB) × (1UP, 2UP) × (home, away) selection that has
    both an engine prob and a book quote must yield `prob * odds - 1`.
    Sides where the book didn't quote stay blank."""
    snap_id = _seed_event_with_priced_snapshot(db, "EV")
    # BP and SB both quote 1UP home + away and 2UP home; neither quotes
    # 2UP away → those two EV cells must stay blank.
    bookmaker_quotes = [
        # (bookmaker, market_id, side, odds)
        ("betpawa",   "1x2_1up_ft", "home", 1.20),
        ("betpawa",   "1x2_1up_ft", "away", 4.50),
        ("betpawa",   "1x2_2up_ft", "home", 1.55),
        ("sportybet", "1x2_1up_ft", "home", 1.18),
        ("sportybet", "1x2_1up_ft", "away", 4.80),
        ("sportybet", "1x2_2up_ft", "home", 1.60),
    ]
    for bm, mid, side, odds in bookmaker_quotes:
        # Need a snapshot row for non-BP bookmakers so the join picks
        # up their prices at the same tick.
        if bm != "betpawa":
            cur = db.execute(
                "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, fetch_status) "
                "VALUES ('2026-05-21T10:00:00Z', 'EV', ?, 'UPCOMING', 'ok')",
                (bm,),
            )
            bm_snap = cur.lastrowid
        else:
            bm_snap = snap_id
        db.execute(
            "INSERT INTO prices (snapshot_id, event_id, ts_utc, bookmaker, "
            "market_id, line, side, odds, probability) "
            "VALUES (?, 'EV', '2026-05-21T10:00:00Z', ?, ?, 0.0, ?, ?, NULL)",
            (bm_snap, bm, mid, side, odds),
        )

    default = configs.load_default(db)
    csv_path = tmp_path / "sim" / "ev.csv"
    runner.run_simulation(
        db, config=default, regime="any", density="all",
        scope={"country": "", "league": "", "date": "", "search": ""},
        csv_path=csv_path,
    )
    rows = _read_csv(csv_path)
    assert len(rows) == 1
    row = rows[0]

    p_h1 = float(row["our_p_home_1"])
    p_a1 = float(row["our_p_away_1"])
    p_h2 = float(row["our_p_home_2"])
    # 2UP away gets priced by the engine even when no book quotes.
    # We don't need it for this assertion since SB+BP didn't quote it.

    assert float(row["bp_1up_home_ev"]) == pytest.approx(p_h1 * 1.20 - 1, abs=1e-9)
    assert float(row["bp_1up_away_ev"]) == pytest.approx(p_a1 * 4.50 - 1, abs=1e-9)
    assert float(row["bp_2up_home_ev"]) == pytest.approx(p_h2 * 1.55 - 1, abs=1e-9)
    assert float(row["sb_1up_home_ev"]) == pytest.approx(p_h1 * 1.18 - 1, abs=1e-9)
    assert float(row["sb_1up_away_ev"]) == pytest.approx(p_a1 * 4.80 - 1, abs=1e-9)
    assert float(row["sb_2up_home_ev"]) == pytest.approx(p_h2 * 1.60 - 1, abs=1e-9)

    # Unquoted 2UP away → blank EV (not 0, not -1).
    assert row["bp_2up_away_ev"] == ""
    assert row["sb_2up_away_ev"] == ""


def test_run_simulation_emits_bp_sb_probabilities(db, tmp_path):
    """BP and SB store a devigged probability alongside each 1UP/2UP
    quote; the CSV must surface those in the matching `*_p_*` columns
    so the reader can compare OUR vs each book's view per selection."""
    snap_id = _seed_event_with_priced_snapshot(db, "P")
    # BP + SB both quote 1UP/2UP with explicit devigged probability.
    quotes = [
        ("betpawa",   "1x2_1up_ft", "home", 1.20, 0.83),
        ("betpawa",   "1x2_2up_ft", "home", 1.55, 0.64),
        ("sportybet", "1x2_1up_ft", "home", 1.18, 0.82),
        ("sportybet", "1x2_2up_ft", "away", 4.80, 0.20),
    ]
    for bm, mid, side, odds, prob in quotes:
        if bm != "betpawa":
            cur = db.execute(
                "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, fetch_status) "
                "VALUES ('2026-05-21T10:00:00Z', 'P', ?, 'UPCOMING', 'ok')",
                (bm,),
            )
            bm_snap = cur.lastrowid
        else:
            bm_snap = snap_id
        db.execute(
            "INSERT INTO prices (snapshot_id, event_id, ts_utc, bookmaker, "
            "market_id, line, side, odds, probability) "
            "VALUES (?, 'P', '2026-05-21T10:00:00Z', ?, ?, 0.0, ?, ?, ?)",
            (bm_snap, bm, mid, side, odds, prob),
        )

    default = configs.load_default(db)
    csv_path = tmp_path / "sim" / "p.csv"
    runner.run_simulation(
        db, config=default, regime="any", density="all",
        scope={"country": "", "league": "", "date": "", "search": ""},
        csv_path=csv_path,
    )
    rows = _read_csv(csv_path)
    assert len(rows) == 1
    row = rows[0]
    # Bookmaker probabilities surface verbatim from the prices table.
    assert float(row["bp_p_1up_home"]) == pytest.approx(0.83)
    assert float(row["bp_p_2up_home"]) == pytest.approx(0.64)
    assert float(row["sb_p_1up_home"]) == pytest.approx(0.82)
    assert float(row["sb_p_2up_away"]) == pytest.approx(0.20)
    # Sides the books didn't quote stay blank — no false zeros.
    assert row["bp_p_1up_away"] == ""
    assert row["sb_p_1up_away"] == ""


def test_run_simulation_emits_simulated_capped_ev(db, tmp_path):
    """The OUR `*_capped_ev` columns must equal `our_prob * our_capped - 1`
    — i.e. the engine's embedded margin per selection. When the engine
    deactivates a side, the corresponding `*_capped_ev` stays blank."""
    _seed_event_with_priced_snapshot(db, "M")
    default = configs.load_default(db)
    csv_path = tmp_path / "sim" / "m.csv"
    runner.run_simulation(
        db, config=default, regime="any", density="all",
        scope={"country": "", "league": "", "date": "", "search": ""},
        csv_path=csv_path,
    )
    rows = _read_csv(csv_path)
    assert len(rows) == 1
    r = rows[0]
    p_h1 = float(r["our_p_home_1"])
    cap_1h = float(r["our_1up_home_capped"])
    assert float(r["our_1up_home_capped_ev"]) == pytest.approx(p_h1 * cap_1h - 1, abs=1e-9)

    p_a2 = float(r["our_p_away_2"])
    cap_2a = float(r["our_2up_away_capped"])
    assert float(r["our_2up_away_capped_ev"]) == pytest.approx(p_a2 * cap_2a - 1, abs=1e-9)


def test_run_simulation_blanks_capped_ev_when_side_settled(db, tmp_path):
    """At score 1-0 the engine deactivates home 1UP (current leader).
    Both `our_p_home_1` and `our_1up_home_capped` go blank, so
    `our_1up_home_capped_ev` must also be blank — never a spurious -1."""
    db.execute(
        "INSERT INTO events (id, home, away, kickoff_utc) "
        "VALUES ('LV', 'H', 'A', '2026-05-22T18:30:00Z')",
    )
    cur = db.execute(
        "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, "
        "score_home, score_away, fetch_status) "
        "VALUES ('2026-05-22T19:00:00Z', 'LV', 'betpawa', 'STARTED', 1, 0, 'ok')",
    )
    snap_id = cur.lastrowid
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
            "VALUES (?, 'LV', '2026-05-22T19:00:00Z', 'betpawa', ?, ?, ?, ?, ?)",
            (snap_id, mid, line, side, odds, prob),
        )

    default = configs.load_default(db)
    csv_path = tmp_path / "sim" / "settled.csv"
    runner.run_simulation(
        db, config=default, regime="any", density="all",
        scope={"country": "", "league": "", "date": "", "search": ""},
        csv_path=csv_path,
    )
    rows = _read_csv(csv_path)
    assert len(rows) == 1
    r = rows[0]
    assert r["our_p_home_1"] == ""
    assert r["our_1up_home_capped"] == ""
    assert r["our_1up_home_capped_ev"] == ""


def test_run_simulation_blanks_ev_when_engine_side_settled(db, tmp_path):
    """A side that the engine deactivates (e.g. home leading 1-0 makes
    home_1up_prob = None) must surface a blank EV cell even when the
    bookmaker did quote that side — multiplying by `None` would crash;
    silently substituting 0 would mislead."""
    db.execute(
        "INSERT INTO events (id, home, away, kickoff_utc) "
        "VALUES ('LEAD', 'H', 'A', '2026-05-22T18:30:00Z')",
    )
    cur = db.execute(
        "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, "
        "score_home, score_away, fetch_status) "
        "VALUES ('2026-05-22T19:00:00Z', 'LEAD', 'betpawa', 'STARTED', 1, 0, 'ok')",
    )
    snap_id = cur.lastrowid
    for mid, line, side, odds, prob in [
        ("1x2_ft", 0.0, "home", 1.85, 0.54),
        ("1x2_ft", 0.0, "draw", 3.40, 0.29),
        ("1x2_ft", 0.0, "away", 4.20, 0.17),
        ("over_under_ft", 2.5, "over",  1.85, 0.55),
        ("over_under_ft", 2.5, "under", 1.95, 0.45),
        # No FTTS: not required by trailing-team path at 1-0.
    ]:
        db.execute(
            "INSERT INTO prices (snapshot_id, event_id, ts_utc, bookmaker, "
            "market_id, line, side, odds, probability) "
            "VALUES (?, 'LEAD', '2026-05-22T19:00:00Z', 'betpawa', ?, ?, ?, ?, ?)",
            (snap_id, mid, line, side, odds, prob),
        )
    # BP quotes home 1UP even though the engine will deactivate it
    # (home is currently leading by 1). The EV cell must be blank.
    db.execute(
        "INSERT INTO prices (snapshot_id, event_id, ts_utc, bookmaker, "
        "market_id, line, side, odds, probability) "
        "VALUES (?, 'LEAD', '2026-05-22T19:00:00Z', 'betpawa', "
        "       '1x2_1up_ft', 0.0, 'home', 1.05, NULL)",
        (snap_id,),
    )

    default = configs.load_default(db)
    csv_path = tmp_path / "sim" / "lead.csv"
    runner.run_simulation(
        db, config=default, regime="any", density="all",
        scope={"country": "", "league": "", "date": "", "search": ""},
        csv_path=csv_path,
    )
    rows = _read_csv(csv_path)
    assert len(rows) == 1
    row = rows[0]
    # Engine deactivated home 1UP → its probability is blank → EV blank
    # despite the BP odds being present in the CSV.
    assert row["our_p_home_1"] == ""
    assert row["bp_1up_home_odds"] == "1.05"
    assert row["bp_1up_home_ev"] == ""


def test_run_simulation_deactivates_1up_for_swung_back_score(db, tmp_path):
    """Event 35124382 reproduction: a match that went 1-0 then equalised
    to 1-1 must NOT re-price home 1UP at the level state — it already
    triggered at 1-0. Without max_home_lead the engine sees only the
    current diff (=0) and uses the level-score branch, re-pricing home
    1UP. With the fix wired through, home 1UP stays None.
    """
    # Reuse the priced-event seeder for both ticks (1-0 then 1-1).
    db.execute(
        "INSERT INTO events (id, home, away, kickoff_utc) "
        "VALUES ('SW', 'H', 'A', '2026-05-22T18:30:00Z')",
    )
    base_prices = [
        ("1x2_ft", 0.0, "home", 1.85, 0.54),
        ("1x2_ft", 0.0, "draw", 3.40, 0.29),
        ("1x2_ft", 0.0, "away", 4.20, 0.17),
        ("over_under_ft", 2.5, "over",  1.85, 0.55),
        ("over_under_ft", 2.5, "under", 1.95, 0.45),
        ("next_goal_ft", 1.0, "home", 1.85, 0.54),
        ("next_goal_ft", 1.0, "none", 8.50, 0.12),
        ("next_goal_ft", 1.0, "away", 3.50, 0.34),
    ]
    for ts, sh, sa in [
        ("2026-05-22T19:00:00Z", 1, 0),  # home took the lead
        ("2026-05-22T19:15:00Z", 1, 1),  # equaliser → current diff = 0
    ]:
        cur = db.execute(
            "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, "
            "score_home, score_away, fetch_status) "
            "VALUES (?, 'SW', 'betpawa', 'STARTED', ?, ?, 'ok')",
            (ts, sh, sa),
        )
        snap_id = cur.lastrowid
        for mid, line, side, odds, prob in base_prices:
            db.execute(
                "INSERT INTO prices (snapshot_id, event_id, ts_utc, bookmaker, "
                "market_id, line, side, odds, probability) "
                "VALUES (?, 'SW', ?, 'betpawa', ?, ?, ?, ?, ?)",
                (snap_id, ts, mid, line, side, odds, prob),
            )

    default = configs.load_default(db)
    csv_path = tmp_path / "sim" / "sw.csv"
    runner.run_simulation(
        db, config=default, regime="live", density="all",
        scope={"country": "", "league": "", "date": "", "search": ""},
        csv_path=csv_path,
    )
    rows = _read_csv(csv_path)
    rows.sort(key=lambda r: r["ts_utc"])
    # Tick 1 at 1-0: home leads NOW → home 1UP deactivated by the
    # current-score path (existing behaviour, unchanged).
    assert rows[0]["our_1up_home_capped"] == ""
    # Tick 2 at 1-1: home does NOT lead now, but already led earlier.
    # Without history awareness this priced; with the fix it stays empty.
    assert rows[1]["our_1up_home_capped"] == ""
    # Away 1UP at 1-1 must still be priced — away has never led.
    assert rows[1]["our_1up_away_capped"] != ""


def test_v1_runner_marks_rows_engines_v1(db, tmp_path):
    """A V1-only run must record `engines="v1"` so downstream tooling
    can filter cleanly when the same CSV mixes engines (future). The
    v2 cells stay blank in a V1-only run."""
    _seed_event_with_priced_snapshot(db, "EVMARK")
    default = configs.load_default(db)
    csv_path = tmp_path / "sim" / "v1.csv"
    runner.run_simulation(
        db, config=default, regime="any", density="all",
        scope={"country": "", "league": "", "date": "", "search": ""},
        csv_path=csv_path,
    )
    rows = _read_csv(csv_path)
    assert rows and rows[0]["engines"] == "v1"
    assert rows[0]["v2_p_home_1"] == ""
    assert rows[0]["v2_our_2up_away_capped_ev"] == ""
