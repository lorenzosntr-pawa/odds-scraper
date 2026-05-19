from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from odds_scraper.models import (
    Bookmaker, EventStatus, FetchStatus, Market, Outcome, Snapshot,
)
from odds_scraper.watcher import EventWatcher, WatcherConfig


def _snap_list(status=EventStatus.UPCOMING):
    base = Snapshot(
        ts_utc=datetime.now(timezone.utc),
        event_bp_id="33660318",
        sr_id="", genius_id="",
        home="A", away="B",
        kickoff_utc=datetime.now(timezone.utc) + timedelta(minutes=10),
        status=status,
        match_minute=None, score_home=None, score_away=None,
        bookmaker=Bookmaker.BETPAWA,
        market=Market.ONE_UP, outcome=Outcome.HOME,
        odds=1.5, probability=0.6,
        fetch_status=FetchStatus.OK, fetch_error="",
    )
    return [base] * 24


def _detail(live=False, ended=False) -> dict:
    if ended:
        return {
            "id": "33660318",
            "participants": [{"name": "A"}, {"name": "B"}],
            "startTime": "2026-05-19T15:00:00Z",
            "additionalInfo": {"live": False},
            "results": {
                "display": {"minute": 90, "currentPeriod": {"name": "FT"}},
                "participantPeriodResults": [],
            },
        }
    return {
        "id": "33660318",
        "participants": [{"name": "A"}, {"name": "B"}],
        "startTime": "2026-05-19T15:00:00Z",
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
    # First sleep after UPCOMING tick = 600
    # Second sleep after STARTED tick = 90
    assert sleeps[0] == 600
    assert sleeps[1] == 90


async def test_status_poll_retries_then_emits_sentinel(cfg, monkeypatch):
    monkeypatch.setattr("odds_scraper.watcher.asyncio.sleep", AsyncMock(return_value=None))

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
