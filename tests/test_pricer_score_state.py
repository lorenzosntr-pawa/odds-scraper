import sqlite3
from pathlib import Path

import pytest

from odds_scraper.db_schema import init_schema
from odds_scraper.pricer import score_state


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(str(tmp_path / "x.db"), isolation_level=None)
    init_schema(c)
    c.row_factory = sqlite3.Row
    return c


def _seed_event(conn: sqlite3.Connection, ev_id: str) -> None:
    conn.execute(
        "INSERT INTO events (id, home, away, kickoff_utc) "
        "VALUES (?, 'H', 'A', '2026-05-22T18:30:00Z')",
        (ev_id,),
    )


def _seed_snap(
    conn: sqlite3.Connection, ev_id: str, ts: str,
    sh: int | None, sa: int | None, bm: str = "betpawa",
) -> None:
    conn.execute(
        "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, "
        "score_home, score_away, fetch_status) "
        "VALUES (?, ?, ?, 'STARTED', ?, ?, 'ok')",
        (ts, ev_id, bm, sh, sa),
    )


def test_max_leads_so_far_returns_zero_for_unscored_match(db):
    _seed_event(db, "E1")
    _seed_snap(db, "E1", "2026-05-22T18:30:00Z", None, None)
    assert score_state.max_leads_so_far(db, "E1") == (0, 0)


def test_max_leads_so_far_picks_up_home_lead(db):
    _seed_event(db, "E1")
    _seed_snap(db, "E1", "2026-05-22T18:30:00Z", 0, 0)
    _seed_snap(db, "E1", "2026-05-22T18:40:00Z", 1, 0)
    _seed_snap(db, "E1", "2026-05-22T18:50:00Z", 1, 1)
    # Home led by 1 at 18:40, away never led.
    assert score_state.max_leads_so_far(db, "E1") == (1, 0)


def test_max_leads_so_far_tracks_swing(db):
    """Mirrors event 35124382: 1-0 → 1-1 → 1-2 → 2-2 → 3-2.
    Both 1UPs trigger; 2UP never triggers."""
    _seed_event(db, "E1")
    timeline = [
        ("10:00", 0, 0),
        ("10:24", 1, 0),
        ("10:38", 1, 1),
        ("10:47", 1, 2),
        ("11:00", 2, 2),
        ("11:30", 3, 2),
    ]
    for ts, sh, sa in timeline:
        _seed_snap(db, "E1", f"2026-05-24T{ts}:00Z", sh, sa)
    assert score_state.max_leads_so_far(db, "E1") == (1, 1)


def test_max_leads_for_events_returns_running_max_per_tick(db):
    _seed_event(db, "E1")
    timeline = [
        ("10:00", 0, 0), ("10:24", 1, 0), ("10:38", 1, 1),
        ("10:47", 1, 2), ("11:00", 2, 2),
    ]
    for ts, sh, sa in timeline:
        _seed_snap(db, "E1", f"2026-05-24T{ts}:00Z", sh, sa)
    leads = score_state.max_leads_for_events(db, ["E1"])
    assert leads[("E1", "2026-05-24T10:00:00Z")] == (0, 0)
    assert leads[("E1", "2026-05-24T10:24:00Z")] == (1, 0)
    assert leads[("E1", "2026-05-24T10:38:00Z")] == (1, 0)
    assert leads[("E1", "2026-05-24T10:47:00Z")] == (1, 1)
    assert leads[("E1", "2026-05-24T11:00:00Z")] == (1, 1)


def test_max_leads_for_events_isolates_events(db):
    """Running max must reset per event — no cross-contamination."""
    _seed_event(db, "E1")
    _seed_event(db, "E2")
    _seed_snap(db, "E1", "2026-05-24T10:00:00Z", 2, 0)
    _seed_snap(db, "E2", "2026-05-24T11:00:00Z", 0, 0)
    leads = score_state.max_leads_for_events(db, ["E1", "E2"])
    assert leads[("E1", "2026-05-24T10:00:00Z")] == (2, 0)
    assert leads[("E2", "2026-05-24T11:00:00Z")] == (0, 0)


def test_max_leads_for_events_collapses_cross_bookmaker_rows(db):
    """Each bookmaker writes its own snapshot row at a given ts. Helper
    must collapse them to one (event_id, ts_utc) entry via MAX."""
    _seed_event(db, "E1")
    _seed_snap(db, "E1", "2026-05-24T10:00:00Z", 1, 0, bm="betpawa")
    _seed_snap(db, "E1", "2026-05-24T10:00:00Z", 1, 0, bm="sportybet")
    leads = score_state.max_leads_for_events(db, ["E1"])
    assert leads == {("E1", "2026-05-24T10:00:00Z"): (1, 0)}


def test_max_leads_for_events_handles_empty(db):
    assert score_state.max_leads_for_events(db, []) == {}


def test_max_leads_latest_returns_cumulative_max_per_event(db):
    _seed_event(db, "E1")
    _seed_event(db, "E2")
    # E1 swings; max ever is (home=1, away=1).
    for ts, sh, sa in [
        ("10:00", 0, 0), ("10:30", 1, 0), ("10:45", 1, 1), ("11:00", 1, 2),
    ]:
        _seed_snap(db, "E1", f"2026-05-24T{ts}:00Z", sh, sa)
    # E2 only ever 0-0.
    _seed_snap(db, "E2", "2026-05-24T11:00:00Z", 0, 0)
    latest = score_state.max_leads_latest_for_events(db, ["E1", "E2"])
    assert latest["E1"] == (1, 1)
    assert latest["E2"] == (0, 0)


def test_max_leads_latest_omits_events_without_scored_snapshots(db):
    """Pure-prematch events have no score yet — return an empty entry."""
    _seed_event(db, "E1")
    _seed_snap(db, "E1", "2026-05-24T11:00:00Z", None, None)
    assert score_state.max_leads_latest_for_events(db, ["E1"]) == {}
