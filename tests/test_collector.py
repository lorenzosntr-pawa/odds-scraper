from unittest.mock import AsyncMock

import pytest

from odds_scraper.collector import OddsCollector
from odds_scraper.models import (
    Bookmaker, EventStatus, FetchStatus, PriceKey,
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
    """Stand-in for bookieskit.markets.types.Outcome."""
    def __init__(self, name, odds, prob=None):
        self.canonical_name = name
        self.odds = odds
        self.true_probability = prob


class _M:
    """Stand-in for bookieskit.markets.types.NormalizedMarket — simple market."""
    def __init__(self, cid, outs):
        self.canonical_id = cid
        self.outcomes = [_O(n, o, p) for n, (o, p) in outs.items()]
        self.lines = None


class _PM:
    """Parameterized market stand-in (O/U-style)."""
    def __init__(self, cid, lines):
        self.canonical_id = cid
        self.outcomes = []
        self.lines = {
            ln: [_O(side, odds, prob) for side, (odds, prob) in sides.items()]
            for ln, sides in lines.items()
        }


def _full_markets():
    return [
        _M("1x2_ft", {
            "home": (1.80, 0.55), "draw": (3.40, 0.29), "away": (4.20, 0.23),
        }),
        _M("1x2_1up_ft", {
            "home": (1.85, 0.54), "draw": (3.20, 0.31), "away": (4.50, 0.22),
        }),
        _M("1x2_2up_ft", {
            "home": (2.50, 0.40), "draw": (3.80, 0.26), "away": (6.00, 0.16),
        }),
        _PM("over_under_ft", {
            2.5: {"over": (1.70, 0.58), "under": (2.10, 0.42)},
            3.5: {"over": (2.50, 0.39), "under": (1.50, 0.61)},
        }),
    ]


@pytest.fixture
def collector():
    return OddsCollector(
        fetchers={
            Bookmaker.BETPAWA: AsyncMock(return_value=_full_markets()),
            Bookmaker.SPORTYBET: AsyncMock(return_value=_full_markets()),
            Bookmaker.BET9JA: AsyncMock(return_value=_full_markets()),
            Bookmaker.BETWAY: AsyncMock(return_value=_full_markets()),
        },
    )


async def test_returns_exactly_four_rows(collector):
    rows = await collector.collect(
        _bp_detail(),
        resolved={Bookmaker.SPORTYBET: "sr:match:1",
                  Bookmaker.BET9JA: "b9j-7",
                  Bookmaker.BETWAY: "sr:match:1"},
        sr_id="sr:match:1", genius_id="",
    )
    assert len(rows) == 4
    bookmakers = {r.bookmaker for r in rows}
    assert bookmakers == set(Bookmaker)


async def test_all_four_rows_ok_on_full_success(collector):
    rows = await collector.collect(
        _bp_detail(),
        resolved={Bookmaker.SPORTYBET: "sr:match:1",
                  Bookmaker.BET9JA: "b9j-7",
                  Bookmaker.BETWAY: "sr:match:1"},
        sr_id="sr:match:1", genius_id="",
    )
    assert all(r.fetch_status == FetchStatus.OK for r in rows)


async def test_betpawa_row_has_simple_market_prices(collector):
    rows = await collector.collect(
        _bp_detail(),
        resolved={Bookmaker.SPORTYBET: "sr:match:1",
                  Bookmaker.BET9JA: "b9j-7",
                  Bookmaker.BETWAY: "sr:match:1"},
        sr_id="sr:match:1", genius_id="",
    )
    bp = next(r for r in rows if r.bookmaker == Bookmaker.BETPAWA)
    assert bp.prices[PriceKey("1x2_ft", None, "home")] == (1.80, 0.55)
    assert bp.prices[PriceKey("1x2_1up_ft", None, "away")] == (4.50, 0.22)


async def test_betpawa_row_has_parameterized_prices(collector):
    rows = await collector.collect(
        _bp_detail(),
        resolved={Bookmaker.SPORTYBET: "sr:match:1",
                  Bookmaker.BET9JA: "b9j-7",
                  Bookmaker.BETWAY: "sr:match:1"},
        sr_id="sr:match:1", genius_id="",
    )
    bp = next(r for r in rows if r.bookmaker == Bookmaker.BETPAWA)
    assert bp.prices[PriceKey("over_under_ft", 2.5, "over")] == (1.70, 0.58)
    assert bp.prices[PriceKey("over_under_ft", 3.5, "under")] == (1.50, 0.61)


async def test_probability_populated_only_for_bp_and_sb(collector):
    rows = await collector.collect(
        _bp_detail(),
        resolved={Bookmaker.SPORTYBET: "sr:match:1",
                  Bookmaker.BET9JA: "b9j-7",
                  Bookmaker.BETWAY: "sr:match:1"},
        sr_id="sr:match:1", genius_id="",
    )
    for r in rows:
        sample_key = PriceKey("1x2_ft", None, "home")
        odds, prob = r.prices[sample_key]
        assert odds == 1.80
        if r.bookmaker in (Bookmaker.BETPAWA, Bookmaker.SPORTYBET):
            assert prob == 0.55
        else:
            assert prob is None


async def test_lookup_failed_emits_empty_row():
    coll = OddsCollector(
        fetchers={
            Bookmaker.BETPAWA: AsyncMock(return_value=_full_markets()),
            Bookmaker.SPORTYBET: AsyncMock(return_value=_full_markets()),
            Bookmaker.BET9JA: AsyncMock(return_value=_full_markets()),
            Bookmaker.BETWAY: AsyncMock(return_value=_full_markets()),
        },
    )
    rows = await coll.collect(
        _bp_detail(),
        resolved={Bookmaker.SPORTYBET: "sr:match:1",
                  Bookmaker.BET9JA: None,
                  Bookmaker.BETWAY: "sr:match:1"},
        sr_id="sr:match:1", genius_id="",
    )
    b9j = next(r for r in rows if r.bookmaker == Bookmaker.BET9JA)
    assert b9j.fetch_status == FetchStatus.LOOKUP_FAILED
    assert b9j.prices == {}


async def test_http_error_emits_empty_row():
    failing = AsyncMock(side_effect=RuntimeError("HTTP 503"))
    coll = OddsCollector(
        fetchers={
            Bookmaker.BETPAWA: AsyncMock(return_value=_full_markets()),
            Bookmaker.SPORTYBET: failing,
            Bookmaker.BET9JA: AsyncMock(return_value=_full_markets()),
            Bookmaker.BETWAY: AsyncMock(return_value=_full_markets()),
        },
    )
    rows = await coll.collect(
        _bp_detail(),
        resolved={Bookmaker.SPORTYBET: "sr:match:1",
                  Bookmaker.BET9JA: "b9j-7",
                  Bookmaker.BETWAY: "sr:match:1"},
        sr_id="sr:match:1", genius_id="",
    )
    sb = next(r for r in rows if r.bookmaker == Bookmaker.SPORTYBET)
    assert sb.fetch_status == FetchStatus.HTTP_ERROR
    assert "HTTP 503" in sb.fetch_error
    assert sb.prices == {}


async def test_missing_market_just_omits_those_keys():
    only_1up = [_M("1x2_1up_ft", {
        "home": (1.85, 0.54), "draw": (3.20, 0.31), "away": (4.50, 0.22),
    })]
    coll = OddsCollector(
        fetchers={
            Bookmaker.BETPAWA: AsyncMock(return_value=_full_markets()),
            Bookmaker.SPORTYBET: AsyncMock(return_value=only_1up),
            Bookmaker.BET9JA: AsyncMock(return_value=_full_markets()),
            Bookmaker.BETWAY: AsyncMock(return_value=_full_markets()),
        },
    )
    rows = await coll.collect(
        _bp_detail(),
        resolved={Bookmaker.SPORTYBET: "sr:match:1",
                  Bookmaker.BET9JA: "b9j-7",
                  Bookmaker.BETWAY: "sr:match:1"},
        sr_id="sr:match:1", genius_id="",
    )
    sb = next(r for r in rows if r.bookmaker == Bookmaker.SPORTYBET)
    assert sb.fetch_status == FetchStatus.OK
    assert PriceKey("1x2_1up_ft", None, "home") in sb.prices
    assert PriceKey("1x2_ft", None, "home") not in sb.prices
    assert PriceKey("1x2_2up_ft", None, "home") not in sb.prices
    assert not any(k.market_id == "over_under_ft" for k in sb.prices)


async def test_out_of_manifest_lines_are_ignored():
    extra_ou = [_PM("over_under_ft", {
        2.0: {"over": (1.40, 0.71), "under": (2.80, 0.29)},
        2.5: {"over": (1.70, 0.58), "under": (2.10, 0.42)},
        12.5: {"over": (50.0, 0.02), "under": (1.01, 0.98)},
    })]
    coll = OddsCollector(
        fetchers={
            Bookmaker.BETPAWA: AsyncMock(return_value=extra_ou),
            Bookmaker.SPORTYBET: AsyncMock(return_value=extra_ou),
            Bookmaker.BET9JA: AsyncMock(return_value=extra_ou),
            Bookmaker.BETWAY: AsyncMock(return_value=extra_ou),
        },
    )
    rows = await coll.collect(
        _bp_detail(),
        resolved={Bookmaker.SPORTYBET: "sr:match:1",
                  Bookmaker.BET9JA: "b9j-7",
                  Bookmaker.BETWAY: "sr:match:1"},
        sr_id="sr:match:1", genius_id="",
    )
    bp = next(r for r in rows if r.bookmaker == Bookmaker.BETPAWA)
    lines_seen = {k.line for k in bp.prices if k.market_id == "over_under_ft"}
    assert lines_seen == {2.5}


async def test_live_regime_skips_bet9ja_and_betway(collector):
    """For STARTED events we only hit BP + SB. Bet9ja and Betway snapshots
    still appear in the per-tick rows (row shape stable) but with empty
    prices and a clear fetch_status indicating the skip is by policy."""
    rows = await collector.collect(
        _bp_detail(live=True),
        resolved={Bookmaker.SPORTYBET: "sr:match:1",
                  Bookmaker.BET9JA: "b9j-7",
                  Bookmaker.BETWAY: "sr:match:1"},
        sr_id="sr:match:1", genius_id="g-9",
    )
    by_bm = {r.bookmaker: r for r in rows}
    assert by_bm[Bookmaker.BETPAWA].fetch_status == FetchStatus.OK
    assert by_bm[Bookmaker.SPORTYBET].fetch_status == FetchStatus.OK
    assert by_bm[Bookmaker.BET9JA].fetch_status == FetchStatus.LOOKUP_FAILED
    assert by_bm[Bookmaker.BET9JA].prices == {}
    assert "live regime" in by_bm[Bookmaker.BET9JA].fetch_error
    assert by_bm[Bookmaker.BETWAY].fetch_status == FetchStatus.LOOKUP_FAILED
    assert by_bm[Bookmaker.BETWAY].prices == {}
    assert "live regime" in by_bm[Bookmaker.BETWAY].fetch_error


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


async def test_outcome_missing_odds_is_skipped(collector):
    no_draw = [_M("1x2_ft", {
        "home": (1.80, 0.55),
        "draw": (None, None),
        "away": (4.20, 0.23),
    })]
    coll = OddsCollector(
        fetchers={
            Bookmaker.BETPAWA: AsyncMock(return_value=no_draw),
            Bookmaker.SPORTYBET: AsyncMock(return_value=no_draw),
            Bookmaker.BET9JA: AsyncMock(return_value=no_draw),
            Bookmaker.BETWAY: AsyncMock(return_value=no_draw),
        },
    )
    rows = await coll.collect(
        _bp_detail(),
        resolved={Bookmaker.SPORTYBET: "sr:match:1",
                  Bookmaker.BET9JA: "b9j-7",
                  Bookmaker.BETWAY: "sr:match:1"},
        sr_id="sr:match:1", genius_id="",
    )
    bp = next(r for r in rows if r.bookmaker == Bookmaker.BETPAWA)
    assert PriceKey("1x2_ft", None, "home") in bp.prices
    assert PriceKey("1x2_ft", None, "draw") not in bp.prices
    assert PriceKey("1x2_ft", None, "away") in bp.prices


def _bp_detail_with_country_league() -> dict:
    """Same shape as _bp_detail() but with region/competition populated."""
    return {
        "id": "33660318",
        "participants": [
            {"id": "1", "name": "Team A", "position": 1},
            {"id": "2", "name": "Team B", "position": 2},
        ],
        "startTime": "2026-05-19T15:00:00Z",
        "additionalInfo": {"live": False},
        "results": None,
        "region":      {"id": "242",   "name": "Germany"},
        "competition": {"id": "12091", "name": "2nd Bundesliga"},
    }


async def test_collector_extracts_country_and_league(collector):
    rows = await collector.collect(
        _bp_detail_with_country_league(),
        resolved={Bookmaker.SPORTYBET: "sr:match:1",
                  Bookmaker.BET9JA: "b9j-7",
                  Bookmaker.BETWAY: "sr:match:1"},
        sr_id="sr:match:1", genius_id="",
    )
    # All four bookmaker rows get identical event-level metadata
    for r in rows:
        assert r.country_id == "242"
        assert r.country_name == "Germany"
        assert r.league_id == "12091"
        assert r.league_name == "2nd Bundesliga"


async def test_collector_handles_missing_region_competition(collector):
    # When the detail has no region/competition keys, all four fields
    # collapse to "" rather than raising.
    rows = await collector.collect(
        _bp_detail(),  # existing helper, no region/competition keys
        resolved={Bookmaker.SPORTYBET: "sr:match:1",
                  Bookmaker.BET9JA: "b9j-7",
                  Bookmaker.BETWAY: "sr:match:1"},
        sr_id="sr:match:1", genius_id="",
    )
    for r in rows:
        assert r.country_id == ""
        assert r.country_name == ""
        assert r.league_id == ""
        assert r.league_name == ""


async def test_collector_extracts_next_goal_prices():
    next_goal_markets = [
        _PM("next_goal_ft", {
            1.0: {"home": (1.85, 0.54), "none": (8.5, 0.12), "away": (3.50, 0.29)},
            2.0: {"home": (2.20, 0.45), "none": (3.0, 0.33), "away": (3.90, 0.26)},
        }),
    ]
    coll = OddsCollector(
        fetchers={
            Bookmaker.BETPAWA: AsyncMock(return_value=next_goal_markets),
            Bookmaker.SPORTYBET: AsyncMock(return_value=next_goal_markets),
            Bookmaker.BET9JA: AsyncMock(return_value=next_goal_markets),
            Bookmaker.BETWAY: AsyncMock(return_value=next_goal_markets),
        },
    )
    rows = await coll.collect(
        _bp_detail(),
        resolved={Bookmaker.SPORTYBET: "sr:match:1",
                  Bookmaker.BET9JA: "b9j-7",
                  Bookmaker.BETWAY: "sr:match:1"},
        sr_id="sr:match:1", genius_id="",
    )
    bp = next(r for r in rows if r.bookmaker == Bookmaker.BETPAWA)
    assert bp.prices[PriceKey("next_goal_ft", 1.0, "home")] == (1.85, 0.54)
    assert bp.prices[PriceKey("next_goal_ft", 1.0, "none")] == (8.5, 0.12)
    assert bp.prices[PriceKey("next_goal_ft", 2.0, "away")] == (3.90, 0.26)


async def test_collector_extracts_per_team_over_under_prices():
    team_ou_markets = [
        _PM("home_over_under_ft", {
            0.5: {"over": (1.30, 0.74), "under": (3.50, 0.26)},
            1.5: {"over": (2.10, 0.46), "under": (1.70, 0.54)},
        }),
        _PM("away_over_under_ft", {
            0.5: {"over": (1.40, 0.69), "under": (3.00, 0.31)},
            1.5: {"over": (2.50, 0.40), "under": (1.55, 0.60)},
        }),
    ]
    coll = OddsCollector(
        fetchers={
            Bookmaker.BETPAWA: AsyncMock(return_value=team_ou_markets),
            Bookmaker.SPORTYBET: AsyncMock(return_value=team_ou_markets),
            Bookmaker.BET9JA: AsyncMock(return_value=team_ou_markets),
            Bookmaker.BETWAY: AsyncMock(return_value=team_ou_markets),
        },
    )
    rows = await coll.collect(
        _bp_detail(),
        resolved={Bookmaker.SPORTYBET: "sr:match:1",
                  Bookmaker.BET9JA: "b9j-7",
                  Bookmaker.BETWAY: "sr:match:1"},
        sr_id="sr:match:1", genius_id="",
    )
    bp = next(r for r in rows if r.bookmaker == Bookmaker.BETPAWA)
    assert bp.prices[PriceKey("home_over_under_ft", 0.5, "over")] == (1.30, 0.74)
    assert bp.prices[PriceKey("home_over_under_ft", 1.5, "under")] == (1.70, 0.54)
    assert bp.prices[PriceKey("away_over_under_ft", 0.5, "over")] == (1.40, 0.69)
    assert bp.prices[PriceKey("away_over_under_ft", 1.5, "under")] == (1.55, 0.60)


async def test_collector_ignores_next_goal_lines_above_manifest_cap():
    # next_goal_ft manifest covers (1..9). Lines beyond that should be
    # silently dropped, same behaviour as over_under_ft.
    out_of_range_markets = [
        _PM("next_goal_ft", {
            5.0: {"home": (1.85, 0.54), "none": (8.5, 0.12), "away": (3.50, 0.29)},
            10.0: {"home": (50.0, 0.02), "none": (100.0, 0.01), "away": (50.0, 0.02)},
        }),
    ]
    coll = OddsCollector(
        fetchers={
            Bookmaker.BETPAWA: AsyncMock(return_value=out_of_range_markets),
            Bookmaker.SPORTYBET: AsyncMock(return_value=out_of_range_markets),
            Bookmaker.BET9JA: AsyncMock(return_value=out_of_range_markets),
            Bookmaker.BETWAY: AsyncMock(return_value=out_of_range_markets),
        },
    )
    rows = await coll.collect(
        _bp_detail(),
        resolved={Bookmaker.SPORTYBET: "sr:match:1",
                  Bookmaker.BET9JA: "b9j-7",
                  Bookmaker.BETWAY: "sr:match:1"},
        sr_id="sr:match:1", genius_id="",
    )
    bp = next(r for r in rows if r.bookmaker == Bookmaker.BETPAWA)
    lines_seen = {k.line for k in bp.prices if k.market_id == "next_goal_ft"}
    assert lines_seen == {5.0}  # 10.0 dropped
