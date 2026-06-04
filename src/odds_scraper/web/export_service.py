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
        where.append("(LOWER(e.home) LIKE ? ESCAPE '\\' OR LOWER(e.away) LIKE ? ESCAPE '\\')")
        escaped = scope["search"].lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"; params += [like, like]
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


def load_tick_prices(
    conn: sqlite3.Connection, event_id: str, ts_utc: str,
    markets: Iterable[tuple[str, float]] | None = None,
    books: Iterable[str] | None = None,
) -> list[dict]:
    """All price rows for one (event, ts) tick, optionally filtered to the
    selected (market_id, line) pairs and bookmakers. Ordered deterministically."""
    cur = conn.cursor()
    cur.row_factory = sqlite3.Row
    rows = [dict(r) for r in cur.execute(
        "SELECT bookmaker, market_id, line, side, odds, probability "
        "FROM prices WHERE event_id = ? AND ts_utc = ?",
        (event_id, ts_utc),
    ).fetchall()]
    if markets is not None:
        sel = {(m, float(l)) for m, l in markets}
        rows = [r for r in rows if (r["market_id"], float(r["line"])) in sel]
    if books is not None:
        bset = set(books)
        rows = [r for r in rows if r["bookmaker"] in bset]
    rows.sort(key=lambda r: (r["bookmaker"], r["market_id"], r["line"], r["side"]))
    return rows


def _fingerprint(rows: list[dict]) -> frozenset:
    """Hashable identity of a tick's selected price set. Odds rounded to 4 dp
    so float storage drift never reads as a 'change'."""
    return frozenset(
        (r["bookmaker"], r["market_id"], float(r["line"]), r["side"],
         None if r["odds"] is None else round(float(r["odds"]), 4))
        for r in rows
    )


def collapse_onchange(
    conn: sqlite3.Connection, ticks: list[dict],
    markets: Iterable[tuple[str, float]] | None,
) -> list[dict]:
    """Drop a tick whose selected-market price set equals the previous KEPT
    tick for the same event. Fingerprint is over the SELECTED markets only."""
    kept: list[dict] = []
    last_fp: dict[str, frozenset] = {}
    for t in ticks:
        fp = _fingerprint(load_tick_prices(conn, t["event_id"], t["ts_utc"], markets))
        if last_fp.get(t["event_id"]) == fp:
            continue
        last_fp[t["event_id"]] = fp
        kept.append(t)
    return kept


def limit_first_last(ticks: list[dict], first_n: int, last_n: int) -> list[dict]:
    """Per event, keep the first N and/or last N ticks (union, original order).
    0/0 is a no-op. Assumes `ticks` already ordered by (event_id, ts_utc)."""
    if not first_n and not last_n:
        return ticks
    by_event: dict[str, list[dict]] = {}
    for t in ticks:
        by_event.setdefault(t["event_id"], []).append(t)
    keep_ids: set[int] = set()
    for evs in by_event.values():
        picked = []
        if first_n:
            picked += evs[:first_n]
        if last_n:
            picked += evs[-last_n:]
        for t in picked:
            keep_ids.add(id(t))
    return [t for t in ticks if id(t) in keep_ids]


LONG_COLUMNS = (
    "event_id", "country_name", "league_name", "home", "away", "kickoff_utc",
    "snapshot_id", "ts_utc", "status", "match_minute", "score_home", "score_away",
    "bookmaker", "market_id", "line", "side", "odds", "probability",
    "is_simulated", "engine",
)


def csv_safe(value):
    """Prefix a leading =,+,-,@ with an apostrophe so spreadsheets don't
    execute the cell as a formula. Non-strings pass through unchanged."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        return "'" + value
    return value


def _meta(t: dict) -> dict:
    return {
        "event_id": t["event_id"], "country_name": t.get("country_name"),
        "league_name": t.get("league_name"), "home": t.get("home"),
        "away": t.get("away"), "kickoff_utc": t.get("kickoff_utc"),
        "snapshot_id": t.get("snapshot_id"), "ts_utc": t["ts_utc"],
        "status": t.get("status"), "match_minute": t.get("match_minute"),
        "score_home": t.get("score_home"), "score_away": t.get("score_away"),
    }


# (market_id, engine) -> (home_odds_col, away_odds_col, home_prob_col, away_prob_col)
_SIM_COLS = {
    ("1x2_1up_ft", "v3"): ("v3_1up_home_capped", "v3_1up_away_capped", "v3_p_home_1", "v3_p_away_1"),
    ("1x2_2up_ft", "v3"): ("v3_2up_home_capped", "v3_2up_away_capped", "v3_p_home_2", "v3_p_away_2"),
    ("1x2_1up_ft", "v4"): ("v4_1up_home_capped", "v4_1up_away_capped", "v4_p_home_1", "v4_p_away_1"),
    ("1x2_2up_ft", "v4"): ("v4_2up_home_capped", "v4_2up_away_capped", "v4_p_home_2", "v4_p_away_2"),
}
_SIM_MARKETS = ("1x2_1up_ft", "1x2_2up_ft")


def _sim_rows(conn, t, meta, markets, sim_engines) -> Iterator[dict]:
    """Stored V3/V4 OUR prices for the in-scope UP markets as LONG rows.
    LEFT-join semantics: if no pricer_live_results row exists for this tick,
    yields nothing (real rows are unaffected)."""
    if markets is not None:
        sel = {m for m, _ in markets}
        up_markets = [m for m in _SIM_MARKETS if m in sel]
    else:
        up_markets = list(_SIM_MARKETS)
    if not up_markets:
        return
    cur = conn.cursor(); cur.row_factory = sqlite3.Row
    row = cur.execute(
        "SELECT * FROM pricer_live_results WHERE event_id=? AND ts_utc=?",
        (t["event_id"], t["ts_utc"]),
    ).fetchone()
    if row is None:
        return
    for market_id in up_markets:
        for engine in sim_engines:
            cols = _SIM_COLS.get((market_id, engine))
            if not cols:
                continue
            oh, oa, ph, pa = cols
            for side, ocol, pcol in (("home", oh, ph), ("away", oa, pa)):
                odds = row[ocol]
                if odds is None:
                    continue
                yield {
                    **meta, "bookmaker": "OUR", "market_id": market_id,
                    "line": 0.0, "side": side,
                    "odds": odds, "probability": row[pcol],
                    "is_simulated": 1, "engine": engine,
                }


def iter_long_rows(
    conn, ticks, *, markets, books, sim_engines=(),
) -> Iterator[dict]:
    """Yield one LONG dict per (tick, bookmaker, market, line, side). Real
    scraped rows first; then stored sim rows (V3/V4) for 1UP/2UP if requested."""
    for t in ticks:
        meta = _meta(t)
        for p in load_tick_prices(conn, t["event_id"], t["ts_utc"], markets, books):
            yield {
                **meta,
                "bookmaker": p["bookmaker"], "market_id": p["market_id"],
                "line": p["line"], "side": p["side"],
                "odds": p["odds"], "probability": p["probability"],
                "is_simulated": 0, "engine": "",
            }
        if sim_engines:
            yield from _sim_rows(conn, t, meta, markets, sim_engines)


WIDE_META = (
    "event_id", "country_name", "league_name", "home", "away", "kickoff_utc",
    "snapshot_id", "ts_utc", "status", "match_minute", "score_home", "score_away",
)


def _wide_book(row: dict) -> str:
    return f"our_{row['engine']}" if row["is_simulated"] else row["bookmaker"]


def to_wide_rows(long_rows: Iterable[dict]) -> tuple[list[str], list[dict]]:
    """Pivot LONG rows to one row per (event, ts). Value columns are
    '{book}__{market}__{line}__{side}__{odds|prob}', sorted for a stable,
    frozen header. Returns (columns, rows)."""
    long_rows = list(long_rows)
    value_cols: set[str] = set()
    by_key: dict[tuple, dict] = {}
    for r in long_rows:
        key = (r["event_id"], r["ts_utc"])
        bucket = by_key.setdefault(key, {m: r.get(m) for m in WIDE_META})
        book = _wide_book(r)
        stem = f"{book}__{r['market_id']}__{r['line']}__{r['side']}"
        ocol, pcol = f"{stem}__odds", f"{stem}__prob"
        bucket[ocol] = r["odds"]; bucket[pcol] = r["probability"]
        value_cols.add(ocol); value_cols.add(pcol)
    columns = list(WIDE_META) + sorted(value_cols)
    rows = [by_key[k] for k in sorted(by_key)]
    return columns, rows


def available_markets(conn, scope: dict) -> list[tuple[str, float]]:
    """Distinct (market_id, line) pairs present for in-scope events. Includes
    the 0.0 sentinel line for the 1x2 family (it's a real selectable market)."""
    where = ["e.home != '' AND e.away != ''"]
    params: list = []
    if scope.get("country"):
        where.append("e.country_id = ?"); params.append(scope["country"])
    if scope.get("league"):
        where.append("e.league_id = ?"); params.append(scope["league"])
    if scope.get("event_id"):
        where.append("p.event_id = ?"); params.append(scope["event_id"])
    if scope.get("date"):
        where.append("DATE(e.kickoff_utc) = ?"); params.append(scope["date"])
    sql = (
        "SELECT DISTINCT p.market_id, p.line FROM prices p "
        "JOIN events e ON e.id = p.event_id WHERE " + " AND ".join(where) +
        " ORDER BY p.market_id, p.line"
    )
    return [(r[0], float(r[1])) for r in conn.execute(sql, params).fetchall()]
