import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from odds_scraper.db_schema import init_schema
from odds_scraper.web.app import create_app


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Minimal DB with one upcoming event and 1x2 prices for one bookmaker."""
    path = tmp_path / "odds.db"
    conn = sqlite3.connect(str(path), isolation_level=None)
    init_schema(conn)
    conn.execute(
        "INSERT INTO events (id, home, away, kickoff_utc) "
        "VALUES ('E1', 'Liverpool', 'Arsenal', '2026-05-22T18:30:00Z')",
    )
    cur = conn.execute(
        "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, fetch_status) "
        "VALUES ('2026-05-21T10:00:00Z', 'E1', 'betpawa', 'UPCOMING', 'ok')",
    )
    snap_id = cur.lastrowid
    for market_id in ("1x2_ft", "1x2_1up_ft", "1x2_2up_ft"):
        for side, odds, prob in [
            ("home", 1.85, 0.54), ("draw", 3.40, 0.29), ("away", 4.20, 0.23),
        ]:
            conn.execute(
                "INSERT INTO prices (snapshot_id, event_id, ts_utc, bookmaker, "
                "market_id, line, side, odds, probability) "
                "VALUES (?, 'E1', '2026-05-21T10:00:00Z', 'betpawa', ?, 0.0, ?, ?, ?)",
                (snap_id, market_id, side, odds, prob),
            )
    # A second snapshot for E1 — verifies history table renders multiple rows
    cur2 = conn.execute(
        "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, fetch_status) "
        "VALUES ('2026-05-21T10:01:30Z', 'E1', 'betpawa', 'UPCOMING', 'ok')",
    )
    snap_id2 = cur2.lastrowid
    conn.execute(
        "INSERT INTO prices (snapshot_id, event_id, ts_utc, bookmaker, "
        "market_id, line, side, odds, probability) "
        "VALUES (?, 'E1', '2026-05-21T10:01:30Z', 'betpawa', '1x2_2up_ft', 0.0, 'home', 1.90, 0.53)",
        (snap_id2,),
    )
    conn.close()
    return path


@pytest.fixture
def client(db_path: Path) -> TestClient:
    app = create_app(db_path=db_path)
    return TestClient(app)


def test_index_returns_page(client: TestClient):
    r = client.get("/")
    assert r.status_code == 200
    assert "ODDS" in r.text
    assert "events-list" in r.text
    assert "LIVE" in r.text and "UPCOMING" in r.text and "ENDED" in r.text


def test_events_fragment_upcoming_lists_event(client: TestClient):
    r = client.get("/events?status=upcoming")
    assert r.status_code == 200
    assert "Liverpool" in r.text and "Arsenal" in r.text
    assert 'id="events-list"' in r.text


def test_events_fragment_includes_polling_trigger(client: TestClient):
    r = client.get("/events?status=upcoming")
    assert 'hx-trigger="every 30s"' in r.text
    r = client.get("/events?status=live")
    assert 'hx-trigger="every 5s"' in r.text
    r = client.get("/events?status=ended")
    assert 'hx-trigger="every 60s"' in r.text


def test_events_card_links_to_detail_page(client: TestClient):
    r = client.get("/events?status=upcoming")
    assert 'href="/events/E1"' in r.text


def test_events_unknown_status_returns_400(client: TestClient):
    r = client.get("/events?status=bogus")
    assert r.status_code == 400


def test_events_empty_status_returns_empty_list(client: TestClient):
    r = client.get("/events?status=live")
    assert r.status_code == 200
    assert 'id="events-list"' in r.text
    assert "Liverpool" not in r.text


def test_event_detail_renders_default_market(client: TestClient):
    r = client.get("/events/E1")
    assert r.status_code == 200
    # Header includes team names + back link
    assert "Liverpool" in r.text and "Arsenal" in r.text
    assert 'href="/"' in r.text
    # Default market is 1x2 — 2 Up; its pill is active
    assert "1x2 — 2 Up" in r.text
    assert 'class="pill active"' in r.text
    # History table shows two snapshots for E1's 1x2_2up_ft home
    assert "1.85" in r.text  # earlier snap
    assert "1.90" in r.text  # later snap
    # Bookmaker headers present
    assert "BetPawa" in r.text


def test_event_detail_market_query_switches_view(client: TestClient):
    r = client.get("/events/E1?market=1x2_ft")
    assert r.status_code == 200
    assert "1x2 — Full Time" in r.text
    # 1x2_ft data only — the 2up extra snap shouldn't bleed in
    assert "1.85" in r.text


def test_event_detail_unknown_event_returns_404(client: TestClient):
    r = client.get("/events/UNKNOWN_ID")
    assert r.status_code == 404


def test_event_detail_unknown_market_returns_400(client: TestClient):
    r = client.get("/events/E1?market=bogus")
    assert r.status_code == 400


def test_event_detail_pills_include_ou_lines(client: TestClient):
    r = client.get("/events/E1")
    for line in (1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5, 8.5, 9.5):
        assert f"OU {line}" in r.text
