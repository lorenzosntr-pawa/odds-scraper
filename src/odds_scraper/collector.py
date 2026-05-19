from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from bookieskit import extract_kickoff, extract_participants

from .models import (
    Bookmaker, FetchStatus, Market, Outcome, Snapshot,
)
from .status import parse_clock, parse_score, parse_status

log = logging.getLogger(__name__)

_PROB_BOOKMAKERS = {Bookmaker.BETPAWA, Bookmaker.SPORTYBET}

Fetcher = Callable[..., Awaitable[list]]


class OddsCollector:
    """Stateless one-tick fan-out. Always returns 24 rows per call.

    Failures (lookup, HTTP, missing market, suspended outcome) are encoded
    into the row via fetch_status; the collector itself never raises.
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

        async def run(b: Bookmaker, target_id: Optional[str]):
            if b != Bookmaker.BETPAWA and not target_id:
                return b, (FetchStatus.LOOKUP_FAILED,
                           "no id resolved for bookmaker", [])
            try:
                if b == Bookmaker.BETPAWA:
                    markets = await self._fetchers[b](bp_detail)
                else:
                    markets = await self._fetchers[b](target_id)
                return b, (FetchStatus.OK, "", markets)
            except Exception as e:  # noqa: BLE001
                # Some bookmakers (Bet9ja behind Akamai) return multi-line
                # HTML error pages — truncate so the log stays one line.
                short = " ".join(str(e).split())[:120]
                log.warning("fetch failed for %s: %s", b.value, short)
                return b, (FetchStatus.HTTP_ERROR, f"{type(e).__name__}: {short}", [])

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
            for market in (Market.ONE_UP, Market.TWO_UP):
                outcomes_map = (
                    _outcomes_for(market, markets)
                    if status_fetch == FetchStatus.OK
                    else None
                )
                for outcome in (Outcome.HOME, Outcome.DRAW, Outcome.AWAY):
                    odds: Optional[float] = None
                    prob: Optional[float] = None
                    row_status = status_fetch
                    row_error = error
                    if status_fetch == FetchStatus.OK:
                        if outcomes_map is None:
                            row_status = FetchStatus.NOT_OFFERED
                            row_error = f"{market.value} not in response"
                        else:
                            o = outcomes_map.get(outcome.value)
                            if o is None:
                                row_status = FetchStatus.SUSPENDED
                                row_error = "no price for outcome"
                            else:
                                odds = o[0]
                                if b in _PROB_BOOKMAKERS:
                                    prob = o[1]
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
                        market=market,
                        outcome=outcome,
                        odds=odds,
                        probability=prob,
                        fetch_status=row_status,
                        fetch_error=row_error,
                    ))
        return rows


def _outcomes_for(
    market: Market, markets: list,
) -> Optional[dict[str, tuple[float, Optional[float]]]]:
    for m in markets:
        if m.canonical_id == market.value:
            out: dict[str, tuple[float, Optional[float]]] = {}
            for o in m.outcomes:
                if o.odds is None:
                    continue
                prob = getattr(o, "true_probability", None)
                out[o.canonical_name] = (
                    float(o.odds),
                    float(prob) if prob is not None else None,
                )
            return out
    return None
