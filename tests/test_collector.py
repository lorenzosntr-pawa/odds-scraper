from unittest.mock import AsyncMock

import pytest

from odds_scraper.collector import OddsCollector
from odds_scraper.models import (
    Bookmaker, EventStatus, FetchStatus, Market, Outcome,
)


def _bp_detail(live: bool = False) -> dict:
    return {
        "id": "33660318",
        "participants": [
            {"id": "1", "name": "Team A", "position": 1},
            {"id": "2", "name": "Team B", "position": 2},
        ],
        "startTime": "2026-05-19T15:00:00Z",
        "additionalInfo": {"live": live},
        "results": {
            "display": {"minute": 34, "currentPeriod": {"name": "1H"}},
            "participantPeriodResults": [
                {"participant": {"type": "HOME"},
                 "periodResults": [{"period": {"slug": "FULL_TIME_EXCLUDING_OVERTIME"}, "result": 1}]},
                {"participant": {"type": "AWAY"},
                 "periodResults": [{"period": {"slug": "FULL_TIME_EXCLUDING_OVERTIME"}, "result": 0}]},
            ],
        } if live else None,
    }


class _O:
    def __init__(self, name, odds, prob):
        self.canonical_name = name
        self.odds = odds
        self.true_probability = prob


class _M:
    def __init__(self, cid, outs):
        self.canonical_id = cid
        self.outcomes = [_O(n, o, p) for n, (o, p) in outs.items()]


def _ok_markets():
    return [
        _M("1x2_1up_ft", {"home": (1.85, 0.54), "draw": (3.2, 0.31), "away": (4.5, 0.22)}),
        _M("1x2_2up_ft", {"home": (2.50, 0.40), "draw": (3.8, 0.26), "away": (6.0, 0.16)}),
    ]


@pytest.fixture
def collector():
    return OddsCollector(
        fetchers={
            Bookmaker.BETPAWA: AsyncMock(return_value=_ok_markets()),
            Bookmaker.SPORTYBET: AsyncMock(return_value=_ok_markets()),
            Bookmaker.BET9JA: AsyncMock(return_value=_ok_markets()),
            Bookmaker.BETWAY: AsyncMock(return_value=_ok_markets()),
        },
    )


async def test_always_24_rows_all_ok(collector):
    rows = await collector.collect(
        _bp_detail(),
        resolved={Bookmaker.SPORTYBET: "sr:match:1",
                  Bookmaker.BET9JA: "b9j-7",
                  Bookmaker.BETWAY: "sr:match:1"},
        sr_id="sr:match:1", genius_id="",
    )
    assert len(rows) == 24
    assert sum(1 for r in rows if r.fetch_status == FetchStatus.OK) == 24
    for b in Bookmaker:
        assert sum(1 for r in rows if r.bookmaker == b) == 6


async def test_lookup_failed_bookmaker_emits_6_empty_rows():
    coll = OddsCollector(
        fetchers={
            Bookmaker.BETPAWA: AsyncMock(return_value=_ok_markets()),
            Bookmaker.SPORTYBET: AsyncMock(return_value=_ok_markets()),
            Bookmaker.BET9JA: AsyncMock(return_value=_ok_markets()),
            Bookmaker.BETWAY: AsyncMock(return_value=_ok_markets()),
        },
    )
    rows = await coll.collect(
        _bp_detail(),
        resolved={Bookmaker.SPORTYBET: "sr:match:1",
                  Bookmaker.BET9JA: None,
                  Bookmaker.BETWAY: "sr:match:1"},
        sr_id="sr:match:1", genius_id="",
    )
    b9j_rows = [r for r in rows if r.bookmaker == Bookmaker.BET9JA]
    assert len(b9j_rows) == 6
    assert all(r.fetch_status == FetchStatus.LOOKUP_FAILED for r in b9j_rows)
    assert all(r.odds is None for r in b9j_rows)


async def test_http_error_emits_6_http_error_rows():
    failing = AsyncMock(side_effect=RuntimeError("HTTP 503"))
    coll = OddsCollector(
        fetchers={
            Bookmaker.BETPAWA: AsyncMock(return_value=_ok_markets()),
            Bookmaker.SPORTYBET: failing,
            Bookmaker.BET9JA: AsyncMock(return_value=_ok_markets()),
            Bookmaker.BETWAY: AsyncMock(return_value=_ok_markets()),
        },
    )
    rows = await coll.collect(
        _bp_detail(),
        resolved={Bookmaker.SPORTYBET: "sr:match:1",
                  Bookmaker.BET9JA: "b9j-7",
                  Bookmaker.BETWAY: "sr:match:1"},
        sr_id="sr:match:1", genius_id="",
    )
    sb_rows = [r for r in rows if r.bookmaker == Bookmaker.SPORTYBET]
    assert len(sb_rows) == 6
    assert all(r.fetch_status == FetchStatus.HTTP_ERROR for r in sb_rows)


async def test_not_offered_when_market_missing():
    only_1up = [_M("1x2_1up_ft", {"home": (1.85, 0.54), "draw": (3.2, 0.31), "away": (4.5, 0.22)})]
    coll = OddsCollector(
        fetchers={
            Bookmaker.BETPAWA: AsyncMock(return_value=_ok_markets()),
            Bookmaker.SPORTYBET: AsyncMock(return_value=only_1up),
            Bookmaker.BET9JA: AsyncMock(return_value=_ok_markets()),
            Bookmaker.BETWAY: AsyncMock(return_value=_ok_markets()),
        },
    )
    rows = await coll.collect(
        _bp_detail(),
        resolved={Bookmaker.SPORTYBET: "sr:match:1",
                  Bookmaker.BET9JA: "b9j-7",
                  Bookmaker.BETWAY: "sr:match:1"},
        sr_id="sr:match:1", genius_id="",
    )
    sb_2up = [r for r in rows
              if r.bookmaker == Bookmaker.SPORTYBET and r.market == Market.TWO_UP]
    assert len(sb_2up) == 3
    assert all(r.fetch_status == FetchStatus.NOT_OFFERED for r in sb_2up)


async def test_live_status_populates_clock_and_score(collector):
    rows = await collector.collect(
        _bp_detail(live=True),
        resolved={Bookmaker.SPORTYBET: "sr:match:1",
                  Bookmaker.BET9JA: "b9j-7",
                  Bookmaker.BETWAY: "sr:match:1"},
        sr_id="sr:match:1", genius_id="g-9",
    )
    assert all(r.status == EventStatus.STARTED for r in rows)
    assert all(r.match_minute == 34 for r in rows)
    assert all(r.score_home == 1 and r.score_away == 0 for r in rows)
    assert all(r.genius_id == "g-9" for r in rows)
    assert all(r.home == "Team A" and r.away == "Team B" for r in rows)


async def test_probability_only_for_betpawa_and_sportybet(collector):
    rows = await collector.collect(
        _bp_detail(),
        resolved={Bookmaker.SPORTYBET: "sr:match:1",
                  Bookmaker.BET9JA: "b9j-7",
                  Bookmaker.BETWAY: "sr:match:1"},
        sr_id="sr:match:1", genius_id="",
    )
    for r in rows:
        if r.bookmaker in (Bookmaker.BETPAWA, Bookmaker.SPORTYBET):
            assert r.probability is not None
        else:
            assert r.probability is None
