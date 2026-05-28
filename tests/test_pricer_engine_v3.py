"""V3 engine tests. V3 is identical to V2 except the margin step: it uses
a logit-linear (level, tilt) odds-ratio model instead of V2's additive
slope*fair_prob + intercept. Every probability output must stay identical
to V2; only the fair/capped odds change."""

import pytest

from odds_scraper.pricer import engine_v2 as ep_v2
from odds_scraper.pricer import engine_v3 as ep_v3


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
        "home_ou": [_ou(0.5, 1.30, 3.40), _ou(1.5, 2.10, 1.75)],
        "away_ou": [_ou(0.5, 1.40, 3.00), _ou(1.5, 2.30, 1.65)],
        "total_ou": [_ou(1.5, 1.25, 4.00), _ou(2.5, 1.85, 1.95), _ou(3.5, 3.20, 1.35)],
        "ftts_home_prob": 0.48, "ftts_away_prob": 0.45,
    }


@pytest.fixture
def strong_home_favorite():
    """Heavy home favorite so the away 2UP prob lands below 0.10."""
    home_1x2, draw_1x2, away_1x2 = 1.18, 7.00, 15.0
    ph, pd, pa = _devig3(home_1x2, draw_1x2, away_1x2)
    return {
        "p_home_win": ph, "p_draw": pd, "p_away_win": pa,
        "home_1x2_odds": home_1x2, "draw_1x2_odds": draw_1x2, "away_1x2_odds": away_1x2,
        "home_ou": [_ou(1.5, 1.50, 2.50), _ou(2.5, 2.40, 1.55)],
        "away_ou": [_ou(0.5, 2.20, 1.65), _ou(1.5, 4.50, 1.18)],
        "total_ou": [_ou(2.5, 1.70, 2.10), _ou(3.5, 2.80, 1.42)],
        "ftts_home_prob": 0.78, "ftts_away_prob": 0.18,
    }


def test_v3_probabilities_identical_to_v2(balanced_match):
    """V3 changes ONLY the margin step — every probability output and the
    lambdas must equal V2 exactly."""
    r2 = ep_v2.price_early_payout_markets(**balanced_match)
    r3 = ep_v3.price_early_payout_markets(**balanced_match)
    for key in ("lambda_home", "lambda_away",
                "p_home_1", "p_away_1", "p_home_2", "p_away_2"):
        assert r3[key] == pytest.approx(r2[key]), f"{key} differs (V2={r2[key]} V3={r3[key]})"


def test_v3_fair_prob_to_odds_always_valid():
    """sigmoid implied prob is always in (0,1) → odds always > 1.0, never
    None, for any p in (0,1) and any (level, tilt)."""
    for p in (1e-6, 1e-4, 0.01, 0.05, 0.2, 0.5, 0.8, 0.95, 0.99, 0.9999, 1 - 1e-7):
        for level, tilt in ((0.1324, 0.9922), (0.0352, 1.0030), (0.0, 1.0), (-0.2, 1.2)):
            odds = ep_v3._fair_prob_to_odds(p, level, tilt)
            assert odds is not None, f"None at p={p} level={level} tilt={tilt}"
            assert odds > 1.0, f"odds={odds} <= 1.0 at p={p} level={level} tilt={tilt}"


def test_v3_super_favorite_margin_never_overflows():
    """V3's margin step never produces implied_prob >= 1.0 for a super-fav
    (sigmoid is bounded) → odds stay > 1.0, no overflow/floor. V2's additive
    1UP margin DOES overflow past 1.0 at the same high p — the bug V3 fixes."""
    for p in (0.951, 0.96, 0.97, 0.98, 0.99, 0.999):
        odds_v3 = ep_v3._fair_prob_to_odds(p, ep_v3.ONEUP_MARGIN_LEVEL, ep_v3.ONEUP_MARGIN_TILT)
        implied_v3 = 1.0 / odds_v3
        assert implied_v3 < 1.0, f"V3 overflow at p={p}: implied={implied_v3}"
    # Contrast: V2's additive 1UP level margin overflows at p=0.99.
    v2_implied = ep_v2.ONEUP_FAVORITE_MARGIN[0] * 0.99 + ep_v2.ONEUP_FAVORITE_MARGIN[1]
    assert v2_implied >= 1.0, "expected V2 additive margin to overflow at p=0.99"


def test_v3_midrange_odds_preserved_vs_v2(balanced_match):
    """For mid-range probs (0.25<=p<=0.75) V3 fair odds stay within ~1.5%
    of V2 — the (level, tilt) params were fit to preserve V2 there."""
    r2 = ep_v2.price_early_payout_markets(**balanced_match)
    r3 = ep_v3.price_early_payout_markets(**balanced_match)
    checked = 0
    for market, idx in (("market_1up", "1"), ("market_2up", "2")):
        for side, prob_key in (("home_fair", "p_home_"), ("away_fair", "p_away_")):
            prob = r2[prob_key + idx]
            if prob is None or not (0.25 <= prob <= 0.75):
                continue
            v2_fair, v3_fair = r2[market][side], r3[market][side]
            if v2_fair is None or v3_fair is None:
                continue
            rel = abs(v3_fair - v2_fair) / v2_fair
            assert rel <= 0.015, (
                f"{market}.{side} p={prob:.3f}: V2={v2_fair:.4f} V3={v3_fair:.4f} rel={rel:.3%}"
            )
            checked += 1
    assert checked > 0, "no mid-range selection exercised by the fixture"


def test_v3_reduction_pct_defaults():
    """V3's cap reductions are odds-space PERCENTS, per market per side:
    1UP fav/dog 2.0%, 2UP fav 2.0% / dog 0.5%."""
    assert ep_v3.ONEUP_FAVORITE_REDUCTION_PCT == 2.0
    assert ep_v3.ONEUP_UNDERDOG_REDUCTION_PCT == 2.0
    assert ep_v3.TWOUP_FAVORITE_REDUCTION_PCT == 2.0
    assert ep_v3.TWOUP_UNDERDOG_REDUCTION_PCT == 0.5


def test_v3_oneup_per_side_reduction_pct_wiring(monkeypatch, strong_home_favorite):
    """The favorite 1UP side caps with ONEUP_FAVORITE_REDUCTION_PCT and the
    underdog side with ONEUP_UNDERDOG_REDUCTION_PCT (mirroring 2UP). Spy on
    _cap_selection to assert the per-side reduction wiring."""
    monkeypatch.setattr(ep_v3, "ONEUP_FAVORITE_REDUCTION_PCT", 1.11)
    monkeypatch.setattr(ep_v3, "ONEUP_UNDERDOG_REDUCTION_PCT", 2.22)
    calls = []
    real_cap = ep_v3._cap_selection
    monkeypatch.setattr(
        ep_v3, "_cap_selection",
        lambda so, sp, src, red: (calls.append(red), real_cap(so, sp, src, red))[1],
    )
    ep_v3.price_early_payout_markets(**strong_home_favorite)
    # Code computes 1UP (home, away) before 2UP (home, away). Home is the
    # favorite in this fixture.
    assert calls[0] == pytest.approx(1.11)  # 1UP favorite (home) reduction %
    assert calls[1] == pytest.approx(2.22)  # 1UP underdog (away) reduction %


def test_v3_cap_selection_odds_space_upside_only():
    """The V3 cap is odds-space and UPSIDE-ONLY: an UP odd longer than the
    1X2 ceiling (source * (1 - pct/100)) is pulled down to it (prob bumped
    to 1/ceiling); an UP odd already shorter is returned untouched; with no
    source we only floor to the minimum offered odds."""
    # binds: 5.0 > ceiling 4.0*0.995=3.98 -> capped to 3.98
    odds, prob = ep_v3._cap_selection(5.0, 0.18, 4.0, 0.5)
    assert odds == pytest.approx(3.98)
    assert prob == pytest.approx(1.0 / 3.98)
    # already shorter than the ceiling -> untouched (no lengthening, no prob change)
    odds, prob = ep_v3._cap_selection(3.0, 0.30, 4.0, 0.5)
    assert odds == pytest.approx(3.0)
    assert prob == pytest.approx(0.30)
    # no 1X2 source -> floor only, prob unchanged
    odds, prob = ep_v3._cap_selection(5.0, 0.18, None, 0.5)
    assert odds == pytest.approx(5.0)
    assert prob == pytest.approx(0.18)
    # None synthetic passes straight through
    assert ep_v3._cap_selection(None, None, 4.0, 0.5) == (None, None)
    # below the offered-odds floor with no source -> floored to 1.01
    odds, _ = ep_v3._cap_selection(1.005, 0.99, None, 0.5)
    assert odds == pytest.approx(ep_v3.CAP_MIN_OFFERED_ODDS)


def test_v3_cap_upside_only_on_real_path(strong_home_favorite):
    """On the full pricing path every offered UP odd equals
    min(fair_floored, 1X2_ceiling): longer-than-ceiling sides are capped to
    the ceiling, shorter ones pass through unchanged. At least one side must
    actually bind (so the cap, not just the floor, is exercised)."""
    r = ep_v3.price_early_payout_markets(**strong_home_favorite)
    f = strong_home_favorite  # home is the favorite
    checks = [
        ("market_1up", "home_fair", "home_margin", f["home_1x2_odds"], ep_v3.ONEUP_FAVORITE_REDUCTION_PCT),
        ("market_1up", "away_fair", "away_margin", f["away_1x2_odds"], ep_v3.ONEUP_UNDERDOG_REDUCTION_PCT),
        ("market_2up", "home_fair", "home_margin", f["home_1x2_odds"], ep_v3.TWOUP_FAVORITE_REDUCTION_PCT),
        ("market_2up", "away_fair", "away_margin", f["away_1x2_odds"], ep_v3.TWOUP_UNDERDOG_REDUCTION_PCT),
    ]
    bound = 0
    for mk, fair_k, marg_k, src, pct in checks:
        fair, offered = r[mk][fair_k], r[mk][marg_k]
        if fair is None or offered is None:
            continue
        floored = max(ep_v3.CAP_MIN_OFFERED_ODDS, fair)
        ceiling = max(ep_v3.CAP_MIN_OFFERED_ODDS, src * (1.0 - pct / 100.0))
        expected = floored if floored <= ceiling else ceiling
        assert offered == pytest.approx(expected), f"{mk}.{marg_k}"
        if floored > ceiling:
            bound += 1
    assert bound > 0, "expected at least one capped (bound) side"


def test_v3_apply_boost_helper():
    """Favorite/underdog odds boost: lengthens the chosen side's odds by
    a %, no-op near-even or on None."""
    # Favorite gets the fav pct.
    assert ep_v3._apply_boost(2.0, True, False, 10.0, 0.0) == pytest.approx(2.2)
    # Underdog gets the dog pct, not the fav pct.
    assert ep_v3._apply_boost(2.0, False, False, 10.0, 0.0) == pytest.approx(2.0)
    assert ep_v3._apply_boost(2.0, False, False, 10.0, 5.0) == pytest.approx(2.1)
    # Near-even → skipped entirely.
    assert ep_v3._apply_boost(2.0, True, True, 10.0, 0.0) == pytest.approx(2.0)
    # None passes through.
    assert ep_v3._apply_boost(None, True, False, 10.0, 0.0) is None


def test_v3_twoup_favorite_odds_boost(monkeypatch, strong_home_favorite):
    """TWOUP_FAVORITE_ODDS_BOOST_PCT lengthens the favorite side's 2UP fair
    odds by that % (pre-cap); the underdog side is untouched."""
    base = ep_v3.price_early_payout_markets(**strong_home_favorite)
    monkeypatch.setattr(ep_v3, "TWOUP_FAVORITE_ODDS_BOOST_PCT", 8.0)
    boosted = ep_v3.price_early_payout_markets(**strong_home_favorite)
    # Home is the favorite in this fixture → its 2UP fair odds get +8%.
    assert boosted["market_2up"]["home_fair"] == pytest.approx(
        base["market_2up"]["home_fair"] * 1.08, rel=1e-6
    )
    # Underdog (away) 2UP fair odds unchanged.
    assert boosted["market_2up"]["away_fair"] == pytest.approx(
        base["market_2up"]["away_fair"], rel=1e-9
    )


def test_v3_odds_boost_skipped_near_even(monkeypatch, balanced_match):
    """A near-even match (|p_home - p_away| < NEAR_EVEN_THRESHOLD) gets no
    boost on either side even when the pct is set."""
    base = ep_v3.price_early_payout_markets(**balanced_match)
    monkeypatch.setattr(ep_v3, "TWOUP_FAVORITE_ODDS_BOOST_PCT", 20.0)
    monkeypatch.setattr(ep_v3, "TWOUP_UNDERDOG_ODDS_BOOST_PCT", 20.0)
    # Force near-even regardless of fixture devig.
    monkeypatch.setattr(ep_v3, "NEAR_EVEN_THRESHOLD", 1.0)
    boosted = ep_v3.price_early_payout_markets(**balanced_match)
    assert boosted["market_2up"]["home_fair"] == pytest.approx(base["market_2up"]["home_fair"])
    assert boosted["market_2up"]["away_fair"] == pytest.approx(base["market_2up"]["away_fair"])


def test_v3_low_prob_less_crush_than_v2(strong_home_favorite):
    """For low-prob selections (p<0.10) V3 fair odds >= V2 fair odds — V2's
    fixed intercept is a huge relative margin at the tail and crushes the
    odds; V3's logit margin doesn't."""
    r2 = ep_v2.price_early_payout_markets(**strong_home_favorite)
    r3 = ep_v3.price_early_payout_markets(**strong_home_favorite)
    checked = 0
    for market, idx in (("market_1up", "1"), ("market_2up", "2")):
        for side, prob_key in (("home_fair", "p_home_"), ("away_fair", "p_away_")):
            prob = r2[prob_key + idx]
            if prob is None or prob >= 0.10:
                continue
            v2_fair, v3_fair = r2[market][side], r3[market][side]
            if v2_fair is None or v3_fair is None:
                continue
            assert v3_fair >= v2_fair - 1e-9, (
                f"{market}.{side} p={prob:.3f}: V3={v3_fair:.3f} < V2={v2_fair:.3f}"
            )
            checked += 1
    assert checked > 0, "no low-prob selection exercised by the fixture"
