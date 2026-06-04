"""Compute + persist OUR engine output per scraper tick.

Writes one `pricer_live_results` row per (event_id, ts_utc) so the
detail-page history can render OUR alongside the bookmaker columns
without re-running the engine on demand. The home-page card SIM
column still computes live on each render — it's cheap and avoids a
JOIN — but the historical record on the detail page comes from here.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Iterable

from ..models import Snapshot
from . import engine_v3, engine_v4, inputs as input_extract, score_state

log = logging.getLogger(__name__)


def snapshots_to_prices_by_book(rows: Iterable[Snapshot]) -> dict[str, list[dict]]:
    """Reshape a tick's per-bookmaker Snapshot list into the row-of-dicts
    form `pricer.inputs.extract` expects. Mirrors the column layout the
    web app and simulator use, so the same engine input contract holds."""
    out: dict[str, list[dict]] = {}
    for snap in rows:
        bucket = out.setdefault(snap.bookmaker.value, [])
        for key, (odds, prob) in snap.prices.items():
            bucket.append({
                "market_id":   key.market_id,
                "line":        key.line if key.line is not None else 0.0,
                "side":        key.side,
                "odds":        odds,
                "probability": prob,
            })
    return out


def compute_and_write(
    conn: sqlite3.Connection,
    event_id: str,
    ts_utc: str,
    prices_by_book: dict[str, list[dict]],
    score: tuple[int, int] = (0, 0),
    max_leads: tuple[int, int] | None = None,
) -> bool:
    """Run the engine on this tick's prices and persist the result.

    Returns True on a successful write, False if the engine couldn't
    produce a result (insufficient inputs / bad data). Idempotent on
    repeat ticks at the same (event_id, ts_utc) via INSERT OR REPLACE.
    Never raises — engine crashes log a warning and return False, so a
    bad tick can't break the watcher's main loop.

    `max_leads` is the (max_home_lead, max_away_lead) for this event up
    to and including this tick. Omit to have the function query the DB
    itself (correct for the hot path, where the snapshot is already
    written). Pass explicitly when the caller is iterating many ticks
    in bulk and wants to avoid a per-tick query (see `backfill_all`).
    """
    engine_inputs, basis = input_extract.extract(prices_by_book)
    if engine_inputs is None:
        return False
    engine_inputs["score"] = (int(score[0]), int(score[1]))
    if max_leads is None:
        max_leads = score_state.max_leads_so_far(conn, event_id)
    engine_inputs["max_home_lead"] = max_leads[0]
    engine_inputs["max_away_lead"] = max_leads[1]
    # Private metadata keys (e.g. _cap_source_home) live on the inputs dict
    # for the CSV layer's benefit but aren't engine kwargs — strip before call.
    engine_kwargs = {k: v for k, v in engine_inputs.items() if not k.startswith("_")}
    # V3 is the must-succeed primary: it supplies the shared basis/lambda and
    # the v3_* block. A V3 crash drops the tick (returns False) — same policy
    # V2 had before it was retired.
    try:
        res_v3 = engine_v3.price_early_payout_markets(**engine_kwargs)
    except Exception as exc:  # noqa: BLE001
        log.warning("v3 engine crashed on event=%s ts=%s — skipping (%s)",
                    event_id, ts_utc, exc)
        return False

    # V4 is best-effort: a crash stores NULL v4 and never drops the tick.
    try:
        res_v4 = engine_v4.price_early_payout_markets(**engine_kwargs)
    except Exception as exc:  # noqa: BLE001
        log.warning("v4 engine crashed on event=%s ts=%s — storing NULL v4 (%s)",
                    event_id, ts_utc, exc)
        res_v4 = None

    def _v4(market, key):
        return res_v4[market][key] if res_v4 is not None else None

    def _v4p(key):
        return res_v4[key] if res_v4 is not None else None

    conn.execute(
        """
        INSERT OR REPLACE INTO pricer_live_results (
            event_id, ts_utc, basis_used,
            lambda_home, lambda_away,
            v3_p_home_1, v3_p_away_1,
            v3_1up_home_fair, v3_1up_home_capped,
            v3_1up_away_fair, v3_1up_away_capped,
            v3_p_home_2, v3_p_away_2,
            v3_2up_home_fair, v3_2up_home_capped,
            v3_2up_away_fair, v3_2up_away_capped,
            v4_p_home_1, v4_p_away_1,
            v4_1up_home_fair, v4_1up_home_capped,
            v4_1up_away_fair, v4_1up_away_capped,
            v4_p_home_2, v4_p_away_2,
            v4_2up_home_fair, v4_2up_home_capped,
            v4_2up_away_fair, v4_2up_away_capped
        ) VALUES (?, ?, ?, ?, ?,
                  ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                  ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id, ts_utc, basis,
            res_v3["lambda_home"], res_v3["lambda_away"],
            res_v3["p_home_1"], res_v3["p_away_1"],
            res_v3["market_1up"]["home_fair"],   res_v3["market_1up"]["home_margin"],
            res_v3["market_1up"]["away_fair"],   res_v3["market_1up"]["away_margin"],
            res_v3["p_home_2"], res_v3["p_away_2"],
            res_v3["market_2up"]["home_fair"],   res_v3["market_2up"]["home_margin"],
            res_v3["market_2up"]["away_fair"],   res_v3["market_2up"]["away_margin"],
            _v4p("p_home_1"), _v4p("p_away_1"),
            _v4("market_1up", "home_fair"),   _v4("market_1up", "home_margin"),
            _v4("market_1up", "away_fair"),   _v4("market_1up", "away_margin"),
            _v4p("p_home_2"), _v4p("p_away_2"),
            _v4("market_2up", "home_fair"),   _v4("market_2up", "home_margin"),
            _v4("market_2up", "away_fair"),   _v4("market_2up", "away_margin"),
        ),
    )
    return True


def compute_and_write_from_snapshots(
    conn: sqlite3.Connection,
    event_id: str,
    ts_utc: str,
    rows: list[Snapshot],
    score: tuple[int, int] = (0, 0),
) -> bool:
    """Convenience wrapper used by the watcher's hot path: takes the
    per-bookmaker Snapshot list directly off the collector output and
    reshapes it for `compute_and_write`."""
    return compute_and_write(
        conn, event_id, ts_utc,
        snapshots_to_prices_by_book(rows), score,
    )


def backfill_all(conn: sqlite3.Connection) -> tuple[int, int]:
    """One-shot: populate `pricer_live_results` for every existing
    (event_id, ts_utc) tick in `snapshots` that doesn't already have a
    row.

    Returns (written, skipped). Skipped covers ticks where the engine
    couldn't produce output (missing 1X2 / OU / FTTS — typical for
    very early prematch snapshots before BetPawa publishes the markets).

    Score per tick comes from MAX(score_home/away) across that tick's
    per-bookmaker snapshots — all four are sourced from BetPawa's
    detail dict, so the values agree, but MAX collapses any rare
    inconsistency to one deterministic number.
    """
    ticks = conn.execute(
        """
        SELECT s.event_id, s.ts_utc,
               MAX(s.score_home) AS sh,
               MAX(s.score_away) AS sa
        FROM snapshots s
        LEFT JOIN pricer_live_results r
          ON r.event_id = s.event_id AND r.ts_utc = s.ts_utc
        WHERE r.event_id IS NULL
        GROUP BY s.event_id, s.ts_utc
        ORDER BY s.event_id, s.ts_utc
        """
    ).fetchall()

    # Bulk lead lookup so the inner loop avoids a per-tick query.
    leads_by_tick = score_state.max_leads_for_events(
        conn, {t[0] for t in ticks},
    )

    written = 0
    skipped = 0
    for ev_id, ts, sh, sa in ticks:
        price_rows = conn.execute(
            "SELECT bookmaker, market_id, line, side, odds, probability "
            "FROM prices WHERE event_id = ? AND ts_utc = ?",
            (ev_id, ts),
        ).fetchall()
        prices_by_book: dict[str, list[dict]] = {}
        for bm, mid, line, side, odds, prob in price_rows:
            prices_by_book.setdefault(bm, []).append({
                "market_id":   mid,
                "line":        line if line is not None else 0.0,
                "side":        side,
                "odds":        odds,
                "probability": prob,
            })
        score = (int(sh), int(sa)) if sh is not None and sa is not None else (0, 0)
        leads = leads_by_tick.get((ev_id, ts), (0, 0))
        if compute_and_write(
            conn, ev_id, ts, prices_by_book, score, max_leads=leads,
        ):
            written += 1
        else:
            skipped += 1
    return written, skipped


def backfill_v3(conn: sqlite3.Connection) -> tuple[int, int]:
    """Fill v3_* on existing pricer_live_results rows that don't have V3 yet.

    A row has V3 iff any v3_* column is non-NULL — a fully score-deactivated
    row can't happen (both sides can't deactivate at once), so "all v3_* NULL"
    reliably means "not computed". Re-extracts engine inputs from `prices`
    exactly as backfill_all does, runs engine_v3, and UPDATEs ONLY the v3_*
    columns. V2 values are left untouched. Idempotent.

    Returns (updated, skipped); skipped = rows whose inputs can't price.
    """
    targets = conn.execute(
        """
        SELECT r.event_id, r.ts_utc,
               MAX(s.score_home) AS sh, MAX(s.score_away) AS sa
        FROM pricer_live_results r
        JOIN snapshots s ON s.event_id = r.event_id AND s.ts_utc = r.ts_utc
        WHERE r.v3_p_home_1 IS NULL AND r.v3_p_away_1 IS NULL
          AND r.v3_1up_home_fair IS NULL AND r.v3_1up_home_capped IS NULL
          AND r.v3_1up_away_fair IS NULL AND r.v3_1up_away_capped IS NULL
          AND r.v3_p_home_2 IS NULL AND r.v3_p_away_2 IS NULL
          AND r.v3_2up_home_fair IS NULL AND r.v3_2up_home_capped IS NULL
          AND r.v3_2up_away_fair IS NULL AND r.v3_2up_away_capped IS NULL
        GROUP BY r.event_id, r.ts_utc
        ORDER BY r.event_id, r.ts_utc
        """
    ).fetchall()
    leads_by_tick = score_state.max_leads_for_events(conn, {t[0] for t in targets})

    updated = 0
    skipped = 0
    for ev_id, ts, sh, sa in targets:
        price_rows = conn.execute(
            "SELECT bookmaker, market_id, line, side, odds, probability "
            "FROM prices WHERE event_id = ? AND ts_utc = ?",
            (ev_id, ts),
        ).fetchall()
        prices_by_book: dict[str, list[dict]] = {}
        for bm, mid, line, side, odds, prob in price_rows:
            prices_by_book.setdefault(bm, []).append({
                "market_id":   mid,
                "line":        line if line is not None else 0.0,
                "side":        side,
                "odds":        odds,
                "probability": prob,
            })
        engine_inputs, _basis = input_extract.extract(prices_by_book)
        if engine_inputs is None:
            skipped += 1
            continue
        score = (int(sh), int(sa)) if sh is not None and sa is not None else (0, 0)
        engine_inputs["score"] = score
        leads = leads_by_tick.get((ev_id, ts), (0, 0))
        engine_inputs["max_home_lead"] = leads[0]
        engine_inputs["max_away_lead"] = leads[1]
        kw = {k: v for k, v in engine_inputs.items() if not k.startswith("_")}
        try:
            r3 = engine_v3.price_early_payout_markets(**kw)
        except Exception as exc:  # noqa: BLE001
            log.warning("v3 backfill crashed event=%s ts=%s (%s)", ev_id, ts, exc)
            skipped += 1
            continue
        conn.execute(
            """
            UPDATE pricer_live_results SET
                v3_p_home_1=?, v3_p_away_1=?,
                v3_1up_home_fair=?, v3_1up_home_capped=?,
                v3_1up_away_fair=?, v3_1up_away_capped=?,
                v3_p_home_2=?, v3_p_away_2=?,
                v3_2up_home_fair=?, v3_2up_home_capped=?,
                v3_2up_away_fair=?, v3_2up_away_capped=?
            WHERE event_id=? AND ts_utc=?
            """,
            (
                r3["p_home_1"], r3["p_away_1"],
                r3["market_1up"]["home_fair"], r3["market_1up"]["home_margin"],
                r3["market_1up"]["away_fair"], r3["market_1up"]["away_margin"],
                r3["p_home_2"], r3["p_away_2"],
                r3["market_2up"]["home_fair"], r3["market_2up"]["home_margin"],
                r3["market_2up"]["away_fair"], r3["market_2up"]["away_margin"],
                ev_id, ts,
            ),
        )
        updated += 1
    return updated, skipped
