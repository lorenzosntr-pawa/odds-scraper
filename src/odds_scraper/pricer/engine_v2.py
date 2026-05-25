"""Pricer engine V2 — May 2026 Java rewrite ("Rebuild 1UP and 2UP
pricing model and cap mechanism", SourceSportradar commit 10351fd1).

V2 unifies the 1UP and 2UP DPs into a single ever_leads_probability
that tracks {ever ±1, ever ±2} together. The 1UP trailing branch now
uses inclusion-exclusion math identical in shape to 2UP, so the
invariant P(1UP) ≥ P(2UP) ⇒ 1UP_odds ≤ 2UP_odds holds by construction.

Two V1 constants — ONEUP_TRAILING_MIN_REDUCTION and
ONEUP_TRAILING_MAX_REDUCTION — survive in this module so that
profile.coefficients dicts produced by configs.py round-trip cleanly,
but V2's 1UP trailing path does not reference them. They are dormant.

Kept module-isolated from engine.py so that with_coefficients overrides
on one engine never cross-contaminate the other.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

# ---- Coefficients (from FeatureProperties.java defaults) ----
ONEUP_FAVORITE_MARGIN = (0.9969, 0.0313)
ONEUP_UNDERDOG_MARGIN = (0.9799, 0.0400)
ONEUP_FAVORITE_MODEL  = (-0.137308, 1.228176, 0.001221, 0.085310)  # (intercept, nextGoal, lambda, underdog)
ONEUP_UNDERDOG_MODEL  = (0.006276, 0.909535, -0.009967, 0.094182)
ONEUP_MIN_GUARANTEED_REDUCTION = 0.02
ONEUP_TRAILING_MIN_REDUCTION = 0.05
ONEUP_TRAILING_MAX_REDUCTION = 0.25

TWOUP_FAVORITE_MARGIN = (0.998, 0.010)
TWOUP_UNDERDOG_MARGIN = (0.994, 0.008)
TWOUP_FAVORITE_BOOST_COEFFICIENT = 0.9
TWOUP_UNDERDOG_BOOST_COEFFICIENT = 0.6
TWOUP_FAVORITE_MIN_GUARANTEED_REDUCTION = 0.02
TWOUP_UNDERDOG_MIN_GUARANTEED_REDUCTION = 0.005
TWOUP_TRAILING_MIN_REDUCTION = 0.05
TWOUP_TRAILING_MAX_REDUCTION = 0.25

TWOUP_DP_MAX_GOALS = 40
TWOUP_DP_NEGLIGIBLE_TAIL = 1e-12

# When enabled (default, matches Java behaviour), the favorite/underdog
# margin pair is blended by favorite_strength so a borderline favorite
# inherits some of the dog's wider margin. When disabled, the favorite
# side gets FAVORITE_MARGIN flat and the dog gets UNDERDOG_MARGIN flat —
# useful for profiles that want pure independent control of the two
# margins without any cross-coupling.
ONEUP_MARGIN_BLEND_ENABLED = True
TWOUP_MARGIN_BLEND_ENABLED = True
# Same toggle for the 2UP boost-coefficient blend.
TWOUP_BOOST_BLEND_ENABLED = True

# ---- Constants from LambdaCalculator.java ----
LAMBDA_TOLERANCE = 1e-6
LAMBDA_MAX = 10.0
LAMBDA_MIN_COMPLEMENT = 0.1
LAMBDA_TYPICAL = 2.5
LAMBDA_RECONCILIATION_THRESHOLD = 0.5


def devig_two_way(odds_yes: float, odds_no: float) -> float:
    """De-vigged P(Yes) for a 2-way market."""
    q_yes = 1.0 / odds_yes
    q_no = 1.0 / odds_no
    return q_yes / (q_yes + q_no)


def devig_three_way(o1: float, o2: float, o3: float) -> Tuple[float, float, float]:
    """De-vigged (p1, p2, p3) for a 3-way market."""
    q1, q2, q3 = 1.0 / o1, 1.0 / o2, 1.0 / o3
    s = q1 + q2 + q3
    return q1 / s, q2 / s, q3 / s


def _poisson_over_prob(lam: float, line: float) -> float:
    """P(N > line) under Poisson(lam). line must be half-line (X.5)."""
    if lam <= 0:
        return 0.0
    n_under = int(math.floor(line)) + 1  # number of CDF terms (k=0..floor(line))
    if n_under <= 0:
        return 1.0 - math.exp(-lam)
    cdf = 0.0
    exp_neg = math.exp(-lam)
    factorial = 1.0
    lambda_pow = 1.0
    for k in range(n_under):
        if k > 0:
            factorial *= k
            lambda_pow *= lam
        cdf += (lambda_pow * exp_neg) / factorial
    return 1.0 - cdf


def _lambda_from_over_prob(over_prob: float, line: float) -> Optional[float]:
    """Bisect to find lambda such that P(N > line) = over_prob."""
    if not (0.0 < over_prob < 1.0):
        return None

    def f(lam: float) -> float:
        return _poisson_over_prob(lam, line) - over_prob

    if f(LAMBDA_TOLERANCE) > 0:
        return None
    if f(LAMBDA_MAX) < 0:
        return LAMBDA_MAX
    low, high = LAMBDA_TOLERANCE, LAMBDA_MAX
    while high - low > LAMBDA_TOLERANCE:
        mid = (low + high) / 2.0
        if f(mid) < 0:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


def _derive_lambda_from_multiple_lines(lines: List[Tuple[float, float]]) -> Optional[float]:
    """
    lines: list of (line, over_prob) — over_prob is already the pre-devigged probability.
    Returns the weighted average of per-line lambdas. Weight = 1 / (1 + |lambda - 2.5|).
    Returns None if no lines yielded a valid lambda.
    """
    if not lines:
        return None
    weighted_sum = 0.0
    total_weight = 0.0
    for line, over_prob in lines:
        # Only half-lines (X.5) are valid per LambdaCalculator.java
        if abs((line * 2) - round(line * 2)) > 1e-9 or (round(line * 2) % 2 != 1):
            continue  # not a half-line
        if over_prob is None:
            continue
        lam = _lambda_from_over_prob(over_prob, line)
        if lam is not None and lam > 0:
            distance = abs(lam - LAMBDA_TYPICAL)
            weight = 1.0 / (1.0 + distance)
            weighted_sum += lam * weight
            total_weight += weight
    if total_weight == 0:
        return None
    return weighted_sum / total_weight


def derive_lambda_pair(
    home_ou: List[Tuple[float, float]],
    away_ou: List[Tuple[float, float]],
    total_ou: List[Tuple[float, float]],
) -> Tuple[Optional[float], Optional[float]]:
    """Port of LambdaCalculator.deriveLambdasFromOuMarkets."""
    home = _derive_lambda_from_multiple_lines(home_ou)
    away = _derive_lambda_from_multiple_lines(away_ou)
    total = _derive_lambda_from_multiple_lines(total_ou)

    if total is None:
        return home, away

    if home is None and away is None:
        half = total / 2.0
        return half, half

    if home is None:
        return max(LAMBDA_MIN_COMPLEMENT, total - (away or 0.0)), away

    if away is None:
        return home, max(LAMBDA_MIN_COMPLEMENT, total - (home or 0.0))

    # Both present — reconcile
    s = home + away
    if abs(s - total) <= LAMBDA_RECONCILIATION_THRESHOLD:
        return home, away
    if s > 0:
        scale = total / s
        return home * scale, away * scale
    half = total / 2.0
    return half, half


def _apply_model(coeffs: Tuple[float, float, float, float],
                 ftts: float, lambda_fav: float, lambda_dog: float) -> float:
    intercept, next_goal, lam, dog = coeffs
    raw = intercept + next_goal * ftts + lam * lambda_fav + dog * lambda_dog
    # Match Java ThreeWay1UPCalculatorImpl.applyModel: clamp to [0, 1] so
    # extreme inputs (lambda far above training distribution) can't exit
    # the valid probability range.
    return max(0.0, min(1.0, raw))


def _blend_margins(strength: float, fav: Tuple[float, float], dog: Tuple[float, float]) -> Tuple[float, float]:
    return (strength * fav[0] + (1 - strength) * dog[0],
            strength * fav[1] + (1 - strength) * dog[1])


def _blend_boost(strength: float, fav_coeff: float, dog_coeff: float) -> float:
    return strength * fav_coeff + (1.0 - strength) * dog_coeff


def _fair_prob_to_odds(fair_prob: float, margin: Tuple[float, float]) -> Optional[float]:
    slope, intercept = margin
    implied = slope * fair_prob + intercept
    if implied <= 0:
        return None
    return 1.0 / implied


def _favorite_strength(p_home: float, p_away: float) -> float:
    fav = max(p_home, p_away)
    if fav >= 0.5:
        return 1.0
    s = p_home + p_away
    if s <= 0:
        return 0.0
    return abs(p_home - p_away) / s


CAP_MIN_OFFERED_ODDS = 1.01
CAP_MAX_IMPLIED_PROB = 1.0 / CAP_MIN_OFFERED_ODDS  # ≈ 0.9901
CAP_SCALE_LOWER = 1.10
CAP_SCALE_UPPER = 2.00
CAP_RELATIVE_GAP_LIMIT = 0.10  # gap never exceeds 10% of source_prob


def _scaled_probability_gap(source_odds: float, configured_gap: float) -> float:
    """Port of SelectionCapping.scaledProbabilityGap.
    Scales the configured gap by source-odds range:
      source ≤ 1.10 → 0 gap (no cap on super-short favorites)
      source ≥ 2.00 → full configured gap
      otherwise     → linear interpolation
    Then bounds the gap to at most RELATIVE_GAP_LIMIT × source_prob so that
    extreme dogs (e.g. source 8.0) don't get capped so tight that the odds
    blow up (this safeguard was added with the SelectionCapping update).
    """
    if source_odds <= CAP_SCALE_LOWER:
        return 0.0
    source_prob = 1.0 / source_odds
    if source_odds >= CAP_SCALE_UPPER:
        absolute_gap = configured_gap
    else:
        absolute_gap = configured_gap * (source_odds - CAP_SCALE_LOWER) / (CAP_SCALE_UPPER - CAP_SCALE_LOWER)
    return min(absolute_gap, source_prob * CAP_RELATIVE_GAP_LIMIT)


def _cap_selection(synthetic_odds, synthetic_prob, source_odds, source_true_prob, min_probability_gap):
    """Port of SelectionCapping.capSelection.

    Caps the synthetic odds so the implied prob is at least `source_implied_prob +
    scaledGap`, never below offered odds 1.01. When the cap binds, the output
    probability is bumped to `source_true_prob + scaledGap` (capped at 0.9901).
    """
    if synthetic_odds is None:
        return synthetic_odds, synthetic_prob

    floored_odds = max(CAP_MIN_OFFERED_ODDS, synthetic_odds)

    if source_odds is None:
        return floored_odds, synthetic_prob

    source_implied_prob = 1.0 / source_odds
    scaled_gap = _scaled_probability_gap(source_odds, min_probability_gap)
    target_min_prob = min(CAP_MAX_IMPLIED_PROB, source_implied_prob + scaled_gap)
    max_allowed_odds = max(CAP_MIN_OFFERED_ODDS, 1.0 / target_min_prob)

    if floored_odds <= max_allowed_odds:
        return floored_odds, synthetic_prob

    if source_true_prob is not None:
        capped_prob = min(CAP_MAX_IMPLIED_PROB, source_true_prob + scaled_gap)
    else:
        capped_prob = synthetic_prob
    return max_allowed_odds, capped_prob


def _poisson_pmf(lam: float, k: int) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    log_prob = -lam + k * math.log(lam)
    for i in range(1, k + 1):
        log_prob -= math.log(i)
    return math.exp(log_prob)


def ever_2up_probability(lambda_h: float, lambda_a: float, initial_diff: int) -> Tuple[float, float, float, float]:
    """
    Port of Ever2UpProbability.compute.

    Random walk on score difference driven by Poisson goals:
      - N = total goals ~ Poisson(lambda_h + lambda_a)
      - each goal is home with prob p = lambda_h / (lambda_h + lambda_a)
      - diff +=1 (home) with prob p, diff -=1 (away) with prob 1-p

    DP tracks (current_diff, has_ever_hit_+2, has_ever_hit_-2) at each step.
    Returns (p_home_ever, p_away_ever, p_home_ever_and_wins, p_away_ever_and_wins).
    """
    if lambda_h <= 0 or lambda_a <= 0:
        return 0.0, 0.0, 0.0, 0.0

    lambda_total = lambda_h + lambda_a
    p = lambda_h / lambda_total

    d_extent = TWOUP_DP_MAX_GOALS + abs(initial_diff) + 2
    size = 2 * d_extent + 1
    offset = d_extent

    # state[d_idx][flag]: flag bit0 = hit_low (≤-2), bit1 = hit_high (≥+2)
    state = [[0.0] * 4 for _ in range(size)]
    init_flag = (2 if initial_diff >= 2 else 0) | (1 if initial_diff <= -2 else 0)
    state[initial_diff + offset][init_flag] = 1.0

    accum = [0.0, 0.0, 0.0, 0.0]  # pHomeEver, pAwayEver, pHomeEverAndWins, pAwayEverAndWins
    exp_neg = math.exp(-lambda_total)

    _ever_2up_accumulate(state, offset, exp_neg, accum)

    lambda_pow = 1.0
    factorial = 1.0
    for n in range(1, TWOUP_DP_MAX_GOALS + 1):
        lambda_pow *= lambda_total
        factorial *= n
        prob_n = (lambda_pow * exp_neg) / factorial

        state = _ever_2up_step(state, offset, p, size)
        _ever_2up_accumulate(state, offset, prob_n, accum)

        if prob_n < TWOUP_DP_NEGLIGIBLE_TAIL and n > lambda_total:
            break

    return accum[0], accum[1], accum[2], accum[3]


def _ever_2up_step(state, offset: int, p: float, size: int):
    nxt = [[0.0] * 4 for _ in range(size)]
    one_minus_p = 1.0 - p
    for d_idx in range(size):
        row = state[d_idx]
        for flag in range(4):
            prob = row[flag]
            if prob == 0.0:
                continue
            diff = d_idx - offset
            hit_high = (flag & 2) != 0
            hit_low = (flag & 1) != 0

            # Home scores
            new_diff_h = diff + 1
            new_idx_h = new_diff_h + offset
            if 0 <= new_idx_h < size:
                new_hit_high = hit_high or new_diff_h >= 2
                new_flag = (2 if new_hit_high else 0) | (1 if hit_low else 0)
                nxt[new_idx_h][new_flag] += p * prob

            # Away scores
            new_diff_a = diff - 1
            new_idx_a = new_diff_a + offset
            if 0 <= new_idx_a < size:
                new_hit_low = hit_low or new_diff_a <= -2
                new_flag = (2 if hit_high else 0) | (1 if new_hit_low else 0)
                nxt[new_idx_a][new_flag] += one_minus_p * prob
    return nxt


def _ever_2up_accumulate(state, offset: int, weight: float, accum) -> None:
    if weight == 0.0:
        return
    for d_idx in range(len(state)):
        row = state[d_idx]
        for flag in range(4):
            prob = row[flag]
            if prob == 0.0:
                continue
            weighted = prob * weight
            diff = d_idx - offset
            hit_high = (flag & 2) != 0
            hit_low = (flag & 1) != 0
            if hit_high:
                accum[0] += weighted
                if diff >= 1:
                    accum[2] += weighted
            if hit_low:
                accum[1] += weighted
                if diff <= -1:
                    accum[3] += weighted


def _poisson_at_least(lam: float, k: int) -> float:
    """P(N >= k) under Poisson(lam). Port of SyntheticMath.poissonAtLeast."""
    if lam <= 0:
        return 0.0 if k > 0 else 1.0
    if k <= 0:
        return 1.0
    cdf = 0.0
    exp_neg = math.exp(-lam)
    factorial = 1.0
    lambda_pow = 1.0
    for i in range(k):
        if i > 0:
            factorial *= i
            lambda_pow *= lam
        cdf += (lambda_pow * exp_neg) / factorial
    return 1.0 - cdf


def _trailing_selection(
    winner_odds: Optional[float],
    winner_prob: Optional[float],
    team_lambda: Optional[float],
    goals_needed: int,
    min_reduction: float,
    max_reduction: float,
) -> Tuple[Optional[float], Optional[float]]:
    """Port of TrailingSelection.calculate. Returns (trailing_odds, boosted_prob), or (None, None)."""
    if team_lambda is None or team_lambda <= 0:
        return None, None
    if winner_odds is None or winner_odds <= 0 or winner_prob is None or winner_prob <= 0:
        return None, None
    poisson_factor = _poisson_at_least(team_lambda, goals_needed)
    reduction_factor = min_reduction + (1.0 - poisson_factor) * (max_reduction - min_reduction)
    trailing_odds = winner_odds * (1.0 - reduction_factor)
    boosted_prob = winner_prob + reduction_factor * (1.0 - winner_prob)
    return trailing_odds, boosted_prob


def price_early_payout_markets(
    *,
    # 1X2 — probabilities (pre-devigged) drive the math
    p_home_win: float,
    p_draw: float,
    p_away_win: float,
    # Original 1X2 decimal odds — used for cap step AND trailing-selection base when live
    home_1x2_odds: float,
    draw_1x2_odds: float,
    away_1x2_odds: float,
    # O/U lines: lists of (line, over_prob) tuples (pre-devigged; no longer (line, over_odds, under_odds))
    total_ou: List[Tuple[float, float]],
    home_ou: List[Tuple[float, float]],
    away_ou: List[Tuple[float, float]],
    # FTTS probabilities (pre-devigged). 1UP level-score needs both; trailing-team path does not.
    ftts_home_prob: Optional[float] = None,
    ftts_away_prob: Optional[float] = None,
    # Live score as (home_goals, away_goals). (0, 0) means prematch / level — default.
    # When score != 0-0, the leading side is DEACTIVATED for 1UP (already triggered);
    # for 2UP, the leading side is deactivated only when |diff| >= 2.
    score: Tuple[int, int] = (0, 0),
    # Max lead either side has held at any point during the match so far.
    # Drives history-aware deactivation: the engine's score-based logic
    # only knows the CURRENT diff, so a 1-0 → 1-1 match would otherwise
    # re-price home 1UP (already triggered at 1-0). Defaults of 0 keep
    # prematch / fresh-call behaviour unchanged.
    max_home_lead: int = 0,
    max_away_lead: int = 0,
) -> Dict:
    """
    Price 1UP and 2UP prematch (score = 0-0) markets following the Java
    ThreeWay1UPCalculatorImpl + Threeway2UpCalculatorImpl logic.

    Takes pre-devigged probabilities as inputs — devig is done at the source DB
    level, not here. p_home_win / p_draw / p_away_win come directly from
    sporty_outcome_*_prob. home_1x2_odds / draw_1x2_odds / away_1x2_odds are
    the raw decimal odds used only for the cap step.

    Returns a dict with keys:
      - lambda_home, lambda_away
      - p_home_win, p_draw, p_away_win (passed through for reference)
      - p_home_1, p_away_1 (1UP probs — for reference / debugging)
      - p_home_2, p_away_2 (2UP probs)
      - market_1up: {home_fair, home_margin, draw, away_fair, away_margin}
        (home_fair = fair odds without cap; home_margin = capped final odds)
      - market_2up: same shape
      - Plus legacy flat fields for calculator_prod compatibility:
        odds_home_1_fair, odds_home_1_margin, odds_away_1_fair, odds_away_1_margin,
        odds_home_2_fair, odds_home_2_margin, odds_away_2_fair, odds_away_2_margin
    """
    # Alias to local short names for internal math (consistent with prior code)
    p_home, p_away = p_home_win, p_away_win

    # ---- 1. Derive lambdas ----
    lambda_home, lambda_away = derive_lambda_pair(home_ou, away_ou, total_ou)

    # If lambdas couldn't be derived, return a "deactivated" result
    if lambda_home is None or lambda_away is None:
        return {
            "lambda_home": lambda_home, "lambda_away": lambda_away,
            "p_home_win": p_home_win, "p_draw": p_draw, "p_away_win": p_away_win,
            "p_home_1": None, "p_away_1": None, "p_home_2": None, "p_away_2": None,
            "market_1up": {"home_fair": None, "home_margin": None, "draw": draw_1x2_odds,
                           "away_fair": None, "away_margin": None},
            "market_2up": {"home_fair": None, "home_margin": None, "draw": draw_1x2_odds,
                           "away_fair": None, "away_margin": None},
            "odds_home_1_fair": None, "odds_home_1_margin": None,
            "odds_away_1_fair": None, "odds_away_1_margin": None,
            "odds_home_2_fair": None, "odds_home_2_margin": None,
            "odds_away_2_fair": None, "odds_away_2_margin": None,
        }

    # ---- 3. Favorite/underdog assignment (used by both 1UP and 2UP) ----
    home_is_favorite = p_home >= p_away
    fav_lambda, dog_lambda = (lambda_home, lambda_away) if home_is_favorite else (lambda_away, lambda_home)
    fs = _favorite_strength(p_home, p_away)
    fav_weight = 0.5 + fs / 2.0
    dog_weight = 1.0 - fav_weight

    # ---- Live-score branching: compute goal_difference ----
    home_score, away_score = score
    goal_difference = home_score - away_score

    # ============== 1UP ==============
    if goal_difference == 0:
        # ---- LEVEL SCORE 1UP: needs FTTS ----
        if ftts_home_prob is None or ftts_away_prob is None:
            home_1up_prob = None
            away_1up_prob = None
            home_1up_fair_odds = None
            away_1up_fair_odds = None
            home_1up_capped = None
            away_1up_capped = None
        else:
            ftts_fav, ftts_dog = (ftts_home_prob, ftts_away_prob) if home_is_favorite else (ftts_away_prob, ftts_home_prob)

            fav_by_fav = _apply_model(ONEUP_FAVORITE_MODEL, ftts_fav, fav_lambda, dog_lambda)
            fav_by_dog = _apply_model(ONEUP_UNDERDOG_MODEL, ftts_fav, fav_lambda, dog_lambda)
            blended_fav = fav_weight * fav_by_fav + dog_weight * fav_by_dog

            dog_by_fav = _apply_model(ONEUP_FAVORITE_MODEL, ftts_dog, fav_lambda, dog_lambda)
            dog_by_dog = _apply_model(ONEUP_UNDERDOG_MODEL, ftts_dog, fav_lambda, dog_lambda)
            # Underdog's blend swaps the weights — matches Java ThreeWay1UPCalculatorImpl
            blended_dog = dog_weight * dog_by_fav + fav_weight * dog_by_dog

            home_1up_prob = max(0.0, blended_fav if home_is_favorite else blended_dog)
            away_1up_prob = max(0.0, blended_dog if home_is_favorite else blended_fav)

            if ONEUP_MARGIN_BLEND_ENABLED:
                fav_margin_1up = _blend_margins(fav_weight, ONEUP_FAVORITE_MARGIN, ONEUP_UNDERDOG_MARGIN)
                dog_margin_1up = _blend_margins(dog_weight, ONEUP_FAVORITE_MARGIN, ONEUP_UNDERDOG_MARGIN)
            else:
                fav_margin_1up = ONEUP_FAVORITE_MARGIN
                dog_margin_1up = ONEUP_UNDERDOG_MARGIN
            home_margin_1up = fav_margin_1up if home_is_favorite else dog_margin_1up
            away_margin_1up = dog_margin_1up if home_is_favorite else fav_margin_1up

            home_1up_fair_odds = _fair_prob_to_odds(home_1up_prob, home_margin_1up)
            away_1up_fair_odds = _fair_prob_to_odds(away_1up_prob, away_margin_1up)

            home_1up_capped, _ = _cap_selection(home_1up_fair_odds, home_1up_prob, home_1x2_odds, p_home, ONEUP_MIN_GUARANTEED_REDUCTION)
            away_1up_capped, _ = _cap_selection(away_1up_fair_odds, away_1up_prob, away_1x2_odds, p_away, ONEUP_MIN_GUARANTEED_REDUCTION)
    else:
        # ---- TRAILING-TEAM 1UP: leading side deactivated, trailing uses Poisson tail on its 1X2 ----
        # Matches ThreeWay1UPCalculatorImpl.calculateWithTrailingTeam
        if goal_difference > 0:
            # Home leads → away is trailing, needs (goal_difference + 1) goals to flip lead
            home_1up_fair_odds = home_1up_capped = home_1up_prob = None
            away_1up_capped, away_1up_prob = _trailing_selection(
                away_1x2_odds, p_away, lambda_away,
                goal_difference + 1,
                ONEUP_TRAILING_MIN_REDUCTION, ONEUP_TRAILING_MAX_REDUCTION,
            )
            away_1up_fair_odds = away_1up_capped  # no separate cap step in trailing path
        else:
            home_deficit = abs(goal_difference)
            home_1up_capped, home_1up_prob = _trailing_selection(
                home_1x2_odds, p_home, lambda_home,
                home_deficit + 1,
                ONEUP_TRAILING_MIN_REDUCTION, ONEUP_TRAILING_MAX_REDUCTION,
            )
            home_1up_fair_odds = home_1up_capped
            away_1up_fair_odds = away_1up_capped = away_1up_prob = None

    # ============== 2UP ==============
    if abs(goal_difference) < 2:
        # ---- LEVEL OR ONE-GOAL 2UP: Ever2UpProbability DP + inclusion-exclusion ----
        # Matches Threeway2UpCalculatorImpl.calculateLevelOrOneGoal
        p_home_ever, p_away_ever, p_home_ever_wins, p_away_ever_wins = ever_2up_probability(
            lambda_home, lambda_away, goal_difference
        )
        home_residual = max(0.0, p_home_ever - p_home_ever_wins)
        away_residual = max(0.0, p_away_ever - p_away_ever_wins)

        # Per-side boost coefficient (blended favorite/underdog)
        if TWOUP_BOOST_BLEND_ENABLED:
            fav_coeff = _blend_boost(fav_weight, TWOUP_FAVORITE_BOOST_COEFFICIENT, TWOUP_UNDERDOG_BOOST_COEFFICIENT)
            dog_coeff = _blend_boost(dog_weight, TWOUP_FAVORITE_BOOST_COEFFICIENT, TWOUP_UNDERDOG_BOOST_COEFFICIENT)
        else:
            fav_coeff = TWOUP_FAVORITE_BOOST_COEFFICIENT
            dog_coeff = TWOUP_UNDERDOG_BOOST_COEFFICIENT
        home_coeff = fav_coeff if home_is_favorite else dog_coeff
        away_coeff = dog_coeff if home_is_favorite else fav_coeff

        home_2up_prob = max(0.0, p_home + home_residual * home_coeff)
        away_2up_prob = max(0.0, p_away + away_residual * away_coeff)

        if TWOUP_MARGIN_BLEND_ENABLED:
            fav_margin_2up = _blend_margins(fav_weight, TWOUP_FAVORITE_MARGIN, TWOUP_UNDERDOG_MARGIN)
            dog_margin_2up = _blend_margins(dog_weight, TWOUP_FAVORITE_MARGIN, TWOUP_UNDERDOG_MARGIN)
        else:
            fav_margin_2up = TWOUP_FAVORITE_MARGIN
            dog_margin_2up = TWOUP_UNDERDOG_MARGIN
        home_margin_2up = fav_margin_2up if home_is_favorite else dog_margin_2up
        away_margin_2up = dog_margin_2up if home_is_favorite else fav_margin_2up

        home_2up_fair_odds = _fair_prob_to_odds(home_2up_prob, home_margin_2up)
        away_2up_fair_odds = _fair_prob_to_odds(away_2up_prob, away_margin_2up)

        home_min_red = TWOUP_FAVORITE_MIN_GUARANTEED_REDUCTION if home_is_favorite else TWOUP_UNDERDOG_MIN_GUARANTEED_REDUCTION
        away_min_red = TWOUP_UNDERDOG_MIN_GUARANTEED_REDUCTION if home_is_favorite else TWOUP_FAVORITE_MIN_GUARANTEED_REDUCTION

        home_2up_capped, _ = _cap_selection(home_2up_fair_odds, home_2up_prob, home_1x2_odds, p_home, home_min_red)
        away_2up_capped, _ = _cap_selection(away_2up_fair_odds, away_2up_prob, away_1x2_odds, p_away, away_min_red)
    else:
        # ---- TRAILING-TEAM 2UP: |diff| >= 2, leading side deactivated ----
        # Matches Threeway2UpCalculatorImpl.calculateWithTrailingTeam
        if goal_difference > 0:
            home_2up_fair_odds = home_2up_capped = home_2up_prob = None
            away_2up_capped, away_2up_prob = _trailing_selection(
                away_1x2_odds, p_away, lambda_away,
                goal_difference + 2,
                TWOUP_TRAILING_MIN_REDUCTION, TWOUP_TRAILING_MAX_REDUCTION,
            )
            away_2up_fair_odds = away_2up_capped
        else:
            home_deficit = abs(goal_difference)
            home_2up_capped, home_2up_prob = _trailing_selection(
                home_1x2_odds, p_home, lambda_home,
                home_deficit + 2,
                TWOUP_TRAILING_MIN_REDUCTION, TWOUP_TRAILING_MAX_REDUCTION,
            )
            home_2up_fair_odds = home_2up_capped
            away_2up_fair_odds = away_2up_capped = away_2up_prob = None

    # History-aware deactivation. The current-score logic above only
    # knows the CURRENT diff, so a level / swung-back score (e.g. 1-1
    # after going 1-0) would re-price markets that already triggered.
    # Each side's market settles once its lead has reached the required
    # margin at any point in the match.
    if max_home_lead >= 1:
        home_1up_prob = home_1up_fair_odds = home_1up_capped = None
    if max_away_lead >= 1:
        away_1up_prob = away_1up_fair_odds = away_1up_capped = None
    if max_home_lead >= 2:
        home_2up_prob = home_2up_fair_odds = home_2up_capped = None
    if max_away_lead >= 2:
        away_2up_prob = away_2up_fair_odds = away_2up_capped = None

    return {
        "lambda_home": lambda_home,
        "lambda_away": lambda_away,
        "p_home_win": p_home_win,
        "p_draw": p_draw,
        "p_away_win": p_away_win,
        "p_home_1": home_1up_prob,
        "p_away_1": away_1up_prob,
        "p_home_2": home_2up_prob,
        "p_away_2": away_2up_prob,
        "market_1up": {
            "home_fair": home_1up_fair_odds,
            "home_margin": home_1up_capped,
            "draw": draw_1x2_odds,
            "away_fair": away_1up_fair_odds,
            "away_margin": away_1up_capped,
        },
        "market_2up": {
            "home_fair": home_2up_fair_odds,
            "home_margin": home_2up_capped,
            "draw": draw_1x2_odds,
            "away_fair": away_2up_fair_odds,
            "away_margin": away_2up_capped,
        },
        # Legacy flat fields (so calculator_prod doesn't need to change shape)
        "odds_home_1_fair": home_1up_fair_odds,
        "odds_home_1_margin": home_1up_capped,
        "odds_away_1_fair": away_1up_fair_odds,
        "odds_away_1_margin": away_1up_capped,
        "odds_home_2_fair": home_2up_fair_odds,
        "odds_home_2_margin": home_2up_capped,
        "odds_away_2_fair": away_2up_fair_odds,
        "odds_away_2_margin": away_2up_capped,
    }
