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
