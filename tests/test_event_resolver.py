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
    out, _ = await resolve_event_ids(
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
    out, _ = await resolve_event_ids(
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
    out, _ = await resolve_event_ids(
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
    out, _ = await resolve_event_ids(
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
    out, _ = await resolve_event_ids(
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
        out, _ = await resolve_event_ids(
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
    out, _ = await resolve_event_ids(
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
    out, _ = await resolve_event_ids(
        standalone_events=[],
        tournaments=["A", "B"],
        bp_client=client,
    )
    assert out.count("99") == 1
    assert sorted(out) == ["100", "200", "99"]


@pytest.mark.asyncio
async def test_empty_inputs_returns_empty():
    client = _make_bp_client({})
    out, _ = await resolve_event_ids(
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
    out, _ = await resolve_event_ids(
        standalone_events=[],
        tournaments=["X"],
        bp_client=client,
    )
    assert out == []


def _events_response_with_kickoff(items: list[tuple[str, str | None]]) -> dict:
    """items = [(event_id, start_time_iso_or_None), ...]."""
    return {
        "responses": [{
            "responses": [
                {"id": ev_id, "participants": [], **({"startTime": kt} if kt else {})}
                for ev_id, kt in items
            ]
        }]
    }


@pytest.mark.asyncio
async def test_global_fetch_sorts_by_kickoff_asc_missing_last():
    """_fetch_global_event_ids paginates per event_type and sorts the
    union by startTime ASC; entries missing startTime drop to the end."""
    from odds_scraper.event_resolver import _fetch_global_event_ids

    client = AsyncMock()
    async def get_events(tournament_id, sport_id, event_type, skip, take):
        assert tournament_id is None
        assert sport_id == "2"
        if event_type == "UPCOMING" and skip == 0:
            return _events_response_with_kickoff([
                ("a", "2026-06-01T18:00:00Z"),
                ("b", "2026-05-30T15:00:00Z"),
                ("missing", None),
            ])
        if event_type == "LIVE" and skip == 0:
            return _events_response_with_kickoff([
                ("c", "2026-05-25T20:00:00Z"),
            ])
        return _events_response_with_kickoff([])
    client.get_events.side_effect = get_events

    out = await _fetch_global_event_ids(
        client, sport_id="2", event_types=("UPCOMING", "LIVE"),
    )
    assert out == ["c", "b", "a", "missing"]


@pytest.mark.asyncio
async def test_global_fetch_one_event_type_failure_returns_partial(caplog):
    """If one event_type's pagination raises, we keep the IDs from the
    successful event_type — a single bad page doesn't wipe the sweep."""
    from odds_scraper.event_resolver import _fetch_global_event_ids

    client = AsyncMock()
    async def get_events(tournament_id, sport_id, event_type, skip, take):
        if event_type == "UPCOMING":
            return _events_response_with_kickoff([
                ("good1", "2026-05-25T18:00:00Z"),
            ])
        if event_type == "LIVE":
            raise RuntimeError("HTTP 500 from BP")
        return _events_response_with_kickoff([])
    client.get_events.side_effect = get_events

    with caplog.at_level(logging.WARNING, logger="odds_scraper.event_resolver"):
        out = await _fetch_global_event_ids(
            client, sport_id="2", event_types=("UPCOMING", "LIVE"),
        )
    assert out == ["good1"]
    assert any("LIVE" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_global_fetch_paginates_until_partial_page():
    """Pagination walks `take`-sized pages until a partial page signals
    the tail. Mirrors `_fetch_tournament_event_ids`."""
    from odds_scraper.event_resolver import _fetch_global_event_ids

    page1 = [(str(i), f"2026-06-{(i%30)+1:02d}T00:00:00Z") for i in range(100)]
    page2 = [(str(i), f"2026-06-{(i%30)+1:02d}T00:00:00Z") for i in range(100, 150)]

    client = AsyncMock()
    async def get_events(tournament_id, sport_id, event_type, skip, take):
        if event_type == "UPCOMING" and skip == 0:
            return _events_response_with_kickoff(page1)
        if event_type == "UPCOMING" and skip == 100:
            return _events_response_with_kickoff(page2)
        return _events_response_with_kickoff([])
    client.get_events.side_effect = get_events

    out = await _fetch_global_event_ids(
        client, sport_id="2", event_types=("UPCOMING",),
    )
    assert len(out) == 150
    assert set(out) == {str(i) for i in range(150)}


@pytest.mark.asyncio
async def test_resolve_returns_priority_set_with_ordered_list():
    """resolve_event_ids returns (ordered_ids, priority_ids). priority_ids
    is the union of standalone + tournament-expanded; ordered keeps the
    'standalone declared order, then tournament IDs lexically' rule."""
    client = _make_bp_client({
        ("11965", "UPCOMING", 0): _events_response(["33", "44"]),
        ("11965", "LIVE", 0): _events_response([]),
    })
    ordered, priority = await resolve_event_ids(
        standalone_events=["77"],
        tournaments=["11965"],
        bp_client=client,
    )
    assert ordered == ["77", "33", "44"]
    assert priority == {"77", "33", "44"}
