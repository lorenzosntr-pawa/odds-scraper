import sqlite3
import pytest
from odds_scraper.db_schema import init_schema
from odds_scraper.web import export_service as ex


def _conn():
    c = sqlite3.connect(":memory:", isolation_level=None)
    c.row_factory = sqlite3.Row
    init_schema(c)
    return c


def _seed_event(c, eid="E1", home="A", away="B",
                country=("ng", "Nigeria"), league=("npl", "NPL")):
    c.execute(
        "INSERT INTO events (id, home, away, kickoff_utc, country_id, "
        "country_name, league_id, league_name) VALUES (?,?,?,?,?,?,?,?)",
        (eid, home, away, "2026-05-22T18:00:00Z", country[0], country[1],
         league[0], league[1]))


def _seed_tick(c, eid, ts, status, *, book="betpawa", minute=0,
               sh=0, sa=0, prices=()):
    """Insert one snapshot + its prices. prices = [(market_id,line,side,odds,prob)]."""
    cur = c.execute(
        "INSERT INTO snapshots (event_id, bookmaker, ts_utc, status, "
        "match_minute, score_home, score_away, fetch_status) "
        "VALUES (?,?,?,?,?,?,?, 'ok')",
        (eid, book, ts, status, minute, sh, sa))
    snap_id = cur.lastrowid
    for market_id, line, side, odds, prob in prices:
        c.execute(
            "INSERT INTO prices (snapshot_id, event_id, bookmaker, ts_utc, "
            "market_id, line, side, odds, probability) VALUES (?,?,?,?,?,?,?,?,?)",
            (snap_id, eid, book, ts, market_id, line, side, odds, prob))
    return snap_id


def test_select_ticks_regime_filters_status():
    c = _conn(); _seed_event(c)
    _seed_tick(c, "E1", "2026-05-22T17:00:00Z", "UPCOMING")
    _seed_tick(c, "E1", "2026-05-22T18:10:00Z", "STARTED")
    pre = ex.select_ticks(c, "prematch", "all", {})
    live = ex.select_ticks(c, "live", "all", {})
    allr = ex.select_ticks(c, "any", "all", {})
    assert [t["ts_utc"] for t in pre] == ["2026-05-22T17:00:00Z"]
    assert [t["ts_utc"] for t in live] == ["2026-05-22T18:10:00Z"]
    assert len(allr) == 2


def test_select_ticks_latest_picks_last_ts_per_event():
    c = _conn(); _seed_event(c)
    _seed_tick(c, "E1", "2026-05-22T18:00:00Z", "STARTED")
    _seed_tick(c, "E1", "2026-05-22T18:30:00Z", "STARTED")
    latest = ex.select_ticks(c, "any", "latest", {})
    assert [t["ts_utc"] for t in latest] == ["2026-05-22T18:30:00Z"]


def test_select_ticks_scope_country_and_invalid_regime():
    c = _conn(); _seed_event(c)
    _seed_tick(c, "E1", "2026-05-22T18:00:00Z", "STARTED")
    assert ex.select_ticks(c, "any", "all", {"country": "zz"}) == []
    assert len(ex.select_ticks(c, "any", "all", {"country": "ng"})) == 1
    with pytest.raises(ValueError):
        ex.select_ticks(c, "bogus", "all", {})


def test_select_ticks_search_escapes_like_metachars():
    c = _conn()
    _seed_event(c, eid="E1", home="A_B", away="X")
    _seed_event(c, eid="E2", home="AZB", away="Y")
    _seed_tick(c, "E1", "2026-05-22T18:00:00Z", "STARTED")
    _seed_tick(c, "E2", "2026-05-22T18:00:00Z", "STARTED")
    # underscore must be literal, not a single-char wildcard
    out = ex.select_ticks(c, "any", "all", {"search": "a_b"})
    ids = {t["event_id"] for t in out}
    assert ids == {"E1"}   # only the literal "A_B" matches, not "AZB"


def test_collapse_onchange_uses_only_selected_markets():
    c = _conn(); _seed_event(c)
    # tick1 and tick2: 1x2 odds identical; an over_under line changes between them
    _seed_tick(c, "E1", "2026-05-22T18:00:00Z", "STARTED", prices=[
        ("1x2_ft", 0.0, "home", 1.80, None),
        ("over_under_ft", 2.5, "over", 1.90, None)])
    _seed_tick(c, "E1", "2026-05-22T18:01:00Z", "STARTED", prices=[
        ("1x2_ft", 0.0, "home", 1.80, None),
        ("over_under_ft", 2.5, "over", 2.10, None)])  # OU moved, 1x2 didn't
    ticks = ex.select_ticks(c, "any", "onchange", {})
    # Selecting ONLY 1x2: second tick is unchanged -> dropped.
    kept_1x2 = ex.collapse_onchange(c, ticks, [("1x2_ft", 0.0)])
    assert [t["ts_utc"] for t in kept_1x2] == ["2026-05-22T18:00:00Z"]
    # Selecting the OU line: it changed -> both kept.
    kept_ou = ex.collapse_onchange(c, ticks, [("over_under_ft", 2.5)])
    assert len(kept_ou) == 2
    # markets=None means ALL markets -> the OU change keeps both
    kept_all = ex.collapse_onchange(c, ticks, None)
    assert len(kept_all) == 2


def _ticks(eid, tss):
    return [{"event_id": eid, "ts_utc": ts} for ts in tss]


def test_limit_first_last_per_event():
    ts = [f"2026-05-22T18:0{i}:00Z" for i in range(5)]  # 5 ticks
    rows = _ticks("E1", ts)
    assert [r["ts_utc"] for r in ex.limit_first_last(rows, 2, 0)] == ts[:2]
    assert [r["ts_utc"] for r in ex.limit_first_last(rows, 0, 2)] == ts[-2:]
    # first 2 + last 2 = union, no dupes, original order
    assert [r["ts_utc"] for r in ex.limit_first_last(rows, 2, 2)] == [ts[0], ts[1], ts[3], ts[4]]
    assert [r["ts_utc"] for r in ex.limit_first_last(rows, 0, 0)] == ts  # no-op


def test_limit_first_last_independent_per_event():
    ts_a = [f"2026-05-22T18:0{i}:00Z" for i in range(4)]
    ts_b = [f"2026-05-22T19:0{i}:00Z" for i in range(4)]
    rows = _ticks("E1", ts_a) + _ticks("E2", ts_b)
    out = ex.limit_first_last(rows, 1, 1)
    # first+last per event, union; each event keeps its own first & last
    assert [r["ts_utc"] for r in out if r["event_id"] == "E1"] == [ts_a[0], ts_a[-1]]
    assert [r["ts_utc"] for r in out if r["event_id"] == "E2"] == [ts_b[0], ts_b[-1]]


def test_csv_safe_escapes_formula_chars():
    assert ex.csv_safe("=cmd()") == "'=cmd()"
    assert ex.csv_safe("+1") == "'+1"
    assert ex.csv_safe("@x") == "'@x"
    assert ex.csv_safe("-7") == "'-7"
    assert ex.csv_safe("FC -Home") == "FC -Home"   # dash not leading -> untouched
    assert ex.csv_safe(1.85) == 1.85               # non-str passthrough
    assert ex.csv_safe(None) is None


def test_iter_long_rows_real_prices():
    c = _conn(); _seed_event(c)
    _seed_tick(c, "E1", "2026-05-22T18:00:00Z", "STARTED", minute=10, prices=[
        ("1x2_ft", 0.0, "home", 1.80, 0.55),
        ("1x2_ft", 0.0, "away", 4.20, 0.20)])
    ticks = ex.select_ticks(c, "any", "all", {})
    rows = list(ex.iter_long_rows(c, ticks, markets=[("1x2_ft", 0.0)],
                                  books=None, sim_engines=()))
    assert len(rows) == 2
    r = next(r for r in rows if r["side"] == "home")
    assert r["event_id"] == "E1" and r["bookmaker"] == "betpawa"
    assert r["market_id"] == "1x2_ft" and r["odds"] == 1.80
    assert r["probability"] == 0.55
    assert r["is_simulated"] == 0 and r["engine"] == ""
    assert r["country_name"] == "Nigeria" and r["league_name"] == "NPL"
    assert r["status"] == "STARTED" and r["match_minute"] == 10
    # every row has exactly the LONG_COLUMNS keys
    assert set(ex.LONG_COLUMNS) == set(r.keys())


def _seed_sim(c, eid, ts):
    c.execute(
        "INSERT INTO pricer_live_results (event_id, ts_utc, basis_used, "
        "v3_1up_home_capped, v3_1up_away_capped, v3_p_home_1, v3_p_away_1, "
        "v4_1up_home_capped, v4_1up_away_capped, v4_p_home_1, v4_p_away_1) "
        "VALUES (?,?, 'bp', 2.1,3.2,0.5,0.3, 2.0,3.0,0.52,0.31)", (eid, ts))


def test_sim_rows_v3_v4_for_up_markets_only():
    c = _conn(); _seed_event(c)
    ts = "2026-05-22T18:00:00Z"
    _seed_tick(c, "E1", ts, "STARTED", prices=[
        ("1x2_ft", 0.0, "home", 1.80, None)])          # non-UP real market
    _seed_sim(c, "E1", ts)
    ticks = ex.select_ticks(c, "any", "all", {})
    rows = list(ex.iter_long_rows(
        c, ticks, markets=[("1x2_ft", 0.0), ("1x2_1up_ft", 0.0)],
        books=None, sim_engines=("v3", "v4")))
    # real 1x2 row retained (LEFT-join semantics — not deleted by the sim join)
    assert any(r["market_id"] == "1x2_ft" and r["is_simulated"] == 0 for r in rows)
    sim = [r for r in rows if r["is_simulated"] == 1]
    engines = {r["engine"] for r in sim}
    assert engines == {"v3", "v4"}
    # sim rows are 1UP home/away, bookmaker OUR, carry capped odds + prob
    v4_home = next(r for r in sim if r["engine"] == "v4" and r["side"] == "home")
    assert v4_home["market_id"] == "1x2_1up_ft" and v4_home["bookmaker"] == "OUR"
    assert v4_home["odds"] == 2.0 and v4_home["probability"] == 0.52
    assert v4_home["line"] == 0.0
    # set(LONG_COLUMNS) shape holds for sim rows too
    assert set(ex.LONG_COLUMNS) == set(v4_home.keys())


def test_sim_rows_left_join_no_row_yields_nothing():
    c = _conn(); _seed_event(c)
    ts = "2026-05-22T18:00:00Z"
    _seed_tick(c, "E1", ts, "STARTED", prices=[("1x2_1up_ft", 0.0, "home", 1.9, None)])
    # NO _seed_sim -> no pricer_live_results row
    ticks = ex.select_ticks(c, "any", "all", {})
    rows = list(ex.iter_long_rows(c, ticks, markets=[("1x2_1up_ft", 0.0)],
                                  books=None, sim_engines=("v3", "v4")))
    # only the real row; no sim rows, no crash
    assert all(r["is_simulated"] == 0 for r in rows)
    assert len(rows) == 1


def test_sim_rows_only_requested_engines_and_up_markets():
    c = _conn(); _seed_event(c)
    ts = "2026-05-22T18:00:00Z"
    _seed_tick(c, "E1", ts, "STARTED", prices=[("1x2_ft", 0.0, "home", 1.8, None)])
    _seed_sim(c, "E1", ts)
    ticks = ex.select_ticks(c, "any", "all", {})
    # markets exclude any UP market -> no sim rows even though sim_engines set
    rows = list(ex.iter_long_rows(c, ticks, markets=[("1x2_ft", 0.0)],
                                  books=None, sim_engines=("v4",)))
    assert all(r["is_simulated"] == 0 for r in rows)
    # request only v3, include the 1up market -> only v3 sim rows
    rows2 = list(ex.iter_long_rows(c, ticks, markets=[("1x2_1up_ft", 0.0)],
                                   books=None, sim_engines=("v3",)))
    assert {r["engine"] for r in rows2 if r["is_simulated"] == 1} == {"v3"}


def _seed_sim_2up(c, eid, ts):
    c.execute(
        "INSERT INTO pricer_live_results (event_id, ts_utc, basis_used, "
        "v3_2up_home_capped, v3_2up_away_capped, v3_p_home_2, v3_p_away_2, "
        "v4_2up_home_capped, v4_2up_away_capped, v4_p_home_2, v4_p_away_2) "
        "VALUES (?,?, 'bp', 5.5,6.6,0.11,0.09, 5.0,6.0,0.12,0.10)", (eid, ts))


def test_sim_rows_2up_column_mapping():
    c = _conn(); _seed_event(c)
    ts = "2026-05-22T18:00:00Z"
    _seed_tick(c, "E1", ts, "STARTED", prices=[("1x2_2up_ft", 0.0, "home", 9.0, None)])
    _seed_sim_2up(c, "E1", ts)
    ticks = ex.select_ticks(c, "any", "all", {})
    rows = list(ex.iter_long_rows(c, ticks, markets=[("1x2_2up_ft", 0.0)],
                                  books=None, sim_engines=("v3", "v4")))
    sim = [r for r in rows if r["is_simulated"] == 1]
    # all sim rows are the 2up market, OUR book
    assert sim and all(r["market_id"] == "1x2_2up_ft" and r["bookmaker"] == "OUR" for r in sim)
    v3_home = next(r for r in sim if r["engine"] == "v3" and r["side"] == "home")
    v4_away = next(r for r in sim if r["engine"] == "v4" and r["side"] == "away")
    # 2up values must come from the *_2up_*_capped / *_p_*_2 columns, NOT 1up
    assert v3_home["odds"] == 5.5 and v3_home["probability"] == 0.11
    assert v4_away["odds"] == 6.0 and v4_away["probability"] == 0.10


def test_to_wide_rows_stable_columns():
    long_rows = [
        {"event_id": "E1", "ts_utc": "T1", "country_name": "NG", "league_name": "L",
         "home": "A", "away": "B", "kickoff_utc": "K", "snapshot_id": 1,
         "status": "STARTED", "match_minute": 5, "score_home": 0, "score_away": 0,
         "bookmaker": "betpawa", "market_id": "1x2_ft", "line": 0.0, "side": "home",
         "odds": 1.80, "probability": 0.55, "is_simulated": 0, "engine": ""},
        {"event_id": "E1", "ts_utc": "T1", "country_name": "NG", "league_name": "L",
         "home": "A", "away": "B", "kickoff_utc": "K", "snapshot_id": 1,
         "status": "STARTED", "match_minute": 5, "score_home": 0, "score_away": 0,
         "bookmaker": "OUR", "market_id": "1x2_1up_ft", "line": 0.0, "side": "home",
         "odds": 2.0, "probability": 0.52, "is_simulated": 1, "engine": "v4"},
    ]
    cols, wide = ex.to_wide_rows(long_rows)
    assert len(wide) == 1
    assert wide[0]["event_id"] == "E1" and wide[0]["ts_utc"] == "T1"
    assert wide[0]["betpawa__1x2_ft__0.0__home__odds"] == 1.80
    assert wide[0]["betpawa__1x2_ft__0.0__home__prob"] == 0.55
    assert wide[0]["our_v4__1x2_1up_ft__0.0__home__odds"] == 2.0
    assert wide[0]["our_v4__1x2_1up_ft__0.0__home__prob"] == 0.52
    # metadata columns come first, value columns sorted after
    meta_n = len(ex.WIDE_META)
    assert cols[:meta_n] == list(ex.WIDE_META)
    assert cols[meta_n:] == sorted(cols[meta_n:])   # value cols deterministically sorted
    # all value columns present in the column list
    assert "betpawa__1x2_ft__0.0__home__odds" in cols
    assert "our_v4__1x2_1up_ft__0.0__home__odds" in cols


def test_to_wide_rows_two_timestamps_two_rows():
    base = {"country_name": "NG", "league_name": "L", "home": "A", "away": "B",
            "kickoff_utc": "K", "snapshot_id": 1, "status": "STARTED",
            "match_minute": 0, "score_home": 0, "score_away": 0,
            "is_simulated": 0, "engine": ""}
    long_rows = [
        {**base, "event_id": "E1", "ts_utc": "T1", "bookmaker": "betpawa",
         "market_id": "1x2_ft", "line": 0.0, "side": "home", "odds": 1.8, "probability": None},
        {**base, "event_id": "E1", "ts_utc": "T2", "bookmaker": "betpawa",
         "market_id": "1x2_ft", "line": 0.0, "side": "home", "odds": 1.9, "probability": None},
    ]
    cols, wide = ex.to_wide_rows(long_rows)
    assert [w["ts_utc"] for w in wide] == ["T1", "T2"]
    assert wide[0]["betpawa__1x2_ft__0.0__home__odds"] == 1.8
    assert wide[1]["betpawa__1x2_ft__0.0__home__odds"] == 1.9
