"""Wiring between bookieskit's real clients and our abstractions.

This is the only module that touches real bookmaker APIs through bookieskit.
The rest of the codebase depends only on the (Bookmaker -> fetcher) contract
and the (bp_detail -> (resolved, sr_id, genius_id)) resolver contract.
"""

from __future__ import annotations

import logging
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


async def make_bookmaker_clients(
    stack: AsyncExitStack, country: str,
) -> dict[Bookmaker, Any]:
    return {
        Bookmaker.BETPAWA: await stack.enter_async_context(BetPawa(country=country)),
        Bookmaker.SPORTYBET: await stack.enter_async_context(SportyBet(country=country)),
        Bookmaker.BET9JA: await stack.enter_async_context(Bet9ja(country=country)),
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
        # SportyBet's get_event_detail uses live=True for in-play; we let the
        # default (live=False) suffice since 1up/2up are prematch-only markets
        # on SportyBet. If live coverage of these markets ever opens, this is
        # the place to switch.
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
      - Bet9ja: prematch via `build_prematch_event_map(sport_id='1')`; live via
        BetGenius id (only id format Bet9ja-live accepts in this lib).

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
        try:
            mapping = await clients[Bookmaker.BET9JA].build_prematch_event_map(sport_id="1")
            b9j_id = mapping.get(sr_id) or mapping.get(f"sr:match:{sr_id}")
        except Exception as e:  # noqa: BLE001
            log.warning("bet9ja prematch map failed: %s", e)

    entry = {
        "sr_id": sr_id, "genius_id": genius_id,
        "sb_id": sb_id, "b9j_id": b9j_id, "bw_id": bw_id,
    }
    cache.set(key, entry)
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
