"""Pure pricing core for ClickHouse 1UP/2UP reconstruction. No IO.

true_proba is already fair, so there is NO devig here (unlike the CSV
deriver). The 1X2 triple is renormalized to sum 1, and the engines' required
1X2 decimal odds are synthesized from those probabilities with a flat 2%
margin (brand-neutral) — offered `price` is intentionally NOT used.
"""
from __future__ import annotations

import functools
from typing import Optional

from .constants import CAP_MARGIN
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
    identical across engines. Returns restore()."""
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
