from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

Status = Literal["live", "upcoming", "ended"]
Scope = Literal["collapsed", "opened"]

_STATUS_DB_VALUES = {
    "live": "STARTED",
    "upcoming": "UPCOMING",
    "ended": "ENDED",
}
VALID_STATUSES: frozenset[str] = frozenset(_STATUS_DB_VALUES)

COLLAPSED_MARKETS: tuple[str, ...] = ("1x2_ft", "1x2_1up_ft", "1x2_2up_ft")


def open_ro_conn(db_path: Path) -> sqlite3.Connection:
    """Open the odds DB read-only with row factory set to sqlite3.Row."""
    uri = f"file:{Path(db_path).as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _utcnow_iso() -> str:
    """Indirection to allow monkeypatching in tests."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_events_by_status(
    conn: sqlite3.Connection, status: Status,
    *, country_id: str = "", league_id: str = "",
    offset: int = 0, limit: int = 20,
    date_from: str = "", date_to: str = "",
) -> list[sqlite3.Row]:
    """Return events whose latest snapshot is in the given status.

    Supports pagination via offset/limit and optional date range filtering
    on snapshot timestamp (date_from/date_to, inclusive, YYYY-MM-DD).
    """
    if status not in _STATUS_DB_VALUES:
        raise ValueError(
            f"unknown status {status!r}; expected one of "
            f"{sorted(_STATUS_DB_VALUES)}",
        )
    db_status = _STATUS_DB_VALUES[status]
    order_clause = {
        "live":     "ORDER BY s.match_minute DESC",
        "upcoming": "ORDER BY e.kickoff_utc ASC",
        "ended":    "ORDER BY s.ts_utc DESC",
    }[status]
    country_clause   = "AND e.country_id = :country_id" if country_id else ""
    league_clause    = "AND e.league_id  = :league_id"  if league_id  else ""
    date_from_clause = "AND s.ts_utc >= :date_from"     if date_from  else ""
    date_to_clause   = "AND s.ts_utc < :date_to_next"   if date_to    else ""
    # Hide upcoming events whose kickoff is >48h in the past — BP
    # sometimes publishes bogus startTime values that never transition.
    # Uses _utcnow_iso() (not datetime.now) so tests can pin "now".
    stale_upcoming_clause = ""
    if status == "upcoming":
        now = datetime.strptime(_utcnow_iso(), "%Y-%m-%dT%H:%M:%SZ")
        stale_cutoff = (now - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")
        stale_upcoming_clause = "AND e.kickoff_utc > :stale_cutoff"
    sql = f"""
        WITH latest AS (
            SELECT event_id, MAX(ts_utc) AS max_ts
            FROM snapshots
            GROUP BY event_id
        )
        SELECT
            e.id, e.home, e.away, e.kickoff_utc,
            s.status, s.match_minute, s.score_home, s.score_away,
            s.ts_utc AS latest_ts
        FROM events e
        JOIN latest l ON l.event_id = e.id
        JOIN snapshots s
          ON s.event_id = l.event_id
         AND s.ts_utc  = l.max_ts
        WHERE s.status = :db_status
          -- Defensive: hide events whose `events` row was only created
          -- via a sentinel snapshot (writer's upsert inserts with
          -- home='', away='' on first contact, then patches the
          -- placeholders when real data arrives). An event with empty
          -- home AND away never saw a successful collector tick — it's
          -- noise on the upcoming page.
          AND e.home != '' AND e.away != ''
          {date_from_clause}
          {date_to_clause}
          {stale_upcoming_clause}
          {country_clause}
          {league_clause}
        GROUP BY e.id
        {order_clause}
        LIMIT :limit OFFSET :offset
    """
    params: dict[str, object] = {
        "db_status": db_status,
        "limit": limit,
        "offset": offset,
    }
    if status == "upcoming":
        params["stale_cutoff"] = stale_cutoff
    if date_from:
        params["date_from"] = f"{date_from}T00:00:00Z"
    if date_to:
        params["date_to_next"] = (
            datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
        ).strftime("%Y-%m-%dT00:00:00Z")
    if country_id:
        params["country_id"] = country_id
    if league_id:
        params["league_id"] = league_id
    return conn.execute(sql, params).fetchall()


def get_date_range(conn: sqlite3.Connection) -> dict[str, str]:
    """Min/max kickoff dates across all events, for date picker bounds."""
    row = conn.execute(
        "SELECT MIN(DATE(kickoff_utc)) AS min_date, "
        "       MAX(DATE(kickoff_utc)) AS max_date "
        "FROM events WHERE home != '' AND away != ''"
    ).fetchone()
    return {
        "min": row["min_date"] or "",
        "max": row["max_date"] or "",
    }


def get_latest_prices_for_event(
    conn: sqlite3.Connection, event_id: str, scope: Scope = "collapsed",
) -> list[sqlite3.Row]:
    """Latest price per (bookmaker, market_id, line, side) for one event.

    scope='collapsed' restricts to the 1x2 family.
    scope='opened' returns all markets.

    Thin wrapper over get_latest_prices_for_events for single-event call sites.
    """
    by_event = get_latest_prices_for_events(conn, [event_id], scope=scope)
    return by_event.get(event_id, [])


def get_latest_prices_for_events(
    conn: sqlite3.Connection,
    event_ids: list[str] | tuple[str, ...],
    scope: Scope = "opened",
) -> dict[str, list[sqlite3.Row]]:
    """Latest price per (bookmaker, market_id, line, side) for many events.

    Returns a dict keyed by event_id. Events with no prices map to [].

    Built around the writer's invariant that every tick writes one
    snapshot per (event, bookmaker) atomically — so "latest price per
    outcome" reduces to "latest snapshot per (event, bookmaker), then
    all prices for those snapshot ids". Two index-friendly steps replace
    the N+1 per-event CTE that turned the home page into ~88s of DB
    work once the prices table grew past ~1M rows.

    scope='collapsed' restricts to the 1x2 family.
    scope='opened' returns all markets.
    """
    if scope not in ("collapsed", "opened"):
        raise ValueError(f"unknown scope {scope!r}")
    if not event_ids:
        return {}

    eid_list = list(event_ids)
    out: dict[str, list[sqlite3.Row]] = {eid: [] for eid in eid_list}

    # Step 1: absolute latest snapshot per (event, bookmaker). We do NOT
    # filter by fetch_status='ok' here — the card MUST reflect the most
    # recent tick's reality, not fall back to older successful ticks. If
    # the latest tick had no markets (failed fetch, or the live-skip
    # policy for B9J/BW), the cell shows em-dash. That's better than
    # showing odds the bookmaker has since removed.
    # Uses the idx_snapshots_event_bm_ts index added in schema v3.
    eid_placeholders = ",".join("?" * len(eid_list))
    snap_sql = f"""
        SELECT s.id
        FROM snapshots s
        JOIN (
            SELECT event_id, bookmaker, MAX(ts_utc) AS max_ts
            FROM snapshots
            WHERE event_id IN ({eid_placeholders})
            GROUP BY event_id, bookmaker
        ) latest
          ON latest.event_id  = s.event_id
         AND latest.bookmaker = s.bookmaker
         AND latest.max_ts    = s.ts_utc
    """
    snap_ids = [r[0] for r in conn.execute(snap_sql, eid_list).fetchall()]
    if not snap_ids:
        return out

    # Step 2: prices keyed by snapshot_id (primary key prefix — fast lookup).
    market_filter = ""
    market_params: tuple[str, ...] = ()
    if scope == "collapsed":
        mp = ",".join("?" * len(COLLAPSED_MARKETS))
        market_filter = f"AND p.market_id IN ({mp})"
        market_params = COLLAPSED_MARKETS
    snap_placeholders = ",".join("?" * len(snap_ids))
    prices_sql = f"""
        SELECT p.event_id, p.bookmaker, p.market_id, p.line, p.side,
               p.odds, p.probability
        FROM prices p
        WHERE p.snapshot_id IN ({snap_placeholders})
          {market_filter}
        ORDER BY p.event_id, p.market_id, p.line, p.side, p.bookmaker
    """
    rows = conn.execute(
        prices_sql, [*snap_ids, *market_params],
    ).fetchall()
    for r in rows:
        out[r["event_id"]].append(r)
    return out


def get_event_meta(
    conn: sqlite3.Connection, event_id: str,
) -> sqlite3.Row | None:
    """Return one Row joining event metadata with its latest snapshot state.

    Used by the detail page to render the header (teams, kickoff,
    current status, minute, score). Returns None if the event doesn't
    exist (e.g., bookmarked link to a deleted event).
    """
    sql = """
        WITH latest AS (
            SELECT event_id, MAX(ts_utc) AS max_ts
            FROM snapshots
            WHERE event_id = ?
            GROUP BY event_id
        )
        SELECT
            e.id, e.home, e.away, e.kickoff_utc,
            e.country_name, e.league_name,
            s.status, s.match_minute, s.score_home, s.score_away,
            s.ts_utc AS latest_ts
        FROM events e
        LEFT JOIN latest l ON l.event_id = e.id
        LEFT JOIN snapshots s
          ON s.event_id = l.event_id
         AND s.ts_utc  = l.max_ts
        WHERE e.id = ?
        LIMIT 1
    """
    return conn.execute(sql, (event_id, event_id)).fetchone()


def get_market_history_for_event(
    conn: sqlite3.Connection,
    event_id: str,
    market_id: str,
    line: float | None = None,
) -> list[sqlite3.Row]:
    """All snapshots' prices for one (event, market) — for the detail page.

    For non-parameterized markets pass line=None (the helper translates
    that to the 0.0 sentinel that the DB stores).

    Returns rows ordered (ts_utc, bookmaker, side) so the caller can
    bucket by ts then by bookmaker then by side.
    """
    db_line = 0.0 if line is None else float(line)
    sql = """
        SELECT p.ts_utc, p.bookmaker, p.side, p.odds, p.probability,
               s.match_minute, s.score_home, s.score_away, s.status
        FROM prices p
        JOIN snapshots s
          ON s.event_id  = p.event_id
         AND s.ts_utc    = p.ts_utc
         AND s.bookmaker = p.bookmaker
        WHERE p.event_id = ?
          AND p.market_id = ?
          AND p.line      = ?
          AND p.odds IS NOT NULL
        ORDER BY p.ts_utc DESC, p.bookmaker, p.side
    """
    return conn.execute(sql, (event_id, market_id, db_line)).fetchall()


def get_our_history_for_event(
    conn: sqlite3.Connection, event_id: str, market_id: str,
) -> dict[str, dict[str, float | None]]:
    """OUR engine output per tick for a 1UP/2UP market, V3 + V4.

    Shape: {ts_utc: {
        "home_odds_v3","away_odds_v3","home_prob_v3","away_prob_v3",
        "home_odds_v4","away_odds_v4","home_prob_v4","away_prob_v4"}}.
    Empty dict if not a UP market or no rows. (V1/V2 retired from the live
    pipeline; their columns remain but are no longer read.)
    """
    if market_id == "1x2_1up_ft":
        v3_oh, v3_oa, v3_ph, v3_pa = (
            "v3_1up_home_capped", "v3_1up_away_capped", "v3_p_home_1", "v3_p_away_1")
        v4_oh, v4_oa, v4_ph, v4_pa = (
            "v4_1up_home_capped", "v4_1up_away_capped", "v4_p_home_1", "v4_p_away_1")
    elif market_id == "1x2_2up_ft":
        v3_oh, v3_oa, v3_ph, v3_pa = (
            "v3_2up_home_capped", "v3_2up_away_capped", "v3_p_home_2", "v3_p_away_2")
        v4_oh, v4_oa, v4_ph, v4_pa = (
            "v4_2up_home_capped", "v4_2up_away_capped", "v4_p_home_2", "v4_p_away_2")
    else:
        return {}
    rows = conn.execute(
        f"SELECT ts_utc, {v3_oh}, {v3_oa}, {v3_ph}, {v3_pa}, "
        f"       {v4_oh}, {v4_oa}, {v4_ph}, {v4_pa} "
        f"FROM pricer_live_results WHERE event_id = ?",
        (event_id,),
    ).fetchall()
    return {
        r["ts_utc"]: {
            "home_odds_v3": r[v3_oh], "away_odds_v3": r[v3_oa],
            "home_prob_v3": r[v3_ph], "away_prob_v3": r[v3_pa],
            "home_odds_v4": r[v4_oh], "away_odds_v4": r[v4_oa],
            "home_prob_v4": r[v4_ph], "away_prob_v4": r[v4_pa],
        }
        for r in rows
    }


def get_country_league_index(
    conn: sqlite3.Connection,
) -> list[dict]:
    """Distinct country+league pairs across all events, grouped by country.

    Skips rows where country_name is NULL or empty. Country list is sorted
    by country_name; leagues within each country sorted by league_name.

    Used to populate the cascading Country → League filter on the home page.
    """
    sql = """
        SELECT DISTINCT country_id, country_name, league_id, league_name
        FROM events
        WHERE country_name IS NOT NULL AND country_name != ''
        ORDER BY country_name, league_name
    """
    out: list[dict] = []
    last_country: tuple[str, str] | None = None
    for r in conn.execute(sql).fetchall():
        key = (r["country_id"] or "", r["country_name"] or "")
        if last_country != key:
            out.append({
                "country_id": key[0],
                "country_name": key[1],
                "leagues": [],
            })
            last_country = key
        out[-1]["leagues"].append({
            "league_id":   r["league_id"]   or "",
            "league_name": r["league_name"] or "",
        })
    return out


def get_snapshot_meta_for_timestamps(
    conn: sqlite3.Connection, event_id: str, timestamps: list[str],
) -> list[sqlite3.Row]:
    """Return aggregated snapshot metadata for specific (event, ts) pairs."""
    if not timestamps:
        return []
    placeholders = ",".join("?" * len(timestamps))
    return conn.execute(
        f"SELECT ts_utc, MAX(status) AS status, "
        f"       MAX(match_minute) AS match_minute, "
        f"       MAX(score_home) AS score_home, "
        f"       MAX(score_away) AS score_away "
        f"FROM snapshots WHERE event_id = ? AND ts_utc IN ({placeholders}) "
        f"GROUP BY ts_utc",
        (event_id, *timestamps),
    ).fetchall()


def get_available_lines(
    conn: sqlite3.Connection, event_id: str,
) -> dict[str, list[float]]:
    """Distinct (market_id, line) pairs that have priced rows for one event.

    Skips the 0.0 sentinel line that SqliteWriter stores for non-parameterized
    markets (1x2 family) so only true parameterized lines come back. Real
    parameterized lines like 0.5 pass through because 0.5 > 0.

    Used by the detail page's two-stage market picker to render only the
    lines that this event actually has data for.
    """
    sql = """
        SELECT DISTINCT market_id, line
        FROM prices
        WHERE event_id = ?
          AND line > 0
        ORDER BY market_id, line
    """
    rows = conn.execute(sql, (event_id,)).fetchall()
    out: dict[str, list[float]] = {}
    for r in rows:
        out.setdefault(r["market_id"], []).append(r["line"])
    return out
