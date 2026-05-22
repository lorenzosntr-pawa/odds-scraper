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

    async def fetch_sportybet(sb_id: str, *, live: bool = False) -> list:
        # SportyBet uses productId=1 for live, productId=3 for prematch.
        # Without `live=True` we silently get prematch data (which is empty
        # for events already in-play) — that's what produced sb=0 ticks on
        # STARTED events before this fix.
        sb = clients[Bookmaker.SPORTYBET]
        detail = await sb.get_event_detail(event_id=sb_id, live=live)
        return parse_markets(
            detail, platform="sportybet", registry=registry, probability="true",
        )

    async def fetch_bet9ja(
        b9j_id: str, *, live: bool = False,
        fallback_id: str | None = None,
    ) -> list:
        # Bet9ja exposes two separate endpoints. Prematch uses
        # get_event_detail (response under D.O with `S_*` odds keys); live
        # uses get_live_event_detail / GetLiveEvent (response under D.O
        # with `LIVES_*` keys). Live ids ideally come from
        # find_event_id_by_sr_id, but that can miss; the BetGenius id
        # sometimes works as the EVENTID too — try it as a fallback when
        # the primary lookup returns no markets.
        b9j = clients[Bookmaker.BET9JA]

        async def _fetch_one(eid: str) -> list:
            if live:
                detail = await b9j.get_live_event_detail(event_id=eid)
            else:
                detail = await b9j.get_event_detail(event_id=eid)
            return parse_markets(detail, platform="bet9ja", registry=registry)

        markets = await _fetch_one(b9j_id)
        if not markets and fallback_id and fallback_id != b9j_id:
            markets = await _fetch_one(fallback_id)
        return markets

    async def fetch_betway(bw_id: str, *, live: bool = False) -> list:
        # Use the auto-paginated get_markets helper: Betway's underlying
        # endpoint caps at 100 markets per page, so for big fixtures the
        # per-team Over/Under and Next Goal markets land past page 1 and
        # would otherwise be silently dropped. get_markets also fetches
        # the scoreboard so the team-name placeholder substitution in the
        # Betway parser fires correctly. `live` is accepted for signature
        # uniformity; Betway uses the same endpoint for prematch and live.
        del live  # endpoint is the same; flag accepted for symmetry
        bw = clients[Bookmaker.BETWAY]
        return await bw.get_markets(event_id=bw_id, registry=registry)

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
    if regime == "live" and sr_id:
        # Bet9ja's live events expose EXTID = SR id. Resolve to Bet9ja's
        # internal numeric id via find_event_id_by_sr_id (which scans the
        # live-events listing once per call). The internal id is what
        # GetLiveEvent expects, not the BetGenius id we used to pass.
        try:
            b9j_id = await clients[Bookmaker.BET9JA].find_event_id_by_sr_id(sr_id)
        except Exception as e:  # noqa: BLE001
            log.warning(
                "bet9ja live id lookup failed for sr=%s: %s: %s",
                sr_id, type(e).__name__,
                " ".join(str(e).split())[:120] or "<no message>",
            )
            b9j_id = None
    elif regime == "prematch" and sr_id:
        mapping = await _bet9ja_prematch_map.get(clients[Bookmaker.BET9JA])
        b9j_id = mapping.get(sr_id) or mapping.get(f"sr:match:{sr_id}")

    entry = {
        "sr_id": sr_id, "genius_id": genius_id,
        "sb_id": sb_id, "b9j_id": b9j_id, "bw_id": bw_id,
    }
    # Don't poison the cache with negative b9j_id resolutions:
    #   - prematch: the bet9ja prematch map takes ~2 min to build, so events
    #     resolved during startup would get b9j_id=None cached forever (the
    #     cache persists to disk across restarts).
    #   - live: find_event_id_by_sr_id depends on Bet9ja's current live-list
    #     which can transiently miss an event during half-time / brief
    #     suspensions; we want to retry next tick rather than lock in None.
    # Skipping the write means the next tick re-resolves. SB/BW ids don't
    # have this problem — they're computed directly from sr_id.
    poisons_cache = b9j_id is None and bool(sr_id)
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
