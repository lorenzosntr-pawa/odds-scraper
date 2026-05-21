import asyncio
import csv
from datetime import datetime, timezone
from pathlib import Path

from odds_scraper.models import (
    Bookmaker, EventStatus, FetchStatus, PriceKey, Snapshot, build_csv_header,
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
        fetch_status=FetchStatus.OK,
        fetch_error="",
        prices={
            PriceKey("1x2_ft", None, "home"): (1.5 + idx * 0.01, None),
        },
    )


async def test_header_written_once_on_fresh_file(tmp_path: Path):
    path = tmp_path / "out.csv"
    async with CsvWriter(path) as w:
        await w.append([_make_snap(0)])
    async with CsvWriter(path) as w:
        await w.append([_make_snap(1)])
    rows = list(csv.reader(path.read_text(encoding="utf-8").splitlines()))
    assert rows[0] == list(build_csv_header())
    assert len(rows) == 3


async def test_concurrent_appends_do_not_interleave(tmp_path: Path):
    path = tmp_path / "out.csv"
    snaps_a = [_make_snap(i, Bookmaker.BETPAWA) for i in range(50)]
    snaps_b = [_make_snap(i, Bookmaker.SPORTYBET) for i in range(50)]

    async with CsvWriter(path) as w:
        await asyncio.gather(w.append(snaps_a), w.append(snaps_b))

    rows = list(csv.reader(path.read_text(encoding="utf-8").splitlines()))
    header = build_csv_header()
    assert rows[0] == list(header)
    data = rows[1:]
    assert len(data) == 100
    assert all(len(r) == len(header) for r in data)
    bookmaker_col = header.index("bookmaker")
    bookmakers = [r[bookmaker_col] for r in data]
    assert bookmakers.count("betpawa") == 50
    assert bookmakers.count("sportybet") == 50


async def test_old_header_file_is_renamed_with_v1_suffix(tmp_path: Path):
    path = tmp_path / "odds_snapshots.csv"
    old_header = (
        "ts_utc,event_bp_id,sr_id,genius_id,home,away,kickoff_utc,"
        "status,match_minute,score_home,score_away,"
        "bookmaker,market,outcome,odds,probability,fetch_status,fetch_error\n"
    )
    path.write_text(old_header + "2026-05-20T11:00:00Z,33,,,,A,B,UPCOMING,,,"
                                  ",betpawa,1x2_1up_ft,home,1.85,0.54,ok,\n",
                    encoding="utf-8")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    async with CsvWriter(path) as w:
        await w.append([_make_snap(0)])

    renamed = tmp_path / f"odds_snapshots_v1_{today}.csv"
    assert renamed.exists(), "old file must be renamed with v1 suffix"
    assert "1x2_1up_ft,home,1.85" in renamed.read_text(encoding="utf-8")
    rows = list(csv.reader(path.read_text(encoding="utf-8").splitlines()))
    assert rows[0] == list(build_csv_header())
    assert len(rows) == 2


async def test_existing_new_header_file_is_appended_not_renamed(tmp_path: Path):
    path = tmp_path / "odds_snapshots.csv"
    new_header_line = ",".join(build_csv_header()) + "\n"
    path.write_text(new_header_line, encoding="utf-8")

    async with CsvWriter(path) as w:
        await w.append([_make_snap(0)])

    siblings = list(tmp_path.glob("odds_snapshots_v1_*.csv"))
    assert siblings == []
    rows = list(csv.reader(path.read_text(encoding="utf-8").splitlines()))
    assert rows[0] == list(build_csv_header())
    assert len(rows) == 2


async def test_to_csv_row_value_round_trips_to_correct_column(tmp_path: Path):
    path = tmp_path / "out.csv"
    snap = _make_snap(0, Bookmaker.BETPAWA)
    async with CsvWriter(path) as w:
        await w.append([snap])
    rows = list(csv.reader(path.read_text(encoding="utf-8").splitlines()))
    header = build_csv_header()
    data = rows[1]
    home_odds_col = header.index("1x2_ft_home_odds")
    assert data[home_odds_col] == "1.50"
