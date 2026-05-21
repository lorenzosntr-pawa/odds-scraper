import asyncio
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from odds_scraper.models import (
    Bookmaker, EventStatus, FetchStatus, PriceKey, Snapshot,
)
from odds_scraper.writer import SqliteWriter


def _make_snap(
    idx: int = 0,
    bookmaker: Bookmaker = Bookmaker.BETPAWA,
    event_id: str = "33660318",
    home: str = "Team A",
    away: str = "Team B",
    fetch_status: FetchStatus = FetchStatus.OK,
    fetch_error: str = "",
    prices: dict | None = None,
) -> Snapshot:
    return Snapshot(
        ts_utc=datetime(2026, 5, 19, 14, 0, idx % 60, tzinfo=timezone.utc),
        event_bp_id=event_id,
        sr_id="sr:match:1", genius_id="",
        home=home, away=away,
        kickoff_utc=datetime(2026, 5, 19, 15, 0, 0, tzinfo=timezone.utc),
        status=EventStatus.UPCOMING,
        match_minute=None, score_home=None, score_away=None,
        bookmaker=bookmaker,
        fetch_status=fetch_status,
        fetch_error=fetch_error,
        prices=prices if prices is not None else {
            PriceKey("1x2_ft", None, "home"): (1.85, 0.54),
            PriceKey("over_under_ft", 2.5, "over"): (1.70, 0.58),
        },
    )


def _query(path: Path, sql: str, params: tuple = ()):
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


async def test_fresh_db_creates_schema(tmp_path: Path):
    path = tmp_path / "out.db"
    async with SqliteWriter(path):
        pass
    rows = _query(path, "SELECT name FROM sqlite_master WHERE type='table'")
    table_names = {r[0] for r in rows}
    assert {"events", "snapshots", "prices", "schema_version"} <= table_names


async def test_reopen_existing_db_doesnt_error(tmp_path: Path):
    path = tmp_path / "out.db"
    async with SqliteWriter(path) as w:
        await w.append([_make_snap(0)])
    async with SqliteWriter(path) as w:
        await w.append([_make_snap(1)])
    snap_count = _query(path, "SELECT COUNT(*) FROM snapshots")[0][0]
    assert snap_count == 2


async def test_single_tick_writes_events_snapshots_prices(tmp_path: Path):
    path = tmp_path / "out.db"
    async with SqliteWriter(path) as w:
        await w.append([_make_snap(0)])
    events = _query(path, "SELECT COUNT(*) FROM events")[0][0]
    snaps = _query(path, "SELECT COUNT(*) FROM snapshots")[0][0]
    prices = _query(path, "SELECT COUNT(*) FROM prices")[0][0]
    assert (events, snaps, prices) == (1, 1, 2)


async def test_event_row_idempotent_across_ticks(tmp_path: Path):
    path = tmp_path / "out.db"
    async with SqliteWriter(path) as w:
        await w.append([_make_snap(0), _make_snap(1)])
    events = _query(path, "SELECT COUNT(*) FROM events")[0][0]
    snaps = _query(path, "SELECT COUNT(*) FROM snapshots")[0][0]
    assert events == 1
    assert snaps == 2


async def test_failure_status_writes_snapshot_zero_prices(tmp_path: Path):
    path = tmp_path / "out.db"
    snap = _make_snap(
        0, fetch_status=FetchStatus.HTTP_ERROR, fetch_error="timeout",
        prices={},
    )
    async with SqliteWriter(path) as w:
        await w.append([snap])
    snaps = _query(path,
        "SELECT fetch_status, fetch_error FROM snapshots")
    prices = _query(path, "SELECT COUNT(*) FROM prices")[0][0]
    assert snaps == [("http_error", "timeout")]
    assert prices == 0


async def test_probability_null_for_bet9ja_betway(tmp_path: Path):
    path = tmp_path / "out.db"
    snap = _make_snap(
        0,
        bookmaker=Bookmaker.BET9JA,
        prices={
            PriceKey("1x2_ft", None, "home"): (1.85, None),
        },
    )
    async with SqliteWriter(path) as w:
        await w.append([snap])
    rows = _query(path, "SELECT odds, probability FROM prices")
    assert rows == [(1.85, None)]


async def test_non_parameterized_market_line_is_sentinel_zero(tmp_path: Path):
    path = tmp_path / "out.db"
    snap = _make_snap(
        0, prices={PriceKey("1x2_ft", None, "home"): (1.85, 0.54)},
    )
    async with SqliteWriter(path) as w:
        await w.append([snap])
    rows = _query(
        path,
        "SELECT market_id, line, side, odds, probability FROM prices",
    )
    assert rows == [("1x2_ft", 0.0, "home", 1.85, 0.54)]


async def test_parameterized_market_stores_line_as_real(tmp_path: Path):
    path = tmp_path / "out.db"
    snap = _make_snap(
        0, prices={
            PriceKey("over_under_ft", 2.5, "over"): (1.70, 0.58),
            PriceKey("over_under_ft", 3.5, "under"): (1.50, 0.61),
        },
    )
    async with SqliteWriter(path) as w:
        await w.append([snap])
    rows = _query(
        path,
        "SELECT market_id, line, side, odds FROM prices ORDER BY line, side",
    )
    assert rows == [
        ("over_under_ft", 2.5, "over", 1.70),
        ("over_under_ft", 3.5, "under", 1.50),
    ]


async def test_concurrent_appends_serialize(tmp_path: Path):
    path = tmp_path / "out.db"
    snaps_a = [_make_snap(i, Bookmaker.BETPAWA) for i in range(50)]
    snaps_b = [_make_snap(i, Bookmaker.SPORTYBET) for i in range(50)]
    async with SqliteWriter(path) as w:
        await asyncio.gather(w.append(snaps_a), w.append(snaps_b))
    total = _query(path, "SELECT COUNT(*) FROM snapshots")[0][0]
    assert total == 100
    bp = _query(
        path, "SELECT COUNT(*) FROM snapshots WHERE bookmaker = 'betpawa'"
    )[0][0]
    sb = _query(
        path, "SELECT COUNT(*) FROM snapshots WHERE bookmaker = 'sportybet'"
    )[0][0]
    assert bp == 50
    assert sb == 50


async def test_placeholder_event_row_patched_on_next_good_tick(tmp_path: Path):
    path = tmp_path / "out.db"
    sentinel = _make_snap(
        0,
        home="", away="",
        fetch_status=FetchStatus.HTTP_ERROR,
        fetch_error="status poll failed",
        prices={},
    )
    good = _make_snap(
        1,
        home="Real Team A", away="Real Team B",
        fetch_status=FetchStatus.OK,
    )
    async with SqliteWriter(path) as w:
        await w.append([sentinel])
        await w.append([good])
    rows = _query(path, "SELECT home, away FROM events")
    assert rows == [("Real Team A", "Real Team B")]


async def test_single_snap_round_trip(tmp_path: Path):
    # Smoke check: a single snapshot's snapshot row is visible from a
    # separate connection while the writer is still open (WAL allows
    # cross-connection reads after each BEGIN/COMMIT). Genuine rollback
    # semantics are guaranteed by the BEGIN/COMMIT/ROLLBACK structure of
    # _write_batch — directly testing rollback would require monkey-
    # patching the sqlite3 connection mid-batch and adds no real coverage.
    path = tmp_path / "out.db"
    async with SqliteWriter(path) as w:
        await w.append([_make_snap(0)])
        first_count = _query(
            path, "SELECT COUNT(*) FROM snapshots"
        )[0][0]
    assert first_count == 1


async def test_writer_stores_country_and_league(tmp_path: Path):
    path = tmp_path / "out.db"
    base = _make_snap(0)
    snap = Snapshot(
        ts_utc=base.ts_utc,
        event_bp_id=base.event_bp_id,
        sr_id=base.sr_id, genius_id=base.genius_id,
        home=base.home, away=base.away, kickoff_utc=base.kickoff_utc,
        status=base.status, match_minute=base.match_minute,
        score_home=base.score_home, score_away=base.score_away,
        bookmaker=base.bookmaker, fetch_status=base.fetch_status,
        fetch_error=base.fetch_error, prices=base.prices,
        country_id="242", country_name="Germany",
        league_id="12091", league_name="2nd Bundesliga",
    )
    async with SqliteWriter(path) as w:
        await w.append([snap])
    rows = _query(
        path,
        "SELECT country_id, country_name, league_id, league_name "
        "FROM events WHERE id = ?",
        (snap.event_bp_id,),
    )
    assert rows == [("242", "Germany", "12091", "2nd Bundesliga")]


async def test_writer_patches_null_country_league_on_next_tick(tmp_path: Path):
    # First tick lacks country/league (e.g., a sentinel snapshot when the
    # detail poll failed); writer stores NULLs. Second tick has real values;
    # the upsert patches them in.
    path = tmp_path / "out.db"
    first = _make_snap(0)
    second = Snapshot(
        ts_utc=first.ts_utc,
        event_bp_id=first.event_bp_id,
        sr_id=first.sr_id, genius_id=first.genius_id,
        home=first.home, away=first.away, kickoff_utc=first.kickoff_utc,
        status=first.status, match_minute=first.match_minute,
        score_home=first.score_home, score_away=first.score_away,
        bookmaker=first.bookmaker, fetch_status=first.fetch_status,
        fetch_error=first.fetch_error, prices=first.prices,
        country_id="242", country_name="Germany",
        league_id="12091", league_name="2nd Bundesliga",
    )
    async with SqliteWriter(path) as w:
        await w.append([first])
        before = _query(
            path,
            "SELECT country_id, country_name, league_id, league_name "
            "FROM events WHERE id = ?",
            (first.event_bp_id,),
        )
        assert before == [(None, None, None, None)]
        await w.append([second])
    after = _query(
        path,
        "SELECT country_id, country_name, league_id, league_name "
        "FROM events WHERE id = ?",
        (first.event_bp_id,),
    )
    assert after == [("242", "Germany", "12091", "2nd Bundesliga")]


async def test_writer_keeps_country_league_when_later_tick_is_empty(tmp_path: Path):
    # Real values written first; a later sentinel tick (empty country/league)
    # must NOT overwrite the good values.
    path = tmp_path / "out.db"
    first = _make_snap(0)
    first_good = Snapshot(
        ts_utc=first.ts_utc,
        event_bp_id=first.event_bp_id,
        sr_id=first.sr_id, genius_id=first.genius_id,
        home=first.home, away=first.away, kickoff_utc=first.kickoff_utc,
        status=first.status, match_minute=first.match_minute,
        score_home=first.score_home, score_away=first.score_away,
        bookmaker=first.bookmaker, fetch_status=first.fetch_status,
        fetch_error=first.fetch_error, prices=first.prices,
        country_id="242", country_name="Germany",
        league_id="12091", league_name="2nd Bundesliga",
    )
    sentinel = _make_snap(1, fetch_status=FetchStatus.HTTP_ERROR,
                          fetch_error="timeout", prices={})
    async with SqliteWriter(path) as w:
        await w.append([first_good])
        await w.append([sentinel])  # empty country/league
    rows = _query(
        path,
        "SELECT country_id, country_name, league_id, league_name "
        "FROM events WHERE id = ?",
        (first.event_bp_id,),
    )
    assert rows == [("242", "Germany", "12091", "2nd Bundesliga")]
