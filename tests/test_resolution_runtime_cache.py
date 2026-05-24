"""Tests for the bet9ja prematch map cache — non-blocking get(),
background build, no cascade-from-cancellation."""
import asyncio
from unittest.mock import AsyncMock

import pytest

from odds_scraper import resolution_runtime as rr


async def test_get_returns_empty_while_build_in_flight(monkeypatch):
    """The first get() kicks off the background build; subsequent get()s
    must return {} immediately without waiting — that's what prevents
    50 concurrent watchers from all hitting their 90s timeouts in a row."""
    cache = rr._Bet9jaPrematchMapCache()
    build_started = asyncio.Event()
    build_should_finish = asyncio.Event()

    async def fake_build(sport_id=None):
        build_started.set()
        await build_should_finish.wait()
        return {"sr1": "b9j1", "sr2": "b9j2"}

    client = AsyncMock()
    client.build_prematch_event_map.side_effect = fake_build

    # First get triggers the build, returns empty fast.
    r1 = await cache.get(client)
    assert r1 == {}
    # Background task is scheduled and started.
    await build_started.wait()
    assert cache._build_task is not None
    assert not cache._build_task.done()

    # 10 concurrent gets all return empty without blocking on the build.
    results = await asyncio.gather(*(cache.get(client) for _ in range(10)))
    assert all(r == {} for r in results)
    assert client.build_prematch_event_map.call_count == 1

    # Let the build finish; next get returns the cached mapping.
    build_should_finish.set()
    await cache._build_task
    r_after = await cache.get(client)
    assert r_after == {"sr1": "b9j1", "sr2": "b9j2"}


async def test_failed_build_sets_cooldown(monkeypatch):
    cache = rr._Bet9jaPrematchMapCache()

    async def boom(sport_id=None):
        raise RuntimeError("403 forbidden")

    client = AsyncMock()
    client.build_prematch_event_map.side_effect = boom

    assert await cache.get(client) == {}
    await cache._build_task
    # Cooldown is set; another get() doesn't trigger a fresh build.
    assert cache._cooldown_until > 0
    n_before = client.build_prematch_event_map.call_count
    assert await cache.get(client) == {}
    assert client.build_prematch_event_map.call_count == n_before


async def test_concurrent_callers_dont_serialize_behind_build():
    """Crucial property — when one caller is "building" (synchronously,
    in our control), other callers must NOT wait on a lock for it."""
    cache = rr._Bet9jaPrematchMapCache()
    block = asyncio.Event()

    async def slow_build(sport_id=None):
        await block.wait()
        return {}

    client = AsyncMock()
    client.build_prematch_event_map.side_effect = slow_build
    await cache.get(client)  # kick off build

    # 20 concurrent gets must all return within a tight time budget
    # because none of them should be waiting on the slow build.
    async def fast_get():
        return await cache.get(client)

    done = await asyncio.wait_for(
        asyncio.gather(*(fast_get() for _ in range(20))),
        timeout=1.0,  # << much less than the build's blocking time
    )
    assert all(r == {} for r in done)
    block.set()
    await cache._build_task
