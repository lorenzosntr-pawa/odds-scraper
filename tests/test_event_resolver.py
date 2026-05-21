import logging
from unittest.mock import AsyncMock

import pytest

from odds_scraper.event_resolver import resolve_event_ids


def _events_response(ids: list[str]) -> dict:
    """Build a BetPawa-shaped events response carrying these IDs."""
    return {
        "responses": [
            {"responses": [{"id": ev_id, "participants": []} for ev_id in ids]}
        ]
    }


def _make_bp_client(tournament_responses: dict) -> AsyncMock:
    """Mock a BetPawa client.

    tournament_responses maps (tournament_id, event_type, skip) -> response dict.
    Unknown keys return empty.
    """
    client = AsyncMock()

    async def get_events(tournament_id, event_type, skip, take):
        key = (str(tournament_id), event_type, skip)
        if key in tournament_responses:
            return tournament_responses[key]
        return _events_response([])

    client.get_events.side_effect = get_events
    return client


@pytest.mark.asyncio
async def test_standalone_only_returns_ids_in_declared_order():
    client = _make_bp_client({})
    out = await resolve_event_ids(
        standalone_events=["33638734", "33583353"],
        tournaments=[],
        bp_client=client,
    )
    assert out == ["33638734", "33583353"]
    client.get_events.assert_not_awaited()


@pytest.mark.asyncio
async def test_single_tournament_single_page():
    client = _make_bp_client({
        ("11965", "UPCOMING", 0): _events_response(["1", "2", "3"]),
        ("11965", "LIVE", 0): _events_response([]),
    })
    out = await resolve_event_ids(
        standalone_events=[],
        tournaments=["11965"],
        bp_client=client,
    )
    assert sorted(out) == ["1", "2", "3"]


@pytest.mark.asyncio
async def test_upcoming_and_live_unioned_dedup_within_tournament():
    client = _make_bp_client({
        ("11965", "UPCOMING", 0): _events_response(["1", "2"]),
        ("11965", "LIVE", 0): _events_response(["2", "3"]),
    })
    out = await resolve_event_ids(
        standalone_events=[],
        tournaments=["11965"],
        bp_client=client,
    )
    assert sorted(out) == ["1", "2", "3"]


@pytest.mark.asyncio
async def test_pagination_walks_until_partial_page():
    page1 = [str(i) for i in range(100)]
    page2 = [str(i) for i in range(100, 200)]
    page3 = [str(i) for i in range(200, 250)]
    client = _make_bp_client({
        ("9", "UPCOMING", 0): _events_response(page1),
        ("9", "UPCOMING", 100): _events_response(page2),
        ("9", "UPCOMING", 200): _events_response(page3),
        ("9", "LIVE", 0): _events_response([]),
    })
    out = await resolve_event_ids(
        standalone_events=[],
        tournaments=["9"],
        bp_client=client,
    )
    assert len(out) == 250
    assert set(out) == set(str(i) for i in range(250))


@pytest.mark.asyncio
async def test_pagination_exactly_100_followed_by_empty():
    page1 = [str(i) for i in range(100)]
    client = _make_bp_client({
        ("9", "UPCOMING", 0): _events_response(page1),
        ("9", "UPCOMING", 100): _events_response([]),
        ("9", "LIVE", 0): _events_response([]),
    })
    out = await resolve_event_ids(
        standalone_events=[],
        tournaments=["9"],
        bp_client=client,
    )
    assert len(out) == 100


@pytest.mark.asyncio
async def test_one_tournament_failure_isolated_others_succeed(caplog):
    client = AsyncMock()

    async def get_events(tournament_id, event_type, skip, take):
        if str(tournament_id) == "BROKEN":
            raise RuntimeError("HTTP 500 disaster strikes")
        if str(tournament_id) == "OK1" and event_type == "UPCOMING" and skip == 0:
            return _events_response(["a", "b"])
        if str(tournament_id) == "OK2" and event_type == "UPCOMING" and skip == 0:
            return _events_response(["c"])
        return _events_response([])

    client.get_events.side_effect = get_events

    with caplog.at_level(logging.WARNING, logger="odds_scraper.event_resolver"):
        out = await resolve_event_ids(
            standalone_events=[],
            tournaments=["OK1", "BROKEN", "OK2"],
            bp_client=client,
        )
    assert sorted(out) == ["a", "b", "c"]
    assert any("BROKEN" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_dedup_standalone_and_tournament_event():
    client = _make_bp_client({
        ("11965", "UPCOMING", 0): _events_response(["33", "44"]),
        ("11965", "LIVE", 0): _events_response([]),
    })
    out = await resolve_event_ids(
        standalone_events=["33"],
        tournaments=["11965"],
        bp_client=client,
    )
    assert out.count("33") == 1
    assert out[0] == "33"
    assert sorted(out) == ["33", "44"]


@pytest.mark.asyncio
async def test_dedup_same_event_across_tournaments():
    client = _make_bp_client({
        ("A", "UPCOMING", 0): _events_response(["99", "100"]),
        ("A", "LIVE", 0): _events_response([]),
        ("B", "UPCOMING", 0): _events_response(["99", "200"]),
        ("B", "LIVE", 0): _events_response([]),
    })
    out = await resolve_event_ids(
        standalone_events=[],
        tournaments=["A", "B"],
        bp_client=client,
    )
    assert out.count("99") == 1
    assert sorted(out) == ["100", "200", "99"]


@pytest.mark.asyncio
async def test_empty_inputs_returns_empty():
    client = _make_bp_client({})
    out = await resolve_event_ids(
        standalone_events=[],
        tournaments=[],
        bp_client=client,
    )
    assert out == []


@pytest.mark.asyncio
async def test_tournament_returning_empty_response_yields_no_events():
    client = _make_bp_client({
        ("X", "UPCOMING", 0): _events_response([]),
        ("X", "LIVE", 0): _events_response([]),
    })
    out = await resolve_event_ids(
        standalone_events=[],
        tournaments=["X"],
        bp_client=client,
    )
    assert out == []
