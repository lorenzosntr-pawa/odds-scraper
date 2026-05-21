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
) -> list[sqlite3.Row]:
    """Return events whose latest snapshot is in the given status.

    Ended events are limited to the last 24 hours.
    """
    if status not in _STATUS_DB_VALUES:
        raise ValueError(
            f"unknown status {status!r}; expected one of "
            f"{sorted(_STATUS_DB_VALUES)}",
        )
    db_status = _STATUS_DB_VALUES[status]
    cutoff: str | None = None
    if status == "ended":
        now = datetime.strptime(_utcnow_iso(), "%Y-%m-%dT%H:%M:%SZ")
        cutoff = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    order_clause = {
        "live":     "ORDER BY s.match_minute DESC",
        "upcoming": "ORDER BY e.kickoff_utc ASC",
        "ended":    "ORDER BY s.ts_utc DESC",
    }[status]
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
          {"AND s.ts_utc >= :cutoff" if cutoff else ""}
        GROUP BY e.id
        {order_clause}
    """
    params: dict[str, str] = {"db_status": db_status}
    if cutoff:
        params["cutoff"] = cutoff
    return conn.execute(sql, params).fetchall()


def get_latest_prices_for_event(
    conn: sqlite3.Connection, event_id: str, scope: Scope = "collapsed",
) -> list[sqlite3.Row]:
    """Latest price per (bookmaker, market_id, line, side) for one event.

    scope='collapsed' restricts to the 1x2 family.
    scope='opened' returns all markets.
    """
    if scope not in ("collapsed", "opened"):
        raise ValueError(f"unknown scope {scope!r}")
    # Group by the full outcome key so the "latest" is computed per outcome,
    # not per bookmaker. Today the writer batches all of a bookmaker's
    # markets at the same ts_utc per tick, so the two formulations would
    # return the same rows — but pinning each outcome to its own MAX(ts)
    # protects against future writer changes that may emit per-market.
    market_filter = ""
    market_params: tuple[str, ...] = ()
    if scope == "collapsed":
        placeholders = ",".join("?" * len(COLLAPSED_MARKETS))
        market_filter = f"AND p.market_id IN ({placeholders})"
        market_params = COLLAPSED_MARKETS
    sql = f"""
        WITH latest_per_outcome AS (
            SELECT event_id, bookmaker, market_id, line, side,
                   MAX(ts_utc) AS max_ts
            FROM prices
            WHERE event_id = ?
            GROUP BY event_id, bookmaker, market_id, line, side
        )
        SELECT p.bookmaker, p.market_id, p.line, p.side,
               p.odds, p.probability
        FROM prices p
        JOIN latest_per_outcome l
          ON l.event_id  = p.event_id
         AND l.bookmaker = p.bookmaker
         AND l.market_id = p.market_id
         AND l.line      = p.line
         AND l.side      = p.side
         AND l.max_ts    = p.ts_utc
        WHERE p.event_id = ?
          {market_filter}
        ORDER BY p.market_id, p.line, p.side, p.bookmaker
    """
    params: list[str | int | float] = [event_id, event_id, *market_params]
    return conn.execute(sql, params).fetchall()


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
        SELECT ts_utc, bookmaker, side, odds, probability
        FROM prices
        WHERE event_id = ?
          AND market_id = ?
          AND line      = ?
          AND odds IS NOT NULL
        ORDER BY ts_utc DESC, bookmaker, side
    """
    return conn.execute(sql, (event_id, market_id, db_line)).fetchall()


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
