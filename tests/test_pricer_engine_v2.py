"""V2 engine tests. At the start of the port engine_v2 is byte-identical
to engine — these tests guard that, then later tasks evolve the V2
math while keeping V1 frozen."""

import pytest

from odds_scraper.pricer import engine as ep_v1
from odds_scraper.pricer import engine_v2 as ep_v2


def _naive_devig_two(over_odds, under_odds):
    q_over = 1.0 / over_odds
    q_under = 1.0 / under_odds
    return q_over / (q_over + q_under)


def _naive_devig_three(o1, o2, o3):
    q1, q2, q3 = 1.0 / o1, 1.0 / o2, 1.0 / o3
    s = q1 + q2 + q3
    return q1 / s, q2 / s, q3 / s


def _ou_prob(line, over_odds, under_odds):
    return (line, _naive_devig_two(over_odds, under_odds))


@pytest.fixture
def balanced_match():
    home_1x2, draw_1x2, away_1x2 = 2.50, 3.30, 2.80
    p_home, p_draw, p_away = _naive_devig_three(home_1x2, draw_1x2, away_1x2)
    return {
        "p_home_win": p_home, "p_draw": p_draw, "p_away_win": p_away,
        "home_1x2_odds": home_1x2, "draw_1x2_odds": draw_1x2, "away_1x2_odds": away_1x2,
        "home_ou": [_ou_prob(0.5, 1.30, 3.40), _ou_prob(1.5, 2.10, 1.75)],
        "away_ou": [_ou_prob(0.5, 1.40, 3.00), _ou_prob(1.5, 2.30, 1.65)],
        "total_ou": [_ou_prob(1.5, 1.25, 4.00), _ou_prob(2.5, 1.85, 1.95),
                     _ou_prob(3.5, 3.20, 1.35)],
        "ftts_home_prob": 0.48, "ftts_away_prob": 0.45,
    }


def test_v2_module_exposes_same_public_surface_as_v1():
    """engine_v2.py must expose the same top-level callable so the
    dual runner can call both interchangeably."""
    assert hasattr(ep_v2, "price_early_payout_markets")


def test_v2_prematch_matches_v1_byte_for_byte(balanced_match):
    """Right after the verbatim copy, V2's prematch (score 0-0) output
    must equal V1's. This guard catches a v2-only change accidentally
    altering the level-score 1UP path."""
    r1 = ep_v1.price_early_payout_markets(**balanced_match)
    r2 = ep_v2.price_early_payout_markets(**balanced_match)
    assert r2["p_home_1"] == pytest.approx(r1["p_home_1"])
    assert r2["p_away_1"] == pytest.approx(r1["p_away_1"])
    assert r2["market_1up"]["home_margin"] == pytest.approx(r1["market_1up"]["home_margin"])
    assert r2["market_1up"]["away_margin"] == pytest.approx(r1["market_1up"]["away_margin"])
    assert r2["p_home_2"] == pytest.approx(r1["p_home_2"])
    assert r2["p_away_2"] == pytest.approx(r1["p_away_2"])
