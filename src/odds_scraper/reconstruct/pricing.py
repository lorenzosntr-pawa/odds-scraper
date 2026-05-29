"""Pure pricing core for ClickHouse 1UP/2UP reconstruction. No IO.

true_proba is already fair, so there is NO devig here (unlike the CSV
deriver). The 1X2 triple is renormalized to sum 1, and the engines' required
1X2 decimal odds are synthesized from those probabilities with a flat 2%
margin (brand-neutral) — offered `price` is intentionally NOT used.
"""
from __future__ import annotations

from typing import Optional

from .constants import CAP_MARGIN


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
