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
