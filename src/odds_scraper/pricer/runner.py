from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Optional

from . import (
    engine, inputs as input_extract, configs as config_mod, csv_export,
    score_state,
)

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


VALID_REGIMES = ("any", "prematch", "live")
VALID_DENSITIES = ("all", "latest")

_PROGRESS_BATCH = 50  # call on_progress(...) every N processed ticks

ProgressCallback = Callable[[int, int], None]


def _select_ticks(
    conn: sqlite3.Connection,
    regime: str,
    density: str,
    scope: dict,
) -> list[dict]:
    """One row per (event_id, ts_utc) tick. Returns a list of dicts with
    everything the runner + CSV writer need so no follow-up queries
    are required mid-loop.

    The dedup is critical: each tick has up to 4 rows in `snapshots`
    (one per bookmaker), and the engine only needs to run once per
    tick. The previous query returned every row separately and the
    runner duplicated work × 4.

    `regime`/`density` semantics:
      regime  in {'any','prematch','live'}  — snapshot-level status filter.
      density in {'all','latest'}            — all matching ticks or only
                                                the last per event.
    """
    where_extra: list[str] = []
    params: list = []
    if regime == "prematch":
        where_extra.append("s.status = 'UPCOMING'")
    elif regime == "live":
        where_extra.append("s.status = 'STARTED'")
    if scope.get("country"):
        where_extra.append("e.country_id = ?")
        params.append(scope["country"])
    if scope.get("league"):
        where_extra.append("e.league_id = ?")
        params.append(scope["league"])
    if scope.get("event_id"):
        # Single-event drill-down — used by the page's event picker so a
        # user can simulate one specific match under different profiles.
        where_extra.append("s.event_id = ?")
        params.append(scope["event_id"])
    if scope.get("date"):
        where_extra.append("DATE(e.kickoff_utc) = ?")
        params.append(scope["date"])
    if scope.get("search"):
        where_extra.append(
            "(LOWER(e.home) LIKE ? ESCAPE '\\' OR LOWER(e.away) LIKE ? ESCAPE '\\')"
        )
        escaped = scope["search"].lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"
        params.extend([like, like])
    where_clause = " AND " + " AND ".join(where_extra) if where_extra else ""

    base_select = """
        SELECT MIN(s.id)           AS snapshot_id,
               s.event_id,
               s.ts_utc,
               MAX(s.status)       AS status,
               MAX(s.match_minute) AS match_minute,
               MAX(s.score_home)   AS score_home,
               MAX(s.score_away)   AS score_away,
               MAX(e.home)         AS home,
               MAX(e.away)         AS away,
               MAX(e.kickoff_utc)  AS kickoff_utc
    """

    if density == "latest":
        # Regime-aware latest: pick the MAX(ts_utc) per event AMONG
        # snapshots matching the regime — not the global head which
        # might be ENDED while the user asked for prematch.
        regime_filter = ""
        if regime == "prematch":
            regime_filter = "WHERE status = 'UPCOMING'"
        elif regime == "live":
            regime_filter = "WHERE status = 'STARTED'"
        sql = f"""
            WITH latest AS (
                SELECT event_id, MAX(ts_utc) AS max_ts
                FROM snapshots
                {regime_filter}
                GROUP BY event_id
            )
            {base_select}
            FROM snapshots s
            JOIN events e ON e.id = s.event_id
            JOIN latest l ON l.event_id = s.event_id AND l.max_ts = s.ts_utc
            WHERE 1=1 {where_clause}
            GROUP BY s.event_id, s.ts_utc
            ORDER BY s.event_id, s.ts_utc
        """
    else:
        sql = f"""
            {base_select}
            FROM snapshots s
            JOIN events e ON e.id = s.event_id
            WHERE 1=1 {where_clause}
            GROUP BY s.event_id, s.ts_utc
            ORDER BY s.event_id, s.ts_utc
        """
    cur = conn.cursor()
    cur.row_factory = sqlite3.Row
    return [dict(row) for row in cur.execute(sql, params).fetchall()]


def _load_tick_prices(
    conn: sqlite3.Connection, event_id: str, ts_utc: str,
) -> dict[str, list]:
    """Return {book: [price_row, ...]} for the (event, ts) tick across
    every bookmaker. One query per tick."""
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


def _ev(our_prob, book_odds):
    """Expected value per unit stake: `p_true * odds - 1`. Returns None
    when either factor is missing — settled / unquoted sides surface as
    a blank CSV cell rather than a misleading zero or `-1`."""
    if our_prob is None or book_odds is None:
        return None
    return our_prob * book_odds - 1.0


def _book_1x2_odds(prices: list) -> tuple:
    """`(home_odds, draw_odds, away_odds)` for one book's tick. Missing or
    suspended sides become empty cells in the CSV so the reader can see
    when a book closed that selection (BP returns odds=0 for suspended)."""
    out = {"home": "", "draw": "", "away": ""}
    for r in prices:
        if r.get("market_id") == "1x2_ft" and r.get("side") in out:
            o = r["odds"]
            out[r["side"]] = o if o not in (None, 0, 0.0) else ""
    return out["home"], out["draw"], out["away"]


def _extract_quoted_up(prices: list) -> dict:
    """`{1up_home, 1up_away, 2up_home, 2up_away}` → `(odds, prob)` for
    one book's tick. Probability is the bookmaker's own devigged true
    prob (column `prices.probability`); BP and SB carry it, B9J and BW
    don't (those callers ignore the prob half of the tuple)."""
    out: dict[str, tuple] = {
        "1up_home": (None, None), "1up_away": (None, None),
        "2up_home": (None, None), "2up_away": (None, None),
    }
    for r in prices:
        m = r["market_id"]
        if m == "1x2_1up_ft" and r["side"] in ("home", "away"):
            out[f"1up_{r['side']}"] = (r["odds"], r.get("probability"))
        elif m == "1x2_2up_ft" and r["side"] in ("home", "away"):
            out[f"2up_{r['side']}"] = (r["odds"], r.get("probability"))
    return out


def count_scope(
    conn: sqlite3.Connection, regime: str, density: str, scope: dict,
) -> tuple[int, int]:
    """Preview (n_events, n_ticks) for the scope. Used by the simulator
    page's HTMX scope-count badge."""
    ticks = _select_ticks(conn, regime, density, scope)
    return len({t["event_id"] for t in ticks}), len(ticks)


def run_simulation(
    conn: sqlite3.Connection,
    *,
    config: config_mod.Profile,
    regime: str = "any",
    density: str = "all",
    scope: dict,
    csv_path: Path,
    on_progress: Optional[ProgressCallback] = None,
) -> tuple[int, int]:
    """Run the engine across the scope and write the CSV.

    Returns (n_events, n_rows). Writes nothing to the database — the
    per-tick OUR record lives in pricer_live_results (written by the
    scraper), and the page-driven sim is a what-if tool whose output
    is the CSV. The web layer keeps progress / history in-memory via
    `on_progress(n_done, n_total)` callbacks (fired every PROGRESS_BATCH
    ticks plus once at the start and end).

    `regime` in {'any','prematch','live'}; `density` in {'all','latest'};
    `scope` carries country/league/date/search; unrecognised filters
    are ignored. Engine crashes on individual ticks are logged and
    skipped (the simulator should not abort a 17k-tick run because
    one BP snapshot happened to have a suspended outcome).
    """
    if regime not in VALID_REGIMES:
        raise ValueError(f"unknown regime {regime!r}")
    if density not in VALID_DENSITIES:
        raise ValueError(f"unknown density {density!r}")

    ticks = _select_ticks(conn, regime, density, scope)
    n_total = len(ticks)
    if on_progress is not None:
        on_progress(0, n_total)

    # Resolve max-lead history once for the full scope. The regime filter
    # may exclude prior ticks (e.g. 'live' skips the 0-0 prematch tick
    # where home_1up has already settled), so leads are computed across
    # the full snapshot timeline of each in-scope event — not just the
    # regime-filtered ticks.
    leads_by_tick = score_state.max_leads_for_events(
        conn, {t["event_id"] for t in ticks},
    )

    overrides = config_mod.coefficients_to_engine_overrides(config.coefficients)
    # V2-only (ONEUP_TRAILING_*_MARGIN) and V3-only (ONEUP/TWOUP_MARGIN_
    # LEVEL/TILT) tunables live on the other engine modules; this engine
    # doesn't define them and with_coefficients does getattr(engine, k),
    # which would crash. Strip both before applying.
    _skip = config_mod.V2_ONLY_TUNABLE_NAMES | config_mod.V3_ONLY_TUNABLE_NAMES
    overrides = {
        k: v for k, v in overrides.items()
        if k not in _skip
    }
    rows: list[tuple] = []
    seen_events: set[str] = set()

    with with_coefficients(overrides):
        for i, t in enumerate(ticks):
            event_id = t["event_id"]
            ts_utc = t["ts_utc"]
            prices_by_book = _load_tick_prices(conn, event_id, ts_utc)
            engine_inputs, basis = input_extract.extract(prices_by_book)
            if engine_inputs is not None:
                sh, sa = t["score_home"], t["score_away"]
                engine_inputs["score"] = (
                    int(sh) if sh is not None else 0,
                    int(sa) if sa is not None else 0,
                )
                mh, ma = leads_by_tick.get((event_id, ts_utc), (0, 0))
                engine_inputs["max_home_lead"] = mh
                engine_inputs["max_away_lead"] = ma
                # Strip private metadata (e.g. _cap_source_home) — those
                # live on the inputs dict for the CSV layer, not the engine.
                engine_kwargs = {
                    k: v for k, v in engine_inputs.items() if not k.startswith("_")
                }
                try:
                    res = engine.price_early_payout_markets(**engine_kwargs)
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
                    bp, sb = quoted["betpawa"], quoted["sportybet"]
                    b9j, bw = quoted["bet9ja"], quoted["betway"]
                    bp_1x2_h, bp_1x2_d, bp_1x2_a = _book_1x2_odds(prices_by_book.get("betpawa", []))
                    sb_1x2_h, sb_1x2_d, sb_1x2_a = _book_1x2_odds(prices_by_book.get("sportybet", []))
                    cap_src_home = engine_inputs.get("_cap_source_home", "")
                    cap_src_away = engine_inputs.get("_cap_source_away", "")
                    p_h1, p_a1 = res["p_home_1"], res["p_away_1"]
                    p_h2, p_a2 = res["p_home_2"], res["p_away_2"]
                    cap_1h = res["market_1up"]["home_margin"]
                    cap_1a = res["market_1up"]["away_margin"]
                    cap_2h = res["market_2up"]["home_margin"]
                    cap_2a = res["market_2up"]["away_margin"]
                    rows.append((
                        # V1-only runner — engines marker, profile A
                        # from the active config, no profile B, V2 block
                        # and pB_* blocks all blank.
                        "v1",
                        config.name, "",
                        t["snapshot_id"], event_id,
                        t["home"], t["away"], t["kickoff_utc"],
                        ts_utc,
                        t["status"], t["match_minute"],
                        t["score_home"], t["score_away"],
                        basis,
                        res["lambda_home"], res["lambda_away"],
                        # 1x2 + next-goal reference: devigged 1x2 probs,
                        # next-goal probs, and the resolved per-side cap
                        # source odds the engine actually used.
                        engine_inputs["p_home_win"], engine_inputs["p_draw"],
                        engine_inputs["p_away_win"],
                        engine_inputs.get("ftts_home_prob"),
                        engine_inputs.get("ftts_away_prob"),
                        engine_inputs["home_1x2_odds"], engine_inputs["away_1x2_odds"],
                        # OUR 1UP / 2UP block — simulated capped EV
                        # paired with each capped odds so the reader
                        # sees the engine's embedded margin per side.
                        p_h1, p_a1,
                        res["market_1up"]["home_fair"], cap_1h, _ev(p_h1, cap_1h),
                        res["market_1up"]["away_fair"], cap_1a, _ev(p_a1, cap_1a),
                        p_h2, p_a2,
                        res["market_2up"]["home_fair"], cap_2h, _ev(p_h2, cap_2h),
                        res["market_2up"]["away_fair"], cap_2a, _ev(p_a2, cap_2a),
                        # V2 + V3 blocks — 16 blanks each in V1-only runs.
                        *(("",) * 16),
                        *(("",) * 16),
                        # BP — prob, odds, EV per selection.
                        bp["1up_home"][1], bp["1up_home"][0], _ev(p_h1, bp["1up_home"][0]),
                        bp["1up_away"][1], bp["1up_away"][0], _ev(p_a1, bp["1up_away"][0]),
                        bp["2up_home"][1], bp["2up_home"][0], _ev(p_h2, bp["2up_home"][0]),
                        bp["2up_away"][1], bp["2up_away"][0], _ev(p_a2, bp["2up_away"][0]),
                        # SB — same shape.
                        sb["1up_home"][1], sb["1up_home"][0], _ev(p_h1, sb["1up_home"][0]),
                        sb["1up_away"][1], sb["1up_away"][0], _ev(p_a1, sb["1up_away"][0]),
                        sb["2up_home"][1], sb["2up_home"][0], _ev(p_h2, sb["2up_home"][0]),
                        sb["2up_away"][1], sb["2up_away"][0], _ev(p_a2, sb["2up_away"][0]),
                        # B9J / BW — odds only (no devigged prob stored).
                        b9j["1up_home"][0], b9j["1up_away"][0],
                        b9j["2up_home"][0], b9j["2up_away"][0],
                        bw["1up_home"][0],  bw["1up_away"][0],
                        bw["2up_home"][0],  bw["2up_away"][0],
                        # 1x2 odds + which book the engine used per side
                        # for the cap source.
                        bp_1x2_h, bp_1x2_d, bp_1x2_a,
                        sb_1x2_h, sb_1x2_d, sb_1x2_a,
                        cap_src_home, cap_src_away,
                        # Profile B blocks — V1 + V2 + V3 OUR (16+16+16) +
                        # BP/SB EV (4+4) = 56 blanks in single-profile runs.
                        *((""),) * 56,
                    ))
                    seen_events.add(event_id)
            if on_progress is not None and (i + 1) % _PROGRESS_BATCH == 0:
                on_progress(i + 1, n_total)

    csv_export.write_csv(csv_path, rows)
    if on_progress is not None:
        on_progress(n_total, n_total)
    return len(seen_events), len(rows)
