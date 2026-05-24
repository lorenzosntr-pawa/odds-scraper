from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from . import engine, inputs as input_extract, configs as config_mod, csv_export

log = logging.getLogger(__name__)


@contextmanager
def with_coefficients(overrides: dict) -> Iterator[None]:
    """Temporarily set module-level constants on engine.py.

    Engine reads constants directly from its module globals. To honour a
    profile's coefficients we setattr the overrides before the call and
    restore originals on exit. Not thread-safe — the web app runs single-
    process asyncio and engine calls are sync within one event loop, so
    no two engine calls overlap. The card OUR column always runs under
    the seeded default (no override), so this contextmanager is only
    entered by the simulator runner.
    """
    saved = {k: getattr(engine, k) for k in overrides}
    try:
        for k, v in overrides.items():
            setattr(engine, k, v)
        yield
    finally:
        for k, v in saved.items():
            setattr(engine, k, v)


_BOOK_PREFIXES = {
    "betpawa":   "bp",
    "sportybet": "sb",
    "bet9ja":    "b9j",
    "betway":    "bw",
}


def _select_snapshot_ids(
    conn: sqlite3.Connection, coverage: str, scope: dict,
) -> list[int]:
    """Return distinct snapshot ids in scope, ordered by event_id then ts.

    coverage:
      'all'      — every snapshot for events that match scope
      'latest'   — only the most recent snapshot per event
      'prematch' — UPCOMING snapshots only
      'live'     — STARTED snapshots only
    """
    status = scope.get("status") or ""
    where_extra: list[str] = []
    params: list = []
    if coverage in ("prematch", "live"):
        where_extra.append("s.status = ?")
        params.append("UPCOMING" if coverage == "prematch" else "STARTED")
    if status == "live":
        where_extra.append("s.status = 'STARTED'")
    elif status == "upcoming":
        where_extra.append("s.status = 'UPCOMING'")
    elif status == "ended":
        where_extra.append("s.status = 'ENDED'")
    if scope.get("country"):
        where_extra.append("e.country_id = ?")
        params.append(scope["country"])
    if scope.get("league"):
        where_extra.append("e.league_id = ?")
        params.append(scope["league"])
    where_clause = " AND " + " AND ".join(where_extra) if where_extra else ""

    if coverage == "latest":
        sql = f"""
            WITH latest AS (
                SELECT event_id, MAX(ts_utc) AS max_ts
                FROM snapshots GROUP BY event_id
            )
            SELECT DISTINCT s.id, s.event_id, s.ts_utc
            FROM snapshots s
            JOIN events e ON e.id = s.event_id
            JOIN latest l ON l.event_id = s.event_id AND l.max_ts = s.ts_utc
            WHERE 1=1 {where_clause}
            ORDER BY s.event_id, s.ts_utc
        """
    else:
        sql = f"""
            SELECT DISTINCT s.id, s.event_id, s.ts_utc
            FROM snapshots s
            JOIN events e ON e.id = s.event_id
            WHERE 1=1 {where_clause}
            ORDER BY s.event_id, s.ts_utc
        """
    # Return (id, event_id, ts_utc) tuples — the caller needs all three.
    # The SQL already selects them; returning the full tuple avoids a
    # second `WHERE id IN (...)` query that for large runs (thousands
    # of snapshots) blows SQLite's variable-count limit.
    return [(row[0], row[1], row[2]) for row in conn.execute(sql, params).fetchall()]


def _load_tick_prices(
    conn: sqlite3.Connection, event_id: str, ts_utc: str,
) -> dict[str, list]:
    """Return {book: [price_row, ...]} for every (event, ts) bucket — i.e.
    every book's snapshot at the same timestamp. SQLite Row is dict-like."""
    rows = conn.execute(
        "SELECT bookmaker, market_id, line, side, odds, probability "
        "FROM prices WHERE event_id = ? AND ts_utc = ?",
        (event_id, ts_utc),
    ).fetchall()
    out: dict[str, list] = {}
    for r in rows:
        out.setdefault(r["bookmaker"], []).append({
            "market_id":   r["market_id"],
            "line":        r["line"],
            "side":        r["side"],
            "odds":        r["odds"],
            "probability": r["probability"],
        })
    return out


def _extract_quoted_up(prices: list) -> dict:
    """Return {1up_home, 1up_away, 2up_home, 2up_away} odds for one book's
    snapshot. Missing rows stay None."""
    out = {"1up_home": None, "1up_away": None, "2up_home": None, "2up_away": None}
    for r in prices:
        m = r["market_id"]
        if m == "1x2_1up_ft" and r["side"] in ("home", "away"):
            out[f"1up_{r['side']}"] = r["odds"]
        elif m == "1x2_2up_ft" and r["side"] in ("home", "away"):
            out[f"2up_{r['side']}"] = r["odds"]
    return out


def _score_for_snapshot(conn: sqlite3.Connection, snapshot_id: int) -> tuple[int, int]:
    row = conn.execute(
        "SELECT score_home, score_away FROM snapshots WHERE id = ?",
        (snapshot_id,),
    ).fetchone()
    if row is None or row["score_home"] is None or row["score_away"] is None:
        return (0, 0)
    return (int(row["score_home"]), int(row["score_away"]))


_PROGRESS_BATCH = 50  # update n_done after every N processed ticks


def is_run_in_progress(conn: sqlite3.Connection) -> bool:
    """True iff any pricer_runs row currently has state='running'.

    Used by the POST /simulator/runs route to refuse a second run
    while one is already in flight."""
    row = conn.execute(
        "SELECT 1 FROM pricer_runs WHERE state = 'running' LIMIT 1"
    ).fetchone()
    return row is not None


def run_simulation(
    conn: sqlite3.Connection,
    *,
    config: config_mod.Profile,
    coverage: str,
    scope: dict,
    csv_dir: Path,
) -> int:
    """Execute a simulation run, persist rows + CSV, return new run id.

    The pricer_runs row is inserted IMMEDIATELY with state='running'
    so the simulator page's progress poller can see the run start.
    `n_done` updates every PROGRESS_BATCH ticks; on success the row
    flips to state='done' with the final n_rows, csv_path, and
    finished_at; on exception it flips to state='failed' (the
    exception propagates to the caller so background-task plumbing
    can log it).

    `coverage` in {'all', 'latest', 'prematch', 'live'}.
    `scope` carries the filter selections so a run can be reproduced.
    """
    snapshots = _select_snapshot_ids(conn, coverage, scope)
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cur = conn.execute(
        "INSERT INTO pricer_runs (created_at, config_id, coverage, scope_json, "
        "n_events, n_rows, csv_path, state, n_total, n_done, started_at) "
        "VALUES (?, ?, ?, ?, 0, 0, '', 'running', ?, 0, ?)",
        (now_iso, config.id, coverage, json.dumps(scope),
         len(snapshots), now_iso),
    )
    run_id = cur.lastrowid

    if not snapshots:
        return _finish_empty_run(conn, run_id, csv_dir)

    try:
        return _execute_run(conn, run_id, config, snapshots, csv_dir)
    except Exception:
        conn.execute(
            "UPDATE pricer_runs SET state = 'failed', finished_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), run_id),
        )
        raise


def _execute_run(
    conn: sqlite3.Connection,
    run_id: int,
    config: config_mod.Profile,
    snapshots: list[tuple[int, str, str]],
    csv_dir: Path,
) -> int:
    overrides = config_mod.coefficients_to_engine_overrides(config.coefficients)
    snap_meta: dict[int, tuple[str, str]] = {
        snap_id: (ev, ts) for (snap_id, ev, ts) in snapshots
    }
    snapshot_ids = [snap_id for (snap_id, _ev, _ts) in snapshots]

    results: list[tuple] = []
    seen_events: set[str] = set()

    with with_coefficients(overrides):
        for i, snap_id in enumerate(snapshot_ids):
            event_id, ts_utc = snap_meta[snap_id]
            prices_by_book = _load_tick_prices(conn, event_id, ts_utc)
            engine_inputs, basis = input_extract.extract(prices_by_book)
            if engine_inputs is not None:
                engine_inputs["score"] = _score_for_snapshot(conn, snap_id)
                try:
                    res = engine.price_early_payout_markets(**engine_inputs)
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "engine crashed on event=%s ts=%s — skipping (%s)",
                        event_id, ts_utc, exc,
                    )
                else:
                    quoted = {
                        book: _extract_quoted_up(prices_by_book.get(book, []))
                        for book in ("betpawa", "sportybet", "bet9ja", "betway")
                    }
                    results.append((
                        snap_id, event_id, ts_utc, basis,
                        res["lambda_home"], res["lambda_away"],
                        res["p_home_1"], res["p_away_1"],
                        res["market_1up"]["home_fair"],   res["market_1up"]["home_margin"],
                        res["market_1up"]["away_fair"],   res["market_1up"]["away_margin"],
                        res["p_home_2"], res["p_away_2"],
                        res["market_2up"]["home_fair"],   res["market_2up"]["home_margin"],
                        res["market_2up"]["away_fair"],   res["market_2up"]["away_margin"],
                        quoted["betpawa"]["1up_home"],   quoted["betpawa"]["1up_away"],
                        quoted["betpawa"]["2up_home"],   quoted["betpawa"]["2up_away"],
                        quoted["sportybet"]["1up_home"], quoted["sportybet"]["1up_away"],
                        quoted["sportybet"]["2up_home"], quoted["sportybet"]["2up_away"],
                        quoted["bet9ja"]["1up_home"],    quoted["bet9ja"]["1up_away"],
                        quoted["bet9ja"]["2up_home"],    quoted["bet9ja"]["2up_away"],
                        quoted["betway"]["1up_home"],    quoted["betway"]["1up_away"],
                        quoted["betway"]["2up_home"],    quoted["betway"]["2up_away"],
                    ))
                    seen_events.add(event_id)
            # Heartbeat: write n_done every PROGRESS_BATCH so the
            # status endpoint shows live progress.
            if (i + 1) % _PROGRESS_BATCH == 0:
                conn.execute(
                    "UPDATE pricer_runs SET n_done = ? WHERE id = ?",
                    (i + 1, run_id),
                )

    csv_path = f"sim/run_{run_id:04d}.csv"

    # 35 columns total: run_id + 34 from each `results` tuple. Build the
    # placeholder string once so the column count is unambiguous.
    _RESULT_COLS = (
        "run_id, snapshot_id, event_id, ts_utc, basis_used, "
        "lambda_home, lambda_away, "
        "our_p_home_1, our_p_away_1, "
        "our_1up_home_fair, our_1up_home_capped, our_1up_away_fair, our_1up_away_capped, "
        "our_p_home_2, our_p_away_2, "
        "our_2up_home_fair, our_2up_home_capped, our_2up_away_fair, our_2up_away_capped, "
        "bp_1up_home_odds, bp_1up_away_odds, bp_2up_home_odds, bp_2up_away_odds, "
        "sb_1up_home_odds, sb_1up_away_odds, sb_2up_home_odds, sb_2up_away_odds, "
        "b9j_1up_home_odds, b9j_1up_away_odds, b9j_2up_home_odds, b9j_2up_away_odds, "
        "bw_1up_home_odds, bw_1up_away_odds, bw_2up_home_odds, bw_2up_away_odds"
    )
    placeholders = ",".join("?" * 35)
    conn.executemany(
        f"INSERT INTO pricer_results ({_RESULT_COLS}) VALUES ({placeholders})",
        [(run_id, *row) for row in results],
    )
    finished_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        "UPDATE pricer_runs "
        "SET csv_path = ?, state = 'done', n_events = ?, n_rows = ?, "
        "    n_done = n_total, finished_at = ? "
        "WHERE id = ?",
        (csv_path, len(seen_events), len(results), finished_iso, run_id),
    )
    csv_export.write_run_csv(conn, run_id, csv_dir / f"run_{run_id:04d}.csv")
    return run_id


def _finish_empty_run(
    conn: sqlite3.Connection, run_id: int, csv_dir: Path,
) -> int:
    """Mark a pre-inserted run row as done when the scope had 0 snapshots."""
    csv_path = f"sim/run_{run_id:04d}.csv"
    finished_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        "UPDATE pricer_runs "
        "SET csv_path = ?, state = 'done', finished_at = ? "
        "WHERE id = ?",
        (csv_path, finished_iso, run_id),
    )
    csv_export.write_run_csv(conn, run_id, csv_dir / f"run_{run_id:04d}.csv")
    return run_id
