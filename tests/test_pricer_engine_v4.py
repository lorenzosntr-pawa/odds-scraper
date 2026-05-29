"""V4 engine tests. V4 = V3 plus three deltas: DP-direct 1UP level score,
invalid-1X2-ref deactivation, and odds-based favourite/near-even for the
margin (the 2UP coefficient blend stays probability-based). Goldens are
cross-checked against the Java XupMarginTest numbers."""

import pytest

from odds_scraper.pricer import engine_v3 as ep_v3
from odds_scraper.pricer import engine_v4 as ep_v4


def _devig3(o1, o2, o3):
    q1, q2, q3 = 1.0 / o1, 1.0 / o2, 1.0 / o3
    s = q1 + q2 + q3
    return q1 / s, q2 / s, q3 / s


def _ou(line, over_odds, under_odds):
    qo, qu = 1.0 / over_odds, 1.0 / under_odds
    return (line, qo / (qo + qu))


@pytest.fixture
def balanced_match():
    home_1x2, draw_1x2, away_1x2 = 2.50, 3.30, 2.80
    ph, pd, pa = _devig3(home_1x2, draw_1x2, away_1x2)
    return {
        "p_home_win": ph, "p_draw": pd, "p_away_win": pa,
        "home_1x2_odds": home_1x2, "draw_1x2_odds": draw_1x2, "away_1x2_odds": away_1x2,
        "home_ou": [_ou(0.5, 1.15, 5.00), _ou(1.5, 1.70, 2.10)],
        "away_ou": [_ou(0.5, 1.20, 4.50), _ou(1.5, 1.85, 1.95)],
        "total_ou": [_ou(1.5, 1.10, 6.00), _ou(2.5, 1.45, 2.70), _ou(3.5, 2.10, 1.75)],
        "ftts_home_prob": 0.48, "ftts_away_prob": 0.45,
    }


def test_v4_valid_ref():
    for bad in (None, 0.0, 1.0, 0.99, -2.0):
        assert ep_v4._valid_ref(bad) is False, bad
    for ok in (1.01, 2.0, 100.0):
        assert ep_v4._valid_ref(ok) is True, ok


def test_v4_is_favorite_by_odds():
    assert ep_v4._is_favorite_by_odds(1.5, 3.0) is True
    assert ep_v4._is_favorite_by_odds(3.0, 1.5) is False
    assert ep_v4._is_favorite_by_odds(2.0, 2.0) is True
    assert ep_v4._is_favorite_by_odds(None, 2.0) is False
    assert ep_v4._is_favorite_by_odds(2.0, None) is True


def test_v4_near_even_by_odds_strict_boundary():
    assert ep_v4._near_even_by_odds(2.0, 2.0, 0.03) is True
    away = 1.0 / 0.46
    assert ep_v4._near_even_by_odds(2.0, away, 0.03) is False
    away_in = 1.0 / 0.475
    assert ep_v4._near_even_by_odds(2.0, away_in, 0.03) is True
    assert ep_v4._near_even_by_odds(None, 2.0, 0.03) is False
    assert ep_v4._near_even_by_odds(2.0, 0.0, 0.03) is False


def test_v4_no_margin_returns_inverse_probability():
    assert ep_v4._fair_prob_to_odds(0.5, 0.0, 1.0) == pytest.approx(2.0)
    assert ep_v4._fair_prob_to_odds(0.8, 0.0, 1.0) == pytest.approx(1.25)


def test_v4_cap_binds_to_ceiling():
    odds, prob = ep_v4._cap_selection(2.0, 0.5, 1.5, 0.0)
    assert odds == pytest.approx(1.5)
    assert prob == pytest.approx(1.0 / 1.5)
    odds, _ = ep_v4._cap_selection(2.0, 0.5, 1.5, 10.0)
    assert odds == pytest.approx(1.35)


def test_v4_min_odds_floor():
    fair = ep_v4._fair_prob_to_odds(1.0, 0.0, 1.0)
    odds, _ = ep_v4._cap_selection(fair, 1.0, None, 0.0)
    assert odds == pytest.approx(ep_v4.CAP_MIN_OFFERED_ODDS)


def test_v4_boost_lengthens_then_suppressed_near_even():
    assert ep_v4._apply_boost(2.0, True, False, 10.0, 20.0) == pytest.approx(2.2)
    assert ep_v4._apply_boost(2.0, False, False, 10.0, 20.0) == pytest.approx(2.4)
    assert ep_v4._apply_boost(2.0, True, True, 10.0, 20.0) == pytest.approx(2.0)


def test_v4_level_1up_differs_from_v3(balanced_match):
    """At a level score with FTTS supplied, v4 reads the DP directly while v3
    runs the next-goal regression — the level 1UP probabilities must differ."""
    r3 = ep_v3.price_early_payout_markets(**balanced_match)
    r4 = ep_v4.price_early_payout_markets(**balanced_match)
    assert r4["p_home_1"] is not None and r3["p_home_1"] is not None
    assert r4["p_home_1"] != pytest.approx(r3["p_home_1"]), "v4 level 1UP should differ from v3"


def test_v4_matches_v3_when_favourites_agree(balanced_match):
    """When the odds favourite equals the probability favourite (normal case),
    trailing 1UP and ALL 2UP match v3. Score 1-0 makes 1UP trailing-only."""
    inp = dict(balanced_match, score=(1, 0))
    r3 = ep_v3.price_early_payout_markets(**inp)
    r4 = ep_v4.price_early_payout_markets(**inp)
    for key in ("p_away_1", "p_home_2", "p_away_2"):
        assert r4[key] == pytest.approx(r3[key]), f"{key} should match v3"
    for mk, side in (("market_1up", "away_margin"),
                     ("market_2up", "home_margin"), ("market_2up", "away_margin")):
        a, b = r4[mk][side], r3[mk][side]
        if a is None or b is None:
            assert a is b
        else:
            assert a == pytest.approx(b), f"{mk}.{side} should match v3"


def _flip_inputs(*, home_odds, away_odds, p_home, p_away):
    return {
        "p_home_win": p_home, "p_draw": 1.0 - p_home - p_away, "p_away_win": p_away,
        "home_1x2_odds": home_odds, "draw_1x2_odds": 3.4, "away_1x2_odds": away_odds,
        "home_ou": [_ou(0.5, 1.40, 3.00), _ou(1.5, 2.30, 1.65)],
        "away_ou": [_ou(0.5, 1.40, 3.00), _ou(1.5, 2.30, 1.65)],
        "total_ou": [_ou(1.5, 1.25, 4.00), _ou(2.5, 1.85, 1.95)],
        "ftts_home_prob": 0.5, "ftts_away_prob": 0.5,
    }


def test_v4_2up_reduction_flips_when_home_odds_fav_prob_dog():
    """Home is the ODDS favourite (1.5 < 3.0) but the PROBABILITY underdog
    (0.2 < 0.6). Home 2UP odd binds the cap: v4 uses fav 2.0% (1.5*0.98=1.47);
    v3 uses dog 0.5% (1.5*0.995=1.4925)."""
    inp = _flip_inputs(home_odds=1.5, away_odds=3.0, p_home=0.2, p_away=0.6)
    r3 = ep_v3.price_early_payout_markets(**inp)
    r4 = ep_v4.price_early_payout_markets(**inp)
    assert r4["market_2up"]["home_margin"] == pytest.approx(1.5 * 0.98)
    assert r3["market_2up"]["home_margin"] == pytest.approx(1.5 * 0.995)
    assert r4["market_2up"]["home_margin"] != pytest.approx(r3["market_2up"]["home_margin"])


def test_v4_2up_reduction_flips_mirror_away():
    """Mirror: away is the ODDS favourite but the PROBABILITY underdog."""
    inp = _flip_inputs(home_odds=3.0, away_odds=1.5, p_home=0.6, p_away=0.2)
    r3 = ep_v3.price_early_payout_markets(**inp)
    r4 = ep_v4.price_early_payout_markets(**inp)
    assert r4["market_2up"]["away_margin"] == pytest.approx(1.5 * 0.98)
    assert r3["market_2up"]["away_margin"] == pytest.approx(1.5 * 0.995)
    assert r4["market_2up"]["away_margin"] != pytest.approx(r3["market_2up"]["away_margin"])


@pytest.mark.parametrize("bad", [None, 0.0, 1.0, 0.99])
def test_v4_invalid_ref_deactivates_home_v3_does_not(balanced_match, bad):
    inp = dict(balanced_match, home_1x2_odds=bad)
    r4 = ep_v4.price_early_payout_markets(**inp)
    r3 = ep_v3.price_early_payout_markets(**inp)
    assert r4["p_home_1"] is None and r4["p_home_2"] is None
    assert r4["market_1up"]["home_margin"] is None and r4["market_2up"]["home_margin"] is None
    assert r4["market_2up"]["away_margin"] is not None
    assert r3["market_2up"]["home_margin"] is not None


@pytest.mark.parametrize("score", [(0, 0), (1, 0), (0, 1), (1, 1), (2, 1)])
def test_v4_oneup_prob_ge_twoup_prob(balanced_match, score):
    r = ep_v4.price_early_payout_markets(**dict(balanced_match, score=score))
    for one_k, two_k in (("p_home_1", "p_home_2"), ("p_away_1", "p_away_2")):
        p1, p2 = r[one_k], r[two_k]
        if p1 is None or p2 is None:
            continue
        assert p1 >= p2 - 1e-9, f"{one_k}={p1} < {two_k}={p2} at score {score}"
