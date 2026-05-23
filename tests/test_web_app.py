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


def test_events_card_shows_probability_for_bp_sb(client: TestClient):
    r = client.get("/events?status=upcoming")
    # Column header marker
    assert "+p" in r.text
    # Fixture writes prob=0.54 for BP — should render as ".54"
    assert ".54" in r.text


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
    # Default market is now 1x2 — 1 Up; its family-pill is active
    assert "1x2 — 1 Up" in r.text
    assert 'class="family-pill active"' in r.text
    # History table shows 1x2_1up_ft prices from the fixture's only snapshot
    assert "1.85" in r.text
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


def test_event_detail_pills_include_ou_lines(db_path: Path):
    """Selecting Match O/U with lines 2.5 + 3.5 in the DB exposes both as
    line pills (two-stage family + line UI)."""
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    snap_id = conn.execute(
        "SELECT id FROM snapshots WHERE event_id='E1' LIMIT 1"
    ).fetchone()[0]
    for line in (2.5, 3.5):
        for side, odds in [("over", 1.7), ("under", 2.1)]:
            conn.execute(
                "INSERT INTO prices (snapshot_id, event_id, ts_utc, bookmaker, "
                "market_id, line, side, odds, probability) "
                "VALUES (?, 'E1', '2026-05-21T10:00:00Z', 'betpawa', "
                "'over_under_ft', ?, ?, ?, NULL)",
                (snap_id, line, side, odds),
            )
    conn.close()
    client = TestClient(create_app(db_path=db_path))
    body = client.get("/events/E1?market=ou_2.5").text
    assert "ou_2.5" in body
    assert "ou_3.5" in body


def test_event_detail_family_pills_includes_new_markets(db_path: Path):
    """Family row exists with chips for all families (1x2 trio + 4 parameterized)."""
    client = TestClient(create_app(db_path=db_path))
    body = client.get("/events/E1").text
    for label in (
        "1x2 — Full Time", "1x2 — 1 Up", "1x2 — 2 Up",
        "Next Goal", "Match O/U", "Home O/U", "Away O/U",
    ):
        assert label in body, f"missing family pill label {label!r}"


def test_event_detail_disables_family_pill_when_no_lines_available(db_path: Path):
    """Fixture has no next_goal / over_under / home_OU / away_OU prices.
    Their family chips must render with a disabled marker."""
    client = TestClient(create_app(db_path=db_path))
    body = client.get("/events/E1").text
    # Implementation uses class="family-pill disabled" on a <span> for
    # disabled chips.
    assert "family-pill disabled" in body


def test_event_detail_line_pills_filtered_to_available_lines(db_path: Path):
    """Insert over_under_ft prices for lines 2.5 and 3.5 only.
    On Match O/U family active → only those two lines appear in line-pill markup."""
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    snap_id = conn.execute(
        "SELECT id FROM snapshots WHERE event_id='E1' LIMIT 1"
    ).fetchone()[0]
    for line in (2.5, 3.5):
        for side, odds in [("over", 1.7), ("under", 2.1)]:
            conn.execute(
                "INSERT INTO prices (snapshot_id, event_id, ts_utc, bookmaker, "
                "market_id, line, side, odds, probability) "
                "VALUES (?, 'E1', '2026-05-21T10:00:00Z', 'betpawa', "
                "'over_under_ft', ?, ?, ?, NULL)",
                (snap_id, line, side, odds),
            )
    conn.close()
    client = TestClient(create_app(db_path=db_path))
    body = client.get("/events/E1?market=ou_2.5").text
    # Active family = Match O/U → line chips visible:
    assert ">2.5<" in body
    assert ">3.5<" in body
    # Lines without data must NOT appear as pills:
    for missing in ("4.5", "5.5", "6.5", "7.5", "8.5", "9.5"):
        assert f"?market=ou_{missing}" not in body


def test_event_detail_active_line_pill_is_marked(db_path: Path):
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    snap_id = conn.execute(
        "SELECT id FROM snapshots WHERE event_id='E1' LIMIT 1"
    ).fetchone()[0]
    for line in (2.5, 3.5):
        for side, odds in [("over", 1.7), ("under", 2.1)]:
            conn.execute(
                "INSERT INTO prices (snapshot_id, event_id, ts_utc, bookmaker, "
                "market_id, line, side, odds, probability) "
                "VALUES (?, 'E1', '2026-05-21T10:00:00Z', 'betpawa', "
                "'over_under_ft', ?, ?, ?, NULL)",
                (snap_id, line, side, odds),
            )
    conn.close()
    client = TestClient(create_app(db_path=db_path))
    body = client.get("/events/E1?market=ou_3.5").text
    # Line-pill for 3.5 has the active class.
    assert 'class="line-pill active"' in body
    # Line-pill for 2.5 does NOT.
    import re
    m = re.search(r'href="/events/E1\?market=ou_2\.5"[^>]*class="line-pill([^"]*)"', body)
    assert m is not None
    assert "active" not in m.group(1)


def test_index_filter_row_includes_search_and_kickoff(client: TestClient):
    r = client.get("/")
    # Filter row labels
    assert "Bookmakers" in r.text and "Kickoff" in r.text and "Search" in r.text
    # Kickoff window pills (1h / 3h / 6h / 24h / 48h)
    for win in ("3600", "10800", "21600", "86400", "172800"):
        assert f'data-window="{win}"' in r.text
    assert 'data-window="all"' in r.text
    # Date picker (replaces the old custom-hours input) and search input
    assert 'id="kickoff-date"' in r.text
    assert 'id="search-input"' in r.text


def test_events_card_carries_filter_data_attributes(client: TestClient):
    r = client.get("/events?status=upcoming")
    # Lower-cased "home + ' ' + away" attribute for client-side substring match
    assert 'data-event-name="liverpool arsenal"' in r.text
    assert 'data-kickoff-utc="2026-05-22T18:30:00Z"' in r.text


def test_events_card_skips_ou_groups_when_no_data(client: TestClient):
    """Fixture only has 1x2 family data — no OU rows in DB → no OU group
    is rendered (we check by group_key absence, not by old market-extra)."""
    r = client.get("/events?status=upcoming")
    assert 'data-group-key="over_under_ft_' not in r.text
    assert 'data-group-key="next_goal_ft_' not in r.text


@pytest.fixture
def db_with_ou_path(tmp_path: Path) -> Path:
    """A DB where event E1 also has OU prices, so the card can expand."""
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
        for side in ("home", "draw", "away"):
            conn.execute(
                "INSERT INTO prices (snapshot_id, event_id, ts_utc, bookmaker, "
                "market_id, line, side, odds, probability) "
                "VALUES (?, 'E1', '2026-05-21T10:00:00Z', 'betpawa', ?, 0.0, ?, 1.85, 0.54)",
                (snap_id, market_id, side),
            )
    # OU 2.5 prices so an extra group becomes visible
    for side in ("over", "under"):
        conn.execute(
            "INSERT INTO prices (snapshot_id, event_id, ts_utc, bookmaker, "
            "market_id, line, side, odds, probability) "
            "VALUES (?, 'E1', '2026-05-21T10:00:00Z', 'betpawa', "
            "'over_under_ft', 2.5, ?, 1.85, 0.54)",
            (snap_id, side),
        )
    conn.close()
    return path


def test_events_card_has_expand_toggle_when_ou_present(db_with_ou_path: Path):
    """With OU 2.5 priced, the card emits a market-block with the
    matching group_key — the per-market collapse hook for the JS layer."""
    app = create_app(db_path=db_with_ou_path)
    client = TestClient(app)
    r = client.get("/events?status=upcoming")
    assert 'data-group-key="over_under_ft_2.5"' in r.text
    assert "Match O/U 2.5" in r.text
    # The retired bottom button must NOT appear.
    assert "expand-toggle" not in r.text
    assert "market-extra" not in r.text


def test_events_card_shows_next_goal_group_when_priced(db_path: Path):
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    snap_id = conn.execute(
        "SELECT id FROM snapshots WHERE event_id='E1' LIMIT 1"
    ).fetchone()[0]
    for side, odds, prob in [
        ("home", 1.85, 0.54), ("none", 8.5, 0.12), ("away", 3.5, 0.29),
    ]:
        conn.execute(
            "INSERT INTO prices (snapshot_id, event_id, ts_utc, bookmaker, "
            "market_id, line, side, odds, probability) "
            "VALUES (?, 'E1', '2026-05-21T10:00:00Z', 'betpawa', 'next_goal_ft', 1.0, ?, ?, ?)",
            (snap_id, side, odds, prob),
        )
    conn.close()
    client = TestClient(create_app(db_path=db_path))
    r = client.get("/events?status=upcoming")
    assert "Next Goal 1" in r.text
    assert "NG 1 · H" in r.text or "NG 1.0 · H" in r.text
    assert "NG 1 · N" in r.text or "NG 1.0 · N" in r.text


def test_events_card_shows_per_team_ou_groups_when_priced(db_path: Path):
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    snap_id = conn.execute(
        "SELECT id FROM snapshots WHERE event_id='E1' LIMIT 1"
    ).fetchone()[0]
    rows = [
        ("home_over_under_ft", 0.5, "over",  1.30, 0.74),
        ("home_over_under_ft", 0.5, "under", 3.50, 0.26),
        ("away_over_under_ft", 1.5, "over",  2.50, 0.40),
        ("away_over_under_ft", 1.5, "under", 1.55, 0.60),
    ]
    for market_id, line, side, odds, prob in rows:
        conn.execute(
            "INSERT INTO prices (snapshot_id, event_id, ts_utc, bookmaker, "
            "market_id, line, side, odds, probability) "
            "VALUES (?, 'E1', '2026-05-21T10:00:00Z', 'betpawa', ?, ?, ?, ?, ?)",
            (snap_id, market_id, line, side, odds, prob),
        )
    conn.close()
    client = TestClient(create_app(db_path=db_path))
    r = client.get("/events?status=upcoming")
    assert "Home O/U 0.5" in r.text
    assert "Away O/U 1.5" in r.text


def test_events_card_omits_market_group_with_no_data(db_path: Path):
    """No next_goal_ft / home_over_under_ft / away_over_under_ft data → those
    group labels are absent. (over_under_ft is also absent for this minimal
    fixture; only 1x2 family appears.)"""
    client = TestClient(create_app(db_path=db_path))
    r = client.get("/events?status=upcoming")
    for label in ("Next Goal", "Match O/U", "Home O/U", "Away O/U"):
        assert label not in r.text, f"expected {label!r} absent in plain fixture"


def test_events_card_expander_groups_in_fixed_order(db_path: Path):
    """When all four parameterized markets have at least one priced line, the
    expander groups must appear in order: next_goal → over_under → home_OU → away_OU."""
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    snap_id = conn.execute(
        "SELECT id FROM snapshots WHERE event_id='E1' LIMIT 1"
    ).fetchone()[0]
    rows = [
        ("over_under_ft",      2.5, "over",  1.70, 0.58),
        ("next_goal_ft",       1.0, "home",  1.85, 0.54),
        ("home_over_under_ft", 0.5, "over",  1.30, 0.74),
        ("away_over_under_ft", 0.5, "over",  1.40, 0.69),
    ]
    for market_id, line, side, odds, prob in rows:
        conn.execute(
            "INSERT INTO prices (snapshot_id, event_id, ts_utc, bookmaker, "
            "market_id, line, side, odds, probability) "
            "VALUES (?, 'E1', '2026-05-21T10:00:00Z', 'betpawa', ?, ?, ?, ?, ?)",
            (snap_id, market_id, line, side, odds, prob),
        )
    conn.close()
    client = TestClient(create_app(db_path=db_path))
    body = client.get("/events?status=upcoming").text
    i_ng  = body.find("Next Goal")
    i_ou  = body.find("Match O/U")
    i_hou = body.find("Home O/U")
    i_aou = body.find("Away O/U")
    assert -1 < i_ng < i_ou < i_hou < i_aou




def test_event_detail_subtitle_renders_country_and_league(db_path: Path):
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.execute(
        "UPDATE events SET country_id='242', country_name='Germany', "
        "league_id='12091', league_name='2nd Bundesliga' WHERE id='E1'"
    )
    conn.close()
    client = TestClient(create_app(db_path=db_path))
    body = client.get("/events/E1").text
    assert "Germany" in body
    assert "2nd Bundesliga" in body
    assert "Germany · 2nd Bundesliga" in body


def test_event_detail_subtitle_omits_when_both_empty(db_path: Path):
    """An event without country/league info should not render a stray separator."""
    client = TestClient(create_app(db_path=db_path))
    body = client.get("/events/E1").text
    # The fixture leaves country/league NULL. The middle-dot separator must not
    # appear in the subtitle position. We anchor the search on the dedicated
    # subtitle class — its absence proves the <div> wasn't emitted.
    assert 'class="event-subtitle' not in body


def test_event_detail_renders_next_goal_market_with_none_side(db_path: Path):
    """next_goal_ft has a 'none' side. Detail page must render its short label
    without KeyError. Seeds one priced next_goal_ft row at line=1.0."""
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    cur = conn.execute(
        "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, fetch_status) "
        "VALUES ('2026-05-21T10:02:00Z', 'E1', 'betpawa', 'UPCOMING', 'ok')",
    )
    snap_id = cur.lastrowid
    for side, odds, prob in [
        ("home", 1.85, 0.54), ("none", 8.50, 0.12), ("away", 3.50, 0.29),
    ]:
        conn.execute(
            "INSERT INTO prices (snapshot_id, event_id, ts_utc, bookmaker, "
            "market_id, line, side, odds, probability) "
            "VALUES (?, 'E1', '2026-05-21T10:02:00Z', 'betpawa', 'next_goal_ft', 1.0, ?, ?, ?)",
            (snap_id, side, odds, prob),
        )
    conn.close()
    app = create_app(db_path=db_path)
    client = TestClient(app)
    r = client.get("/events/E1?market=ng_1.0")
    assert r.status_code == 200
    # The "N" short label for the "none" outcome must appear in the table head.
    assert 'class="side-h">N<' in r.text


def test_index_embeds_country_league_index_json(db_path: Path):
    """The home page must include the index payload as JSON so the client
    can render the dropdowns without a second HTTP round-trip."""
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.execute(
        "UPDATE events SET country_id='242', country_name='Germany', "
        "league_id='BL2', league_name='2nd Bundesliga' WHERE id='E1'"
    )
    conn.close()
    client = TestClient(create_app(db_path=db_path))
    body = client.get("/").text
    assert 'id="country-league-index"' in body
    assert 'type="application/json"' in body
    assert "Germany" in body
    assert "2nd Bundesliga" in body


def test_events_fragment_filters_by_country(db_path: Path):
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.executemany(
        "INSERT INTO events (id, home, away, kickoff_utc, country_id, country_name) "
        "VALUES (?, 'H', 'A', '2026-05-22T18:30:00Z', ?, ?)",
        [("E_DE", "242", "Germany"), ("E_US", "USA1", "USA")],
    )
    for eid in ("E_DE", "E_US"):
        conn.execute(
            "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, fetch_status) "
            "VALUES ('2026-05-21T10:00:00Z', ?, 'betpawa', 'UPCOMING', 'ok')",
            (eid,),
        )
    conn.close()
    client = TestClient(create_app(db_path=db_path))
    r = client.get("/events?status=upcoming&country=242")
    assert r.status_code == 200
    assert 'href="/events/E_DE"' in r.text
    assert 'href="/events/E_US"' not in r.text


def test_events_fragment_filters_by_league(db_path: Path):
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.executemany(
        "INSERT INTO events (id, home, away, kickoff_utc, league_id, league_name) "
        "VALUES (?, 'H', 'A', '2026-05-22T18:30:00Z', ?, ?)",
        [("E_BL1", "BL1", "Bundesliga"), ("E_BL2", "BL2", "2nd Bundesliga")],
    )
    for eid in ("E_BL1", "E_BL2"):
        conn.execute(
            "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, fetch_status) "
            "VALUES ('2026-05-21T10:00:00Z', ?, 'betpawa', 'UPCOMING', 'ok')",
            (eid,),
        )
    conn.close()
    client = TestClient(create_app(db_path=db_path))
    r = client.get("/events?status=upcoming&league=BL2")
    assert 'href="/events/E_BL2"' in r.text
    assert 'href="/events/E_BL1"' not in r.text


def test_index_filter_row_has_country_and_league_selects(db_path: Path):
    client = TestClient(create_app(db_path=db_path))
    body = client.get("/").text
    assert 'id="country-select"' in body
    assert 'id="league-select"' in body


def test_event_detail_history_row_renders_live_state(db_path: Path):
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    cur = conn.execute(
        "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, "
        "match_minute, score_home, score_away, fetch_status) "
        "VALUES ('2026-05-22T18:34:12Z', 'E1', 'betpawa', 'STARTED', "
        "34, 1, 0, 'ok')",
    )
    snap_id = cur.lastrowid
    conn.execute(
        "INSERT INTO prices (snapshot_id, event_id, ts_utc, bookmaker, "
        "market_id, line, side, odds, probability) "
        "VALUES (?, 'E1', '2026-05-22T18:34:12Z', 'betpawa', '1x2_1up_ft', "
        "0.0, 'home', 1.85, 0.54)",
        (snap_id,),
    )
    conn.close()
    client = TestClient(create_app(db_path=db_path))
    body = client.get("/events/E1").text
    assert "34' · 1–0" in body


def test_event_detail_history_row_renders_ended_state(db_path: Path):
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    cur = conn.execute(
        "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, "
        "match_minute, score_home, score_away, fetch_status) "
        "VALUES ('2026-05-22T20:00:00Z', 'E1', 'betpawa', 'ENDED', "
        "NULL, 2, 1, 'ok')",
    )
    snap_id = cur.lastrowid
    conn.execute(
        "INSERT INTO prices (snapshot_id, event_id, ts_utc, bookmaker, "
        "market_id, line, side, odds, probability) "
        "VALUES (?, 'E1', '2026-05-22T20:00:00Z', 'betpawa', '1x2_1up_ft', "
        "0.0, 'home', 1.85, 0.54)",
        (snap_id,),
    )
    conn.close()
    client = TestClient(create_app(db_path=db_path))
    body = client.get("/events/E1").text
    assert "FT · 2–1" in body


def test_event_detail_history_row_renders_dash_for_upcoming(db_path: Path):
    """The default fixture seeds an UPCOMING snapshot. The STATE cell
    for that row must contain the em-dash."""
    client = TestClient(create_app(db_path=db_path))
    body = client.get("/events/E1").text
    assert 'class="state-col">—' in body


def test_event_detail_history_table_has_state_header(db_path: Path):
    client = TestClient(create_app(db_path=db_path))
    body = client.get("/events/E1").text
    assert ">STATE<" in body


def test_events_card_emits_group_key_per_market(client: TestClient):
    """Every market block carries data-group-key for the JS collapse layer.
    1x2 family group_key = canonical_id."""
    r = client.get("/events?status=upcoming")
    assert 'data-group-key="1x2_ft"' in r.text
    assert 'data-group-key="1x2_1up_ft"' in r.text
    assert 'data-group-key="1x2_2up_ft"' in r.text


def test_events_card_no_is_extra_marker(client: TestClient):
    """The retired is_extra flag must not leak into rendered markup —
    regression guard for the data-group-key migration."""
    r = client.get("/events?status=upcoming")
    assert "market-extra" not in r.text
    assert "expand-toggle" not in r.text


def test_event_detail_default_market_is_1up(client: TestClient):
    """Without ?market= query, the active family chip is 1x2 — 1 Up."""
    r = client.get("/events/E1")
    body = r.text
    # The active class lands on the 1up chip, not the 2up chip.
    import re
    m_1up = re.search(r'class="family-pill[^"]*"[^>]*>1x2 — 1 Up<', body)
    m_2up = re.search(r'class="family-pill[^"]*"[^>]*>1x2 — 2 Up<', body)
    assert m_1up is not None and "active" in m_1up.group(0)
    assert m_2up is not None and "active" not in m_2up.group(0)


def test_history_table_has_centered_headers_css_hook(client: TestClient):
    """Sanity check that the table class hook is rendered so CSS can attach.
    Visual centring is verified manually; this test guards the markup contract."""
    r = client.get("/events/E1")
    assert '<table class="history-table">' in r.text


def test_index_kickoff_date_input_is_type_date(client: TestClient):
    """Native <input type="date"> opens the OS calendar picker on mobile
    and desktop alike — verify the type attribute survives the template."""
    r = client.get("/")
    assert 'id="kickoff-date"' in r.text
    assert 'type="date"' in r.text


def test_index_no_longer_has_custom_hours_input(client: TestClient):
    """The old kickoff-custom-hours number input has been retired."""
    r = client.get("/")
    assert 'id="kickoff-custom-hours"' not in r.text


def test_events_card_wraps_grid_in_card_grid_div(client: TestClient):
    """The event card wraps its column header + market blocks in a single
    .card-grid div so the phone media query can make ONE scroll container
    per card. The event-title link (.ev) stays OUTSIDE the wrapper so it
    never scrolls. This test guards the HTML contract the mobile CSS
    depends on."""
    r = client.get("/events?status=upcoming")
    body = r.text
    assert '<div class="card-grid">' in body
    # The event-title anchor must appear BEFORE the card-grid wrapper so it
    # stays put above the horizontal scroll area on phone.
    ev_pos = body.find('<a class="ev"')
    grid_pos = body.find('<div class="card-grid"')
    assert ev_pos != -1 and grid_pos != -1, "both elements must exist"
    assert ev_pos < grid_pos, "<a class='ev'> must precede <div class='card-grid'>"
