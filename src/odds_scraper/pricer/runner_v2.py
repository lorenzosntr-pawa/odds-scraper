"""Dual-engine simulator runner.

Calls one or both of `engine.price_early_payout_markets` and
`engine_v2.price_early_payout_markets` per tick and writes a single
CSV row with v1_* and v2_* columns side-by-side. Coefficient
overrides are applied independently to each engine module — the V1
engine sees its own `with_coefficients`, the V2 engine sees a sibling
`with_v2_coefficients` so the two modules never cross-contaminate
each other's constants.
"""
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Optional, Sequence

from . import (
    engine, engine_v2, engine_v3, engine_v4, inputs as input_extract, configs as config_mod,
    csv_export, score_state,
)
from .runner import (
    VALID_REGIMES, VALID_DENSITIES, _PROGRESS_BATCH, _ev,
    _select_ticks, _load_tick_prices, _extract_quoted_up,
    _book_1x2_odds, prices_fingerprint,
    _our_block_dict, _book_block_dict, _book_odds_only_dict, _pb_book_ev_dict,
    with_coefficients as with_v1_coefficients,
)

log = logging.getLogger(__name__)


VALID_ENGINES = ("v1", "v2", "v3", "v4")


@contextmanager
def with_v2_coefficients(overrides: dict) -> Iterator[None]:
    """Mirror of `runner.with_coefficients` but targeting engine_v2.
    Necessary because the override mechanism setattrs on a module — if
    we used V1's with_coefficients on engine_v2, the wrong module would
    be touched.

    Tolerates V1-only override keys (e.g. ONEUP_TRAILING_MIN_REDUCTION) by
    skipping any name engine_v2 doesn't define — V2 doesn't use those, so
    they don't need to be settable on it."""
    applicable = {k: v for k, v in overrides.items() if hasattr(engine_v2, k)}
    saved = {k: getattr(engine_v2, k) for k in applicable}
    try:
        for k, v in applicable.items():
            setattr(engine_v2, k, v)
        yield
    finally:
        for k, v in saved.items():
            setattr(engine_v2, k, v)


@contextmanager
def with_v3_coefficients(overrides: dict) -> Iterator[None]:
    """Mirror of `with_v2_coefficients` but targeting engine_v3. The
    hasattr filter skips V1/V2-only keys (e.g. ONEUP_FAVORITE_MARGIN,
    ONEUP_TRAILING_*) that engine_v3 doesn't define — V3 only reads its
    own ONEUP/TWOUP_MARGIN_LEVEL/TILT plus the shared model/boost/cap
    constants."""
    applicable = {k: v for k, v in overrides.items() if hasattr(engine_v3, k)}
    saved = {k: getattr(engine_v3, k) for k in applicable}
    try:
        for k, v in applicable.items():
            setattr(engine_v3, k, v)
        yield
    finally:
        for k, v in saved.items():
            setattr(engine_v3, k, v)


@contextmanager
def with_v4_coefficients(overrides: dict) -> Iterator[None]:
    """Mirror of `with_v3_coefficients` but targeting engine_v4. The hasattr
    filter skips keys engine_v4 doesn't define (the removed 1UP regression
    models, V1/V2 trailing margins) — V4 reads the same ONEUP/TWOUP margin /
    boost / reduction / near-even constants V3 does."""
    applicable = {k: v for k, v in overrides.items() if hasattr(engine_v4, k)}
    saved = {k: getattr(engine_v4, k) for k in applicable}
    try:
        for k, v in applicable.items():
            setattr(engine_v4, k, v)
        yield
    finally:
        for k, v in saved.items():
            setattr(engine_v4, k, v)


def run_simulation_dual(
    conn: sqlite3.Connection,
    *,
    config: config_mod.Profile,
    regime: str = "any",
    density: str = "all",
    scope: dict,
    csv_path: Path,
    engines: Sequence[str] = ("v1", "v2"),
    config_b: Optional[config_mod.Profile] = None,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> tuple[int, int]:
    """Iterate ticks once, call each selected engine, write a wide CSV.

    `engines` may contain any of "v1" / "v2"; raises ValueError on an
    empty or unknown selection. Lead state (`max_home_lead`,
    `max_away_lead`) is computed once per tick and reused by both
    engines so a downstream invariant comparison is apples-to-apples.

    `config_b` is an optional second profile. When set, every selected
    engine runs twice — once with `config`, once with `config_b` — and
    the row carries both blocks (Profile A in the main columns, Profile
    B in the `pB_*` columns). Bookmaker odds + devigged probs are
    profile-independent so they're written once; only Profile B's EV
    against the books is duplicated.

    Returns (n_events, n_rows) matching `runner.run_simulation`.
    """
    if regime not in VALID_REGIMES:
        raise ValueError(f"unknown regime {regime!r}")
    if density not in VALID_DENSITIES:
        raise ValueError(f"unknown density {density!r}")
    eng = tuple(engines)
    if not eng:
        raise ValueError("at least one engine must be selected")
    unknown = set(eng) - set(VALID_ENGINES)
    if unknown:
        raise ValueError(f"unknown engine(s): {sorted(unknown)}")

    ticks = _select_ticks(conn, regime, density, scope)
    n_total = len(ticks)
    if on_progress is not None:
        on_progress(0, n_total)

    leads_by_tick = score_state.max_leads_for_events(
        conn, {t["event_id"] for t in ticks},
    )

    engine_overrides = config_mod.coefficients_to_engine_overrides(config.coefficients)
    # V1's engine.py doesn't define the V2-only trailing margins or the
    # V3-only logit-margin params; with_v1_coefficients's getattr would
    # crash on them. Strip before applying to V1 — engine_v2 / engine_v3
    # get the full dict and filter by hasattr themselves.
    _v1_skip = config_mod.V2_ONLY_TUNABLE_NAMES | config_mod.V3_ONLY_TUNABLE_NAMES
    engine_overrides_v1 = {
        k: v for k, v in engine_overrides.items()
        if k not in _v1_skip
    }
    engine_overrides_b = (
        config_mod.coefficients_to_engine_overrides(config_b.coefficients)
        if config_b is not None else None
    )
    engine_overrides_b_v1 = (
        {k: v for k, v in engine_overrides_b.items()
         if k not in _v1_skip}
        if engine_overrides_b is not None else None
    )
    rows: list[tuple] = []
    seen_events: set[str] = set()
    # onchange density: drop a prematch tick whose full price set matches the
    # previous kept prematch tick for that event (identical inputs reprice
    # identically). STARTED ticks are never collapsed — a live tick is a
    # distinct match state. Done here (not in _select_ticks) so the prices are
    # the ones already loaded per tick and selection stays a cheap query.
    prev_fp_by_event: dict[str, frozenset] = {}
    engines_cell = ",".join(eng)
    profile_a_name = config.name
    profile_b_name = config_b.name if config_b is not None else ""

    def _run_engines(inputs: dict) -> tuple[Optional[dict], Optional[dict], Optional[dict], Optional[dict]]:
        """Call whichever engines are selected — caller's
        `with_*_coefficients` context decides which profile's
        coefficients are in force."""
        r1 = None
        r2 = None
        r3 = None
        r4 = None
        if "v1" in eng:
            try:
                r1 = engine.price_early_payout_markets(**inputs)
            except Exception as exc:  # noqa: BLE001
                log.warning("v1 engine crashed on event=%s ts=%s — skipping (%s)",
                            inputs.get("_event_id"), inputs.get("_ts_utc"), exc)
        if "v2" in eng:
            try:
                r2 = engine_v2.price_early_payout_markets(**inputs)
            except Exception as exc:  # noqa: BLE001
                log.warning("v2 engine crashed on event=%s ts=%s — skipping (%s)",
                            inputs.get("_event_id"), inputs.get("_ts_utc"), exc)
        if "v3" in eng:
            try:
                r3 = engine_v3.price_early_payout_markets(**inputs)
            except Exception as exc:  # noqa: BLE001
                log.warning("v3 engine crashed on event=%s ts=%s — skipping (%s)",
                            inputs.get("_event_id"), inputs.get("_ts_utc"), exc)
        if "v4" in eng:
            try:
                r4 = engine_v4.price_early_payout_markets(**inputs)
            except Exception as exc:  # noqa: BLE001
                log.warning("v4 engine crashed on event=%s ts=%s — skipping (%s)",
                            inputs.get("_event_id"), inputs.get("_ts_utc"), exc)
        return r1, r2, r3, r4

    # Both context managers active for Profile A's full duration — extras
    # are no-ops on the engine that isn't being called this run. For
    # Profile B (if set) the context flips mid-tick.
    with with_v1_coefficients(engine_overrides_v1), \
         with_v2_coefficients(engine_overrides), \
         with_v3_coefficients(engine_overrides), \
         with_v4_coefficients(engine_overrides):
        for i, t in enumerate(ticks):
            event_id = t["event_id"]
            ts_utc = t["ts_utc"]
            prices_by_book = _load_tick_prices(conn, event_id, ts_utc)
            if density == "onchange" and t["status"] == "UPCOMING":
                fp = prices_fingerprint(prices_by_book)
                if prev_fp_by_event.get(event_id) == fp:
                    if on_progress is not None and (i + 1) % _PROGRESS_BATCH == 0:
                        on_progress(i + 1, n_total)
                    continue  # unchanged prematch odds — skip the reprice
                prev_fp_by_event[event_id] = fp
            engine_inputs, basis = input_extract.extract(prices_by_book)
            if engine_inputs is None:
                if on_progress is not None and (i + 1) % _PROGRESS_BATCH == 0:
                    on_progress(i + 1, n_total)
                continue
            sh, sa = t["score_home"], t["score_away"]
            engine_inputs["score"] = (
                int(sh) if sh is not None else 0,
                int(sa) if sa is not None else 0,
            )
            mh, ma = leads_by_tick.get((event_id, ts_utc), (0, 0))
            engine_inputs["max_home_lead"] = mh
            engine_inputs["max_away_lead"] = ma

            # Profile A in scope here (the outer with_*_coefficients
            # blocks installed engine_overrides at loop entry).
            engine_inputs["_event_id"] = event_id
            engine_inputs["_ts_utc"] = ts_utc
            r_v1, r_v2, r_v3, r_v4 = _run_engines(
                {k: v for k, v in engine_inputs.items() if not k.startswith("_")}
            )

            # Profile B (if set) flips the engine module constants briefly,
            # runs the selected engines again, restores on exit.
            r_v1_b = None
            r_v2_b = None
            r_v3_b = None
            r_v4_b = None
            if engine_overrides_b is not None:
                with with_v1_coefficients(engine_overrides_b_v1), \
                     with_v2_coefficients(engine_overrides_b), \
                     with_v3_coefficients(engine_overrides_b), \
                     with_v4_coefficients(engine_overrides_b):
                    r_v1_b, r_v2_b, r_v3_b, r_v4_b = _run_engines(
                        {k: v for k, v in engine_inputs.items() if not k.startswith("_")}
                    )

            # Skip the tick if every selected engine failed for BOTH
            # profiles — writing a row with only metadata would be misleading.
            a_succeeded = (
                ("v1" in eng and r_v1 is not None)
                or ("v2" in eng and r_v2 is not None)
                or ("v3" in eng and r_v3 is not None)
                or ("v4" in eng and r_v4 is not None)
            )
            a_failed = not a_succeeded
            b_succeeded = (
                ("v1" in eng and r_v1_b is not None)
                or ("v2" in eng and r_v2_b is not None)
                or ("v3" in eng and r_v3_b is not None)
                or ("v4" in eng and r_v4_b is not None)
            )
            b_failed = engine_overrides_b is not None and not b_succeeded
            if a_failed and (engine_overrides_b is None or b_failed):
                if on_progress is not None and (i + 1) % _PROGRESS_BATCH == 0:
                    on_progress(i + 1, n_total)
                continue

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

            # EV against bookmaker odds uses V1's prob when V1 ran, then V2,
            # then V3, then V4. V1 stays the engine-of-record for live EVs.
            ev_src = r_v1 if r_v1 is not None else (
                r_v2 if r_v2 is not None else (r_v3 if r_v3 is not None else r_v4))
            p_h1, p_a1 = ev_src["p_home_1"], ev_src["p_away_1"]
            p_h2, p_a2 = ev_src["p_home_2"], ev_src["p_away_2"]
            lambdas_src = r_v1 if r_v1 is not None else (
                r_v2 if r_v2 is not None else (r_v3 if r_v3 is not None else r_v4))

            # Profile B's probabilities drive the pB_* bookmaker EV cells
            # (same book odds as Profile A). None when no Profile B ran.
            pB_ev_src = r_v1_b if r_v1_b is not None else (
                r_v2_b if r_v2_b is not None else (r_v3_b if r_v3_b is not None else r_v4_b))
            pB_p_h1 = pB_ev_src["p_home_1"] if pB_ev_src else None
            pB_p_a1 = pB_ev_src["p_away_1"] if pB_ev_src else None
            pB_p_h2 = pB_ev_src["p_home_2"] if pB_ev_src else None
            pB_p_a2 = pB_ev_src["p_away_2"] if pB_ev_src else None

            # Row assembled by column name (see csv_export.write_csv). Each
            # engine's OUR block is emitted only if that engine ran; omitted
            # blocks and all pB_* cells with no Profile B are blanked by
            # write_csv — no positional padding to keep in sync.
            rows.append({
                "engines": engines_cell,
                "profile_a": profile_a_name, "profile_b": profile_b_name,
                "snapshot_id": t["snapshot_id"], "event_id": event_id,
                "home": t["home"], "away": t["away"], "kickoff_utc": t["kickoff_utc"],
                "ts_utc": ts_utc,
                "status": t["status"], "match_minute": t["match_minute"],
                "score_home": t["score_home"], "score_away": t["score_away"],
                "basis_used": basis,
                "lambda_home": lambdas_src["lambda_home"], "lambda_away": lambdas_src["lambda_away"],
                # 1x2 + next-goal reference (profile-independent inputs).
                "p_home_win": engine_inputs["p_home_win"], "p_draw": engine_inputs["p_draw"],
                "p_away_win": engine_inputs["p_away_win"],
                "ftts_home_prob": engine_inputs.get("ftts_home_prob"),
                "ftts_away_prob": engine_inputs.get("ftts_away_prob"),
                "cap_1x2_home_odds": engine_inputs["home_1x2_odds"],
                "cap_1x2_away_odds": engine_inputs["away_1x2_odds"],
                **_our_block_dict("our_", "our_", r_v1),
                **_our_block_dict("v2_", "v2_our_", r_v2),
                **_our_block_dict("v3_", "v3_our_", r_v3),
                **_our_block_dict("v4_", "v4_our_", r_v4),
                **_book_block_dict("bp", bp, p_h1, p_a1, p_h2, p_a2),
                **_book_block_dict("sb", sb, p_h1, p_a1, p_h2, p_a2),
                **_book_odds_only_dict("b9j", b9j),
                **_book_odds_only_dict("bw", bw),
                "bp_1x2_home_odds": bp_1x2_h, "bp_1x2_draw_odds": bp_1x2_d, "bp_1x2_away_odds": bp_1x2_a,
                "sb_1x2_home_odds": sb_1x2_h, "sb_1x2_draw_odds": sb_1x2_d, "sb_1x2_away_odds": sb_1x2_a,
                "cap_source_home": cap_src_home, "cap_source_away": cap_src_away,
                **_our_block_dict("pB_our_", "pB_our_", r_v1_b),
                **_our_block_dict("pB_v2_", "pB_v2_our_", r_v2_b),
                **_our_block_dict("pB_v3_", "pB_v3_our_", r_v3_b),
                **_our_block_dict("pB_v4_", "pB_v4_our_", r_v4_b),
                **_pb_book_ev_dict("bp", bp, pB_p_h1, pB_p_a1, pB_p_h2, pB_p_a2),
                **_pb_book_ev_dict("sb", sb, pB_p_h1, pB_p_a1, pB_p_h2, pB_p_a2),
            })
            seen_events.add(event_id)
            if on_progress is not None and (i + 1) % _PROGRESS_BATCH == 0:
                on_progress(i + 1, n_total)

    csv_export.write_csv(csv_path, rows)
    if on_progress is not None:
        on_progress(n_total, n_total)
    return len(seen_events), len(rows)
