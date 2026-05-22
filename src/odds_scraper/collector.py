from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from bookieskit import extract_kickoff, extract_participants

from .models import (
    MARKET_MANIFEST, PROB_BOOKMAKERS, Bookmaker, FetchStatus, PriceKey, Snapshot,
)
from .status import parse_clock, parse_score, parse_status

log = logging.getLogger(__name__)

Fetcher = Callable[..., Awaitable[list]]

# Bookmakers we hit during live (STARTED) events. Bet9ja's live endpoint
# has known coverage gaps for our fixtures and Betway's live data tends
# to lag / stay stale, so for live ticks we only fetch BP + SB. Prematch
# uses all four. The skipped bookmakers still get a Snapshot row each
# tick (with empty prices and a clear fetch_status) so the per-event row
# shape is stable across regimes.
_LIVE_BOOKMAKERS: frozenset[Bookmaker] = frozenset(
    {Bookmaker.BETPAWA, Bookmaker.SPORTYBET},
)


class OddsCollector:
    """Stateless one-tick fan-out. Always returns 4 Snapshot rows (one per
    bookmaker). Failures are encoded into the row via fetch_status; the
    collector itself never raises.
    """

    def __init__(self, fetchers: dict[Bookmaker, Fetcher]):
        missing = [b for b in Bookmaker if b not in fetchers]
        if missing:
            raise ValueError(f"fetcher missing for: {[b.value for b in missing]}")
        self._fetchers = fetchers

    async def collect(
        self,
        bp_detail: dict[str, Any],
        resolved: dict[Bookmaker, Optional[str]],
        sr_id: str,
        genius_id: str,
    ) -> list[Snapshot]:
        ts = datetime.now(timezone.utc)
        status = parse_status(bp_detail)
        minute = parse_clock(bp_detail)
        score = parse_score(bp_detail)

        participants = extract_participants(bp_detail, "betpawa")
        home = participants.home or ""
        away = participants.away or ""

        kickoff = extract_kickoff(bp_detail, "betpawa") or ts

        # Country and league come straight from BetPawa's structured
        # top-level keys. or {} defends against missing keys; or "" makes
        # individual missing names empty strings so the writer's upsert
        # treats them the same as a sentinel-row update (no-op patching).
        region = bp_detail.get("region") or {}
        competition = bp_detail.get("competition") or {}
        country_id = str(region.get("id") or "")
        country_name = str(region.get("name") or "")
        league_id = str(competition.get("id") or "")
        league_name = str(competition.get("name") or "")

        event_id = str(bp_detail.get("id", ""))
        regime = "live" if status.value == "STARTED" else "non-live"

        async def run(b: Bookmaker, target_id: Optional[str]):
            if regime == "live" and b not in _LIVE_BOOKMAKERS:
                # Skipped by policy — Bet9ja live + Betway live are
                # currently unreliable, so we don't hit them during
                # STARTED ticks. Emit an empty snapshot so the row shape
                # per tick stays stable.
                return b, (FetchStatus.LOOKUP_FAILED,
                           "skipped: live regime restricted to BP+SB", [])
            if b != Bookmaker.BETPAWA and not target_id:
                log.warning(
                    "no id resolved for %s (event=%s, regime=%s, sr=%s, genius=%s)",
                    b.value, event_id, regime, sr_id or "-", genius_id or "-",
                )
                return b, (FetchStatus.LOOKUP_FAILED,
                           "no id resolved for bookmaker", [])
            try:
                if b == Bookmaker.BETPAWA:
                    markets = await self._fetchers[b](bp_detail)
                elif b == Bookmaker.BET9JA:
                    # Bet9ja live needs to also try genius_id as a fallback
                    # candidate id — bookieskit's find_event_id_by_sr_id
                    # can return None or a stale id, and empirically the
                    # BetGenius id sometimes works as the b9j EVENTID.
                    fallback = (
                        genius_id if regime == "live" and genius_id
                        and genius_id != target_id else None
                    )
                    markets = await self._fetchers[b](
                        target_id, live=(regime == "live"),
                        fallback_id=fallback,
                    )
                else:
                    # SportyBet uses a different productId for live; Betway
                    # uses the same endpoint for both.
                    markets = await self._fetchers[b](
                        target_id, live=(regime == "live"),
                    )
                if not markets:
                    log.info(
                        "fetcher for %s returned empty markets (event=%s, regime=%s, id=%s)",
                        b.value, event_id, regime, target_id or "-",
                    )
                return b, (FetchStatus.OK, "", markets)
            except Exception as e:  # noqa: BLE001
                # Some bookmakers (Bet9ja behind Akamai) return multi-line
                # HTML error pages — collapse whitespace and truncate so the
                # log stays one line. Some exception classes have empty
                # __str__ (info lives on attributes), so always include the
                # type name so we never log a bare "fetch failed for X:".
                short = " ".join(str(e).split())[:120] or "<no message>"
                etype = type(e).__name__
                log.warning(
                    "fetch failed for %s: %s: %s (event=%s, regime=%s, id=%s)",
                    b.value, etype, short, event_id, regime, target_id or "-",
                )
                return b, (FetchStatus.HTTP_ERROR,
                           f"{etype}: {short}", [])

        coros = [
            run(b, resolved.get(b) if b != Bookmaker.BETPAWA else None)
            for b in Bookmaker
        ]
        results: dict[Bookmaker, tuple[FetchStatus, str, list]] = {}
        for b, payload in await asyncio.gather(*coros):
            results[b] = payload

        rows: list[Snapshot] = []
        for b in Bookmaker:
            status_fetch, error, markets = results[b]
            want_prob = b in PROB_BOOKMAKERS
            prices = (
                _extract_prices_for_manifest(markets, want_prob)
                if status_fetch == FetchStatus.OK
                else {}
            )
            rows.append(Snapshot(
                ts_utc=ts,
                event_bp_id=str(bp_detail.get("id", "")),
                sr_id=sr_id or "",
                genius_id=genius_id or "",
                home=home, away=away,
                kickoff_utc=kickoff,
                status=status,
                match_minute=minute,
                score_home=score[0] if score else None,
                score_away=score[1] if score else None,
                bookmaker=b,
                fetch_status=status_fetch,
                fetch_error=error,
                prices=prices,
                country_id=country_id,
                country_name=country_name,
                league_id=league_id,
                league_name=league_name,
            ))
        return rows


def _extract_prices_for_manifest(
    markets: list, want_prob: bool,
) -> dict[PriceKey, tuple[Optional[float], Optional[float]]]:
    by_canon = {m.canonical_id: m for m in markets}
    out: dict[PriceKey, tuple[Optional[float], Optional[float]]] = {}
    for spec in MARKET_MANIFEST:
        m = by_canon.get(spec.canonical_id)
        if m is None:
            continue
        if spec.lines is None:
            by_side = {o.canonical_name: o for o in m.outcomes}
            for side in spec.sides:
                o = by_side.get(side)
                if o is None or o.odds is None:
                    continue
                prob = o.true_probability if want_prob else None
                out[PriceKey(spec.canonical_id, None, side)] = (
                    float(o.odds),
                    float(prob) if prob is not None else None,
                )
        else:
            # NormalizedMarket.lines is `dict | None` per bookieskit types,
            # so the None coalesce is load-bearing here.
            lines_map = m.lines or {}
            for line in spec.lines:
                outcomes = lines_map.get(line)
                if not outcomes:
                    continue
                by_side = {o.canonical_name: o for o in outcomes}
                for side in spec.sides:
                    o = by_side.get(side)
                    if o is None or o.odds is None:
                        continue
                    prob = o.true_probability if want_prob else None
                    out[PriceKey(spec.canonical_id, line, side)] = (
                        float(o.odds),
                        float(prob) if prob is not None else None,
                    )
    return out
