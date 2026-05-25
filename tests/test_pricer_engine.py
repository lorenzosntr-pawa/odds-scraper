"""Tests for the Java-derived engine_prod_v1 pricer."""

import math
import pytest

from odds_scraper.pricer import engine as ep


def _naive_devig_two(over_odds, under_odds):
    """Naive two-way devig for fixture setup only."""
    q_over = 1.0 / over_odds
    q_under = 1.0 / under_odds
    return q_over / (q_over + q_under)


def _naive_devig_three(o1, o2, o3):
    """Naive three-way devig for fixture setup only."""
    q1, q2, q3 = 1.0 / o1, 1.0 / o2, 1.0 / o3
    s = q1 + q2 + q3
    return q1 / s, q2 / s, q3 / s


def _ou_prob(line, over_odds, under_odds):
    """Convert (line, over_odds, under_odds) to (line, over_prob) via naive devig."""
    return (line, _naive_devig_two(over_odds, under_odds))


@pytest.fixture
def balanced_match():
    """A roughly balanced match with multiple O/U lines available + FTTS so 1UP is active."""
    home_1x2_odds, draw_1x2_odds, away_1x2_odds = 2.50, 3.30, 2.80
    p_home_win, p_draw, p_away_win = _naive_devig_three(home_1x2_odds, draw_1x2_odds, away_1x2_odds)
    return {
        "p_home_win": p_home_win,
        "p_draw": p_draw,
        "p_away_win": p_away_win,
        "home_1x2_odds": home_1x2_odds,
        "draw_1x2_odds": draw_1x2_odds,
        "away_1x2_odds": away_1x2_odds,
        "home_ou": [_ou_prob(0.5, 1.30, 3.40), _ou_prob(1.5, 2.10, 1.75)],
        "away_ou": [_ou_prob(0.5, 1.40, 3.00), _ou_prob(1.5, 2.30, 1.65)],
        "total_ou": [_ou_prob(1.5, 1.25, 4.00), _ou_prob(2.5, 1.85, 1.95),
                     _ou_prob(3.5, 3.20, 1.35)],
        "ftts_home_prob": 0.48,
        "ftts_away_prob": 0.45,
    }


@pytest.fixture
def strong_home_favorite():
    home_1x2_odds, draw_1x2_odds, away_1x2_odds = 1.40, 4.50, 7.00
    p_home_win, p_draw, p_away_win = _naive_devig_three(home_1x2_odds, draw_1x2_odds, away_1x2_odds)
    return {
        "p_home_win": p_home_win,
        "p_draw": p_draw,
        "p_away_win": p_away_win,
        "home_1x2_odds": home_1x2_odds,
        "draw_1x2_odds": draw_1x2_odds,
        "away_1x2_odds": away_1x2_odds,
        "home_ou": [_ou_prob(0.5, 1.15, 5.50), _ou_prob(1.5, 1.60, 2.30), _ou_prob(2.5, 2.90, 1.40)],
        "away_ou": [_ou_prob(0.5, 1.80, 2.00), _ou_prob(1.5, 3.50, 1.30)],
        "total_ou": [_ou_prob(2.5, 1.70, 2.15), _ou_prob(3.5, 2.50, 1.55)],
        "ftts_home_prob": 0.70,
        "ftts_away_prob": 0.25,
    }


def test_devig_three_way_sums_to_one():
    p1, p2, p3 = ep.devig_three_way(2.1, 3.2, 3.5)
    assert p1 + p2 + p3 == pytest.approx(1.0)


def test_lambda_pair_balanced(balanced_match):
    lam_h, lam_a = ep.derive_lambda_pair(
        balanced_match["home_ou"], balanced_match["away_ou"], balanced_match["total_ou"]
    )
    assert lam_h is not None and lam_a is not None
    assert 0.5 < lam_h < 3.0
    assert 0.5 < lam_a < 3.0


def test_lambda_pair_reconciles_against_total(balanced_match):
    lam_h, lam_a = ep.derive_lambda_pair(
        balanced_match["home_ou"], balanced_match["away_ou"], balanced_match["total_ou"]
    )
    # Match-total list also yields its own lambda; reconciliation either keeps
    # (if close enough) or scales. Either way, the sum should be plausible.
    assert 1.5 < (lam_h + lam_a) < 4.5


def test_lambda_pair_returns_nones_when_no_data():
    h, a = ep.derive_lambda_pair([], [], [])
    assert h is None and a is None


def test_price_returns_full_dict(balanced_match):
    r = ep.price_early_payout_markets(**balanced_match)
    assert r["lambda_home"] is not None
    assert r["market_1up"]["home_margin"] is not None
    assert r["market_2up"]["home_margin"] is not None
    assert r["market_1up"]["draw"] == balanced_match["draw_1x2_odds"]
    assert r["market_2up"]["draw"] == balanced_match["draw_1x2_odds"]


def test_strong_favorite_has_lower_1up_odds_than_underdog(strong_home_favorite):
    r = ep.price_early_payout_markets(**strong_home_favorite)
    assert r["market_1up"]["home_margin"] < r["market_1up"]["away_margin"]
    assert r["market_2up"]["home_margin"] < r["market_2up"]["away_margin"]


def test_capping_enforces_probability_gap(strong_home_favorite):
    """Our 1UP odds must be at most 1 / (source_implied + scaledGap), per
    the new SelectionCapping logic."""
    r = ep.price_early_payout_markets(**strong_home_favorite)
    for side in ("home", "away"):
        source_odds = strong_home_favorite[f"{side}_1x2_odds"]
        gap = ep._scaled_probability_gap(source_odds, ep.ONEUP_MIN_GUARANTEED_REDUCTION)
        target = min(ep.CAP_MAX_IMPLIED_PROB, 1.0 / source_odds + gap)
        max_allowed = max(ep.CAP_MIN_OFFERED_ODDS, 1.0 / target)
        assert r["market_1up"][f"{side}_margin"] <= max_allowed + 1e-9


def test_ever_2up_returns_probabilities_in_range():
    p_h_ever, p_a_ever, p_h_ever_wins, p_a_ever_wins = ep.ever_2up_probability(1.5, 1.0, 0)
    for v in (p_h_ever, p_a_ever, p_h_ever_wins, p_a_ever_wins):
        assert 0.0 <= v <= 1.0
    # Joint <= marginal
    assert p_h_ever_wins <= p_h_ever + 1e-12
    assert p_a_ever_wins <= p_a_ever + 1e-12


def test_ever_2up_higher_for_stronger_team():
    p_h_ever_strong, _, _, _ = ep.ever_2up_probability(2.5, 0.8, 0)
    p_h_ever_weak, _, _, _ = ep.ever_2up_probability(0.8, 2.5, 0)
    assert p_h_ever_strong > p_h_ever_weak


def test_2up_uses_per_side_probability_gap(strong_home_favorite):
    """Per Java SelectionCapping, 2UP home (favorite) uses favoriteGap (0.03);
    away (underdog) uses underdogGap (0.05). Both follow the source-odds-scaled
    probability-gap formula."""
    r = ep.price_early_payout_markets(**strong_home_favorite)
    for side, gap_const in (
        ("home", ep.TWOUP_FAVORITE_MIN_GUARANTEED_REDUCTION),
        ("away", ep.TWOUP_UNDERDOG_MIN_GUARANTEED_REDUCTION),
    ):
        source_odds = strong_home_favorite[f"{side}_1x2_odds"]
        gap = ep._scaled_probability_gap(source_odds, gap_const)
        target = min(ep.CAP_MAX_IMPLIED_PROB, 1.0 / source_odds + gap)
        max_allowed = max(ep.CAP_MIN_OFFERED_ODDS, 1.0 / target)
        assert r["market_2up"][f"{side}_margin"] <= max_allowed + 1e-9


def test_2up_residual_only_uses_ever_above_wins(strong_home_favorite):
    """Sanity: the home 2UP prob is strictly above home_win_prob (gets a boost),
    but bounded above by home_win_prob + p_home_ever * fav_coeff."""
    r = ep.price_early_payout_markets(**strong_home_favorite)
    p_home_win = r["p_home_win"]
    p_home_2up = r["p_home_2"]
    p_h_ever, _, p_h_ever_wins, _ = ep.ever_2up_probability(
        r["lambda_home"], r["lambda_away"], 0
    )
    residual = max(0.0, p_h_ever - p_h_ever_wins)
    upper = p_home_win + residual * ep.TWOUP_FAVORITE_BOOST_COEFFICIENT + 1e-9
    assert p_home_win <= p_home_2up <= upper


def test_scaled_gap_zero_below_1_10():
    assert ep._scaled_probability_gap(1.05, 0.05) == 0.0
    assert ep._scaled_probability_gap(1.10, 0.05) == 0.0


def test_scaled_gap_full_above_2_00_when_relative_limit_not_binding():
    # source 2.00 → source_prob=0.5; relative limit = 0.05 (10% of 0.5). Absolute
    # gap 0.05 == relative limit, so the absolute branch returns 0.05.
    assert ep._scaled_probability_gap(2.00, 0.05) == pytest.approx(0.05, abs=1e-12)


def test_scaled_gap_capped_by_relative_limit_for_extreme_dog():
    """For source 8.0 (source_prob = 0.125), absolute gap 0.05 would multiply
    implied prob by 0.175/0.125 ≈ 1.4. The relative limit caps gap to
    0.125 × 0.10 = 0.0125."""
    gap = ep._scaled_probability_gap(8.0, 0.05)
    assert gap == pytest.approx(0.0125, abs=1e-12)


def test_scaled_gap_linear_between():
    # midpoint between 1.10 and 2.00 → half absolute gap
    # source 1.55 → source_prob=0.645; relative limit = 0.0645. Absolute = 0.025.
    # Absolute < relative limit so absolute wins.
    assert ep._scaled_probability_gap(1.55, 0.05) == pytest.approx(0.025, abs=1e-12)


def test_cap_floors_odds_at_1_01():
    """If synthetic odds < 1.01 and source absent, output is floored to 1.01."""
    odds, prob = ep._cap_selection(synthetic_odds=0.5, synthetic_prob=0.99,
                                   source_odds=None, source_true_prob=None,
                                   min_probability_gap=0.05)
    assert odds == ep.CAP_MIN_OFFERED_ODDS


def test_cap_no_gap_for_super_short_favorite():
    """When source_odds ≤ 1.10, no probability gap is applied — the cap just
    enforces the source-odds ceiling (no sub-source-odds is required)."""
    # Source odds 1.05 (super-short fav), synthetic 1.06 — should pass through
    # because scaled gap is 0 so max_allowed = 1/(1/1.05 + 0) = 1.05.
    odds, _ = ep._cap_selection(synthetic_odds=1.06, synthetic_prob=0.90,
                                source_odds=1.05, source_true_prob=0.95,
                                min_probability_gap=0.05)
    assert odds == pytest.approx(1.05, abs=1e-9)


def test_ever_2up_initial_diff_already_two_marks_flag():
    # When initial diff = +2, p_home_ever should be 1.0 from t=0.
    p_h_ever, p_a_ever, _, _ = ep.ever_2up_probability(1.0, 1.0, 2)
    assert p_h_ever == pytest.approx(1.0, abs=1e-12)
    # Away can still ever lead by 2 if they score 4 unanswered, etc.
    assert 0.0 <= p_a_ever <= 1.0


def test_no_ou_data_returns_none_lambdas(balanced_match):
    bm = dict(balanced_match)
    bm["home_ou"] = []
    bm["away_ou"] = []
    bm["total_ou"] = []
    r = ep.price_early_payout_markets(**bm)
    assert r["lambda_home"] is None
    assert r["market_1up"]["home_margin"] is None


def test_lambda_clamps_at_max():
    """When over-prob cannot be matched even at LAMBDA_MAX, return LAMBDA_MAX."""
    # P(N > 4.5) = P(N >= 5) under Poisson(10) ~ 0.9707. Even at lambda=10,
    # P(N > 4.5) < 0.9999, so a target of 0.9999 saturates at LAMBDA_MAX.
    lam = ep._lambda_from_over_prob(0.9999, 4.5)
    assert lam == pytest.approx(ep.LAMBDA_MAX, abs=0.01)


def test_devig_two_way_basic():
    p = ep.devig_two_way(2.0, 2.0)
    assert p == pytest.approx(0.5)


# ---- Live-score (trailing-team) tests ----

def test_score_home_leads_1up_deactivates_home_activates_away(balanced_match):
    """Home leads 1-0 → home 1UP is DEACTIVATED (already triggered); away 1UP uses trailing."""
    r = ep.price_early_payout_markets(**{**balanced_match, "score": (1, 0)})
    assert r["market_1up"]["home_margin"] is None
    # Away 1UP must be a valid price (trailing path doesn't need FTTS)
    assert r["market_1up"]["away_margin"] is not None
    assert r["market_1up"]["away_margin"] > 0


def test_score_away_leads_2up_deactivates_away_if_two_or_more(balanced_match):
    """Away leads 0-2 → away 2UP is DEACTIVATED (event triggered); home 2UP via trailing."""
    r = ep.price_early_payout_markets(**{**balanced_match, "score": (0, 2)})
    assert r["market_2up"]["away_margin"] is None
    assert r["market_2up"]["home_margin"] is not None


def test_score_one_goal_lead_2up_uses_level_branch(balanced_match):
    """|diff| < 2 → 2UP uses level/one-goal logic, both sides active."""
    r = ep.price_early_payout_markets(**{**balanced_match, "score": (1, 0)})
    assert r["market_2up"]["home_margin"] is not None
    assert r["market_2up"]["away_margin"] is not None


def test_score_zero_zero_unchanged_from_prematch(balanced_match):
    """score=(0,0) default behaviour stays unchanged."""
    r_default = ep.price_early_payout_markets(**balanced_match)
    r_explicit = ep.price_early_payout_markets(**{**balanced_match, "score": (0, 0)})
    assert r_default["market_1up"]["home_margin"] == r_explicit["market_1up"]["home_margin"]
    assert r_default["market_2up"]["home_margin"] == r_explicit["market_2up"]["home_margin"]


def test_trailing_doesnt_need_ftts(balanced_match):
    """When the score is non-zero, the trailing-team 1UP path does NOT need FTTS."""
    bm = dict(balanced_match)
    bm.pop("ftts_home_prob")
    bm.pop("ftts_away_prob")
    bm["score"] = (1, 0)
    r = ep.price_early_payout_markets(**bm)
    # Away 1UP via trailing should still be priced (no FTTS dependency on this path)
    assert r["market_1up"]["away_margin"] is not None


def test_poisson_at_least_basic():
    # P(N >= 1) under Poisson(2.5) = 1 - exp(-2.5) ≈ 0.9179
    p = ep._poisson_at_least(2.5, 1)
    assert p == pytest.approx(1.0 - math.exp(-2.5), rel=1e-9)


def test_trailing_selection_zero_lambda_returns_none():
    o, p = ep._trailing_selection(2.0, 0.5, 0.0, 1, 0.05, 0.25)
    assert o is None and p is None


# ---- Max-lead deactivation (history-aware 1UP/2UP) ----

def test_max_home_lead_1_deactivates_home_1up_at_level_score(balanced_match):
    """A match that went 0-0 → 1-0 → 1-1 has home 1UP already triggered.
    At current score (1,1) the engine would otherwise re-price home 1UP via
    the level-score branch; passing max_home_lead=1 must keep it None."""
    r = ep.price_early_payout_markets(
        **balanced_match, score=(1, 1), max_home_lead=1,
    )
    assert r["market_1up"]["home_margin"] is None
    assert r["market_1up"]["home_fair"] is None
    assert r["p_home_1"] is None
    # Away 1UP must still be priced — only home has settled.
    assert r["market_1up"]["away_margin"] is not None


def test_max_lead_both_deactivates_both_1up_at_level_score(balanced_match):
    """Match went 0-0 → 1-0 → 1-1 → 1-2 → 2-2. Both 1UPs triggered."""
    r = ep.price_early_payout_markets(
        **balanced_match, score=(2, 2),
        max_home_lead=1, max_away_lead=1,
    )
    assert r["market_1up"]["home_margin"] is None
    assert r["market_1up"]["away_margin"] is None


def test_max_home_lead_2_deactivates_home_2up(balanced_match):
    """If home ever led by 2 (e.g. went 2-0 then conceded to 2-2),
    home 2UP must stay None even at a level score."""
    r = ep.price_early_payout_markets(
        **balanced_match, score=(2, 2),
        max_home_lead=2, max_away_lead=0,
    )
    assert r["market_2up"]["home_margin"] is None
    # Away 2UP still active (away never led by 2)
    assert r["market_2up"]["away_margin"] is not None


def test_max_lead_defaults_preserve_prior_behavior(balanced_match):
    """Default max_home_lead=0/max_away_lead=0 must match the pre-existing
    prematch result — no silent regression for callers that don't pass them."""
    r_old = ep.price_early_payout_markets(**balanced_match)
    r_new = ep.price_early_payout_markets(
        **balanced_match, max_home_lead=0, max_away_lead=0,
    )
    assert r_old["market_1up"]["home_margin"] == r_new["market_1up"]["home_margin"]
    assert r_old["market_2up"]["home_margin"] == r_new["market_2up"]["home_margin"]


def test_oneup_margin_blend_off_uses_favorite_margin_directly(balanced_match):
    """With the 1UP margin blend disabled, the favorite side must take
    `ONEUP_FAVORITE_MARGIN` exactly (no mixing toward the dog margin)
    and the dog side takes `ONEUP_UNDERDOG_MARGIN` exactly. Uses a
    borderline-favorite fixture — `_favorite_strength` saturates to 1.0
    once either side reaches 50% so blending is already a no-op there."""
    saved = ep.ONEUP_MARGIN_BLEND_ENABLED
    try:
        ep.ONEUP_MARGIN_BLEND_ENABLED = False
        r_off = ep.price_early_payout_markets(**balanced_match)
        ep.ONEUP_MARGIN_BLEND_ENABLED = True
        r_on = ep.price_early_payout_markets(**balanced_match)
    finally:
        ep.ONEUP_MARGIN_BLEND_ENABLED = saved

    p_h_1 = r_off["p_home_1"]
    p_a_1 = r_off["p_away_1"]
    home_is_fav = balanced_match["p_home_win"] >= balanced_match["p_away_win"]
    home_margin = ep.ONEUP_FAVORITE_MARGIN if home_is_fav else ep.ONEUP_UNDERDOG_MARGIN
    away_margin = ep.ONEUP_UNDERDOG_MARGIN if home_is_fav else ep.ONEUP_FAVORITE_MARGIN
    expected_home_fair = ep._fair_prob_to_odds(p_h_1, home_margin)
    expected_away_fair = ep._fair_prob_to_odds(p_a_1, away_margin)

    assert r_off["market_1up"]["home_fair"] == pytest.approx(expected_home_fair, abs=1e-9)
    assert r_off["market_1up"]["away_fair"] == pytest.approx(expected_away_fair, abs=1e-9)
    assert r_off["market_1up"]["home_fair"] != pytest.approx(r_on["market_1up"]["home_fair"])


def test_twoup_margin_blend_off_uses_favorite_margin_directly(balanced_match):
    saved = ep.TWOUP_MARGIN_BLEND_ENABLED
    try:
        ep.TWOUP_MARGIN_BLEND_ENABLED = False
        r_off = ep.price_early_payout_markets(**balanced_match)
        ep.TWOUP_MARGIN_BLEND_ENABLED = True
        r_on = ep.price_early_payout_markets(**balanced_match)
    finally:
        ep.TWOUP_MARGIN_BLEND_ENABLED = saved

    p_h_2 = r_off["p_home_2"]
    home_is_fav = balanced_match["p_home_win"] >= balanced_match["p_away_win"]
    home_margin = ep.TWOUP_FAVORITE_MARGIN if home_is_fav else ep.TWOUP_UNDERDOG_MARGIN
    expected_home_fair = ep._fair_prob_to_odds(p_h_2, home_margin)
    assert r_off["market_2up"]["home_fair"] == pytest.approx(expected_home_fair, abs=1e-9)
    assert r_off["market_2up"]["home_fair"] != pytest.approx(r_on["market_2up"]["home_fair"])


def test_twoup_boost_blend_off_uses_favorite_boost_directly(balanced_match):
    """With boost blend off, the favorite side's 2UP probability uses
    `TWOUP_FAVORITE_BOOST_COEFFICIENT` flat instead of a blended value
    — the on/off results must therefore differ."""
    saved = ep.TWOUP_BOOST_BLEND_ENABLED
    try:
        ep.TWOUP_BOOST_BLEND_ENABLED = False
        r_off = ep.price_early_payout_markets(**balanced_match)
        ep.TWOUP_BOOST_BLEND_ENABLED = True
        r_on = ep.price_early_payout_markets(**balanced_match)
    finally:
        ep.TWOUP_BOOST_BLEND_ENABLED = saved
    assert r_off["p_home_2"] != pytest.approx(r_on["p_home_2"])


def test_blend_flags_default_to_on_preserve_prior_behavior(strong_home_favorite):
    """Brand-new behavioural contract: defaults must keep the original
    blended math so legacy profile rows (with no flag fields) still
    produce identical results to the engine before this change."""
    assert ep.ONEUP_MARGIN_BLEND_ENABLED is True
    assert ep.TWOUP_MARGIN_BLEND_ENABLED is True
    assert ep.TWOUP_BOOST_BLEND_ENABLED is True


def test_max_lead_does_not_resurrect_active_side(balanced_match):
    """At score (1,0) with no prior history, home 1UP is None (current
    leader) and away 1UP is priced via trailing. Passing max_home_lead=1
    (consistent with current state) must not change those answers."""
    r_no = ep.price_early_payout_markets(**balanced_match, score=(1, 0))
    r_with = ep.price_early_payout_markets(
        **balanced_match, score=(1, 0), max_home_lead=1,
    )
    assert r_no["market_1up"]["home_margin"] == r_with["market_1up"]["home_margin"]
    assert r_no["market_1up"]["away_margin"] == r_with["market_1up"]["away_margin"]
