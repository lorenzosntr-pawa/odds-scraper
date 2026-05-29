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
