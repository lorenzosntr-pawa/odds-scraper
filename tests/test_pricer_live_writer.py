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
    # v5 must still apply on any later schema version.
    assert SCHEMA_VERSION >= 5


def test_compute_and_write_inserts_row(tmp_path: Path):
    conn = sqlite3.connect(str(tmp_path / "x.db"), isolation_level=None)
    init_schema(conn)
    conn.row_factory = sqlite3.Row
    # BP has full inputs (probs); SB/B9J/BW just have 1X2 odds — engine
    # uses BP basis, persists one row keyed by (event_id, ts_utc).
    rows = [
        _tick_snapshot(Bookmaker.BETPAWA, with_prob=True),
        _tick_snapshot(Bookmaker.SPORTYBET, with_prob=False),
        _tick_snapshot(Bookmaker.BET9JA, with_prob=False),
        _tick_snapshot(Bookmaker.BETWAY, with_prob=False),
    ]
    ok = live_writer.compute_and_write_from_snapshots(
        conn, "E1", "2026-05-22T18:30:00Z", rows, (0, 0),
    )
    assert ok is True
    row = conn.execute(
        "SELECT basis_used, v3_p_home_1, v3_1up_home_capped, v4_p_home_1 "
        "FROM pricer_live_results WHERE event_id=? AND ts_utc=?",
        ("E1", "2026-05-22T18:30:00Z"),
    ).fetchone()
    assert row is not None
    assert row["basis_used"] == "bp"
    assert row["v3_p_home_1"] is not None      # V3 primary computed
    assert row["v3_1up_home_capped"] is not None  # V3 primary computed
    assert row["v4_p_home_1"] is not None      # V4 best-effort computed


def test_compute_and_write_returns_false_on_insufficient_inputs(tmp_path: Path):
    """No 1X2 prob anywhere → engine inputs unbuildable → no row written."""
    conn = sqlite3.connect(str(tmp_path / "x.db"), isolation_level=None)
    init_schema(conn)
    no_prob_rows = [
        _tick_snapshot(b, with_prob=False) for b in Bookmaker
    ]
    ok = live_writer.compute_and_write_from_snapshots(
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
        live_writer.compute_and_write_from_snapshots(
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


def test_backfill_all_populates_missing_ticks(tmp_path: Path):
    """backfill_all walks every (event_id, ts_utc) tick in snapshots
    that doesn't yet have a pricer_live_results row, computes OUR,
    and inserts. Existing rows are skipped (idempotent re-run)."""
    db = tmp_path / "odds.db"
    # Use the writer to seed two ticks (one prematch UPCOMING, one live)
    import asyncio
    from datetime import timedelta

    rows1 = [_tick_snapshot(b, with_prob=(b == Bookmaker.BETPAWA),
                            event_id="EV1") for b in Bookmaker]
    # Force a different ts for the second tick
    for r in rows1:
        object.__setattr__(r, "status", EventStatus.UPCOMING)

    rows2_ts = datetime(2026, 5, 22, 19, 0, tzinfo=timezone.utc)
    rows2 = []
    for b in Bookmaker:
        snap = _tick_snapshot(b, with_prob=(b == Bookmaker.BETPAWA),
                              event_id="EV1")
        object.__setattr__(snap, "ts_utc", rows2_ts)
        rows2.append(snap)

    async def seed():
        async with SqliteWriter(db) as w:
            await w.append(rows1)
            await w.append(rows2)
    asyncio.get_event_loop().run_until_complete(seed()) if False else asyncio.run(seed())

    conn = sqlite3.connect(str(db), isolation_level=None)
    # No OUR rows yet.
    assert conn.execute(
        "SELECT COUNT(*) FROM pricer_live_results"
    ).fetchone()[0] == 0

    written, skipped = live_writer.backfill_all(conn)
    assert written == 2, f"expected 2 ticks backfilled, got {written}"
    assert skipped == 0

    # Re-run is idempotent — both ticks already covered.
    written2, _ = live_writer.backfill_all(conn)
    assert written2 == 0
    conn.close()


def test_live_writer_v2_columns_now_null(tmp_path: Path):
    """live_writer runs V3 primary — our_* (V1) and v2_* columns are NULL,
    v3_* columns populated."""
    conn = sqlite3.connect(str(tmp_path / "v2.db"), isolation_level=None)
    init_schema(conn)
    conn.row_factory = sqlite3.Row
    rows = [
        _tick_snapshot(Bookmaker.BETPAWA, with_prob=True),
        _tick_snapshot(Bookmaker.SPORTYBET, with_prob=False),
    ]
    ok = live_writer.compute_and_write_from_snapshots(
        conn, "E1", "2026-05-21T10:00:00Z", rows, (0, 0),
    )
    assert ok
    row = conn.execute(
        "SELECT v2_p_home_1, v3_p_home_1 "
        "FROM pricer_live_results WHERE event_id='E1'"
    ).fetchone()
    assert row is not None
    # V2 cells NULL — V2 no longer runs in live pipeline
    assert row["v2_p_home_1"] is None
    # V3 cells populated
    assert row["v3_p_home_1"] is not None
    conn.close()


def test_live_writer_persists_v3_matching_direct_call(tmp_path: Path):
    """live_writer writes v3_* as primary, and the persisted v3 values
    equal a direct engine_v3 call on the same extracted inputs."""
    from odds_scraper.pricer import engine_v3, inputs as input_extract
    conn = sqlite3.connect(str(tmp_path / "v3.db"), isolation_level=None)
    init_schema(conn)
    conn.row_factory = sqlite3.Row
    rows = [
        _tick_snapshot(Bookmaker.BETPAWA, with_prob=True),
        _tick_snapshot(Bookmaker.SPORTYBET, with_prob=False),
    ]
    ok = live_writer.compute_and_write_from_snapshots(
        conn, "E1", "2026-05-22T18:30:00Z", rows, (0, 0),
    )
    assert ok
    row = conn.execute(
        "SELECT v3_2up_home_capped, v3_1up_home_capped, v3_p_home_2 "
        "FROM pricer_live_results WHERE event_id='E1'"
    ).fetchone()
    pbb = live_writer.snapshots_to_prices_by_book(rows)
    engine_inputs, _ = input_extract.extract(pbb)
    engine_inputs["score"] = (0, 0)
    engine_inputs["max_home_lead"] = 0
    engine_inputs["max_away_lead"] = 0
    kw = {k: v for k, v in engine_inputs.items() if not k.startswith("_")}
    direct = engine_v3.price_early_payout_markets(**kw)
    assert row["v3_2up_home_capped"] == direct["market_2up"]["home_margin"]
    assert row["v3_p_home_2"] == direct["p_home_2"]
    conn.close()


def test_v3_crash_skips_tick(tmp_path: Path, monkeypatch):
    """A V3 engine exception (V3 is now the must-succeed primary) drops the
    tick entirely: compute_and_write returns False and no row is written."""
    conn = sqlite3.connect(str(tmp_path / "v3crash.db"), isolation_level=None)
    init_schema(conn)
    conn.row_factory = sqlite3.Row

    monkeypatch.setattr(live_writer.engine_v3, "price_early_payout_markets",
                        lambda **kw: (_ for _ in ()).throw(RuntimeError("v3 down")))
    rows = [
        _tick_snapshot(Bookmaker.BETPAWA, with_prob=True),
        _tick_snapshot(Bookmaker.SPORTYBET, with_prob=False),
    ]
    result = live_writer.compute_and_write_from_snapshots(
        conn, "E2", "2026-05-22T18:30:00Z", rows, (0, 0),
    )
    assert result is False
    n = conn.execute(
        "SELECT COUNT(*) FROM pricer_live_results WHERE event_id='E2'"
    ).fetchone()[0]
    assert n == 0
    conn.close()


def test_backfill_v3_fills_existing_rows_and_is_idempotent(tmp_path: Path):
    """backfill_v3 fills v3_* on rows that lack it (re-extracting inputs from
    `prices`), and is idempotent on re-run."""
    import asyncio
    db = tmp_path / "odds.db"
    rows = [_tick_snapshot(b, with_prob=(b == Bookmaker.BETPAWA), event_id="EV1")
            for b in Bookmaker]

    async def seed():
        async with SqliteWriter(db) as w:
            await w.append(rows)
    asyncio.run(seed())

    conn = sqlite3.connect(str(db), isolation_level=None)
    conn.row_factory = sqlite3.Row
    # Create the pricer_live_results row (writes v3 + v4) for this tick.
    written, _ = live_writer.backfill_all(conn)
    assert written == 1
    # Simulate a pre-v3 row: NULL out every v3_* column.
    conn.execute(
        "UPDATE pricer_live_results SET "
        "v3_p_home_1=NULL, v3_p_away_1=NULL, v3_1up_home_fair=NULL, "
        "v3_1up_home_capped=NULL, v3_1up_away_fair=NULL, v3_1up_away_capped=NULL, "
        "v3_p_home_2=NULL, v3_p_away_2=NULL, v3_2up_home_fair=NULL, "
        "v3_2up_home_capped=NULL, v3_2up_away_fair=NULL, v3_2up_away_capped=NULL"
    )

    updated, skipped = live_writer.backfill_v3(conn)
    assert updated == 1
    row = conn.execute(
        "SELECT v3_2up_home_capped FROM pricer_live_results"
    ).fetchone()
    assert row["v3_2up_home_capped"] is not None     # v3 filled

    again, _ = live_writer.backfill_v3(conn)
    assert again == 0                                  # idempotent
    conn.close()


def _live_trailing_snapshot(bookmaker: Bookmaker) -> Snapshot:
    """Snapshot at score 1-0 minute 91 — V1 heuristic vs V2 DP diverge."""
    base = {
        PriceKey("1x2_ft", None, "home"): (1.85, 0.54),
        PriceKey("1x2_ft", None, "draw"): (3.40, 0.29),
        PriceKey("1x2_ft", None, "away"): (4.20, 0.17),
        PriceKey("over_under_ft", 2.5, "over"):  (1.85, 0.55),
        PriceKey("over_under_ft", 2.5, "under"): (1.95, 0.45),
    }
    ts = datetime(2026, 5, 22, 18, 30, tzinfo=timezone.utc)
    return Snapshot(
        ts_utc=ts, event_bp_id="E1", sr_id="", genius_id="",
        home="A", away="B", kickoff_utc=ts,
        status=EventStatus.STARTED,
        match_minute=91, score_home=1, score_away=0,
        bookmaker=bookmaker, fetch_status=FetchStatus.OK, fetch_error="",
        prices=base,
    )


def test_live_writer_v2_trailing_produces_output(tmp_path: Path):
    """At a live trailing score (1-0), V3 trailing 1UP and V4 trailing 1UP
    are both populated; v2_1up_away_capped is now NULL."""
    conn = sqlite3.connect(str(tmp_path / "v2_live.db"), isolation_level=None)
    init_schema(conn)
    conn.row_factory = sqlite3.Row
    rows = [
        _live_trailing_snapshot(Bookmaker.BETPAWA),
        _live_trailing_snapshot(Bookmaker.SPORTYBET),
    ]
    ok = live_writer.compute_and_write_from_snapshots(
        conn, "E1", "2026-05-22T10:00:00Z", rows, (1, 0),
    )
    assert ok
    row = conn.execute(
        "SELECT v3_1up_away_capped, v4_1up_away_capped, v2_1up_away_capped "
        "FROM pricer_live_results WHERE event_id='E1'"
    ).fetchone()
    assert row is not None
    assert row["v3_1up_away_capped"] is not None  # V3 trailing away populated
    assert row["v4_1up_away_capped"] is not None  # V4 trailing away populated
    assert row["v2_1up_away_capped"] is None       # V2 no longer runs
    conn.close()


def test_live_writer_persists_v3_v4_not_v2(tmp_path):
    import sqlite3
    from odds_scraper.db_schema import init_schema
    from odds_scraper.models import Bookmaker
    from odds_scraper.pricer import live_writer
    conn = sqlite3.connect(str(tmp_path / "v4.db"), isolation_level=None)
    init_schema(conn); conn.row_factory = sqlite3.Row
    rows = [
        _tick_snapshot(Bookmaker.BETPAWA, with_prob=True),
        _tick_snapshot(Bookmaker.SPORTYBET, with_prob=False),
    ]
    assert live_writer.compute_and_write_from_snapshots(
        conn, "E1", "2026-05-22T18:30:00Z", rows, (0, 0))
    row = conn.execute(
        "SELECT v2_p_home_1, v3_p_home_1, v4_p_home_1, "
        "       v3_1up_home_capped, v4_1up_home_capped "
        "FROM pricer_live_results WHERE event_id='E1'").fetchone()
    assert row["v2_p_home_1"] is None
    assert row["v3_p_home_1"] is not None
    assert row["v4_p_home_1"] is not None
    assert row["v3_1up_home_capped"] is not None
    assert row["v4_1up_home_capped"] is not None
    conn.close()


def test_live_writer_v4_crash_keeps_row_with_v3(tmp_path, monkeypatch):
    import sqlite3
    from odds_scraper.db_schema import init_schema
    from odds_scraper.models import Bookmaker
    from odds_scraper.pricer import live_writer
    conn = sqlite3.connect(str(tmp_path / "v4crash.db"), isolation_level=None)
    init_schema(conn); conn.row_factory = sqlite3.Row
    monkeypatch.setattr(live_writer.engine_v4, "price_early_payout_markets",
                        lambda **kw: (_ for _ in ()).throw(RuntimeError("v4 down")))
    rows = [
        _tick_snapshot(Bookmaker.BETPAWA, with_prob=True),
        _tick_snapshot(Bookmaker.SPORTYBET, with_prob=False),
    ]
    assert live_writer.compute_and_write_from_snapshots(
        conn, "E2", "2026-05-22T18:30:00Z", rows, (0, 0))
    row = conn.execute(
        "SELECT v3_2up_home_capped, v4_2up_home_capped "
        "FROM pricer_live_results WHERE event_id='E2'").fetchone()
    assert row["v3_2up_home_capped"] is not None
    assert row["v4_2up_home_capped"] is None
    conn.close()
