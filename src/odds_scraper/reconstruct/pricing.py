"""Pure pricing core for ClickHouse 1UP/2UP reconstruction. No IO.

true_proba is already fair, so there is NO devig here (unlike the CSV
deriver). The 1X2 triple is renormalized to sum 1, and the engines' required
1X2 decimal odds are synthesized from those probabilities with a flat 2%
margin (brand-neutral) — offered `price` is intentionally NOT used.
"""
from __future__ import annotations

import functools
from datetime import datetime
from typing import Optional

from .constants import CAP_MARGIN
from .constants import (MARKET_1X2, MARKET_OU_TOTAL, MARKET_OU_HOME,
                        MARKET_OU_AWAY, SEL_OVER, SEL_NG_HOME, SEL_NG_AWAY)
from odds_scraper.pricer import engine_v2, engine_v3, engine_v4


def renormalize_1x2(p_home: float, p_draw: float, p_away: float):
    """Return (home, draw, away) scaled to sum 1, plus drift = raw_sum - 1."""
    s = p_home + p_draw + p_away
    if s <= 0:
        return 0.0, 0.0, 0.0, 0.0
    return p_home / s, p_draw / s, p_away / s, s - 1.0


def cap_odds_from_prob(prob: float, margin: float = CAP_MARGIN) -> Optional[float]:
    """Synthetic 1X2 source odds for the engine cap: fair odds with a flat
    margin baked in. Returns None for a non-priceable probability."""
    implied = prob * (1.0 + margin)
    if implied <= 0:
        return None
    return 1.0 / implied


def next_goal_index(home_score: int, away_score: int) -> int:
    """Goal number of the next goal = goals already scored + 1.
    The next-goal market line (handicap/4.0) equals this index."""
    return home_score + away_score + 1


def assemble_engine_kwargs(moment: dict) -> dict:
    """Build the kwargs accepted by every engine's price_early_payout_markets
    from a Moment dict. Renormalizes 1X2, synthesizes cap odds, passes O/U
    over-probabilities and FTTS through unchanged (already fair)."""
    ph, pd, pa, _drift = renormalize_1x2(
        moment["p_home_raw"], moment["p_draw_raw"], moment["p_away_raw"])
    return dict(
        p_home_win=ph, p_draw=pd, p_away_win=pa,
        home_1x2_odds=cap_odds_from_prob(ph),
        draw_1x2_odds=cap_odds_from_prob(pd),
        away_1x2_odds=cap_odds_from_prob(pa),
        total_ou=list(moment["total_ou"]),
        home_ou=list(moment["home_ou"]),
        away_ou=list(moment["away_ou"]),
        ftts_home_prob=moment["ftts_home"],
        ftts_away_prob=moment["ftts_away"],
        score=(int(moment["home_score"]), int(moment["away_score"])),
    )


_dp_cached = None


def install_dp_cache(round_dp: int = 4):
    """Monkeypatch ever_leads_probability in all three engines to share one
    lru_cache keyed on rounded (lambda_h, lambda_a, initial_diff). The DP is
    identical across engines. Returns restore().

    Not re-entrant: call restore() before installing again, or the second
    install captures the first wrapper as its base."""
    global _dp_cached
    originals = {
        m: m.ever_leads_probability for m in (engine_v2, engine_v3, engine_v4)
    }
    base = engine_v2.ever_leads_probability

    @functools.lru_cache(maxsize=200_000)
    def _cached(lh: float, la: float, d: int):
        return base(lh, la, d)

    def wrapper(lambda_h, lambda_a, initial_diff):
        return _cached(round(lambda_h, round_dp), round(lambda_a, round_dp), initial_diff)

    _dp_cached = _cached
    for m in originals:
        m.ever_leads_probability = wrapper

    def restore():
        for m, fn in originals.items():
            m.ever_leads_probability = fn

    return restore


def dp_cache_info():
    if _dp_cached is None:
        raise RuntimeError("dp_cache_info() called before install_dp_cache()")
    return _dp_cached.cache_info()


_ENGINES = {"v2": engine_v2, "v3": engine_v3, "v4": engine_v4}


def _ev(prob, odds):
    if prob is None or odds is None:
        return None
    return prob * odds - 1.0


def _side_cells(prefix, res, market_key, prob_key_home, prob_key_away):
    m = res[market_key]
    ph, pa = res[prob_key_home], res[prob_key_away]
    oh, oa = m["home_margin"], m["away_margin"]
    return {
        f"{prefix}_home_odds": oh, f"{prefix}_home_prob": ph, f"{prefix}_home_ev": _ev(ph, oh),
        f"{prefix}_away_odds": oa, f"{prefix}_away_prob": pa, f"{prefix}_away_ev": _ev(pa, oa),
    }


def price_moment(moment: dict, *, run_ts: str,
                 max_home_lead: int, max_away_lead: int) -> dict | None:
    """Price one moment with v2/v3/v4. Returns an OUTPUT_COLUMNS-shaped dict,
    or None if the moment carries no priceable market (no full 1X2, or no
    derivable lambda)."""
    ph, pd, pa, drift = renormalize_1x2(
        moment["p_home_raw"], moment["p_draw_raw"], moment["p_away_raw"])
    if not (ph > 0 and pd > 0 and pa > 0):
        return None
    kw = assemble_engine_kwargs(moment)
    kw["max_home_lead"] = max_home_lead
    kw["max_away_lead"] = max_away_lead
    has_1up = kw["ftts_home_prob"] is not None and kw["ftts_away_prob"] is not None

    results = {}
    for name, eng in _ENGINES.items():
        res = eng.price_early_payout_markets(**kw)
        results[name] = res
    # Use v2 as the gate for derivable lambda (DP identical across engines).
    if results["v2"]["lambda_home"] is None or results["v2"]["lambda_away"] is None:
        return None

    row = {
        "run_ts": run_ts, "brand": moment["brand"],
        "event_id": moment["event_id"], "sr_id": moment["sr_id"],
        "event_name": moment["event_name"], "sr_start_time": moment["sr_start_time"],
        "in_play": moment["in_play"], "moment_ts": moment["moment_ts"],
        "home_score": int(moment["home_score"]), "away_score": int(moment["away_score"]),
        "p_home": ph, "p_draw": pd, "p_away": pa,
        "lambda_home": results["v2"]["lambda_home"],
        "lambda_away": results["v2"]["lambda_away"],
        "ftts_home": kw["ftts_home_prob"], "ftts_away": kw["ftts_away_prob"],
        "has_1up": has_1up,
        "max_input_staleness_seconds": int(moment["max_input_staleness_seconds"]),
        "est_input_drift_pct": None,   # filled by the CLI drift pass
        "renorm_drift": round(drift, 6),
    }
    for name, res in results.items():
        row.update(_side_cells(f"{name}_1up", res, "market_1up", "p_home_1", "p_away_1"))
        row.update(_side_cells(f"{name}_2up", res, "market_2up", "p_home_2", "p_away_2"))
    return row


_TS_FMT = "%Y-%m-%d %H:%M:%S"
_OU_FAMILY = {MARKET_OU_TOTAL: "total_ou", MARKET_OU_HOME: "home_ou",
              MARKET_OU_AWAY: "away_ou"}


def _is_next_goal(market_name: str) -> bool:
    return market_name.endswith(" Goal") and market_name[:-5].isdigit()


def moments_from_rows(rows):
    """Group aligned long rows (Task 8 output) into Moment dicts, ordered as
    the rows arrive. `rows` must be grouped by (event_id, in_play, moment_ts)
    contiguously (the extraction SQL ORDER BY guarantees this)."""
    def key(r):
        return (r["event_id"], r["in_play"], r["moment_ts"])

    for (_eid, _ip, _mts), group in _groupby_contiguous(rows, key):
        group = list(group)
        head = group[0]
        # 1X2 from the anchor selection columns (same on every row of the group)
        p = {}
        for r in group:
            p[r["x12_selection"]] = r["x12_proba"]
        if not ({"Home", "Draw", "Away"} <= set(p)):
            continue
        hs, as_ = int(head["home_score"]), int(head["away_score"])
        active_line = float(next_goal_index(hs, as_))
        ou = {"total_ou": {}, "home_ou": {}, "away_ou": {}}
        ng = {}
        sel_ts_used = []
        for r in group:
            mkt, sel = r["market_name"], r["selection_name"]
            if mkt in _OU_FAMILY and sel == SEL_OVER:
                ou[_OU_FAMILY[mkt]][float(r["line"])] = r["true_proba"]
                sel_ts_used.append(r["sel_ts"])
            elif _is_next_goal(mkt) and float(r["line"]) == active_line:
                ng[sel] = r["true_proba"]
                sel_ts_used.append(r["sel_ts"])
        ftts_home = ng.get(SEL_NG_HOME)
        ftts_away = ng.get(SEL_NG_AWAY)
        if ftts_home is None or ftts_away is None:
            ftts_home = ftts_away = None
        moment_ts = head["moment_ts"]
        stale = _staleness_seconds(moment_ts, sel_ts_used)
        yield {
            "event_id": head["event_id"], "sr_id": head["sr_id"],
            "brand": head["brand"], "event_name": head["event_name"],
            "sr_start_time": head["sr_start_time"],
            "in_play": head["in_play"], "moment_ts": moment_ts,
            "home_score": hs, "away_score": as_,
            "p_home_raw": p["Home"], "p_draw_raw": p["Draw"], "p_away_raw": p["Away"],
            "total_ou": sorted(ou["total_ou"].items()),
            "home_ou": sorted(ou["home_ou"].items()),
            "away_ou": sorted(ou["away_ou"].items()),
            "ftts_home": ftts_home, "ftts_away": ftts_away,
            "max_input_staleness_seconds": stale,
        }


def _groupby_contiguous(rows, key):
    cur_key, bucket = object(), []
    for r in rows:
        k = key(r)
        if k != cur_key and bucket:
            yield cur_key, bucket
            bucket = []
        cur_key = k
        bucket.append(r)
    if bucket:
        yield cur_key, bucket


def _staleness_seconds(moment_ts: str, sel_ts_list) -> int:
    if not sel_ts_list:
        return 0
    t0 = _parse_ts(moment_ts)
    worst = max((t0 - _parse_ts(s)).total_seconds() for s in sel_ts_list if s)
    return int(round(max(worst, 0)))


def _parse_ts(s):
    if isinstance(s, datetime):
        return s
    return datetime.strptime(str(s)[:19], _TS_FMT)


def run_pricing(moments_iter, *, run_ts: str):
    """Price a stream of moments, tracking per-event max lead so live
    deactivation is history-aware across the moments we observed. Moments must
    be grouped by event_id contiguously (extraction SQL ORDER BY guarantees
    it). Yields output rows (skips None)."""
    cur_event = object()
    max_h = max_a = 0
    for m in moments_iter:
        if m["event_id"] != cur_event:
            cur_event, max_h, max_a = m["event_id"], 0, 0
        diff = int(m["home_score"]) - int(m["away_score"])
        max_h = max(max_h, diff)
        max_a = max(max_a, -diff)
        row = price_moment(m, run_ts=run_ts,
                           max_home_lead=max_h, max_away_lead=max_a)
        if row is not None:
            yield row
