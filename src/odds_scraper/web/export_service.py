from __future__ import annotations

import sqlite3
from typing import Iterable, Iterator

VALID_REGIMES = ("any", "prematch", "live")
VALID_DENSITIES = ("all", "latest", "onchange")
SIM_ENGINES = ("v3", "v4")
_STATUS_BY_REGIME = {"prematch": "UPCOMING", "live": "STARTED"}


def _regime_status(regime: str) -> str | None:
    if regime not in VALID_REGIMES:
        raise ValueError(f"unknown regime {regime!r}")
    return _STATUS_BY_REGIME.get(regime)


def select_ticks(
    conn: sqlite3.Connection, regime: str, density: str, scope: dict,
) -> list[dict]:
    """One row per (event_id, ts_utc) tick with event + snapshot metadata.

    regime: any/prematch/live (snapshot status filter).
    density: all (every tick) / latest (last per event, MAX ts then MAX
             snapshot_id tiebreak) / onchange (same set as 'all'; the actual
             collapse is applied later by collapse_onchange).
    scope: country/league/event/date/search (all optional).
    """
    if density not in VALID_DENSITIES:
        raise ValueError(f"unknown density {density!r}")
    status = _regime_status(regime)

    where = ["e.home != '' AND e.away != ''"]
    params: list = []
    if status:
        where.append("s.status = ?"); params.append(status)
    if scope.get("country"):
        where.append("e.country_id = ?"); params.append(scope["country"])
    if scope.get("league"):
        where.append("e.league_id = ?"); params.append(scope["league"])
    if scope.get("event_id"):
        where.append("s.event_id = ?"); params.append(scope["event_id"])
    if scope.get("date"):
        where.append("DATE(e.kickoff_utc) = ?"); params.append(scope["date"])
    if scope.get("search"):
        where.append("(LOWER(e.home) LIKE ? OR LOWER(e.away) LIKE ?)")
        like = f"%{scope['search'].lower()}%"; params += [like, like]
    where_sql = " AND ".join(where)

    base = """
        SELECT MIN(s.id)           AS snapshot_id,
               s.event_id, s.ts_utc,
               MAX(s.status)       AS status,
               MAX(s.match_minute) AS match_minute,
               MAX(s.score_home)   AS score_home,
               MAX(s.score_away)   AS score_away,
               MAX(e.home)         AS home,
               MAX(e.away)         AS away,
               MAX(e.kickoff_utc)  AS kickoff_utc,
               MAX(e.country_name) AS country_name,
               MAX(e.league_name)  AS league_name
        FROM snapshots s JOIN events e ON e.id = s.event_id
    """
    if density == "latest":
        regime_filter = f"WHERE status = '{status}'" if status else ""
        sql = f"""
            WITH latest AS (
                SELECT event_id, MAX(ts_utc) AS max_ts
                FROM snapshots {regime_filter}
                GROUP BY event_id
            )
            {base}
            JOIN latest l ON l.event_id = s.event_id AND l.max_ts = s.ts_utc
            WHERE {where_sql}
            GROUP BY s.event_id, s.ts_utc
            ORDER BY s.event_id, s.ts_utc, MAX(s.id)
        """
    else:
        sql = f"""
            {base}
            WHERE {where_sql}
            GROUP BY s.event_id, s.ts_utc
            ORDER BY s.event_id, s.ts_utc
        """
    cur = conn.cursor(); cur.row_factory = sqlite3.Row
    return [dict(r) for r in cur.execute(sql, params).fetchall()]
