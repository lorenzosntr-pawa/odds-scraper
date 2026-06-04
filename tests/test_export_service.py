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
