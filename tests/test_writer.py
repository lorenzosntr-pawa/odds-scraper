import asyncio
import csv
from datetime import datetime, timezone
from pathlib import Path

from odds_scraper.models import (
    Bookmaker, CSV_HEADER, EventStatus, FetchStatus, Market, Outcome, Snapshot,
)
from odds_scraper.writer import CsvWriter


def _make_snap(idx: int, bookmaker=Bookmaker.BETPAWA) -> Snapshot:
    return Snapshot(
        ts_utc=datetime(2026, 5, 19, 14, 0, idx % 60, tzinfo=timezone.utc),
        event_bp_id="33660318",
        sr_id="sr:match:1", genius_id="",
        home="A", away="B",
        kickoff_utc=datetime(2026, 5, 19, 15, 0, 0, tzinfo=timezone.utc),
        status=EventStatus.UPCOMING,
        match_minute=None, score_home=None, score_away=None,
        bookmaker=bookmaker,
        market=Market.ONE_UP,
        outcome=Outcome.HOME,
        odds=1.5 + idx * 0.01,
        probability=None,
        fetch_status=FetchStatus.OK,
        fetch_error="",
    )


async def test_header_written_once(tmp_path: Path):
    path = tmp_path / "out.csv"
    async with CsvWriter(path) as w:
        await w.append([_make_snap(0)])
    async with CsvWriter(path) as w:
        await w.append([_make_snap(1)])
    rows = list(csv.reader(path.read_text(encoding="utf-8").splitlines()))
    assert rows[0] == list(CSV_HEADER)
    assert len(rows) == 3


async def test_concurrent_appends_do_not_interleave(tmp_path: Path):
    path = tmp_path / "out.csv"
    snaps_a = [_make_snap(i, Bookmaker.BETPAWA) for i in range(50)]
    snaps_b = [_make_snap(i, Bookmaker.SPORTYBET) for i in range(50)]

    async with CsvWriter(path) as w:
        await asyncio.gather(w.append(snaps_a), w.append(snaps_b))

    rows = list(csv.reader(path.read_text(encoding="utf-8").splitlines()))
    assert rows[0] == list(CSV_HEADER)
    data = rows[1:]
    assert len(data) == 100
    assert all(len(r) == len(CSV_HEADER) for r in data)
    bookmakers = [r[11] for r in data]
    assert bookmakers.count("betpawa") == 50
    assert bookmakers.count("sportybet") == 50
