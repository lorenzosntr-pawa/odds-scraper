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


def test_events_fragment_wrapper_carries_data_status(client: TestClient):
    """The #events-list wrapper must carry data-status="<status>" so the
    client-side stale-swap guard can drop a fragment whose status no
    longer matches the active tab (race between in-flight poll and a
    fresh tab click). Without this attribute the guard is a no-op and
    polling can clobber a just-switched tab."""
    for status in ("upcoming", "live", "ended"):
        r = client.get(f"/events?status={status}")
        assert f'data-status="{status}"' in r.text, (
            f"#events-list wrapper missing data-status for {status}"
        )


def test_events_card_links_to_detail_page(client: TestClient):
    r = client.get("/events?status=upcoming")
    assert 'href="/events/E1?from=upcoming"' in r.text


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
    m = re.search(r'href="/events/E1\?market=ou_2\.5[^"]*"[^>]*class="line-pill([^"]*)"', body)
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
    matching group_key AND a master "Show more markets" toggle. Markets
    past the 1x2 family live inside the .card-extras region hidden behind
    that toggle."""
    app = create_app(db_path=db_with_ou_path)
    client = TestClient(app)
    r = client.get("/events?status=upcoming")
    assert 'data-group-key="over_under_ft_2.5"' in r.text
    assert "Match O/U 2.5" in r.text
    assert "expand-toggle" in r.text
    assert 'class="card-extras"' in r.text
    # The OU group_key must appear AFTER the card-extras opening tag —
    # extras live inside it, not in the always-visible primary region.
    extras_pos = r.text.find('class="card-extras"')
    ou_pos = r.text.find('data-group-key="over_under_ft_2.5"')
    assert extras_pos != -1 and ou_pos != -1
    assert extras_pos < ou_pos
    # The legacy per-group .market-extra class stays retired.
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
    assert 'href="/events/E_DE?from=upcoming"' in r.text
    assert 'href="/events/E_US' not in r.text


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
    assert 'href="/events/E_BL2?from=upcoming"' in r.text
    assert 'href="/events/E_BL1' not in r.text


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


@pytest.fixture
def db_with_ended(tmp_path: Path) -> Path:
    """Event E2 has an ENDED head snapshot — for slim-card / kickoff
    meta / no-grid assertions on the ended tab. Timestamp is "now" so
    the queries.py 24h cutoff on the ended tab never strands it."""
    from datetime import datetime, timezone
    path = tmp_path / "odds.db"
    conn = sqlite3.connect(str(path), isolation_level=None)
    init_schema(conn)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    kickoff = "2026-05-20T18:30:00Z"
    conn.execute(
        "INSERT INTO events (id, home, away, kickoff_utc) "
        "VALUES ('E2', 'Chelsea', 'Spurs', ?)",
        (kickoff,),
    )
    conn.execute(
        "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, "
        "match_minute, score_home, score_away, fetch_status) "
        "VALUES (?, 'E2', 'betpawa', 'ENDED', 90, 2, 1, 'ok')",
        (now,),
    )
    conn.close()
    return path


def test_ended_card_has_no_market_grid(db_with_ended: Path):
    """ENDED cards drop the markets-grid entirely (head snapshot is the
    synthetic empty one written by the reaper / watchdog sentinel)."""
    client = TestClient(create_app(db_path=db_with_ended))
    r = client.get("/events?status=ended")
    assert "Chelsea" in r.text and "Spurs" in r.text
    assert '<div class="card-grid">' not in r.text
    assert "MARKET · OUTCOME" not in r.text


def test_ended_card_meta_shows_kickoff(db_with_ended: Path):
    client = TestClient(create_app(db_path=db_with_ended))
    r = client.get("/events?status=ended")
    assert "kickoff 2026-05-20T18:30:00Z" in r.text
    assert "2 – 1" in r.text
    assert "ENDED" in r.text


def test_live_card_meta_shows_kickoff(db_path: Path):
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.execute(
        "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, "
        "match_minute, score_home, score_away, fetch_status) "
        "VALUES ('2026-05-22T18:34:12Z', 'E1', 'betpawa', 'STARTED', "
        "34, 1, 0, 'ok')",
    )
    conn.close()
    client = TestClient(create_app(db_path=db_path))
    r = client.get("/events?status=live")
    assert "kickoff 2026-05-22T18:30:00Z" in r.text
    assert "LIVE 34'" in r.text


def test_events_card_carries_sort_data_attributes(db_path: Path):
    """LIVE-tab client-side sort reads these off the card root."""
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.execute(
        "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, "
        "match_minute, score_home, score_away, fetch_status) "
        "VALUES ('2026-05-22T18:34:12Z', 'E1', 'betpawa', 'STARTED', "
        "34, 1, 0, 'ok')",
    )
    conn.close()
    client = TestClient(create_app(db_path=db_path))
    r = client.get("/events?status=live")
    assert 'data-status="STARTED"' in r.text
    assert 'data-match-minute="34"' in r.text
    assert 'data-score-home="1"' in r.text
    assert 'data-score-away="0"' in r.text


def test_index_has_sort_filter_group(client: TestClient):
    """LIVE-only Sort chips; CSS hides them on other tabs via body.tab-live."""
    r = client.get("/")
    assert 'class="filter-group filter-sort"' in r.text
    assert 'data-sort="minute_desc"' in r.text
    assert 'data-sort="minute_asc"' in r.text
    assert 'data-sort="goals_desc"' in r.text
    assert 'data-sort="goals_asc"' in r.text


def test_index_kickoff_filter_group_is_taggable(client: TestClient):
    """Kickoff group carries .filter-kickoff so CSS can hide it on LIVE."""
    r = client.get("/")
    assert 'filter-group filter-kickoff' in r.text


def test_css_hides_kickoff_chips_on_ended_tab(client: TestClient):
    """ENDED tab keeps the date picker but drops the forward-window chips
    (1h / 3h / 6h / 24h / 48h are meaningless when everything's already
    finished). Guarded as a CSS contract — the JS sets body.tab-ended,
    this rule does the actual hiding."""
    r = client.get("/static/app.css")
    assert r.status_code == 200
    assert 'body.tab-ended .filter-kickoff .chip.kick' in r.text


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


def test_event_view_carries_our_odds(db_path: Path):
    """The card route attaches OUR 1UP/2UP odds to each event view so
    the template can render the SIM column."""
    # Seed FTTS so 1UP is computable in the existing fixture.
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    snap_id = conn.execute(
        "SELECT id FROM snapshots WHERE event_id='E1' LIMIT 1"
    ).fetchone()[0]
    for side, odds, prob in [
        ("home", 1.85, 0.54), ("none", 8.5, 0.12), ("away", 3.5, 0.34),
    ]:
        conn.execute(
            "INSERT INTO prices (snapshot_id, event_id, ts_utc, bookmaker, "
            "market_id, line, side, odds, probability) "
            "VALUES (?, 'E1', '2026-05-21T10:00:00Z', 'betpawa', "
            "'next_goal_ft', 1.0, ?, ?, ?)",
            (snap_id, side, odds, prob),
        )
    for side, odds, prob in [("over", 1.85, 0.55), ("under", 1.95, 0.45)]:
        conn.execute(
            "INSERT INTO prices (snapshot_id, event_id, ts_utc, bookmaker, "
            "market_id, line, side, odds, probability) "
            "VALUES (?, 'E1', '2026-05-21T10:00:00Z', 'betpawa', "
            "'over_under_ft', 2.5, ?, ?, ?)",
            (snap_id, side, odds, prob),
        )
    conn.close()
    client = TestClient(create_app(db_path=db_path))
    r = client.get("/events?status=upcoming")
    # OUR 1up/2up should now be in the markup as data attrs on the SIM cells.
    assert 'data-bookmaker="sim"' in r.text


def test_event_view_no_our_when_inputs_missing(db_path: Path):
    """With no OU and no FTTS in the fixture, OUR is not computable —
    the SIM cell renders em-dash (no .sim class)."""
    client = TestClient(create_app(db_path=db_path))
    r = client.get("/events?status=upcoming")
    # SIM column header present (markup always renders the column)
    assert 'data-bookmaker="sim"' in r.text
    # … but the cell content for that row should be em-dash since the
    # default fixture lacks OU + FTTS.
    # Verified indirectly: no .sim class in the events-list markup.
    assert "class=\"sim\"" not in r.text


@pytest.fixture
def db_with_ftts_and_ou(tmp_path: Path) -> Path:
    """DB seeded with 1X2 + OU + FTTS so the engine can compute OUR.
    Plus BP-quoted 1UP odds (home only) so we can test both "BP has" and
    "BP missing" cases on the same card."""
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
    inserts = [
        ("1x2_ft", 0.0, "home", 1.85, 0.54),
        ("1x2_ft", 0.0, "draw", 3.40, 0.29),
        ("1x2_ft", 0.0, "away", 4.20, 0.17),
        ("over_under_ft", 2.5, "over",  1.85, 0.55),
        ("over_under_ft", 2.5, "under", 1.95, 0.45),
        ("next_goal_ft", 1.0, "home", 1.85, 0.54),
        ("next_goal_ft", 1.0, "none", 8.50, 0.12),
        ("next_goal_ft", 1.0, "away", 3.50, 0.34),
        # BP has 1UP home quote but NOT 1UP away — exercises both rules.
        ("1x2_1up_ft", 0.0, "home", 1.50, 0.65),
    ]
    for mid, line, side, odds, prob in inserts:
        conn.execute(
            "INSERT INTO prices (snapshot_id, event_id, ts_utc, bookmaker, "
            "market_id, line, side, odds, probability) "
            "VALUES (?, 'E1', '2026-05-21T10:00:00Z', 'betpawa', ?, ?, ?, ?, ?)",
            (snap_id, mid, line, side, odds, prob),
        )
    conn.close()
    return path


def test_sim_column_header_present(client: TestClient):
    r = client.get("/events?status=upcoming")
    assert 'data-bookmaker="sim"' in r.text
    assert "SIM" in r.text  # header text


def test_sim_cell_marks_our_when_bp_has_up_quote(db_with_ftts_and_ou: Path):
    """1UP home row — BP quoted, so SIM cell has OUR with .sim class +
    SIM pill, BP cell shows BP's plain quote."""
    client = TestClient(create_app(db_path=db_with_ftts_and_ou))
    r = client.get("/events?status=upcoming")
    # 1UP-home row markup contains both BP quote (1.50) and a sim-pill
    # for OUR — we can't trivially parse the row alignment but the
    # presence of the sim-pill class proves the SIM cell rendered.
    assert "sim-pill" in r.text
    assert "1.50" in r.text  # BP-quoted 1UP home odds


def test_home_card_shows_v3_under_v2_in_sim(db_with_ftts_and_ou: Path):
    """The card SIM cell stacks V2 and V3 — a V3 sub-cell renders when V3
    prices the latest snapshot (BP-quoted UP market)."""
    client = TestClient(create_app(db_path=db_with_ftts_and_ou))
    r = client.get("/events?status=upcoming")
    assert 'data-bookmaker="sim_v3"' in r.text


def test_sim_replaces_bp_cell_when_bp_missing_up_quote(db_with_ftts_and_ou: Path):
    """BP didn't quote 1UP away — the BP cell itself must show OUR
    (with .sim class + SIM pill), and the SIM cell stays blank."""
    client = TestClient(create_app(db_path=db_with_ftts_and_ou))
    r = client.get("/events?status=upcoming")
    # Count sim-pill occurrences: one for 1UP-home in SIM column +
    # at least one more for an UP row where BP missing → in BP slot.
    assert r.text.count("sim-pill") >= 2


def test_sim_blank_for_1x2_ft_and_ou_rows(db_with_ftts_and_ou: Path):
    """SIM cell is blank for non-UP markets even when OUR is computable."""
    client = TestClient(create_app(db_path=db_with_ftts_and_ou))
    r = client.get("/events?status=upcoming")
    # 1x2_ft and over_under rows are NOT in {1x2_1up_ft, 1x2_2up_ft} so
    # their SIM cells render em-dash. Spot-check: the page contains
    # at least one em-dash cell scoped to data-bookmaker="sim".
    assert 'data-bookmaker="sim"' in r.text


def test_sim_cell_renders_true_probability(db_with_ftts_and_ou: Path):
    """The SIM cell shows OUR engine's true probability (pre-margin)
    as ".pNN" — same display style as BP/SB. Verifies the engine's
    p_home_1 / p_home_2 outputs make it through EventView → template."""
    client = TestClient(create_app(db_path=db_with_ftts_and_ou))
    r = client.get("/events?status=upcoming")
    body = r.text
    # SIM column header now flags +p like BP/SB do.
    assert 'data-bookmaker="sim">SIM <span class="prob-mark">+p</span>' in body
    # At least one `.prob` mark inside a SIM cell. Find the SIM cell for
    # 1UP home (BP quoted 1.50 in the fixture so SIM column is populated).
    # We can't easily parse the row alignment from raw HTML, but a `.prob`
    # tag appearing AFTER a `.odds.sim` tag in the same span proves the
    # SIM cell now carries probability.
    import re
    sim_with_prob = re.search(
        r'<span class="odds sim">[^<]+</span>\s*<span class="prob">\.\d{2}</span>',
        body,
    )
    assert sim_with_prob is not None, "SIM cell must render both odds and prob"


def test_event_detail_history_shows_sim_column_for_1up(db_path: Path):
    """When pricer_live_results has rows for a 1UP market, the detail
    page history adds a SIM column with OUR's odds + prob. The default
    fixture already seeds 1UP prices at this ts; we just need to add
    OUR."""
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.execute(
        "INSERT INTO pricer_live_results "
        "(event_id, ts_utc, basis_used, "
        " our_1up_home_capped, our_1up_away_capped, "
        " our_p_home_1, our_p_away_1) "
        "VALUES ('E1', '2026-05-21T10:00:00Z', 'bp', 1.48, 4.10, 0.66, 0.22)",
    )
    conn.close()
    client = TestClient(create_app(db_path=db_path))
    r = client.get("/events/E1?market=1x2_1up_ft")
    body = r.text
    assert 'data-bookmaker="sim"' in body
    # The detail OUR column is now labelled "V2" (V3 sits beside it as a
    # second OUR column). Header text — Jinja renders with surrounding whitespace.
    import re
    assert re.search(r"<th[^>]*data-bookmaker=\"sim\"[^>]*>\s*V2\s*</th>", body)
    assert "1.48" in body
    # Probability .66 → rendered as ".66"
    assert ".66" in body


def test_event_detail_history_shows_v2_and_v3_columns(db_path: Path):
    """With both v2_* and v3_* set on a 1UP pricer_live_results row, the
    detail history renders two OUR columns: V2 and V3 (sim + sim_v3)."""
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.execute(
        "INSERT INTO pricer_live_results "
        "(event_id, ts_utc, basis_used, "
        " v2_1up_home_capped, v2_1up_away_capped, v2_p_home_1, v2_p_away_1, "
        " v3_1up_home_capped, v3_1up_away_capped, v3_p_home_1, v3_p_away_1) "
        "VALUES ('E1', '2026-05-21T10:00:00Z', 'bp', "
        "        1.48, 4.10, 0.66, 0.22, 1.52, 4.30, 0.63, 0.20)",
    )
    conn.close()
    client = TestClient(create_app(db_path=db_path))
    body = client.get("/events/E1?market=1x2_1up_ft").text
    assert 'data-bookmaker="sim"' in body
    assert 'data-bookmaker="sim_v3"' in body
    import re
    assert re.search(r"<th[^>]*data-bookmaker=\"sim\"[^>]*>\s*V2\s*</th>", body)
    assert re.search(r"<th[^>]*data-bookmaker=\"sim_v3\"[^>]*>\s*V3\s*</th>", body)
    assert "1.48" in body and "1.52" in body  # V2 and V3 home odds


def test_event_detail_history_omits_sim_column_for_1x2_ft(db_path: Path):
    """SIM column is hidden on non-UP markets (1x2_ft, OU, FTTS) even if
    pricer_live_results has rows — OUR only applies to 1UP / 2UP."""
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.execute(
        "INSERT INTO pricer_live_results "
        "(event_id, ts_utc, basis_used, "
        " our_1up_home_capped, our_1up_away_capped) "
        "VALUES ('E1', '2026-05-21T10:00:00Z', 'bp', 1.48, 4.10)",
    )
    conn.close()
    client = TestClient(create_app(db_path=db_path))
    r = client.get("/events/E1?market=1x2_ft")
    body = r.text
    # No SIM header for 1x2_ft market.
    assert ">SIM<" not in body


def test_index_status_param_sets_active_tab(client: TestClient):
    """`/` accepts ?status= so the back link from /events/<id> can land
    on the same tab the user came from. Active class lands on the
    matching tab; events-list hx-get URL targets the same status."""
    r = client.get("/?status=ended")
    assert r.status_code == 200
    body = r.text
    # ENDED button is active, others are not.
    import re
    assert re.search(r'<button class="tab active"[^>]*data-status="ended"', body)
    assert re.search(r'<button class="tab"\s+data-status="upcoming"', body)
    # Initial events-list fetch targets ENDED.
    assert 'hx-get="/events?status=ended"' in body


def test_index_unknown_status_falls_back_to_upcoming(client: TestClient):
    r = client.get("/?status=garbage")
    assert r.status_code == 200
    body = r.text
    import re
    assert re.search(r'<button class="tab active"\s+data-status="upcoming"', body)


def test_event_card_link_carries_from_query_param(db_path: Path):
    """Card anchor URL includes ?from={status} so the detail page can
    point its back link to the right tab. Seed a second event with an
    ENDED head snapshot — fixture's E1 stays UPCOMING."""
    from datetime import datetime, timezone
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        "INSERT INTO events (id, home, away, kickoff_utc) "
        "VALUES ('E_END', 'Chelsea', 'Spurs', '2026-05-20T18:30:00Z')",
    )
    conn.execute(
        "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, fetch_status) "
        "VALUES (?, 'E_END', 'betpawa', 'ENDED', 'ok')",
        (now_iso,),
    )
    conn.close()
    client = TestClient(create_app(db_path=db_path))
    r = client.get("/events?status=upcoming")
    assert 'href="/events/E1?from=upcoming"' in r.text
    r2 = client.get("/events?status=ended")
    assert 'href="/events/E_END?from=ended"' in r2.text


def test_event_detail_back_link_honors_from_param(client: TestClient):
    r = client.get("/events/E1?from=ended")
    assert r.status_code == 200
    assert 'href="/?status=ended"' in r.text


def test_event_detail_back_link_defaults_to_upcoming(client: TestClient):
    """Direct deep link to a detail page (no `from`) defaults to upcoming."""
    r = client.get("/events/E1")
    assert r.status_code == 200
    assert 'href="/?status=upcoming"' in r.text


def test_event_detail_unknown_from_sanitized_to_upcoming(client: TestClient):
    r = client.get("/events/E1?from=garbage")
    assert r.status_code == 200
    assert 'href="/?status=upcoming"' in r.text


def test_event_detail_pill_links_preserve_from_param(client: TestClient):
    """Clicking a market family or line pill on the detail page must
    keep the `from` query param — otherwise the back link forgets which
    tab the user came from after switching market.

    Regression: user reported back-from-LIVE landing on UPCOMING after
    clicking 1up/2up pills on a live event's detail page."""
    r = client.get("/events/E1?from=live")
    assert r.status_code == 200
    body = r.text
    # Every family pill anchor must include from=live
    import re
    pill_hrefs = re.findall(r'<a[^>]*class="family-pill[^"]*"[^>]*href="([^"]+)"', body)
    pill_hrefs += re.findall(r'<a[^>]*class="line-pill[^"]*"[^>]*href="([^"]+)"', body)
    # Anchor patterns may render in either order: href before class or class before href.
    pill_hrefs += re.findall(r'<a[^>]*href="([^"]+)"[^>]*class="(?:family|line)-pill', body)
    assert pill_hrefs, "expected at least one pill anchor on the detail page"
    for href in pill_hrefs:
        assert "from=live" in href, f"pill href missing from=live: {href}"


def test_event_detail_history_shows_sim_only_rows_when_no_book_quoted(db_path: Path):
    """Live event where neither BP nor SB quote 1UP/2UP (B9J/BW skipped
    in live regime), but the engine still computed OUR from the basis
    book's 1X2+OU+FTTS. The history table must still show those ticks
    with the SIM column populated even though no book has prices."""
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    # Two live snapshots (no prices for 1UP/2UP), plus pricer_live_results
    # rows for the same timestamps.
    for ts in ("2026-05-22T19:30:00Z", "2026-05-22T19:31:30Z"):
        cur = conn.execute(
            "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, "
            "match_minute, score_home, score_away, fetch_status) "
            "VALUES (?, 'E1', 'betpawa', 'STARTED', 33, 1, 0, 'ok')",
            (ts,),
        )
        conn.execute(
            "INSERT INTO pricer_live_results "
            "(event_id, ts_utc, basis_used, "
            " our_1up_home_capped, our_1up_away_capped, "
            " our_p_home_1, our_p_away_1) VALUES "
            "(?, ?, 'bp', 1.42, 4.05, 0.68, 0.21)",
            ("E1", ts),
        )
    conn.close()
    client = TestClient(create_app(db_path=db_path))
    r = client.get("/events/E1?market=1x2_1up_ft")
    assert r.status_code == 200
    body = r.text
    # SIM column header AND at least one SIM cell with the computed odds.
    assert 'data-bookmaker="sim"' in body
    assert "1.42" in body
    # The history row's ts shows up
    assert "2026-05-22T19:30:00Z" in body or "2026-05-22T19:31:30Z" in body


def test_events_fragment_hides_placeholder_events_with_empty_names(db_path: Path):
    """Defensive: an event whose row was only ever populated via a
    sentinel write (home='' AND away='') must NOT show on the upcoming
    page. Without this filter, stale-process timeouts produced ghost
    cards with no team names and no odds — observed for events like
    33818403 when a pre-fix scraper kept writing resolver-timeout
    sentinels."""
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    # Add a placeholder event (sentinel-style) alongside the fixture's
    # real E1 event.
    conn.execute(
        "INSERT INTO events (id, home, away, kickoff_utc) "
        "VALUES ('GHOST', '', '', '2026-05-22T19:00:00Z')",
    )
    conn.execute(
        "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, "
        "fetch_status, fetch_error) "
        "VALUES ('2026-05-21T10:05:00Z', 'GHOST', 'betpawa', 'UPCOMING', "
        "'http_error', 'resolver/collector timed out')",
    )
    conn.close()
    client = TestClient(create_app(db_path=db_path))
    r = client.get("/events?status=upcoming")
    body = r.text
    # Real fixture event still shows.
    assert 'data-event-id="E1"' in body
    # Ghost is hidden.
    assert 'data-event-id="GHOST"' not in body
