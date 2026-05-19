"""Capture raw JSON responses from one or more bookmakers for a given event.

Usage:
    python scripts/capture_fixtures.py 33660318 --bookmaker betpawa \
        --country ng --out tests/fixtures/betpawa_event_real.json

For non-BetPawa bookmakers the event id is the bookmaker's native id,
already prefixed correctly (sr:match:... for SportyBet, raw numeric for
Betway, Bet9ja internal id, etc.). Use scripts/probe_resolution.py (or
the live resolution_runtime.resolve_event) to find these.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from contextlib import AsyncExitStack
from pathlib import Path

from bookieskit import Bet9ja, BetPawa, Betway, SportyBet

_CLIENTS = {
    "betpawa": BetPawa,
    "sportybet": SportyBet,
    "bet9ja": Bet9ja,
    "betway": Betway,
}


async def main_async(args) -> int:
    cls = _CLIENTS[args.bookmaker]
    async with AsyncExitStack() as stack:
        client = await stack.enter_async_context(cls(country=args.country))
        if args.bookmaker == "betpawa":
            data = await client.get_event_detail(event_id=args.event_id)
        elif args.bookmaker == "sportybet":
            data = await client.get_event_detail(
                event_id=args.event_id, live=args.live,
            )
        elif args.bookmaker == "bet9ja":
            data = await client.get_event_detail(event_id=args.event_id)
        elif args.bookmaker == "betway":
            data = await client.get_event_markets(event_id=args.event_id)
        else:
            raise ValueError(args.bookmaker)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"wrote {out}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("event_id")
    p.add_argument("--bookmaker", required=True, choices=list(_CLIENTS))
    p.add_argument("--country", default="ng")
    p.add_argument("--live", action="store_true")
    p.add_argument("--out", required=True)
    args = p.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
