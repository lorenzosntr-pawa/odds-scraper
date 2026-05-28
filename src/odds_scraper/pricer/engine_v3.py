"""Pricer engine V3 — identical to V2 except the margin step.

V2 converts a fair probability to offered odds ADDITIVELY:
    implied = slope * fair_prob + intercept ; odds = 1 / implied
That additive intercept misbehaves at the tails — at low prob it becomes a
huge RELATIVE margin (needing the separate ONEUP_TRAILING_* patch), and at
high prob `slope*p + intercept` overflows past 1.0 so odds slam into the
1.01 floor and the margin structure is lost.

V3 replaces it with a probability-weighted ODDS-RATIO (logit-linear) margin:
    implied = sigmoid(level + tilt * logit(fair_prob)) ; odds = 1 / implied
`implied` is always in (0,1) so odds are always valid (>1.0) — no null odds,
no overflow, no floor. `level` is the overall margin knob (0 = fair, negative
= giveaway); `tilt` is the favorite-longshot skew (>1 = more margin on
favorites). A single (level, tilt) pair per market REPLACES V2's entire
margin config for that market: the favorite/underdog split, the trailing
pair, AND the margin blend. The margin depends only on each selection's own
probability — there is no fav/dog margin distinction and no _blend_margins.

EVERYTHING ELSE is byte-for-byte identical to engine_v2.py: lambda
derivation, the ever_leads_probability DP, the 1UP level-score regression
model, the 2UP boost coefficient (and its blend), the residual /
inclusion-exclusion math, SelectionCapping, and all deactivation rules.

Module-isolated from engine.py / engine_v2.py so with_coefficients overrides
on one engine never cross-contaminate the others. V1/V2-only override keys
are silently skipped by runner_v2.with_v3_coefficients — V3 doesn't define
or read them.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

# ---- 1UP regression model (UNCHANGED from V2 — this is NOT margin) ----
ONEUP_FAVORITE_MODEL  = (-0.137308, 1.228176, 0.001221, 0.085310)  # (intercept, nextGoal, lambda, underdog)
ONEUP_UNDERDOG_MODEL  = (0.006276, 0.909535, -0.009967, 0.094182)
ONEUP_MIN_GUARANTEED_REDUCTION = 0.02

# ---- 2UP boost (UNCHANGED from V2 — this is NOT margin) ----
TWOUP_FAVORITE_BOOST_COEFFICIENT = 0.9
TWOUP_UNDERDOG_BOOST_COEFFICIENT = 0.6
TWOUP_FAVORITE_MIN_GUARANTEED_REDUCTION = 0.02
TWOUP_UNDERDOG_MIN_GUARANTEED_REDUCTION = 0.005
TWOUP_BOOST_BLEND_ENABLED = True

# ---- V3 margin model (logit-linear). One (level, tilt) per market
# REPLACES all of V2's margin config. Defaults fitted from a 122k-row V2
# pricer run to preserve V2's mid-range odds where 0.25 <= p <= 0.75. ----
ONEUP_MARGIN_LEVEL = 0.1324
ONEUP_MARGIN_TILT  = 0.9922
TWOUP_MARGIN_LEVEL = 0.0352
TWOUP_MARGIN_TILT  = 1.0030

# ---- Marketing odds-boost: lengthen one side's odds by a % AFTER the
# margin and BEFORE the cap. Default 0.0 = no-op. Skipped on near-even
# matches (|p_home - p_away| < NEAR_EVEN_THRESHOLD) where there's no
# clear favorite/underdog to boost. A boost can't push odds past the
# 1X2 cap — that's expected (the cap still binds). ----
ONEUP_FAVORITE_ODDS_BOOST_PCT = 0.0
ONEUP_UNDERDOG_ODDS_BOOST_PCT = 0.0
TWOUP_FAVORITE_ODDS_BOOST_PCT = 0.0
TWOUP_UNDERDOG_ODDS_BOOST_PCT = 0.0
NEAR_EVEN_THRESHOLD = 0.03

TWOUP_DP_MAX_GOALS = 40
TWOUP_DP_NEGLIGIBLE_TAIL = 1e-12

# ---- Constants from LambdaCalculator.java ----
LAMBDA_TOLERANCE = 1e-6
LAMBDA_MAX = 10.0
LAMBDA_MIN_COMPLEMENT = 0.1
LAMBDA_MIN_COMPLEMENT_RATIO = 0.04
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


def _poisson_over_prob(lam: float, line: float, score_offset: int = 0) -> float:
    """P(N_remaining > line - score_offset) under Poisson(lam).

    line must be half-line (X.5). `score_offset` is the number of goals
    already scored on this side; the lambda is the REMAINING-time rate,
    so the comparison is against `line - score_offset` goals in what's
    left of the match. When `score_offset >= ceil(line)` the over is
    certain (current score already past the line)."""
    if lam <= 0:
        return 0.0
    n_under = int(math.floor(line)) + 1 - score_offset
    if n_under <= 0:
        return 1.0
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


def _lambda_from_over_prob(
    over_prob: float, line: float, score_offset: int = 0,
) -> Optional[float]:
    """Bisect to find lambda such that P(N > line - score_offset) = over_prob."""
    if not (0.0 < over_prob < 1.0):
        return None
    # OU line already below current score: provides no info about
    # remaining-time scoring rate (the over is certain).
    if line < score_offset:
        return None

    def f(lam: float) -> float:
        return _poisson_over_prob(lam, line, score_offset) - over_prob

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


def _derive_lambda_from_multiple_lines(
    lines: List[Tuple[float, float]], score_offset: int = 0,
) -> Optional[float]:
    """Weighted average of per-line lambdas. Each lambda is the
    remaining-time rate that explains the bookmaker's devigged over
    probability at that line, given the current `score_offset` goals
    already scored on this side."""
    if not lines:
        return None
    weighted_sum = 0.0
    total_weight = 0.0
    for line, over_prob in lines:
        if abs((line * 2) - round(line * 2)) > 1e-9 or (round(line * 2) % 2 != 1):
            continue
        if over_prob is None:
            continue
        lam = _lambda_from_over_prob(over_prob, line, score_offset)
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
    score_home: int = 0,
    score_away: int = 0,
) -> Tuple[Optional[float], Optional[float]]:
    """Score-aware lambda derivation.

    OU lines describe full-match goal totals; the lambdas the engine
    consumes are REMAINING-time Poisson rates. So we subtract the
    relevant score off each line before inverting: home OU uses
    `score_home`, away OU uses `score_away`, total OU uses their sum.
    At prematch (score=0) the math collapses to the original full-match
    interpretation."""
    home = _derive_lambda_from_multiple_lines(home_ou, score_home)
    away = _derive_lambda_from_multiple_lines(away_ou, score_away)
    total = _derive_lambda_from_multiple_lines(total_ou, score_home + score_away)

    if total is None:
        return home, away

    if home is None and away is None:
        half = total / 2.0
        return half, half

    # Complement floor scales with the total so small remaining-time
    # totals don't get an over-inflated complement (Java: min(0.1, total*0.04)).
    floor = min(LAMBDA_MIN_COMPLEMENT, total * LAMBDA_MIN_COMPLEMENT_RATIO)

    if home is None:
        return max(floor, total - (away or 0.0)), away

    if away is None:
        return home, max(floor, total - (home or 0.0))

    # Both present — reconcile
    s = home + away
    if abs(s - total) <= LAMBDA_RECONCILIATION_THRESHOLD:
        return home, away
    if s > 0:
        scale = total / s
        return home * scale, away * scale
    half = total / 2.0
    return half, half


def _clamp_prob(value: float) -> float:
    """Clamp to [0, 1]. P(wins) (from 1x2) and the DP residual (from OU)
    are on different probability bases, so their sum can land outside
    [0, 1] when the two markets disagree. Matches Java clampToProbability."""
    return max(0.0, min(1.0, value))


def _apply_model(coeffs: Tuple[float, float, float, float],
                 ftts: float, lambda_fav: float, lambda_dog: float) -> float:
    intercept, next_goal, lam, dog = coeffs
    raw = intercept + next_goal * ftts + lam * lambda_fav + dog * lambda_dog
    # Match Java ThreeWay1UPCalculatorImpl.applyModel: clamp to [0, 1] so
    # extreme inputs (lambda far above training distribution) can't exit
    # the valid probability range.
    return _clamp_prob(raw)


def _blend_boost(strength: float, fav_coeff: float, dog_coeff: float) -> float:
    return strength * fav_coeff + (1.0 - strength) * dog_coeff


def _apply_boost(odds, is_favorite: bool, near_even: bool,
                 fav_pct: float, dog_pct: float):
    """Lengthen one side's odds by a marketing % (favorite or underdog).
    No-op when odds is None or the match is near-even (no clear side)."""
    if odds is None or near_even:
        return odds
    pct = fav_pct if is_favorite else dog_pct
    return odds * (1.0 + pct / 100.0)


def _fair_prob_to_odds(fair_prob: float, level: float, tilt: float) -> float:
    """Logit-linear (odds-ratio) margin:
        implied = sigmoid(level + tilt * logit(fair_prob)) ; odds = 1 / implied
    `implied` is always in (0,1) so odds are always > 1.0 — never None, no
    overflow, no floor. Replaces V2's additive slope*p + intercept margin."""
    x = min(max(fair_prob, 1e-9), 1.0 - 1e-9)
    z = level + tilt * math.log(x / (1.0 - x))
    implied = 1.0 / (1.0 + math.exp(-z))
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




# Bit-packed hit flags. Layout mirrors EverLeadsProbability.java:
#   bit 0 (1)  = "score difference has ever been <= -2 during the match"
#   bit 1 (2)  = "                              has ever been <= -1"
#   bit 2 (4)  = "                              has ever been >= +1"
#   bit 3 (8)  = "                              has ever been >= +2"
_LEADS_F_LOW2  = 1
_LEADS_F_LOW1  = 1 << 1
_LEADS_F_HIGH1 = 1 << 2
_LEADS_F_HIGH2 = 1 << 3
_LEADS_N_FLAGS = 16


def ever_leads_probability(
    lambda_h: float, lambda_a: float, initial_diff: int,
) -> Tuple[float, float, float, float, float, float, float, float]:
    """Joint DP over the score-difference random walk tracking 4 hit
    flags ({ever <=-2, <=-1, >=+1, >=+2}) and the final-result winner.

    Returns an 8-tuple matching Java's Stats record:
        (p_home_ever_1, p_away_ever_1,
         p_home_ever_1_and_wins, p_away_ever_1_and_wins,
         p_home_ever_2, p_away_ever_2,
         p_home_ever_2_and_wins, p_away_ever_2_and_wins)

    p_home_ever_1 + win is enough to compute P(home 1UP) by inclusion-
    exclusion: P(home 1UP) = P(home wins) + max(0, ever1 - ever1AndWins).
    """
    if lambda_h <= 0.0 or lambda_a <= 0.0:
        return (0.0,) * 8
    lambda_total = lambda_h + lambda_a
    p = lambda_h / lambda_total

    d_extent = TWOUP_DP_MAX_GOALS + abs(initial_diff) + 2
    size = 2 * d_extent + 1
    offset = d_extent

    state = [[0.0] * _LEADS_N_FLAGS for _ in range(size)]
    init_flag = 0
    if initial_diff >= 1: init_flag |= _LEADS_F_HIGH1
    if initial_diff >= 2: init_flag |= _LEADS_F_HIGH2
    if initial_diff <= -1: init_flag |= _LEADS_F_LOW1
    if initial_diff <= -2: init_flag |= _LEADS_F_LOW2
    state[initial_diff + offset][init_flag] = 1.0

    accum = [0.0] * 8
    exp_neg = math.exp(-lambda_total)

    _ever_leads_accumulate(state, offset, exp_neg, accum)

    lambda_pow = 1.0
    factorial = 1.0
    for n in range(1, TWOUP_DP_MAX_GOALS + 1):
        lambda_pow *= lambda_total
        factorial *= n
        prob_n = (lambda_pow * exp_neg) / factorial

        state = _ever_leads_step(state, offset, p, size)
        _ever_leads_accumulate(state, offset, prob_n, accum)

        if prob_n < TWOUP_DP_NEGLIGIBLE_TAIL and n > lambda_total:
            break

    return tuple(accum)


def _ever_leads_step(state, offset, p, size):
    """One Poisson-goal transition. Home goal moves diff -> diff+1
    with probability p; away symmetric. ORs the appropriate threshold
    flags as the new diff crosses +-1 / +-2."""
    nxt = [[0.0] * _LEADS_N_FLAGS for _ in range(size)]
    one_minus_p = 1.0 - p
    for d_idx in range(size):
        row = state[d_idx]
        for flag in range(_LEADS_N_FLAGS):
            prob = row[flag]
            if prob == 0.0:
                continue
            diff = d_idx - offset

            # Home scores: diff -> diff + 1
            new_diff_h = diff + 1
            new_idx_h = new_diff_h + offset
            if 0 <= new_idx_h < size:
                new_flag = flag
                if new_diff_h >= 1: new_flag |= _LEADS_F_HIGH1
                if new_diff_h >= 2: new_flag |= _LEADS_F_HIGH2
                nxt[new_idx_h][new_flag] += p * prob

            # Away scores: diff -> diff - 1
            new_diff_a = diff - 1
            new_idx_a = new_diff_a + offset
            if 0 <= new_idx_a < size:
                new_flag = flag
                if new_diff_a <= -1: new_flag |= _LEADS_F_LOW1
                if new_diff_a <= -2: new_flag |= _LEADS_F_LOW2
                nxt[new_idx_a][new_flag] += one_minus_p * prob
    return nxt


def _ever_leads_accumulate(state, offset, weight, accum):
    """Folds the current state into the 8-accumulator at this Poisson
    weight. `weight` is the probability the match ends with the current
    goal count; accum entries are layout described in the public API."""
    if weight == 0.0:
        return
    for d_idx in range(len(state)):
        row = state[d_idx]
        for flag in range(_LEADS_N_FLAGS):
            prob = row[flag]
            if prob == 0.0:
                continue
            weighted = prob * weight
            diff = d_idx - offset
            h1 = (flag & _LEADS_F_HIGH1) != 0
            h2 = (flag & _LEADS_F_HIGH2) != 0
            l1 = (flag & _LEADS_F_LOW1) != 0
            l2 = (flag & _LEADS_F_LOW2) != 0
            home_wins = diff >= 1
            away_wins = diff <= -1

            if h1:
                accum[0] += weighted
                if home_wins: accum[2] += weighted
            if l1:
                accum[1] += weighted
                if away_wins: accum[3] += weighted
            if h2:
                accum[4] += weighted
                if home_wins: accum[6] += weighted
            if l2:
                accum[5] += weighted
                if away_wins: accum[7] += weighted


def price_early_payout_markets(
    *,
    # 1X2 — probabilities (pre-devigged) drive the math
    p_home_win: float,
    p_draw: float,
    p_away_win: float,
    # Original 1X2 decimal odds — used for cap step AND trailing-selection base when live.
    # Per-side Optional: a suspended selection (BP returns odds=0) reaches us as None
    # via inputs.extract; the cap handles None as "no source, floor to 1.01".
    home_1x2_odds: Optional[float],
    draw_1x2_odds: Optional[float],
    away_1x2_odds: Optional[float],
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
    Price 1UP and 2UP markets following the V2 logic exactly, except the
    fair-prob → odds margin step uses the logit-linear (level, tilt) model.

    Takes pre-devigged probabilities as inputs — devig is done at the source DB
    level, not here. Returns the same dict shape as engine_v2.
    """
    # Alias to local short names for internal math (consistent with prior code)
    p_home, p_away = p_home_win, p_away_win

    # Score is unpacked early — lambdas are score-aware (the OU lines
    # describe full-match goal totals, but the engine consumes
    # remaining-time Poisson rates).
    home_score, away_score = score
    goal_difference = home_score - away_score

    # ---- 1. Derive lambdas (score-aware) ----
    lambda_home, lambda_away = derive_lambda_pair(
        home_ou, away_ou, total_ou,
        score_home=int(home_score), score_away=int(away_score),
    )

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

    # ---- 3. Favorite/underdog assignment (used by 1UP model + 2UP boost) ----
    home_is_favorite = p_home >= p_away
    fav_lambda, dog_lambda = (lambda_home, lambda_away) if home_is_favorite else (lambda_away, lambda_home)
    fs = _favorite_strength(p_home, p_away)
    fav_weight = 0.5 + fs / 2.0
    dog_weight = 1.0 - fav_weight
    # Odds-boost is skipped near-even — no clear favorite/underdog side.
    near_even = abs(p_home - p_away) < NEAR_EVEN_THRESHOLD

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

            home_1up_prob = _clamp_prob(blended_fav if home_is_favorite else blended_dog)
            away_1up_prob = _clamp_prob(blended_dog if home_is_favorite else blended_fav)

            # V3 margin: single (level, tilt) per market, applied to each
            # selection's own probability. No fav/dog split, no blend.
            home_1up_fair_odds = _apply_boost(
                _fair_prob_to_odds(home_1up_prob, ONEUP_MARGIN_LEVEL, ONEUP_MARGIN_TILT),
                home_is_favorite, near_even,
                ONEUP_FAVORITE_ODDS_BOOST_PCT, ONEUP_UNDERDOG_ODDS_BOOST_PCT,
            )
            away_1up_fair_odds = _apply_boost(
                _fair_prob_to_odds(away_1up_prob, ONEUP_MARGIN_LEVEL, ONEUP_MARGIN_TILT),
                not home_is_favorite, near_even,
                ONEUP_FAVORITE_ODDS_BOOST_PCT, ONEUP_UNDERDOG_ODDS_BOOST_PCT,
            )

            home_1up_capped, _ = _cap_selection(home_1up_fair_odds, home_1up_prob, home_1x2_odds, p_home, ONEUP_MIN_GUARANTEED_REDUCTION)
            away_1up_capped, _ = _cap_selection(away_1up_fair_odds, away_1up_prob, away_1x2_odds, p_away, ONEUP_MIN_GUARANTEED_REDUCTION)
    else:
        # ---- TRAILING-TEAM 1UP: DP-based, leading side deactivated ----
        # Probability math identical to V2; only the margin differs (V3's
        # logit margin is bounded at the low end so the separate trailing
        # margin pair V2 needed is no longer required).
        stats = ever_leads_probability(lambda_home, lambda_away, goal_difference)
        p_h_ever_1, p_a_ever_1 = stats[0], stats[1]
        p_h_ever_1_wins, p_a_ever_1_wins = stats[2], stats[3]
        home_residual = max(0.0, p_h_ever_1 - p_h_ever_1_wins)
        away_residual = max(0.0, p_a_ever_1 - p_a_ever_1_wins)
        # Inclusion-exclusion: P(X 1UP) = P(X wins) + residual.
        home_1up_prob_raw = _clamp_prob(p_home + home_residual)
        away_1up_prob_raw = _clamp_prob(p_away + away_residual)

        home_1up_fair_odds = _apply_boost(
            _fair_prob_to_odds(home_1up_prob_raw, ONEUP_MARGIN_LEVEL, ONEUP_MARGIN_TILT),
            home_is_favorite, near_even,
            ONEUP_FAVORITE_ODDS_BOOST_PCT, ONEUP_UNDERDOG_ODDS_BOOST_PCT,
        )
        away_1up_fair_odds = _apply_boost(
            _fair_prob_to_odds(away_1up_prob_raw, ONEUP_MARGIN_LEVEL, ONEUP_MARGIN_TILT),
            not home_is_favorite, near_even,
            ONEUP_FAVORITE_ODDS_BOOST_PCT, ONEUP_UNDERDOG_ODDS_BOOST_PCT,
        )

        home_1up_capped, _ = _cap_selection(
            home_1up_fair_odds, home_1up_prob_raw, home_1x2_odds, p_home,
            ONEUP_MIN_GUARANTEED_REDUCTION,
        )
        away_1up_capped, _ = _cap_selection(
            away_1up_fair_odds, away_1up_prob_raw, away_1x2_odds, p_away,
            ONEUP_MIN_GUARANTEED_REDUCTION,
        )

        # Leading side has already triggered its 1UP — deactivate.
        if goal_difference > 0:
            home_1up_prob = home_1up_fair_odds = home_1up_capped = None
            away_1up_prob = away_1up_prob_raw
        else:
            home_1up_prob = home_1up_prob_raw
            away_1up_prob = away_1up_fair_odds = away_1up_capped = None

    # ============== 2UP (UNIFIED) ==============
    # Reuse the trailing-branch stats when goal_difference != 0 to avoid a
    # second DP pass; recompute only on the level-score path.
    if goal_difference == 0:
        stats = ever_leads_probability(lambda_home, lambda_away, goal_difference)
    p_home_ever_2 = stats[4]
    p_away_ever_2 = stats[5]
    p_home_ever_2_wins = stats[6]
    p_away_ever_2_wins = stats[7]
    home_residual = max(0.0, p_home_ever_2 - p_home_ever_2_wins)
    away_residual = max(0.0, p_away_ever_2 - p_away_ever_2_wins)

    # Per-side boost coefficient (blended favorite/underdog) — UNCHANGED from V2.
    if TWOUP_BOOST_BLEND_ENABLED:
        fav_coeff = _blend_boost(fav_weight, TWOUP_FAVORITE_BOOST_COEFFICIENT, TWOUP_UNDERDOG_BOOST_COEFFICIENT)
        dog_coeff = _blend_boost(dog_weight, TWOUP_FAVORITE_BOOST_COEFFICIENT, TWOUP_UNDERDOG_BOOST_COEFFICIENT)
    else:
        fav_coeff = TWOUP_FAVORITE_BOOST_COEFFICIENT
        dog_coeff = TWOUP_UNDERDOG_BOOST_COEFFICIENT
    home_coeff = fav_coeff if home_is_favorite else dog_coeff
    away_coeff = dog_coeff if home_is_favorite else fav_coeff

    home_2up_prob_raw = _clamp_prob(p_home + home_residual * home_coeff)
    away_2up_prob_raw = _clamp_prob(p_away + away_residual * away_coeff)

    # V3 margin: single (level, tilt) per market.
    home_2up_fair_odds = _apply_boost(
        _fair_prob_to_odds(home_2up_prob_raw, TWOUP_MARGIN_LEVEL, TWOUP_MARGIN_TILT),
        home_is_favorite, near_even,
        TWOUP_FAVORITE_ODDS_BOOST_PCT, TWOUP_UNDERDOG_ODDS_BOOST_PCT,
    )
    away_2up_fair_odds = _apply_boost(
        _fair_prob_to_odds(away_2up_prob_raw, TWOUP_MARGIN_LEVEL, TWOUP_MARGIN_TILT),
        not home_is_favorite, near_even,
        TWOUP_FAVORITE_ODDS_BOOST_PCT, TWOUP_UNDERDOG_ODDS_BOOST_PCT,
    )

    home_min_red = TWOUP_FAVORITE_MIN_GUARANTEED_REDUCTION if home_is_favorite else TWOUP_UNDERDOG_MIN_GUARANTEED_REDUCTION
    away_min_red = TWOUP_UNDERDOG_MIN_GUARANTEED_REDUCTION if home_is_favorite else TWOUP_FAVORITE_MIN_GUARANTEED_REDUCTION

    home_2up_capped, _ = _cap_selection(home_2up_fair_odds, home_2up_prob_raw, home_1x2_odds, p_home, home_min_red)
    away_2up_capped, _ = _cap_selection(away_2up_fair_odds, away_2up_prob_raw, away_1x2_odds, p_away, away_min_red)

    home_2up_prob = home_2up_prob_raw
    away_2up_prob = away_2up_prob_raw
    # Current-score deactivation for sides whose lead has already
    # reached ±2 (their 2UP has triggered).
    if goal_difference >= 2:
        home_2up_prob = home_2up_fair_odds = home_2up_capped = None
    if goal_difference <= -2:
        away_2up_prob = away_2up_fair_odds = away_2up_capped = None

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
