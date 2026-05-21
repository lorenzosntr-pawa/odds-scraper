import sqlite3
from pathlib import Path

import pytest

from odds_scraper.db_schema import init_schema
from odds_scraper.web.queries import (
    get_event_meta, get_events_by_status, get_latest_prices_for_event,
    get_market_history_for_event, open_ro_conn,
)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    """A DB seeded with a few events, snapshots, prices across states."""
    path = tmp_path / "odds.db"
    conn = sqlite3.connect(str(path), isolation_level=None)
    init_schema(conn)

    events = [
        ("E_LIVE",     "Live Home",     "Live Away",     "2026-05-21T10:00:00Z"),
        ("E_UPCOMING", "Up Home",       "Up Away",       "2026-05-22T18:30:00Z"),
        ("E_UP2",      "Up2 Home",      "Up2 Away",      "2026-05-22T20:00:00Z"),
        ("E_ENDED",    "Ended Home",    "Ended Away",    "2026-05-20T15:00:00Z"),
        ("E_OLD",      "Old Home",      "Old Away",      "2026-05-18T15:00:00Z"),
    ]
    for eid, h, a, ko in events:
        conn.execute(
            "INSERT INTO events (id, home, away, kickoff_utc) VALUES (?, ?, ?, ?)",
            (eid, h, a, ko),
        )

    snaps = [
        ("E_LIVE",     "2026-05-21T11:00:00Z", "betpawa",   "STARTED",  34, 1, 0),
        ("E_UPCOMING", "2026-05-21T09:00:00Z", "betpawa",   "UPCOMING", None, None, None),
        ("E_UP2",      "2026-05-21T09:00:00Z", "betpawa",   "UPCOMING", None, None, None),
        ("E_ENDED",    "2026-05-20T17:00:00Z", "betpawa",   "ENDED",    90, 2, 1),
        ("E_OLD",      "2026-05-18T17:00:00Z", "betpawa",   "ENDED",    90, 0, 3),
    ]
    snap_ids: dict[str, int] = {}
    for eid, ts, bm, status, minute, sh, sa in snaps:
        cur = conn.execute(
            "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, "
            "match_minute, score_home, score_away, fetch_status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'ok')",
            (ts, eid, bm, status, minute, sh, sa),
        )
        snap_ids[eid] = cur.lastrowid

    bp_snap = snap_ids["E_LIVE"]
    prices_e_live = [
        ("1x2_ft",        0.0, "home", 1.85, 0.54),
        ("1x2_ft",        0.0, "draw", 3.40, 0.29),
        ("1x2_ft",        0.0, "away", 4.20, 0.23),
        ("1x2_1up_ft",    0.0, "home", 1.65, 0.60),
        ("1x2_1up_ft",    0.0, "draw", 3.20, 0.31),
        ("1x2_1up_ft",    0.0, "away", 4.50, 0.22),
        ("1x2_2up_ft",    0.0, "home", 2.50, 0.40),
        ("1x2_2up_ft",    0.0, "draw", 3.80, 0.26),
        ("1x2_2up_ft",    0.0, "away", 6.00, 0.16),
        ("over_under_ft", 2.5, "over",  1.70, 0.58),
        ("over_under_ft", 2.5, "under", 2.10, 0.42),
    ]
    for market_id, line, side, odds, prob in prices_e_live:
        conn.execute(
            "INSERT INTO prices (snapshot_id, event_id, ts_utc, bookmaker, "
            "market_id, line, side, odds, probability) "
            "VALUES (?, ?, ?, 'betpawa', ?, ?, ?, ?, ?)",
            (bp_snap, "E_LIVE", "2026-05-21T11:00:00Z",
             market_id, line, side, odds, prob),
        )
    conn.close()
    return path


def test_open_ro_conn_returns_readonly(db: Path):
    conn = open_ro_conn(db)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO events (id, home, away, kickoff_utc) "
                     "VALUES ('X', 'X', 'X', '2026-01-01T00:00:00Z')")
    conn.close()


def test_get_events_by_status_live(db: Path):
    conn = open_ro_conn(db)
    rows = get_events_by_status(conn, "live")
    conn.close()
    ids = [r["id"] for r in rows]
    assert ids == ["E_LIVE"]


def test_get_events_by_status_upcoming_ordered_by_kickoff(db: Path):
    conn = open_ro_conn(db)
    rows = get_events_by_status(conn, "upcoming")
    conn.close()
    ids = [r["id"] for r in rows]
    assert ids == ["E_UPCOMING", "E_UP2"]


def test_get_events_by_status_ended_excludes_older_than_24h(db: Path, monkeypatch):
    import odds_scraper.web.queries as q
    monkeypatch.setattr(q, "_utcnow_iso",
                        lambda: "2026-05-21T12:00:00Z")
    conn = open_ro_conn(db)
    rows = get_events_by_status(conn, "ended")
    conn.close()
    ids = [r["id"] for r in rows]
    assert "E_ENDED" in ids
    assert "E_OLD" not in ids


def test_get_latest_prices_for_event_collapsed_only_1x2(db: Path):
    conn = open_ro_conn(db)
    rows = get_latest_prices_for_event(conn, "E_LIVE", scope="collapsed")
    conn.close()
    market_ids = {r["market_id"] for r in rows}
    assert market_ids == {"1x2_ft", "1x2_1up_ft", "1x2_2up_ft"}


def test_get_latest_prices_for_event_opened_includes_over_under(db: Path):
    conn = open_ro_conn(db)
    rows = get_latest_prices_for_event(conn, "E_LIVE", scope="opened")
    conn.close()
    market_ids = {r["market_id"] for r in rows}
    assert "over_under_ft" in market_ids
    assert {"1x2_ft", "1x2_1up_ft", "1x2_2up_ft"} <= market_ids


def test_invalid_status_raises(db: Path):
    conn = open_ro_conn(db)
    with pytest.raises(ValueError):
        get_events_by_status(conn, "bogus")
    conn.close()


def test_get_event_meta_returns_event_joined_with_latest_snapshot(db: Path):
    conn = open_ro_conn(db)
    row = get_event_meta(conn, "E_LIVE")
    conn.close()
    assert row is not None
    assert row["id"] == "E_LIVE"
    assert row["home"] == "Live Home"
    assert row["away"] == "Live Away"
    # E_LIVE's latest snapshot has status STARTED, minute 34, score 1-0
    assert row["status"] == "STARTED"
    assert row["match_minute"] == 34


def test_get_event_meta_unknown_returns_none(db: Path):
    conn = open_ro_conn(db)
    assert get_event_meta(conn, "NO_SUCH_EVENT") is None
    conn.close()


def test_get_market_history_for_event_1x2_ft(db: Path):
    conn = open_ro_conn(db)
    rows = get_market_history_for_event(conn, "E_LIVE", "1x2_ft", line=None)
    conn.close()
    # Three sides (H/D/A) × one bookmaker in the fixture
    assert len(rows) == 3
    sides = {r["side"] for r in rows}
    assert sides == {"home", "draw", "away"}
    # Odds correctly populated from the fixture seeds
    home = next(r for r in rows if r["side"] == "home")
    assert home["odds"] == 1.85
    assert home["probability"] == 0.54


def test_get_market_history_for_event_over_under_line_filter(db: Path):
    conn = open_ro_conn(db)
    rows = get_market_history_for_event(conn, "E_LIVE", "over_under_ft", line=2.5)
    conn.close()
    sides = {r["side"] for r in rows}
    assert sides == {"over", "under"}


def test_get_market_history_for_event_returns_newest_first(db: Path):
    # Add a second snapshot for E_LIVE 1x2_ft home with a different ts
    import sqlite3 as s
    conn = s.connect(str(db), isolation_level=None)
    cur = conn.execute(
        "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, fetch_status) "
        "VALUES ('2026-05-21T11:05:00Z', 'E_LIVE', 'betpawa', 'STARTED', 'ok')",
    )
    snap_id = cur.lastrowid
    conn.execute(
        "INSERT INTO prices (snapshot_id, event_id, ts_utc, bookmaker, "
        "market_id, line, side, odds, probability) "
        "VALUES (?, 'E_LIVE', '2026-05-21T11:05:00Z', 'betpawa', '1x2_ft', 0.0, 'home', 1.92, 0.52)",
        (snap_id,),
    )
    conn.close()

    conn = open_ro_conn(db)
    rows = get_market_history_for_event(conn, "E_LIVE", "1x2_ft", line=None)
    conn.close()
    # We have two snapshots for home; ordering must put the newer ts first.
    home_rows = [r for r in rows if r["side"] == "home"]
    assert len(home_rows) == 2
    assert home_rows[0]["ts_utc"] > home_rows[1]["ts_utc"]
    assert home_rows[0]["odds"] == 1.92


def test_get_available_lines_returns_only_lines_with_data(db: Path):
    from odds_scraper.web.queries import get_available_lines
    conn = open_ro_conn(db)
    avail = get_available_lines(conn, "E_LIVE")
    conn.close()
    # The shared fixture writes over_under_ft at line=2.5 only, plus
    # 1x2_ft / 1x2_1up_ft / 1x2_2up_ft at the sentinel line=0.0.
    # Sentinel-zero rows must be filtered out.
    assert avail == {"over_under_ft": [2.5]}


def test_get_event_meta_returns_country_and_league(db: Path):
    conn = sqlite3.connect(str(db), isolation_level=None)
    conn.execute(
        "UPDATE events SET country_id='242', country_name='Germany', "
        "league_id='12091', league_name='2nd Bundesliga' WHERE id='E_LIVE'"
    )
    conn.close()
    conn = open_ro_conn(db)
    row = get_event_meta(conn, "E_LIVE")
    conn.close()
    assert row is not None
    assert row["country_name"] == "Germany"
    assert row["league_name"] == "2nd Bundesliga"


def test_get_country_league_index_groups_by_country(tmp_path: Path):
    from odds_scraper.web.queries import get_country_league_index
    db = tmp_path / "odds.db"
    conn = sqlite3.connect(str(db), isolation_level=None)
    init_schema(conn)
    rows = [
        ("E1", "Germany", "242", "Bundesliga",      "BL1"),
        ("E2", "Germany", "242", "2nd Bundesliga",  "BL2"),
        ("E3", "USA",     "USA1", "MLS",            "MLS1"),
    ]
    for eid, country_name, country_id, league_name, league_id in rows:
        conn.execute(
            "INSERT INTO events (id, home, away, kickoff_utc, country_id, "
            "country_name, league_id, league_name) "
            "VALUES (?, 'H', 'A', '2026-05-22T00:00:00Z', ?, ?, ?, ?)",
            (eid, country_id, country_name, league_id, league_name),
        )
    conn.close()
    conn = open_ro_conn(db)
    index = get_country_league_index(conn)
    conn.close()
    assert index == [
        {
            "country_id": "242", "country_name": "Germany",
            "leagues": [
                {"league_id": "BL2", "league_name": "2nd Bundesliga"},
                {"league_id": "BL1", "league_name": "Bundesliga"},
            ],
        },
        {
            "country_id": "USA1", "country_name": "USA",
            "leagues": [
                {"league_id": "MLS1", "league_name": "MLS"},
            ],
        },
    ]


def test_get_country_league_index_skips_empty_country_name(tmp_path: Path):
    from odds_scraper.web.queries import get_country_league_index
    db = tmp_path / "odds.db"
    conn = sqlite3.connect(str(db), isolation_level=None)
    init_schema(conn)
    conn.execute(
        "INSERT INTO events (id, home, away, kickoff_utc, country_name, league_name) "
        "VALUES ('E_OK',    'H', 'A', '2026-05-22T00:00:00Z', 'Spain', 'La Liga'),"
        "       ('E_NULL',  'H', 'A', '2026-05-22T00:00:00Z', NULL,    NULL),"
        "       ('E_EMPTY', 'H', 'A', '2026-05-22T00:00:00Z', '',      '')",
    )
    conn.close()
    conn = open_ro_conn(db)
    index = get_country_league_index(conn)
    conn.close()
    country_names = [c["country_name"] for c in index]
    assert country_names == ["Spain"]


def test_get_events_by_status_filters_by_country_id(tmp_path: Path):
    db = tmp_path / "odds.db"
    conn = sqlite3.connect(str(db), isolation_level=None)
    init_schema(conn)
    conn.executemany(
        "INSERT INTO events (id, home, away, kickoff_utc, country_id, country_name) "
        "VALUES (?, 'H', 'A', '2026-05-22T18:30:00Z', ?, ?)",
        [
            ("E_DE", "242", "Germany"),
            ("E_US", "USA1", "USA"),
        ],
    )
    for eid in ("E_DE", "E_US"):
        conn.execute(
            "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, fetch_status) "
            "VALUES ('2026-05-21T10:00:00Z', ?, 'betpawa', 'UPCOMING', 'ok')",
            (eid,),
        )
    conn.close()
    conn = open_ro_conn(db)
    rows = get_events_by_status(conn, "upcoming", country_id="242")
    conn.close()
    ids = [r["id"] for r in rows]
    assert ids == ["E_DE"]


def test_get_events_by_status_filters_by_league_id(tmp_path: Path):
    db = tmp_path / "odds.db"
    conn = sqlite3.connect(str(db), isolation_level=None)
    init_schema(conn)
    conn.executemany(
        "INSERT INTO events (id, home, away, kickoff_utc, league_id, league_name) "
        "VALUES (?, 'H', 'A', '2026-05-22T18:30:00Z', ?, ?)",
        [
            ("E_BL1", "BL1", "Bundesliga"),
            ("E_BL2", "BL2", "2nd Bundesliga"),
        ],
    )
    for eid in ("E_BL1", "E_BL2"):
        conn.execute(
            "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, fetch_status) "
            "VALUES ('2026-05-21T10:00:00Z', ?, 'betpawa', 'UPCOMING', 'ok')",
            (eid,),
        )
    conn.close()
    conn = open_ro_conn(db)
    rows = get_events_by_status(conn, "upcoming", league_id="BL2")
    conn.close()
    ids = [r["id"] for r in rows]
    assert ids == ["E_BL2"]


def test_get_events_by_status_no_filter_returns_all(tmp_path: Path):
    """Empty country_id / league_id are no-ops."""
    db = tmp_path / "odds.db"
    conn = sqlite3.connect(str(db), isolation_level=None)
    init_schema(conn)
    conn.executemany(
        "INSERT INTO events (id, home, away, kickoff_utc) "
        "VALUES (?, 'H', 'A', '2026-05-22T18:30:00Z')",
        [("E1",), ("E2",)],
    )
    for eid in ("E1", "E2"):
        conn.execute(
            "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, fetch_status) "
            "VALUES ('2026-05-21T10:00:00Z', ?, 'betpawa', 'UPCOMING', 'ok')",
            (eid,),
        )
    conn.close()
    conn = open_ro_conn(db)
    rows = get_events_by_status(conn, "upcoming", country_id="", league_id="")
    conn.close()
    ids = {r["id"] for r in rows}
    assert ids == {"E1", "E2"}


def test_get_available_lines_multi_market_multi_line(db: Path):
    """Verify ordering and grouping when several lines and markets coexist."""
    from odds_scraper.web.queries import get_available_lines
    conn = sqlite3.connect(str(db), isolation_level=None)
    # Reuse the E_LIVE snapshot_id from the fixture. We need its id.
    snap_id = conn.execute(
        "SELECT id FROM snapshots WHERE event_id='E_LIVE' LIMIT 1"
    ).fetchone()[0]
    extra_prices = [
        ("over_under_ft",      3.5, "over",  2.50, None),
        ("next_goal_ft",       1.0, "home",  1.85, None),
        ("next_goal_ft",       2.0, "away",  3.90, None),
        ("home_over_under_ft", 0.5, "over",  1.30, None),
        ("away_over_under_ft", 1.5, "under", 1.55, None),
    ]
    for market_id, line, side, odds, prob in extra_prices:
        conn.execute(
            "INSERT INTO prices (snapshot_id, event_id, ts_utc, bookmaker, "
            "market_id, line, side, odds, probability) "
            "VALUES (?, 'E_LIVE', '2026-05-21T11:00:00Z', 'betpawa', "
            "?, ?, ?, ?, ?)",
            (snap_id, market_id, line, side, odds, prob),
        )
    conn.close()
    conn = open_ro_conn(db)
    avail = get_available_lines(conn, "E_LIVE")
    conn.close()
    assert avail == {
        "over_under_ft":      [2.5, 3.5],
        "next_goal_ft":       [1.0, 2.0],
        "home_over_under_ft": [0.5],
        "away_over_under_ft": [1.5],
    }
