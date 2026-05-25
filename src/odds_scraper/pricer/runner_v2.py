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
    engine, engine_v2, inputs as input_extract, configs as config_mod,
    csv_export, score_state,
)
from .runner import (
    VALID_REGIMES, VALID_DENSITIES, _PROGRESS_BATCH, _ev,
    _select_ticks, _load_tick_prices, _extract_quoted_up,
    with_coefficients as with_v1_coefficients,
)

log = logging.getLogger(__name__)


VALID_ENGINES = ("v1", "v2")


@contextmanager
def with_v2_coefficients(overrides: dict) -> Iterator[None]:
    """Mirror of `runner.with_coefficients` but targeting engine_v2.
    Necessary because the override mechanism setattrs on a module — if
    we used V1's with_coefficients on engine_v2, the wrong module would
    be touched."""
    saved = {k: getattr(engine_v2, k) for k in overrides}
    try:
        for k, v in overrides.items():
            setattr(engine_v2, k, v)
        yield
    finally:
        for k, v in saved.items():
            setattr(engine_v2, k, v)


_EMPTY_OUR = ("",) * 16


def _our_block(res: Optional[dict], p_h1, p_a1, p_h2, p_a2):
    """Per-engine 16-cell OUR block: probs + fair + capped + capped_ev
    for 1UP and 2UP home/away. Returns blanks when `res` is None."""
    if res is None:
        return _EMPTY_OUR
    cap_1h = res["market_1up"]["home_margin"]
    cap_1a = res["market_1up"]["away_margin"]
    cap_2h = res["market_2up"]["home_margin"]
    cap_2a = res["market_2up"]["away_margin"]
    return (
        p_h1, p_a1,
        res["market_1up"]["home_fair"], cap_1h, _ev(p_h1, cap_1h),
        res["market_1up"]["away_fair"], cap_1a, _ev(p_a1, cap_1a),
        p_h2, p_a2,
        res["market_2up"]["home_fair"], cap_2h, _ev(p_h2, cap_2h),
        res["market_2up"]["away_fair"], cap_2a, _ev(p_a2, cap_2a),
    )


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
    engine_overrides_b = (
        config_mod.coefficients_to_engine_overrides(config_b.coefficients)
        if config_b is not None else None
    )
    rows: list[tuple] = []
    seen_events: set[str] = set()
    engines_cell = ",".join(eng)
    profile_a_name = config.name
    profile_b_name = config_b.name if config_b is not None else ""

    def _run_engines(inputs: dict) -> tuple[Optional[dict], Optional[dict]]:
        """Call whichever engines are selected — caller's
        `with_*_coefficients` context decides which profile's
        coefficients are in force."""
        r1 = None
        r2 = None
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
        return r1, r2

    # Both context managers active for Profile A's full duration — extras
    # are no-ops on the engine that isn't being called this run. For
    # Profile B (if set) the context flips mid-tick.
    with with_v1_coefficients(engine_overrides), with_v2_coefficients(engine_overrides):
        for i, t in enumerate(ticks):
            event_id = t["event_id"]
            ts_utc = t["ts_utc"]
            prices_by_book = _load_tick_prices(conn, event_id, ts_utc)
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
            r_v1, r_v2 = _run_engines(
                {k: v for k, v in engine_inputs.items() if not k.startswith("_")}
            )

            # Profile B (if set) flips the engine module constants briefly,
            # runs both engines again, restores on exit.
            r_v1_b = None
            r_v2_b = None
            if engine_overrides_b is not None:
                with with_v1_coefficients(engine_overrides_b), \
                     with_v2_coefficients(engine_overrides_b):
                    r_v1_b, r_v2_b = _run_engines(
                        {k: v for k, v in engine_inputs.items() if not k.startswith("_")}
                    )

            # Skip the tick if every selected engine failed for BOTH
            # profiles — writing a row with only metadata would be misleading.
            a_failed = (
                ("v1" in eng and r_v1 is None) and ("v2" in eng and r_v2 is None)
            ) or (
                "v1" in eng and "v2" not in eng and r_v1 is None
            ) or (
                "v2" in eng and "v1" not in eng and r_v2 is None
            )
            b_failed = engine_overrides_b is not None and ((
                ("v1" in eng and r_v1_b is None) and ("v2" in eng and r_v2_b is None)
            ) or (
                "v1" in eng and "v2" not in eng and r_v1_b is None
            ) or (
                "v2" in eng and "v1" not in eng and r_v2_b is None
            ))
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

            # EV against bookmaker odds uses V1's prob when V1 ran;
            # otherwise V2's. V1 stays the engine-of-record for live EVs.
            ev_src = r_v1 if r_v1 is not None else r_v2
            p_h1 = ev_src["p_home_1"]
            p_a1 = ev_src["p_away_1"]
            p_h2 = ev_src["p_home_2"]
            p_a2 = ev_src["p_away_2"]

            v1_block = _our_block(
                r_v1,
                r_v1["p_home_1"] if r_v1 else None,
                r_v1["p_away_1"] if r_v1 else None,
                r_v1["p_home_2"] if r_v1 else None,
                r_v1["p_away_2"] if r_v1 else None,
            )
            v2_block = _our_block(
                r_v2,
                r_v2["p_home_1"] if r_v2 else None,
                r_v2["p_away_1"] if r_v2 else None,
                r_v2["p_home_2"] if r_v2 else None,
                r_v2["p_away_2"] if r_v2 else None,
            )

            lambdas_src = r_v1 if r_v1 is not None else r_v2

            # Profile B's blocks. Same shape as Profile A's; the OUR
            # blocks come from r_v1_b / r_v2_b, the BP/SB EV cells use
            # Profile B's probability against the same book odds.
            pB_v1_block = _our_block(
                r_v1_b,
                r_v1_b["p_home_1"] if r_v1_b else None,
                r_v1_b["p_away_1"] if r_v1_b else None,
                r_v1_b["p_home_2"] if r_v1_b else None,
                r_v1_b["p_away_2"] if r_v1_b else None,
            )
            pB_v2_block = _our_block(
                r_v2_b,
                r_v2_b["p_home_1"] if r_v2_b else None,
                r_v2_b["p_away_1"] if r_v2_b else None,
                r_v2_b["p_home_2"] if r_v2_b else None,
                r_v2_b["p_away_2"] if r_v2_b else None,
            )
            pB_ev_src = r_v1_b if r_v1_b is not None else r_v2_b
            pB_p_h1 = pB_ev_src["p_home_1"] if pB_ev_src else None
            pB_p_a1 = pB_ev_src["p_away_1"] if pB_ev_src else None
            pB_p_h2 = pB_ev_src["p_home_2"] if pB_ev_src else None
            pB_p_a2 = pB_ev_src["p_away_2"] if pB_ev_src else None

            rows.append((
                engines_cell,
                profile_a_name, profile_b_name,
                t["snapshot_id"], event_id,
                t["home"], t["away"], t["kickoff_utc"],
                ts_utc,
                t["status"], t["match_minute"],
                t["score_home"], t["score_away"],
                basis,
                lambdas_src["lambda_home"], lambdas_src["lambda_away"],
                *v1_block,
                *v2_block,
                bp["1up_home"][1], bp["1up_home"][0], _ev(p_h1, bp["1up_home"][0]),
                bp["1up_away"][1], bp["1up_away"][0], _ev(p_a1, bp["1up_away"][0]),
                bp["2up_home"][1], bp["2up_home"][0], _ev(p_h2, bp["2up_home"][0]),
                bp["2up_away"][1], bp["2up_away"][0], _ev(p_a2, bp["2up_away"][0]),
                sb["1up_home"][1], sb["1up_home"][0], _ev(p_h1, sb["1up_home"][0]),
                sb["1up_away"][1], sb["1up_away"][0], _ev(p_a1, sb["1up_away"][0]),
                sb["2up_home"][1], sb["2up_home"][0], _ev(p_h2, sb["2up_home"][0]),
                sb["2up_away"][1], sb["2up_away"][0], _ev(p_a2, sb["2up_away"][0]),
                b9j["1up_home"][0], b9j["1up_away"][0],
                b9j["2up_home"][0], b9j["2up_away"][0],
                bw["1up_home"][0],  bw["1up_away"][0],
                bw["2up_home"][0],  bw["2up_away"][0],
                *pB_v1_block,
                *pB_v2_block,
                _ev(pB_p_h1, bp["1up_home"][0]), _ev(pB_p_a1, bp["1up_away"][0]),
                _ev(pB_p_h2, bp["2up_home"][0]), _ev(pB_p_a2, bp["2up_away"][0]),
                _ev(pB_p_h1, sb["1up_home"][0]), _ev(pB_p_a1, sb["1up_away"][0]),
                _ev(pB_p_h2, sb["2up_home"][0]), _ev(pB_p_a2, sb["2up_away"][0]),
            ))
            seen_events.add(event_id)
            if on_progress is not None and (i + 1) % _PROGRESS_BATCH == 0:
                on_progress(i + 1, n_total)

    csv_export.write_csv(csv_path, rows)
    if on_progress is not None:
        on_progress(n_total, n_total)
    return len(seen_events), len(rows)
