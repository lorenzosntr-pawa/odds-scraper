"""One-shot diagnostic: probe bet9ja's live events list.

Usage:
    .venv\\Scripts\\python.exe scripts\\diagnose_bet9ja_live.py

Prints:
  - Total live event count returned by bet9ja for soccer (sport 3000001).
  - Sample event rows (internal id, EXTID, DS).
  - Whether the SR ids from `data/odds.db`'s live events appear in the list.

Use this to tell apart:
  a) bet9ja's live API is well-populated but doesn't have OUR events
     → genuine coverage gap, nothing to fix in our code.
  b) bet9ja's live API returns few/zero events
     → endpoint issue or wrong sport_id.
"""

from __future__ import annotations

import asyncio
import pathlib
import sqlite3
import sys

from bookieskit import Bet9ja


async def main() -> int:
    # Pick the live SR ids the scraper is currently trying to resolve, from
    # the live DB. Falls back to the user's failing examples from the log
    # if the DB is unavailable.
    db_path = pathlib.Path("data/odds.db")
    target_sr_ids: list[str] = []
    if db_path.exists():
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT DISTINCT events.sr_id, events.home, events.away "
                "FROM events "
                "JOIN snapshots ON snapshots.event_id = events.id "
                "WHERE snapshots.status = 'STARTED' "
                "  AND events.sr_id != '' "
                "ORDER BY snapshots.ts_utc DESC "
                "LIMIT 10"
            ).fetchall()
            target_sr_ids = [r[0] for r in rows]
            print(f"Found {len(target_sr_ids)} live events in DB:")
            for r in rows:
                print(f"  sr={r[0]:>10}  {r[1]} vs {r[2]}")
        finally:
            conn.close()
    if not target_sr_ids:
        # Fallback to the examples from the live log
        target_sr_ids = ["70906796", "70906794", "70906792", "67091316"]
        print(f"No DB live events; using fallback list: {target_sr_ids}")
    print()

    async with Bet9ja(country="ng", max_concurrent=2, request_delay=0.5) as b9j:
        print("Calling get_live_events(sport_id='3000001') ...")
        live = await b9j.get_live_events(sport_id="3000001")
        events = (live.get("D") or {}).get("E") or {}
        print(f"bet9ja live events returned: {len(events)} total\n")

        if not events:
            print("WARNING: bet9ja's live events list is empty.")
            print("Either no soccer is live right now, the sport_id is wrong,")
            print("or the endpoint is having issues.")
            return 0

        # Print a few sample rows
        print("Sample of bet9ja's live events:")
        for i, (internal_id, ev) in enumerate(events.items()):
            if i >= 8:
                break
            print(f"  internal={internal_id}  EXTID={ev.get('EXTID')!r:>14}  DS={ev.get('DS')!r}")
        print()

        # For each target sr id, check if bet9ja has it in their live list
        ext_to_internal: dict[str, str] = {
            str(ev.get("EXTID", "") or ""): str(internal_id)
            for internal_id, ev in events.items()
        }
        print("Looking up our target SR ids in bet9ja's live list:")
        hits = 0
        for sr_id in target_sr_ids:
            bare = sr_id.removeprefix("sr:match:")
            internal = ext_to_internal.get(bare) or ext_to_internal.get(f"sr:match:{bare}")
            mark = "FOUND" if internal else "missing"
            print(f"  sr={bare:>10}  {mark:>8}  internal={internal or '-'}")
            if internal:
                hits += 1
        print()
        print(f"Summary: {hits}/{len(target_sr_ids)} of our live events are in bet9ja's live list.")
        if hits == 0:
            print(
                "→ bet9ja has live events but NONE of ours. Either bet9ja "
                "doesn't cover these fixtures live, OR the SR-id format "
                "differs between BetPawa and bet9ja for these matches."
            )
        elif hits < len(target_sr_ids):
            print(
                "→ partial coverage: bet9ja has SOME of our events live. "
                "find_event_id_by_sr_id will work for those; the rest "
                "genuinely aren't on bet9ja live right now."
            )
        else:
            print("→ full coverage. find_event_id_by_sr_id should be working.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
