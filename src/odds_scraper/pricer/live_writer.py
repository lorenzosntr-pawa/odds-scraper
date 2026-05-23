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
from . import engine, inputs as input_extract

log = logging.getLogger(__name__)


def _snapshots_to_prices_by_book(rows: Iterable[Snapshot]) -> dict[str, list[dict]]:
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
    rows: list[Snapshot],
    score: tuple[int, int] = (0, 0),
) -> bool:
    """Run the engine on this tick's prices and persist the result.

    Returns True on a successful write, False if the engine couldn't
    produce a result (insufficient inputs / bad data). Idempotent on
    repeat ticks at the same (event_id, ts_utc) via INSERT OR REPLACE.
    Never raises — engine crashes log a warning and return False, so a
    bad tick can't break the watcher's main loop.
    """
    prices_by_book = _snapshots_to_prices_by_book(rows)
    engine_inputs, basis = input_extract.extract(prices_by_book)
    if engine_inputs is None:
        return False
    engine_inputs["score"] = (int(score[0]), int(score[1]))
    try:
        res = engine.price_early_payout_markets(**engine_inputs)
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "engine crashed on event=%s ts=%s — skipping (%s)",
            event_id, ts_utc, exc,
        )
        return False

    conn.execute(
        """
        INSERT OR REPLACE INTO pricer_live_results (
            event_id, ts_utc, basis_used,
            lambda_home, lambda_away,
            our_p_home_1, our_p_away_1,
            our_1up_home_fair, our_1up_home_capped,
            our_1up_away_fair, our_1up_away_capped,
            our_p_home_2, our_p_away_2,
            our_2up_home_fair, our_2up_home_capped,
            our_2up_away_fair, our_2up_away_capped
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id, ts_utc, basis,
            res["lambda_home"], res["lambda_away"],
            res["p_home_1"], res["p_away_1"],
            res["market_1up"]["home_fair"],   res["market_1up"]["home_margin"],
            res["market_1up"]["away_fair"],   res["market_1up"]["away_margin"],
            res["p_home_2"], res["p_away_2"],
            res["market_2up"]["home_fair"],   res["market_2up"]["home_margin"],
            res["market_2up"]["away_fair"],   res["market_2up"]["away_margin"],
        ),
    )
    return True
