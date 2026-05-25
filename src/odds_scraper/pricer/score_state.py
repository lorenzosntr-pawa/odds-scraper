"""History-aware score state for early-payout markets.

The pricing engine sees only the CURRENT score on each tick, but 1UP/2UP
markets settle once a side has ever held the required lead during the
match. A match that goes 1-0 → 1-1 → 1-2 has both home 1UP and away 1UP
already triggered; the current diff at 1-1 doesn't tell you that. These
helpers read the snapshot timeline so callers can pass max_home_lead /
max_away_lead to `engine.price_early_payout_markets`.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable


def max_leads_so_far(
    conn: sqlite3.Connection, event_id: str,
) -> tuple[int, int]:
    """`(max_home_lead, max_away_lead)` seen across all snapshots
    written so far for `event_id`. Returns `(0, 0)` when no snapshot has
    a recorded score (prematch / very early ticks)."""
    row = conn.execute(
        "SELECT MAX(score_home - score_away), "
        "       MAX(score_away - score_home) "
        "FROM snapshots WHERE event_id = ? AND score_home IS NOT NULL",
        (event_id,),
    ).fetchone()
    mh = row[0] if row and row[0] is not None else 0
    ma = row[1] if row and row[1] is not None else 0
    return max(0, mh), max(0, ma)


_CHUNK = 500  # Stay under SQLite's 999 host-parameter limit.


def max_leads_latest_for_events(
    conn: sqlite3.Connection, event_ids: Iterable[str],
) -> dict[str, tuple[int, int]]:
    """`{event_id: (max_home_lead, max_away_lead)}` — the cumulative max
    over the whole event timeline. Used by the home page card, where
    only the latest tick's max matters (not the running max per tick).
    Events with no scored snapshot are omitted from the result."""
    ids = list(event_ids)
    if not ids:
        return {}
    out: dict[str, tuple[int, int]] = {}
    for i in range(0, len(ids), _CHUNK):
        chunk = ids[i : i + _CHUNK]
        placeholders = ",".join("?" * len(chunk))
        rows = conn.execute(
            f"SELECT event_id, "
            f"       MAX(score_home - score_away) AS mh, "
            f"       MAX(score_away - score_home) AS ma "
            f"FROM snapshots WHERE event_id IN ({placeholders}) "
            f"AND score_home IS NOT NULL "
            f"GROUP BY event_id",
            chunk,
        ).fetchall()
        for ev_id, mh, ma in rows:
            out[ev_id] = (max(0, mh or 0), max(0, ma or 0))
    return out


def max_leads_for_events(
    conn: sqlite3.Connection, event_ids: Iterable[str],
) -> dict[tuple[str, str], tuple[int, int]]:
    """For every `(event_id, ts_utc)` snapshot of the given events,
    return the running `(max_home_lead, max_away_lead)` up to and
    including that tick. Per-bookmaker rows are collapsed via MAX since
    all four books mirror BetPawa's detail score.

    Suitable for the simulator runner / backfill loop: one query per
    chunk of events, then an in-memory pass to build the per-tick map.
    """
    ids = list(event_ids)
    if not ids:
        return {}
    out: dict[tuple[str, str], tuple[int, int]] = {}
    for i in range(0, len(ids), _CHUNK):
        chunk = ids[i : i + _CHUNK]
        placeholders = ",".join("?" * len(chunk))
        rows = conn.execute(
            f"SELECT event_id, ts_utc, "
            f"       MAX(score_home) AS sh, MAX(score_away) AS sa "
            f"FROM snapshots WHERE event_id IN ({placeholders}) "
            f"AND score_home IS NOT NULL "
            f"GROUP BY event_id, ts_utc "
            f"ORDER BY event_id, ts_utc",
            chunk,
        ).fetchall()
        cur_event: str | None = None
        mh = ma = 0
        for ev_id, ts, sh, sa in rows:
            if ev_id != cur_event:
                cur_event = ev_id
                mh = ma = 0
            mh = max(mh, sh - sa)
            ma = max(ma, sa - sh)
            out[(ev_id, ts)] = (max(0, mh), max(0, ma))
    return out
