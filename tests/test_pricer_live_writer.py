"""Tests for per-tick OUR persistence (pricer/live_writer.py + schema v5)."""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from odds_scraper.db_schema import SCHEMA_VERSION, init_schema
from odds_scraper.models import (
    Bookmaker, EventStatus, FetchStatus, PriceKey, Snapshot,
)
from odds_scraper.pricer import live_writer
from odds_scraper.writer import SqliteWriter


def _tick_snapshot(
    bookmaker: Bookmaker, *, with_prob: bool, event_id: str = "E1",
) -> Snapshot:
    """A single per-bookmaker Snapshot for one tick. The four-snapshot
    set covers the engine inputs (1X2 + OU + FTTS) for the BP basis."""
    base = {
        PriceKey("1x2_ft", None, "home"): (1.85, 0.54 if with_prob else None),
        PriceKey("1x2_ft", None, "draw"): (3.40, 0.29 if with_prob else None),
        PriceKey("1x2_ft", None, "away"): (4.20, 0.17 if with_prob else None),
        PriceKey("over_under_ft", 2.5, "over"):  (1.85, 0.55 if with_prob else None),
        PriceKey("over_under_ft", 2.5, "under"): (1.95, 0.45 if with_prob else None),
        PriceKey("next_goal_ft", 1.0, "home"): (1.85, 0.54 if with_prob else None),
        PriceKey("next_goal_ft", 1.0, "none"): (8.50, 0.12 if with_prob else None),
        PriceKey("next_goal_ft", 1.0, "away"): (3.50, 0.34 if with_prob else None),
    }
    ts = datetime(2026, 5, 22, 18, 30, tzinfo=timezone.utc)
    return Snapshot(
        ts_utc=ts, event_bp_id=event_id, sr_id="", genius_id="",
        home="A", away="B", kickoff_utc=ts,
        status=EventStatus.STARTED,
        match_minute=12, score_home=0, score_away=0,
        bookmaker=bookmaker, fetch_status=FetchStatus.OK, fetch_error="",
        prices=base,
    )


def test_schema_v5_creates_pricer_live_results(tmp_path: Path):
    conn = sqlite3.connect(str(tmp_path / "x.db"), isolation_level=None)
    init_schema(conn)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert "pricer_live_results" in tables
    assert SCHEMA_VERSION == 5


def test_compute_and_write_inserts_row(tmp_path: Path):
    conn = sqlite3.connect(str(tmp_path / "x.db"), isolation_level=None)
    init_schema(conn)
    # BP has full inputs (probs); SB/B9J/BW just have 1X2 odds — engine
    # uses BP basis, persists one row keyed by (event_id, ts_utc).
    rows = [
        _tick_snapshot(Bookmaker.BETPAWA, with_prob=True),
        _tick_snapshot(Bookmaker.SPORTYBET, with_prob=False),
        _tick_snapshot(Bookmaker.BET9JA, with_prob=False),
        _tick_snapshot(Bookmaker.BETWAY, with_prob=False),
    ]
    ok = live_writer.compute_and_write(
        conn, "E1", "2026-05-22T18:30:00Z", rows, (0, 0),
    )
    assert ok is True
    persisted = conn.execute(
        "SELECT basis_used, our_p_home_1, our_1up_home_capped, "
        "our_p_home_2, our_2up_home_capped "
        "FROM pricer_live_results WHERE event_id=? AND ts_utc=?",
        ("E1", "2026-05-22T18:30:00Z"),
    ).fetchone()
    assert persisted is not None
    assert persisted[0] == "bp"
    assert persisted[1] is not None  # 1UP prob computed
    assert persisted[2] is not None  # 1UP capped odds computed
    assert persisted[3] is not None  # 2UP prob computed
    assert persisted[4] is not None  # 2UP capped odds computed


def test_compute_and_write_returns_false_on_insufficient_inputs(tmp_path: Path):
    """No 1X2 prob anywhere → engine inputs unbuildable → no row written."""
    conn = sqlite3.connect(str(tmp_path / "x.db"), isolation_level=None)
    init_schema(conn)
    no_prob_rows = [
        _tick_snapshot(b, with_prob=False) for b in Bookmaker
    ]
    ok = live_writer.compute_and_write(
        conn, "E1", "2026-05-22T18:30:00Z", no_prob_rows, (0, 0),
    )
    assert ok is False
    n = conn.execute(
        "SELECT COUNT(*) FROM pricer_live_results WHERE event_id='E1'"
    ).fetchone()[0]
    assert n == 0


def test_compute_and_write_is_idempotent(tmp_path: Path):
    """Repeated calls at the same (event_id, ts_utc) must not duplicate."""
    conn = sqlite3.connect(str(tmp_path / "x.db"), isolation_level=None)
    init_schema(conn)
    rows = [_tick_snapshot(b, with_prob=(b == Bookmaker.BETPAWA))
            for b in Bookmaker]
    for _ in range(3):
        live_writer.compute_and_write(
            conn, "E1", "2026-05-22T18:30:00Z", rows, (0, 0),
        )
    n = conn.execute(
        "SELECT COUNT(*) FROM pricer_live_results WHERE event_id='E1'"
    ).fetchone()[0]
    assert n == 1


async def test_writer_append_pricer_live_writes_row(tmp_path: Path):
    """SqliteWriter.append_pricer_live wraps live_writer.compute_and_write
    in the same lock + executor that protects append() — same tick should
    produce a single row."""
    db_path = tmp_path / "odds.db"
    async with SqliteWriter(db_path) as w:
        rows = [_tick_snapshot(b, with_prob=(b == Bookmaker.BETPAWA))
                for b in Bookmaker]
        ok = await w.append_pricer_live(
            "E1", "2026-05-22T18:30:00Z", rows, (0, 0),
        )
    assert ok is True
    check = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    n = check.execute(
        "SELECT COUNT(*) FROM pricer_live_results WHERE event_id='E1'"
    ).fetchone()[0]
    check.close()
    assert n == 1
