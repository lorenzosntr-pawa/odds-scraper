import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from odds_scraper.models import (
    Bookmaker, EventStatus, FetchStatus, PriceKey, Snapshot,
)
from odds_scraper.watcher import EventWatcher, WatcherConfig


def _one_snap(bookmaker: Bookmaker, status: EventStatus,
              prices: dict | None = None) -> Snapshot:
    return Snapshot(
        ts_utc=datetime.now(timezone.utc),
        event_bp_id="33660318",
        sr_id="", genius_id="",
        home="A", away="B",
        kickoff_utc=datetime.now(timezone.utc) + timedelta(minutes=10),
        status=status,
        match_minute=None, score_home=None, score_away=None,
        bookmaker=bookmaker,
        fetch_status=FetchStatus.OK,
        fetch_error="",
        prices=prices or {},
    )


def _snap_list(status=EventStatus.UPCOMING):
    """4 snapshots (one per bookmaker), each with a small prices dict."""
    return [
        _one_snap(b, status, prices={
            PriceKey("1x2_ft", None, "home"): (1.85, 0.54 if b in (
                Bookmaker.BETPAWA, Bookmaker.SPORTYBET) else None),
        })
        for b in Bookmaker
    ]


def _detail(live=False, ended=False) -> dict:
    # Kickoff is set far enough in the future that the watcher's watchdog
    # (10800s after kickoff in cfg) cannot trip during the test, regardless
    # of when the test is actually run.
    future_kickoff = (
        datetime.now(timezone.utc) + timedelta(days=30)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    if ended:
        return {
            "id": "33660318",
            "participants": [{"name": "A"}, {"name": "B"}],
            "startTime": future_kickoff,
            "additionalInfo": {"live": False},
            "results": {
                "display": {"minute": 90, "currentPeriod": {"name": "FT"}},
                "participantPeriodResults": [],
            },
        }
    return {
        "id": "33660318",
        "participants": [{"name": "A"}, {"name": "B"}],
        "startTime": future_kickoff,
        "additionalInfo": {"live": live},
        "results": {
            "display": {"minute": 34, "currentPeriod": {"name": "1H"}},
            "participantPeriodResults": [],
        } if live else None,
    }


@pytest.fixture
def cfg():
    return WatcherConfig(
        prematch_seconds=600,
        live_seconds=90,
        status_retry_backoff_seconds=(5, 15, 45),
        watchdog_after_kickoff_seconds=10800,
    )


async def test_exits_on_ended(cfg):
    bp_client = AsyncMock()
    bp_client.get_event_detail.return_value = _detail(ended=True)
    collector = AsyncMock()
    collector.collect.return_value = _snap_list(EventStatus.ENDED)
    writer = MagicMock()
    writer.append = AsyncMock()
    resolver = AsyncMock(return_value=(
        {Bookmaker.SPORTYBET: "sr:match:1",
         Bookmaker.BET9JA: "b9j-7",
         Bookmaker.BETWAY: "sr:match:1"},
        "sr:match:1", ""))

    watcher = EventWatcher("33660318", cfg, bp_client, collector, writer, resolver)
    await watcher.run()
    assert writer.append.call_count == 1


async def test_cadence_switch_at_kickoff(cfg, monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr("odds_scraper.watcher.asyncio.sleep", fake_sleep)

    statuses = iter([_detail(live=False), _detail(live=True), _detail(ended=True)])
    bp_client = AsyncMock()
    bp_client.get_event_detail.side_effect = lambda _id: next(statuses)
    collector = AsyncMock()
    collector.collect.return_value = _snap_list()
    writer = MagicMock()
    writer.append = AsyncMock()
    resolver = AsyncMock(return_value=(
        {Bookmaker.SPORTYBET: "sr:match:1",
         Bookmaker.BET9JA: "b9j-7",
         Bookmaker.BETWAY: "sr:match:1"},
        "sr:match:1", ""))

    watcher = EventWatcher("33660318", cfg, bp_client, collector, writer, resolver)
    await watcher.run()
    assert sleeps[0] == 600
    assert sleeps[1] == 90


async def test_status_poll_retries_then_emits_sentinel(cfg, monkeypatch):
    monkeypatch.setattr(
        "odds_scraper.watcher.asyncio.sleep", AsyncMock(return_value=None),
    )

    call_count = {"n": 0}

    async def flaky(_):
        call_count["n"] += 1
        if call_count["n"] <= 4:
            raise RuntimeError("net down")
        return _detail(ended=True)

    bp_client = AsyncMock()
    bp_client.get_event_detail.side_effect = flaky
    collector = AsyncMock()
    collector.collect.return_value = _snap_list(EventStatus.ENDED)
    writer = MagicMock()
    writer.append = AsyncMock()
    resolver = AsyncMock(return_value=(
        {Bookmaker.SPORTYBET: "sr:match:1",
         Bookmaker.BET9JA: "b9j-7",
         Bookmaker.BETWAY: "sr:match:1"},
        "sr:match:1", ""))

    watcher = EventWatcher("33660318", cfg, bp_client, collector, writer, resolver)
    await watcher.run()

    written_statuses = []
    for call in writer.append.call_args_list:
        for snap in call.args[0]:
            written_statuses.append(snap.fetch_status)
    assert FetchStatus.HTTP_ERROR in written_statuses


def test_sentinel_rows_produces_one_per_bookmaker(cfg):
    bp_client = AsyncMock()
    collector = AsyncMock()
    writer = MagicMock()
    resolver = AsyncMock()
    watcher = EventWatcher("33660318", cfg, bp_client, collector, writer, resolver)
    rows = watcher._sentinel_rows("status poll failed")
    assert len(rows) == 4
    assert {r.bookmaker for r in rows} == set(Bookmaker)
    assert all(r.fetch_status == FetchStatus.HTTP_ERROR for r in rows)
    assert all(r.fetch_error == "status poll failed" for r in rows)
    assert all(r.prices == {} for r in rows)


def test_log_tick_summary_format(cfg, caplog):
    bp_client = AsyncMock()
    collector = AsyncMock()
    writer = MagicMock()
    resolver = AsyncMock()
    watcher = EventWatcher("33660318", cfg, bp_client, collector, writer, resolver)
    watcher._last_status = EventStatus.STARTED
    rows = [
        _one_snap(Bookmaker.BETPAWA, EventStatus.STARTED, prices={
            PriceKey("1x2_ft", None, "home"): (1.85, 0.54),
            PriceKey("1x2_ft", None, "draw"): (3.20, 0.31),
        }),
        _one_snap(Bookmaker.SPORTYBET, EventStatus.STARTED, prices={
            PriceKey("1x2_ft", None, "home"): (1.90, 0.53),
        }),
        _one_snap(Bookmaker.BET9JA, EventStatus.STARTED, prices={
            PriceKey("1x2_ft", None, "home"): (1.88, None),
        }),
        _one_snap(Bookmaker.BETWAY, EventStatus.STARTED, prices={
            PriceKey("1x2_ft", None, "home"): (1.87, None),
            PriceKey("1x2_ft", None, "draw"): (3.30, None),
            PriceKey("1x2_ft", None, "away"): (4.10, None),
        }),
    ]
    with caplog.at_level(logging.INFO, logger="odds_scraper.watcher"):
        watcher._log_tick_summary(rows)
    msgs = [r.getMessage() for r in caplog.records]
    # Denominators come from MARKET_MANIFEST via _price_cell_count:
    #   27 outcomes (3 simple × 3 sides + 9 O/U lines × 2 sides);
    #   ×2 cells for BP/SB (odds+prob), ×1 for B9J/BW (odds only).
    # If MARKET_MANIFEST changes, update both this test and the watcher's
    # _price_cell_count consumers together.
    expected = "tick 33660318 status=STARTED bp=4/54 sb=2/54 b9j=1/27 bw=3/27"
    assert any(expected in m for m in msgs), f"didn't find expected log: {msgs}"


async def test_watchdog_does_not_trip_when_kickoff_is_in_future(cfg, monkeypatch):
    # A prematch event whose kickoff is days away must NEVER have the
    # watchdog trip — this was the bug causing all watchers to die at
    # exactly cfg.watchdog_after_kickoff_seconds after the watcher started
    # (rather than after kickoff).
    monkeypatch.setattr(
        "odds_scraper.watcher.asyncio.sleep", AsyncMock(return_value=None),
    )

    kickoff_future = (
        datetime.now(timezone.utc) + timedelta(hours=24)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    polls = {"n": 0}

    async def detail_then_ended(_id):
        # First 5 polls return UPCOMING; 6th returns ENDED so we get out
        polls["n"] += 1
        common = {
            "id": "33660318",
            "participants": [{"name": "A"}, {"name": "B"}],
            "startTime": kickoff_future,
        }
        if polls["n"] < 6:
            return {**common, "additionalInfo": {"live": False}, "results": None}
        return {
            **common,
            "additionalInfo": {"live": False},
            "results": {
                "display": {"minute": 90, "currentPeriod": {"name": "FT"}},
                "participantPeriodResults": [],
            },
        }

    bp_client = AsyncMock()
    bp_client.get_event_detail.side_effect = detail_then_ended
    collector = AsyncMock()
    collector.collect.return_value = _snap_list()
    writer = MagicMock()
    writer.append = AsyncMock()
    resolver = AsyncMock(return_value=(
        {Bookmaker.SPORTYBET: "sr:match:1",
         Bookmaker.BET9JA: "b9j-7",
         Bookmaker.BETWAY: "sr:match:1"},
        "sr:match:1", ""))

    watcher = EventWatcher("33660318", cfg, bp_client, collector, writer, resolver)
    await watcher.run()  # returns when ENDED arrives

    # Make sure no watchdog warning was emitted
    assert polls["n"] == 6


async def test_watchdog_trips_after_kickoff_plus_window(monkeypatch):
    # Configure the watchdog window very small (1s) and use a kickoff
    # 10 seconds in the past so the watcher trips immediately on the
    # second tick after observing kickoff.
    cfg = WatcherConfig(
        prematch_seconds=600, live_seconds=90,
        status_retry_backoff_seconds=(5, 15, 45),
        watchdog_after_kickoff_seconds=1,
    )
    monkeypatch.setattr(
        "odds_scraper.watcher.asyncio.sleep", AsyncMock(return_value=None),
    )
    kickoff_past = (
        datetime.now(timezone.utc) - timedelta(seconds=10)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    async def detail(_id):
        return {
            "id": "33660318",
            "participants": [{"name": "A"}, {"name": "B"}],
            "startTime": kickoff_past,
            "additionalInfo": {"live": True},
            "results": {
                "display": {"minute": 34, "currentPeriod": {"name": "1H"}},
                "participantPeriodResults": [],
            },
        }

    bp_client = AsyncMock()
    bp_client.get_event_detail.side_effect = detail
    collector = AsyncMock()
    collector.collect.return_value = _snap_list(EventStatus.STARTED)
    writer = MagicMock()
    writer.append = AsyncMock()
    resolver = AsyncMock(return_value=(
        {Bookmaker.SPORTYBET: "sr:match:1",
         Bookmaker.BET9JA: "b9j-7",
         Bookmaker.BETWAY: "sr:match:1"},
        "sr:match:1", ""))

    watcher = EventWatcher("33660318", cfg, bp_client, collector, writer, resolver)
    await watcher.run()  # watchdog trips → returns
    # No assertion needed beyond "run() returned" — the watchdog trip
    # is what causes the return when status is not ENDED.
