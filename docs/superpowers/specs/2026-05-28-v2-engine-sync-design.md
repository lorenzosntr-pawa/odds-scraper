# V2 Engine Sync — Design Spec

**Date:** 2026-05-28
**Status:** Draft

## Goal

Bring `engine_v2.py` in sync with the latest Sportradar Java pricing engine. Our port predates the two most recent Java commits; this applies their behavioral changes.

## Background

`engine_v2.py` is a Python port of the Java 1UP/2UP synthetic pricing engine in `SourceSportradar`. It already matches the post-rebuild Java engine on: the EverLeads DP, unified 2UP path, score-aware lambda derivation, capping, trailing margins, regression models, boost coefficients, asymmetric min-reductions, and all FeatureProperties defaults.

A file-by-file diff against the Java HEAD found exactly two functional commits the port is missing (both 2026-05-27):
- `406ac9ba` — Clamp 1UP/2UP fair probabilities to [0,1]
- `01093a8c` — Scale complement floor proportionally for remaining-time lambdas

The intervening commit `d99cec76` (sonar refactor) is non-behavioral — verified the DP transition/accumulation logic is identical.

## Changes

### Change 1 — Proportional complement floor

**File:** `src/odds_scraper/pricer/engine_v2.py`, in `derive_lambda_pair`.

When one side's lambda is missing and derived as `total − present`, the floor is currently a flat `LAMBDA_MIN_COMPLEMENT = 0.1`. Java now scales it down for small totals:

```python
LAMBDA_MIN_COMPLEMENT_RATIO = 0.04  # new constant

# in derive_lambda_pair, where one side is None:
floor = min(LAMBDA_MIN_COMPLEMENT, total * LAMBDA_MIN_COMPLEMENT_RATIO)
missing = max(floor, total - present)
```

**Why:** for late-match remaining-time lambdas the total OU is small (e.g. 0.5–1.5 goals left). A flat 0.1 floor over-inflated the complement side. The proportional floor (`min(0.1, total·0.04)`) only kicks in below total ≈ 2.5, where it correctly shrinks.

Matches Java `LambdaCalculator.complement`:
```java
final double floor = Math.min(MIN_COMPLEMENT, total * MIN_COMPLEMENT_RATIO);
return Math.max(floor, total - knownSide);
```

### Change 2 — Clamp 1UP/2UP probabilities to [0,1]

**File:** `src/odds_scraper/pricer/engine_v2.py`, in `price_early_payout_markets`.

The trailing-1UP and 2UP raw probabilities are currently floored at 0 but not capped at 1.0:
- Trailing 1UP: `home_1up_prob_raw = max(0.0, p_home + home_residual)`
- 2UP: `home_2up_prob_raw = max(0.0, p_home + home_residual * home_coeff)`

`p_win` and the DP residual are on different probability bases, so their sum can exceed 1.0. Java replaced `clampNonNegative` with `clampToProbability` (= `Math.clamp(v, 0, 1)`) on these outputs.

Add a helper and apply it to the trailing-1UP and 2UP raw probabilities (both home and away sides):
```python
def _clamp_prob(value: float) -> float:
    return max(0.0, min(1.0, value))
```

Replace the `max(0.0, ...)` calls for the trailing-1UP raw and 2UP raw probabilities with `_clamp_prob(...)`.

**Note:** the level-score 1UP path already stays in [0,1] — `_apply_model` clamps each model output, and the blend is a convex combination — but applying `_clamp_prob` there too is harmless and matches Java's defensive clamp. Apply consistently.

## What Does NOT Change

- `configs.py` — the `0.04` ratio is an internal engine constant (Java keeps it `private static final`, not a tunable). No new profile knob.
- The EverLeads DP, capping, margins, models, boosts, deactivation rules — already in sync.
- V1 engine, runner, simulator, UI, schema — untouched.

## Testing

- **Complement floor:** a test where total OU is small (e.g. total=1.0, one side present) asserts the derived complement uses the proportional floor `0.04` (e.g. floor = 0.04, not 0.1).
- **Clamp:** a test with inputs that drive `p_win + residual > 1.0` (strong favorite, trailing) asserts the output 1UP/2UP probability ≤ 1.0.
- Full existing V2 test suite (`test_pricer_engine_v2.py`) must still pass — these changes only affect edge cases, so the bulk of existing assertions are unaffected.
