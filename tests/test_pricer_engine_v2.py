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


def test_ever_leads_returns_8_tuple():
    stats = ep_v2.ever_leads_probability(1.4, 1.1, 0)
    assert len(stats) == 8
    for v in stats:
        assert 0.0 <= v <= 1.0


def test_ever_leads_zero_lambdas_returns_zeros():
    assert ep_v2.ever_leads_probability(0.0, 1.0, 0) == (0.0,) * 8
    assert ep_v2.ever_leads_probability(1.0, 0.0, 0) == (0.0,) * 8


def test_ever_leads_monotonic_ever_1_geq_ever_2():
    """P(ever ±1) must be >= P(ever ±2) - reaching +2 means passing
    through +1. This is the construction-time invariant V2 relies on."""
    for lh, la, d in [(1.4, 1.1, 0), (2.0, 0.8, 0), (1.0, 1.0, -1), (0.6, 1.5, 2)]:
        ev1h, ev1a, _, _, ev2h, ev2a, _, _ = ep_v2.ever_leads_probability(lh, la, d)
        assert ev1h >= ev2h - 1e-12, f"home: ever1={ev1h} < ever2={ev2h}"
        assert ev1a >= ev2a - 1e-12, f"away: ever1={ev1a} < ever2={ev2a}"


def test_ever_leads_initial_diff_sets_flags():
    """An initial_diff of +1 must have HIGH1 already triggered -> P(ever+-1)
    on the home side starts at 1.0 (no time-dependent build-up needed)."""
    ev1h, _, _, _, ev2h, _, _, _ = ep_v2.ever_leads_probability(1.4, 1.1, 1)
    assert ev1h == pytest.approx(1.0)
    # +2 -> both HIGH1 and HIGH2 pre-set.
    ev1h, _, _, _, ev2h, _, _, _ = ep_v2.ever_leads_probability(1.4, 1.1, 2)
    assert ev1h == pytest.approx(1.0)
    assert ev2h == pytest.approx(1.0)


def test_ever_leads_symmetry_under_team_swap():
    """Swapping (lambdaH, lambdaA) and negating initial_diff must swap home/away
    statistics. Confidence that the home/away wiring is right."""
    a = ep_v2.ever_leads_probability(1.4, 1.1, 1)
    b = ep_v2.ever_leads_probability(1.1, 1.4, -1)
    # Stats layout: (home1, away1, home1wins, away1wins, home2, away2, home2wins, away2wins)
    # Under swap, home<->away.
    assert a[0] == pytest.approx(b[1])  # home_ever_1 <-> away_ever_1
    assert a[1] == pytest.approx(b[0])
    assert a[2] == pytest.approx(b[3])
    assert a[3] == pytest.approx(b[2])
    assert a[4] == pytest.approx(b[5])
    assert a[5] == pytest.approx(b[4])
    assert a[6] == pytest.approx(b[7])
    assert a[7] == pytest.approx(b[6])


def test_v2_dog_margin_intercept_bumped():
    """V2's TWOUP_UNDERDOG_MARGIN intercept is 0.014 (Java rewrite)."""
    assert ep_v2.TWOUP_UNDERDOG_MARGIN == (0.994, 0.014)


def test_v1_dog_margin_intercept_unchanged():
    """V1 stays at 0.008 — V2 must not bleed into V1."""
    assert ep_v1.TWOUP_UNDERDOG_MARGIN == (0.994, 0.008)


@pytest.fixture
def home_trailing_match(balanced_match):
    """Same fixture as balanced_match but score 1-2: away leads by 1.
    Home is the trailing side; home's 1UP must be repriced via DP
    (V1 used _trailing_selection here; V2 uses the new DP)."""
    return {**balanced_match, "score": (1, 2)}


def test_v2_oneup_trailing_uses_dp_not_heuristic(home_trailing_match):
    """V2 must produce a different 1UP odds than V1 on a trailing tick.
    Both methods are valid, but they're different shapes; if V2 equals
    V1 here, the rewrite didn't take effect."""
    r1 = ep_v1.price_early_payout_markets(**home_trailing_match)
    r2 = ep_v2.price_early_payout_markets(**home_trailing_match)
    # Trailing side: HOME (away leads). V1 used _trailing_selection, V2
    # uses inclusion-exclusion on ever_leads_probability.
    v1_home_1up = r1["market_1up"]["home_margin"]
    v2_home_1up = r2["market_1up"]["home_margin"]
    assert v1_home_1up is not None
    assert v2_home_1up is not None
    assert v1_home_1up != pytest.approx(v2_home_1up, rel=1e-6)


def test_v2_oneup_trailing_deactivates_leading_side(home_trailing_match):
    """The away side (currently leading 1-2) has already triggered its
    1UP — must be None just like V1."""
    r2 = ep_v2.price_early_payout_markets(**home_trailing_match)
    assert r2["market_1up"]["away_margin"] is None
    assert r2["market_1up"]["away_fair"] is None
    assert r2["p_away_1"] is None


def test_v2_oneup_trailing_uses_inclusion_exclusion(balanced_match):
    """Hand-compute: P(home 1UP at score 0-1) =
        P(home wins FT) + max(0, P(home ever +1) - P(home ever +1 AND wins))
    Use only the trailing branch's inputs (no draw_1x2 mutation)."""
    inputs = {**balanced_match, "score": (0, 1)}
    r2 = ep_v2.price_early_payout_markets(**inputs)
    lam_h, lam_a = r2["lambda_home"], r2["lambda_away"]
    stats = ep_v2.ever_leads_probability(lam_h, lam_a, -1)
    p_home_ever_1, _, p_home_ever_1_wins, *_ = stats
    expected_residual = max(0.0, p_home_ever_1 - p_home_ever_1_wins)
    expected_home_1up = inputs["p_home_win"] + expected_residual
    assert r2["p_home_1"] == pytest.approx(expected_home_1up, abs=1e-9)


def test_v2_prematch_oneup_unchanged_vs_v1(balanced_match):
    """Score 0-0 → level-score branch — not touched by V2. p_home_1
    must equal V1's bit-for-bit."""
    r1 = ep_v1.price_early_payout_markets(**balanced_match)
    r2 = ep_v2.price_early_payout_markets(**balanced_match)
    assert r2["p_home_1"] == pytest.approx(r1["p_home_1"], abs=1e-12)
    assert r2["p_away_1"] == pytest.approx(r1["p_away_1"], abs=1e-12)
