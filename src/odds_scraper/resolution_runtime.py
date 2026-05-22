"""Wiring between bookieskit's real clients and our abstractions.

This is the only module that touches real bookmaker APIs through bookieskit.
The rest of the codebase depends only on the (Bookmaker -> fetcher) contract
and the (bp_detail -> (resolved, sr_id, genius_id)) resolver contract.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import AsyncExitStack
from typing import Any, Awaitable, Callable

from bookieskit import (
    Bet9ja, BetPawa, Betway, SportyBet, extract_event_ids,
)
from bookieskit.markets import MarketRegistry, parse_markets

from .models import Bookmaker, EventStatus
from .resolution import ResolutionCache, ResolutionKey
from .status import parse_status

log = logging.getLogger(__name__)

# Bet9ja's prematch event map walks every soccer tournament (~200 HTTP calls).
# Build it ONCE per process and share across all event resolutions; otherwise
# 4 concurrent watchers each trigger the full walk on startup and Bet9ja's
# Akamai shield returns 403 for the next ~30 min.
_BET9JA_PREMATCH_MAP_TTL_SECONDS = 1800  # 30 min — events come and go slowly


class _Bet9jaPrematchMapCache:
    def __init__(self) -> None:
        self._mapping: dict[str, str] | None = None
        self._built_at: float = 0.0
        self._lock = asyncio.Lock()
        self._cooldown_until: float = 0.0  # back off after a 403

    async def get(self, client) -> dict[str, str]:
        async with self._lock:
            now = time.monotonic()
            if self._mapping is not None and (now - self._built_at) < _BET9JA_PREMATCH_MAP_TTL_SECONDS:
                return self._mapping
            if now < self._cooldown_until:
                log.warning(
                    "bet9ja prematch map cooldown in effect (%ds left), "
                    "returning empty map",
                    int(self._cooldown_until - now),
                )
                return {}
            try:
                log.info("building bet9ja prematch event map (one-shot, shared)")
                self._mapping = await client.build_prematch_event_map(sport_id="1")
                self._built_at = now
                log.info("bet9ja prematch map built: %d entries", len(self._mapping or {}))
                return self._mapping or {}
            except Exception as e:  # noqa: BLE001
                # 30-minute cooldown after any failure to avoid hammering.
                self._cooldown_until = now + 1800
                log.warning("bet9ja prematch map build failed: %s — 30 min cooldown", e)
                return {}


_bet9ja_prematch_map = _Bet9jaPrematchMapCache()


async def make_bookmaker_clients(
    stack: AsyncExitStack, country: str,
) -> dict[Bookmaker, Any]:
    # Bet9ja's Akamai shield is aggressive. Cap concurrency to 2 and add a
    # small per-request delay so we never look like a scraper to them. The
    # prematch-map walk (~200 tournaments) still completes in a couple of
    # minutes but won't get us banned.
    return {
        Bookmaker.BETPAWA: await stack.enter_async_context(BetPawa(country=country)),
        Bookmaker.SPORTYBET: await stack.enter_async_context(SportyBet(country=country)),
        Bookmaker.BET9JA: await stack.enter_async_context(
            Bet9ja(country=country, max_concurrent=2, request_delay=0.5),
        ),
        Bookmaker.BETWAY: await stack.enter_async_context(Betway(country=country)),
    }


def make_fetchers(
    clients: dict[Bookmaker, Any],
    registry: MarketRegistry,
) -> dict[Bookmaker, Callable[..., Awaitable[list]]]:
    """Each fetcher returns parsed markets (list of NormalizedMarket).

    BetPawa receives the detail dict already in hand (passed through, no extra
    HTTP call). Others receive their resolved id and call their detail/markets
    endpoint themselves.
    """

    async def fetch_betpawa(bp_detail: dict) -> list:
        return parse_markets(
            bp_detail, platform="betpawa", registry=registry, probability="true",
        )

    async def fetch_sportybet(sb_id: str) -> list:
        sb = clients[Bookmaker.SPORTYBET]
        detail = await sb.get_event_detail(event_id=sb_id)
        return parse_markets(
            detail, platform="sportybet", registry=registry, probability="true",
        )

    async def fetch_bet9ja(b9j_id: str) -> list:
        b9j = clients[Bookmaker.BET9JA]
        detail = await b9j.get_event_detail(event_id=b9j_id)
        return parse_markets(detail, platform="bet9ja", registry=registry)

    async def fetch_betway(bw_id: str) -> list:
        bw = clients[Bookmaker.BETWAY]
        detail = await bw.get_event_markets(event_id=bw_id)
        return parse_markets(detail, platform="betway", registry=registry)

    return {
        Bookmaker.BETPAWA: fetch_betpawa,
        Bookmaker.SPORTYBET: fetch_sportybet,
        Bookmaker.BET9JA: fetch_bet9ja,
        Bookmaker.BETWAY: fetch_betway,
    }


async def resolve_event(
    bp_detail: dict[str, Any],
    *,
    clients: dict[Bookmaker, Any],
    cache: ResolutionCache,
) -> tuple[dict[Bookmaker, str | None], str, str]:
    """Resolve the BetPawa anchor event to SportyBet / Bet9ja / Betway ids.

    Strategy:
      - Extract SR id and BetGenius id from BetPawa detail.
      - SportyBet & Betway: SR id direct (different prefix conventions per
        bookmaker — sb wants `sr:match:`, bw wants the raw numeric id).
      - Bet9ja: prematch via shared `build_prematch_event_map` (built once
        per process); live via BetGenius id.

    Cache key includes the regime because Bet9ja switches id types at kickoff.
    """
    status = parse_status(bp_detail)
    regime = "live" if status == EventStatus.STARTED else "prematch"
    event_id = str(bp_detail.get("id", ""))
    key = ResolutionKey(event_id, regime)
    cached = cache.get(key)
    if cached is not None:
        return _from_cached(cached)

    ids = extract_event_ids(bp_detail, platform="betpawa")
    sr_id = ids.sportradar or ""
    genius_id = ids.genius or ""

    sb_id = f"sr:match:{sr_id}" if sr_id else None
    bw_id = sr_id or None

    b9j_id: str | None = None
    if regime == "live" and genius_id:
        b9j_id = genius_id
    elif regime == "prematch" and sr_id:
        mapping = await _bet9ja_prematch_map.get(clients[Bookmaker.BET9JA])
        b9j_id = mapping.get(sr_id) or mapping.get(f"sr:match:{sr_id}")

    entry = {
        "sr_id": sr_id, "genius_id": genius_id,
        "sb_id": sb_id, "b9j_id": b9j_id, "bw_id": bw_id,
    }
    # Don't poison the cache with negative b9j_id resolutions for prematch
    # events: the bet9ja prematch map takes ~2 min to build, so events
    # resolved during startup would get b9j_id=None cached forever (the
    # cache persists to disk across restarts). Skipping the write means the
    # next tick re-resolves and picks up the now-built map. SB/BW ids
    # don't have this problem — they're computed directly from sr_id.
    poisons_cache = regime == "prematch" and b9j_id is None and bool(sr_id)
    if not poisons_cache:
        cache.set(key, entry)
    # Log once per resolve attempt (cache miss). For poisoned entries this
    # logs every tick until the map is built, which is the visible signal
    # that retries are happening.
    log.info(
        "resolved event %s (regime=%s) sr=%s genius=%s sb=%s b9j=%s bw=%s%s",
        event_id, regime,
        sr_id or "-", genius_id or "-",
        sb_id or "-", b9j_id or "-", bw_id or "-",
        " [not cached, will retry]" if poisons_cache else "",
    )
    return _from_cached(entry)


def _from_cached(entry: dict) -> tuple[dict[Bookmaker, str | None], str, str]:
    return (
        {
            Bookmaker.SPORTYBET: entry.get("sb_id") or None,
            Bookmaker.BET9JA: entry.get("b9j_id") or None,
            Bookmaker.BETWAY: entry.get("bw_id") or None,
        },
        entry.get("sr_id") or "",
        entry.get("genius_id") or "",
    )
