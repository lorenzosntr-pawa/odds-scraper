"""Tests for the startup _reap_stuck_started_events helper."""
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from odds_scraper.main import _reap_stuck_started_events
from odds_scraper.models import Bookmaker
from odds_scraper.writer import SqliteWriter


def _seed_event(
    conn: sqlite3.Connection,
    *, event_id: str, kickoff_utc: datetime, status: str,
    score: tuple[int, int] | None = None,
    head_ts: datetime | None = None,
) -> None:
    """Seed event + one snapshot. head_ts defaults to kickoff_utc; pass
    a fresher value to simulate an active watcher (the reaper's stale-
    head branch only fires when head_ts is older than its threshold)."""
    conn.execute(
        "INSERT INTO events (id, home, away, kickoff_utc) "
        "VALUES (?, 'A', 'B', ?)",
        (event_id, kickoff_utc.strftime("%Y-%m-%dT%H:%M:%SZ")),
    )
    score_home, score_away = score if score else (None, None)
    ts = (head_ts or kickoff_utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, "
        "match_minute, score_home, score_away, fetch_status) "
        "VALUES (?, ?, 'betpawa', ?, 90, ?, ?, 'ok')",
        (ts, event_id, status, score_home, score_away),
    )


async def test_reaper_writes_ended_for_stuck_started_events(tmp_path: Path):
    """An event with kickoff far in the past and latest snapshot STARTED
    must get a synthetic ENDED snapshot written on next startup."""
    db_path = tmp_path / "odds.db"
    # Init schema via the writer ctx manager once
    async with SqliteWriter(db_path) as w:
        pass
    seed = sqlite3.connect(str(db_path), isolation_level=None)
    long_ago = datetime.now(timezone.utc) - timedelta(hours=4)
    _seed_event(seed, event_id="STUCK", kickoff_utc=long_ago,
                status="STARTED", score=(2, 1))
    seed.close()

    async with SqliteWriter(db_path) as w:
        reaped = await _reap_stuck_started_events(
            db_path, w, watchdog_seconds=10800,
        )
    assert reaped == 1

    check = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    check.row_factory = sqlite3.Row
    rows = check.execute(
        "SELECT bookmaker, status, score_home, score_away "
        "FROM snapshots WHERE event_id = 'STUCK' AND status = 'ENDED' "
        "ORDER BY bookmaker"
    ).fetchall()
    check.close()
    assert len(rows) == len(Bookmaker)
    assert all(r["status"] == "ENDED" for r in rows)
    assert all(r["score_home"] == 2 and r["score_away"] == 1 for r in rows)


async def test_reaper_skips_events_within_watchdog_window(tmp_path: Path):
    """An event kicked off recently is genuinely in-play — don't reap."""
    db_path = tmp_path / "odds.db"
    async with SqliteWriter(db_path) as w:
        pass
    seed = sqlite3.connect(str(db_path), isolation_level=None)
    recent = datetime.now(timezone.utc) - timedelta(minutes=20)
    fresh_head = datetime.now(timezone.utc) - timedelta(seconds=30)
    _seed_event(seed, event_id="FRESH", kickoff_utc=recent,
                status="STARTED", score=(0, 0), head_ts=fresh_head)
    seed.close()

    async with SqliteWriter(db_path) as w:
        reaped = await _reap_stuck_started_events(
            db_path, w, watchdog_seconds=10800,
        )
    assert reaped == 0


async def test_reaper_skips_already_ended_events(tmp_path: Path):
    """Events already at ENDED — even past the watchdog window — must not
    be re-reaped on startup."""
    db_path = tmp_path / "odds.db"
    async with SqliteWriter(db_path) as w:
        pass
    seed = sqlite3.connect(str(db_path), isolation_level=None)
    long_ago = datetime.now(timezone.utc) - timedelta(hours=4)
    _seed_event(seed, event_id="DONE", kickoff_utc=long_ago,
                status="ENDED", score=(1, 0))
    seed.close()

    async with SqliteWriter(db_path) as w:
        reaped = await _reap_stuck_started_events(
            db_path, w, watchdog_seconds=10800,
        )
    assert reaped == 0


async def test_reaper_reaps_event_with_stale_head_within_watchdog_window(tmp_path: Path):
    """Watcher hung mid-match: kickoff is only 90 min ago (within the 3h
    watchdog window) but head_ts hasn't been updated in 15 minutes.
    The reaper's new staleness branch must catch this."""
    db_path = tmp_path / "odds.db"
    async with SqliteWriter(db_path) as w:
        pass
    seed = sqlite3.connect(str(db_path), isolation_level=None)
    recent_kickoff = datetime.now(timezone.utc) - timedelta(minutes=90)
    stale_head = datetime.now(timezone.utc) - timedelta(minutes=15)
    seed.execute(
        "INSERT INTO events (id, home, away, kickoff_utc) "
        "VALUES ('HUNG', 'A', 'B', ?)",
        (recent_kickoff.strftime("%Y-%m-%dT%H:%M:%SZ"),),
    )
    seed.execute(
        "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, "
        "match_minute, score_home, score_away, fetch_status) "
        "VALUES (?, 'HUNG', 'betpawa', 'STARTED', 91, 2, 1, 'ok')",
        (stale_head.strftime("%Y-%m-%dT%H:%M:%SZ"),),
    )
    seed.close()

    async with SqliteWriter(db_path) as w:
        reaped = await _reap_stuck_started_events(
            db_path, w,
            watchdog_seconds=10800,         # 3h — kickoff is INSIDE this
            head_stale_seconds=600,         # 10 min — head IS older
        )
    assert reaped == 1


async def test_reaper_skips_alive_watcher_with_fresh_head(tmp_path: Path):
    """Genuinely in-play match: kickoff 1h ago and head_ts only 1 min old
    (active watcher). Reaper must not touch it."""
    db_path = tmp_path / "odds.db"
    async with SqliteWriter(db_path) as w:
        pass
    seed = sqlite3.connect(str(db_path), isolation_level=None)
    recent_kickoff = datetime.now(timezone.utc) - timedelta(minutes=60)
    fresh_head = datetime.now(timezone.utc) - timedelta(minutes=1)
    seed.execute(
        "INSERT INTO events (id, home, away, kickoff_utc) "
        "VALUES ('LIVE', 'A', 'B', ?)",
        (recent_kickoff.strftime("%Y-%m-%dT%H:%M:%SZ"),),
    )
    seed.execute(
        "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, "
        "match_minute, score_home, score_away, fetch_status) "
        "VALUES (?, 'LIVE', 'betpawa', 'STARTED', 62, 0, 0, 'ok')",
        (fresh_head.strftime("%Y-%m-%dT%H:%M:%SZ"),),
    )
    seed.close()

    async with SqliteWriter(db_path) as w:
        reaped = await _reap_stuck_started_events(
            db_path, w,
            watchdog_seconds=10800,
            head_stale_seconds=600,
        )
    assert reaped == 0
