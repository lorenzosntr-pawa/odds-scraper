# Pricer engine v4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a fourth pricing engine `v4` to the check_merging simulator that mirrors the latest SourceSportradar Java pricing (DP-direct 1UP level score, invalid-ref deactivation, odds-based favourite/near-even for the margin) and wire it as the new latest engine across the runner, CSV export, and web simulator.

**Architecture:** `engine_v4.py` is `engine_v3.py` with exactly three deltas applied via targeted edits; everything else (lambda derivation, the ever-leads DP, 2UP inclusion-exclusion + boost-coefficient blend, deactivation rules, return-dict shape) stays identical. Wiring mirrors every `v3` seam. `configs.py` needs no change (v4 reuses v3's tunable keys; `with_v4_coefficients` filters by `hasattr`). `live_writer`/DB schema are out of scope.

**Tech Stack:** Python 3, pytest, FastAPI + Jinja2 (web), SQLite (test fixtures). Run tests with `.venv\Scripts\python -m pytest`.

**Source of truth:** spec at `docs/superpowers/specs/2026-05-29-pricer-v4-engine-design.md`; Java at `C:\Users\loren\Desktop\betpawa\1UP_PR\SourceSportradar` (branch `up-markets-pricing-rewrite`).

---

## File Structure

- **Create** `src/odds_scraper/pricer/engine_v4.py` — the v4 engine (copy of v3 + 3 deltas). One responsibility: price 1UP/2UP for v4.
- **Create** `tests/test_pricer_engine_v4.py` — v4 engine unit tests + v4-vs-v3 differentials.
- **Modify** `src/odds_scraper/pricer/runner_v2.py` — register v4 (import, `VALID_ENGINES`, `with_v4_coefficients`, `_run_engines` → r4, blocks, fallbacks, row unpacking).
- **Modify** `src/odds_scraper/pricer/csv_export.py` — add `v4_*` and `pB_v4_*` 16-cell blocks to `CSV_COLUMNS`.
- **Modify** `tests/test_pricer_csv.py` — add v4/pB_v4 defaults + a column-position test.
- **Modify** `tests/test_pricer_runner_v2.py` — v4-only run + all-four-engines run.
- **Modify** `src/odds_scraper/web/pricer_routes.py` — `LATEST_ENGINE = "v4"` + comment.
- **Modify** `src/odds_scraper/web/templates/simulator.html` — v4 checkbox (checked); v3 unchecked.
- **No change** `src/odds_scraper/pricer/configs.py`, `src/odds_scraper/models.py`.

**Commit coupling:** the runner row tuple is positional against `CSV_COLUMNS`. Adding v4 columns without the runner emitting the v4 block (or vice-versa) misaligns every CSV row. So **Task 3 edits `csv_export.py` and `runner_v2.py` together in one commit.**

---

## Task 1: Create `engine_v4.py` and its tests

**Files:**
- Create: `src/odds_scraper/pricer/engine_v4.py`
- Test: `tests/test_pricer_engine_v4.py`

- [ ] **Step 1: Copy engine_v3.py to engine_v4.py verbatim**

```bash
cp src/odds_scraper/pricer/engine_v3.py src/odds_scraper/pricer/engine_v4.py
```

(Windows PowerShell: `Copy-Item src\odds_scraper\pricer\engine_v3.py src\odds_scraper\pricer\engine_v4.py`)

- [ ] **Step 2: Apply Delta edits to engine_v4.py**

Apply each edit below exactly (find the OLD block, replace with NEW). They are ordered top-to-bottom in the file.

**Edit A — module docstring (top of file, lines 1–29).** Replace the entire `"""..."""` docstring with:

```python
"""Pricer engine V4 — the latest SourceSportradar Java port.

V4 starts from V3 (the xUP logit-linear margin) and applies three deltas
that bring it in line with the latest Java pricing rewrite:

  1. 1UP LEVEL score is DP-direct. Java calculate1upLevelProbabilities no
     longer uses FTTS or the 1UP next-goal regression / fav-dog blend; at
     level score it reads ever_leads_probability(lh, la, 0) and takes
     pHomeEver1 / pAwayEver1 directly (win mass already included). Trailing
     1UP and all of 2UP keep V3's inclusion-exclusion math. The ftts_* kwargs
     stay in the signature for runner compatibility but are UNUSED.

  2. A side whose own 1X2 win odd can't anchor the cap (missing / <= 1.0) is
     DEACTIVATED (odds and prob both None) for BOTH its 1UP and 2UP markets,
     mirroring XupMargin.isCapReferenceValid + the calculators' priceSide.
     This replaces V3's "no source -> floor to 1.01" path for active sides.

  3. The MARGIN's favourite + near-even come from ODDS, not probability:
       - which boost % / reduction % a side gets uses XupMargin.isFavorite
         (LOWER valid 1X2 win odds; tie or missing-opposite-price -> favourite),
       - near_even uses XupMargin.nearEven (|1/home_ref - 1/away_ref| < thr).
     BUT the 2UP boost-COEFFICIENT blend and _favorite_strength STILL use the
     PROBABILITY-based favourite (Java ThreeWayCommon) — unchanged from V3.

EVERYTHING ELSE is identical to engine_v3.py. Module-isolated so
with_coefficients overrides never cross-contaminate the other engines.
"""
```

**Edit B — remove the 1UP regression model constants.** OLD:

```python
# ---- 1UP regression model (UNCHANGED from V2 — this is NOT margin) ----
ONEUP_FAVORITE_MODEL  = (-0.137308, 1.228176, 0.001221, 0.085310)  # (intercept, nextGoal, lambda, underdog)
ONEUP_UNDERDOG_MODEL  = (0.006276, 0.909535, -0.009967, 0.094182)

# ---- 2UP boost (UNCHANGED from V2 — this is NOT margin) ----
```

NEW:

```python
# ---- 2UP boost (UNCHANGED from V3/V2 — this is NOT margin) ----
```

**Edit C — remove `_apply_model` (no longer used).** OLD:

```python
def _apply_model(coeffs: Tuple[float, float, float, float],
                 ftts: float, lambda_fav: float, lambda_dog: float) -> float:
    intercept, next_goal, lam, dog = coeffs
    raw = intercept + next_goal * ftts + lam * lambda_fav + dog * lambda_dog
    # Match Java ThreeWay1UPCalculatorImpl.applyModel: clamp to [0, 1] so
    # extreme inputs (lambda far above training distribution) can't exit
    # the valid probability range.
    return _clamp_prob(raw)


def _blend_boost(strength: float, fav_coeff: float, dog_coeff: float) -> float:
```

NEW:

```python
def _blend_boost(strength: float, fav_coeff: float, dog_coeff: float) -> float:
```

**Edit D — add the odds-based favourite/near-even helpers.** Find this line (the start of `_favorite_strength`):

```python
def _favorite_strength(p_home: float, p_away: float) -> float:
```

Insert ABOVE it:

```python
def _valid_ref(odds: Optional[float]) -> bool:
    """Java XupMargin.isCapReferenceValid: a 1X2 win odd can anchor the cap
    only if it is present and a real decimal odd (> 1.0)."""
    return odds is not None and odds > 1.0


def _is_favorite_by_odds(this_odds: Optional[float], other_odds: Optional[float]) -> bool:
    """Java XupMargin.isFavorite on validity-normalised refs: this side is the
    favourite when its 1X2 win odd is the lower (shorter) one. Missing own
    price -> not favourite; missing opposite price -> favourite; exact tie ->
    favourite (<=). Home and away are evaluated INDEPENDENTLY, so on a tie
    both sides read as favourite (matches Java's per-side priceSide)."""
    if this_odds is None:
        return False
    if other_odds is None:
        return True
    return this_odds <= other_odds


def _near_even_by_odds(home_odds: Optional[float], away_odds: Optional[float],
                       threshold: float) -> bool:
    """Java XupMargin.nearEven on validity-normalised refs: near-even when the
    devigged win-odds gap is below the threshold. False if either ref is
    missing or non-positive."""
    if home_odds is None or away_odds is None or home_odds <= 0.0 or away_odds <= 0.0:
        return False
    return abs(1.0 / home_odds - 1.0 / away_odds) < threshold


def _favorite_strength(p_home: float, p_away: float) -> float:
```

**Edit E — favourite/near-even assignment block.** OLD:

```python
    # ---- 3. Favorite/underdog assignment (used by 1UP model + 2UP boost) ----
    home_is_favorite = p_home >= p_away
    fav_lambda, dog_lambda = (lambda_home, lambda_away) if home_is_favorite else (lambda_away, lambda_home)
    fs = _favorite_strength(p_home, p_away)
    fav_weight = 0.5 + fs / 2.0
    dog_weight = 1.0 - fav_weight
    # Odds-boost is skipped near-even — no clear favorite/underdog side.
    near_even = abs(p_home - p_away) < NEAR_EVEN_THRESHOLD
    # Per-side 1UP cap reduction % (favorite/underdog), mirroring the 2UP pattern.
    oneup_home_red_pct = ONEUP_FAVORITE_REDUCTION_PCT if home_is_favorite else ONEUP_UNDERDOG_REDUCTION_PCT
    oneup_away_red_pct = ONEUP_UNDERDOG_REDUCTION_PCT if home_is_favorite else ONEUP_FAVORITE_REDUCTION_PCT
```

NEW:

```python
    # ---- 3. Favourite/underdog assignment ----
    # PROBABILITY-based favourite (Java ThreeWayCommon) — still drives the 2UP
    # boost-coefficient blend and favourite-strength weighting (unchanged).
    home_is_favorite = p_home >= p_away
    fs = _favorite_strength(p_home, p_away)
    fav_weight = 0.5 + fs / 2.0
    dog_weight = 1.0 - fav_weight

    # Validity-normalised 1X2 win-odds refs (Java XupMargin.isCapReferenceValid).
    # An invalid ref (None / <= 1.0) becomes None and deactivates that side.
    home_ref = home_1x2_odds if _valid_ref(home_1x2_odds) else None
    away_ref = away_1x2_odds if _valid_ref(away_1x2_odds) else None

    # ODDS-based favourite + near-even (Java XupMargin) drive the MARGIN: which
    # boost % and which reduction % each side gets. Evaluated PER SIDE so a tie
    # or a missing opposite price reads both sides as favourite, like Java.
    margin_home_is_fav = _is_favorite_by_odds(home_ref, away_ref)
    margin_away_is_fav = _is_favorite_by_odds(away_ref, home_ref)
    near_even = _near_even_by_odds(home_ref, away_ref, NEAR_EVEN_THRESHOLD)

    # Per-side 1UP cap reduction % keyed by the ODDS favourite.
    oneup_home_red_pct = ONEUP_FAVORITE_REDUCTION_PCT if margin_home_is_fav else ONEUP_UNDERDOG_REDUCTION_PCT
    oneup_away_red_pct = ONEUP_FAVORITE_REDUCTION_PCT if margin_away_is_fav else ONEUP_UNDERDOG_REDUCTION_PCT
```

**Edit F — 1UP LEVEL-score branch (DP-direct).** OLD (the whole `if goal_difference == 0:` level block, ending just before `else:`):

```python
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

            home_1up_capped, _ = _cap_selection(home_1up_fair_odds, home_1up_prob, home_1x2_odds, oneup_home_red_pct)
            away_1up_capped, _ = _cap_selection(away_1up_fair_odds, away_1up_prob, away_1x2_odds, oneup_away_red_pct)
```

NEW:

```python
    if goal_difference == 0:
        # ---- LEVEL SCORE 1UP: DP-direct (next-goal regression REMOVED) ----
        # Java calculate1upLevelProbabilities reads the ever-leads DP directly;
        # pHomeEver1 / pAwayEver1 already include the win mass, so there is no
        # inclusion-exclusion and no FTTS dependence at level score.
        stats = ever_leads_probability(lambda_home, lambda_away, 0)
        home_1up_prob = _clamp_prob(stats[0])   # p_home_ever_1
        away_1up_prob = _clamp_prob(stats[1])   # p_away_ever_1

        home_1up_fair_odds = _apply_boost(
            _fair_prob_to_odds(home_1up_prob, ONEUP_MARGIN_LEVEL, ONEUP_MARGIN_TILT),
            margin_home_is_fav, near_even,
            ONEUP_FAVORITE_ODDS_BOOST_PCT, ONEUP_UNDERDOG_ODDS_BOOST_PCT,
        )
        away_1up_fair_odds = _apply_boost(
            _fair_prob_to_odds(away_1up_prob, ONEUP_MARGIN_LEVEL, ONEUP_MARGIN_TILT),
            margin_away_is_fav, near_even,
            ONEUP_FAVORITE_ODDS_BOOST_PCT, ONEUP_UNDERDOG_ODDS_BOOST_PCT,
        )

        home_1up_capped, _ = _cap_selection(home_1up_fair_odds, home_1up_prob, home_ref, oneup_home_red_pct)
        away_1up_capped, _ = _cap_selection(away_1up_fair_odds, away_1up_prob, away_ref, oneup_away_red_pct)
```

**Edit G — trailing 1UP branch: odds favourite + ref cap source.** OLD (inside the `else:` block):

```python
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
            home_1up_fair_odds, home_1up_prob_raw, home_1x2_odds,
            oneup_home_red_pct,
        )
        away_1up_capped, _ = _cap_selection(
            away_1up_fair_odds, away_1up_prob_raw, away_1x2_odds,
            oneup_away_red_pct,
        )
```

NEW:

```python
        home_1up_fair_odds = _apply_boost(
            _fair_prob_to_odds(home_1up_prob_raw, ONEUP_MARGIN_LEVEL, ONEUP_MARGIN_TILT),
            margin_home_is_fav, near_even,
            ONEUP_FAVORITE_ODDS_BOOST_PCT, ONEUP_UNDERDOG_ODDS_BOOST_PCT,
        )
        away_1up_fair_odds = _apply_boost(
            _fair_prob_to_odds(away_1up_prob_raw, ONEUP_MARGIN_LEVEL, ONEUP_MARGIN_TILT),
            margin_away_is_fav, near_even,
            ONEUP_FAVORITE_ODDS_BOOST_PCT, ONEUP_UNDERDOG_ODDS_BOOST_PCT,
        )

        home_1up_capped, _ = _cap_selection(
            home_1up_fair_odds, home_1up_prob_raw, home_ref,
            oneup_home_red_pct,
        )
        away_1up_capped, _ = _cap_selection(
            away_1up_fair_odds, away_1up_prob_raw, away_ref,
            oneup_away_red_pct,
        )
```

**Edit H — 2UP: drop the redundant level-score DP recompute.** OLD:

```python
    # ============== 2UP (UNIFIED) ==============
    # Reuse the trailing-branch stats when goal_difference != 0 to avoid a
    # second DP pass; recompute only on the level-score path.
    if goal_difference == 0:
        stats = ever_leads_probability(lambda_home, lambda_away, goal_difference)
    p_home_ever_2 = stats[4]
```

NEW:

```python
    # ============== 2UP (UNIFIED) ==============
    # `stats` is already computed above on BOTH paths (the level branch reads
    # ever1 directly from it; the trailing branch built it for the 1UP
    # residual) — reuse it, no second DP pass.
    p_home_ever_2 = stats[4]
```

**Edit I — 2UP margin: odds favourite + ref cap source.** OLD:

```python
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

    home_red_pct = TWOUP_FAVORITE_REDUCTION_PCT if home_is_favorite else TWOUP_UNDERDOG_REDUCTION_PCT
    away_red_pct = TWOUP_UNDERDOG_REDUCTION_PCT if home_is_favorite else TWOUP_FAVORITE_REDUCTION_PCT

    home_2up_capped, _ = _cap_selection(home_2up_fair_odds, home_2up_prob_raw, home_1x2_odds, home_red_pct)
    away_2up_capped, _ = _cap_selection(away_2up_fair_odds, away_2up_prob_raw, away_1x2_odds, away_red_pct)
```

NEW:

```python
    # V4 margin: single (level, tilt) per market. Boost side keyed by the ODDS
    # favourite; the boost COEFFICIENT blend above stays probability-based.
    home_2up_fair_odds = _apply_boost(
        _fair_prob_to_odds(home_2up_prob_raw, TWOUP_MARGIN_LEVEL, TWOUP_MARGIN_TILT),
        margin_home_is_fav, near_even,
        TWOUP_FAVORITE_ODDS_BOOST_PCT, TWOUP_UNDERDOG_ODDS_BOOST_PCT,
    )
    away_2up_fair_odds = _apply_boost(
        _fair_prob_to_odds(away_2up_prob_raw, TWOUP_MARGIN_LEVEL, TWOUP_MARGIN_TILT),
        margin_away_is_fav, near_even,
        TWOUP_FAVORITE_ODDS_BOOST_PCT, TWOUP_UNDERDOG_ODDS_BOOST_PCT,
    )

    # 2UP cap reduction % keyed by the ODDS favourite (asymmetric fav 2.0% /
    # dog 0.5%) — picking this side by odds vs probability changes the capped odd.
    home_red_pct = TWOUP_FAVORITE_REDUCTION_PCT if margin_home_is_fav else TWOUP_UNDERDOG_REDUCTION_PCT
    away_red_pct = TWOUP_FAVORITE_REDUCTION_PCT if margin_away_is_fav else TWOUP_UNDERDOG_REDUCTION_PCT

    home_2up_capped, _ = _cap_selection(home_2up_fair_odds, home_2up_prob_raw, home_ref, home_red_pct)
    away_2up_capped, _ = _cap_selection(away_2up_fair_odds, away_2up_prob_raw, away_ref, away_red_pct)
```

**Edit J — invalid-ref deactivation (before the return).** OLD:

```python
    if max_home_lead >= 2:
        home_2up_prob = home_2up_fair_odds = home_2up_capped = None
    if max_away_lead >= 2:
        away_2up_prob = away_2up_fair_odds = away_2up_capped = None

    return {
```

NEW:

```python
    if max_home_lead >= 2:
        home_2up_prob = home_2up_fair_odds = home_2up_capped = None
    if max_away_lead >= 2:
        away_2up_prob = away_2up_fair_odds = away_2up_capped = None

    # Invalid 1X2 cap reference deactivates BOTH UP markets for that side
    # (Java priceSide returns DEACTIVATED when its own win odd is missing or
    # not a real decimal odd > 1.0). Replaces V3's "no source -> floor to 1.01".
    if home_ref is None:
        home_1up_prob = home_1up_fair_odds = home_1up_capped = None
        home_2up_prob = home_2up_fair_odds = home_2up_capped = None
    if away_ref is None:
        away_1up_prob = away_1up_fair_odds = away_1up_capped = None
        away_2up_prob = away_2up_fair_odds = away_2up_capped = None

    return {
```

- [ ] **Step 3: Sanity-import the new module**

Run: `.venv\Scripts\python -c "from odds_scraper.pricer import engine_v4; print('ok')"`
Expected: prints `ok` (no NameError from a dangling `_apply_model` / `ONEUP_FAVORITE_MODEL` / `fav_lambda` reference). If it raises, a removed symbol is still referenced — re-check Edits B/C/E/F.

- [ ] **Step 4: Write the test file** `tests/test_pricer_engine_v4.py`

```python
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
        "home_ou": [_ou(0.5, 1.30, 3.40), _ou(1.5, 2.10, 1.75)],
        "away_ou": [_ou(0.5, 1.40, 3.00), _ou(1.5, 2.30, 1.65)],
        "total_ou": [_ou(1.5, 1.25, 4.00), _ou(2.5, 1.85, 1.95), _ou(3.5, 3.20, 1.35)],
        "ftts_home_prob": 0.48, "ftts_away_prob": 0.45,
    }


# ---------- Delta 3 helpers: odds-based favourite + near-even ----------

def test_v4_valid_ref():
    for bad in (None, 0.0, 1.0, 0.99, -2.0):
        assert ep_v4._valid_ref(bad) is False, bad
    for ok in (1.01, 2.0, 100.0):
        assert ep_v4._valid_ref(ok) is True, ok


def test_v4_is_favorite_by_odds():
    assert ep_v4._is_favorite_by_odds(1.5, 3.0) is True    # lower odds = favourite
    assert ep_v4._is_favorite_by_odds(3.0, 1.5) is False
    assert ep_v4._is_favorite_by_odds(2.0, 2.0) is True    # tie -> favourite (<=)
    assert ep_v4._is_favorite_by_odds(None, 2.0) is False  # missing own price
    assert ep_v4._is_favorite_by_odds(2.0, None) is True   # missing opposite -> favourite


def test_v4_near_even_by_odds_strict_boundary():
    # gap = |0.5 - 0.5| = 0 < 0.03 -> near-even
    assert ep_v4._near_even_by_odds(2.0, 2.0, 0.03) is True
    # gap exactly 0.03 is NOT < 0.03 (strict) -> not near-even.
    # home implied 0.5 (odds 2.0); away implied 0.47 (odds 1/0.47).
    away = 1.0 / 0.47
    assert ep_v4._near_even_by_odds(2.0, away, 0.03) is False
    # just inside the threshold -> near-even.
    away_in = 1.0 / 0.475
    assert ep_v4._near_even_by_odds(2.0, away_in, 0.03) is True
    # missing / non-positive ref -> not near-even.
    assert ep_v4._near_even_by_odds(None, 2.0, 0.03) is False
    assert ep_v4._near_even_by_odds(2.0, 0.0, 0.03) is False


# ---------- Margin goldens (XupMarginTest) ----------

def test_v4_no_margin_returns_inverse_probability():
    # level=0, tilt=1 -> odds = 1/p.
    assert ep_v4._fair_prob_to_odds(0.5, 0.0, 1.0) == pytest.approx(2.0)
    assert ep_v4._fair_prob_to_odds(0.8, 0.0, 1.0) == pytest.approx(1.25)


def test_v4_cap_binds_to_ceiling():
    # fair 2.0 vs ceiling 1.5*(1-0) = 1.5 -> capped to 1.5.
    odds, prob = ep_v4._cap_selection(2.0, 0.5, 1.5, 0.0)
    assert odds == pytest.approx(1.5)
    assert prob == pytest.approx(1.0 / 1.5)
    # 10% reduction lowers the ceiling to 1.35.
    odds, _ = ep_v4._cap_selection(2.0, 0.5, 1.5, 10.0)
    assert odds == pytest.approx(1.35)


def test_v4_min_odds_floor():
    # p=1.0 clamps to 1-eps -> implied ~1 -> odds ~1.0; cap floors to 1.01.
    fair = ep_v4._fair_prob_to_odds(1.0, 0.0, 1.0)
    odds, _ = ep_v4._cap_selection(fair, 1.0, None, 0.0)
    assert odds == pytest.approx(ep_v4.CAP_MIN_OFFERED_ODDS)


def test_v4_boost_lengthens_then_suppressed_near_even():
    assert ep_v4._apply_boost(2.0, True, False, 10.0, 20.0) == pytest.approx(2.2)   # favourite
    assert ep_v4._apply_boost(2.0, False, False, 10.0, 20.0) == pytest.approx(2.4)  # underdog
    assert ep_v4._apply_boost(2.0, True, True, 10.0, 20.0) == pytest.approx(2.0)    # near-even -> no boost


# ---------- Delta 1: DP-direct level 1UP differs from v3's regression ----------

def test_v4_level_1up_differs_from_v3(balanced_match):
    """At a level score with FTTS supplied, v4 reads the DP directly while v3
    runs the next-goal regression — the level 1UP probabilities must differ."""
    r3 = ep_v3.price_early_payout_markets(**balanced_match)
    r4 = ep_v4.price_early_payout_markets(**balanced_match)
    assert r4["p_home_1"] is not None and r3["p_home_1"] is not None
    assert r4["p_home_1"] != pytest.approx(r3["p_home_1"]), "v4 level 1UP should differ from v3"


# ---------- Delta 1: trailing 1UP and all 2UP match v3 when favourites agree ----------

def test_v4_matches_v3_when_favourites_agree(balanced_match):
    """When the odds favourite equals the probability favourite (the normal
    case — both come from the same 1X2), trailing 1UP and ALL 2UP match v3.
    Score 1-0 makes 1UP trailing-only (home leading deactivated, away trailing)."""
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


# ---------- Delta 3: odds-vs-prob favourite flip changes the 2UP reduction side ----------

def _flip_inputs(*, home_odds, away_odds, p_home, p_away):
    """1X2 win odds say one side is favourite; the (independently supplied)
    devigged probs say the other is. OU lines give derivable lambdas."""
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
    (0.2 < 0.6). The home 2UP odd is long enough to bind the cap, so the
    reduction-side choice shows: v4 uses fav 2.0% (ceiling 1.5*0.98=1.47);
    v3 uses dog 0.5% (ceiling 1.5*0.995=1.4925)."""
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


# ---------- Delta 2: invalid 1X2 ref deactivates the side ----------

@pytest.mark.parametrize("bad", [None, 0.0, 1.0, 0.99])
def test_v4_invalid_ref_deactivates_home_v3_does_not(balanced_match, bad):
    inp = dict(balanced_match, home_1x2_odds=bad)
    r4 = ep_v4.price_early_payout_markets(**inp)
    r3 = ep_v3.price_early_payout_markets(**inp)
    # v4: home side fully deactivated for BOTH markets.
    assert r4["p_home_1"] is None and r4["p_home_2"] is None
    assert r4["market_1up"]["home_margin"] is None and r4["market_2up"]["home_margin"] is None
    # away side still active.
    assert r4["market_2up"]["away_margin"] is not None
    # v3 keeps home active (floors against no/invalid source instead of None).
    assert r3["market_2up"]["home_margin"] is not None


# ---------- Core invariant: P(1UP) >= P(2UP) per side at every score ----------

@pytest.mark.parametrize("score", [(0, 0), (1, 0), (0, 1), (1, 1), (2, 1)])
def test_v4_oneup_prob_ge_twoup_prob(balanced_match, score):
    r = ep_v4.price_early_payout_markets(**dict(balanced_match, score=score))
    for one_k, two_k in (("p_home_1", "p_home_2"), ("p_away_1", "p_away_2")):
        p1, p2 = r[one_k], r[two_k]
        if p1 is None or p2 is None:
            continue
        assert p1 >= p2 - 1e-9, f"{one_k}={p1} < {two_k}={p2} at score {score}"
```

- [ ] **Step 5: Run the v4 engine tests**

Run: `.venv\Scripts\python -m pytest tests/test_pricer_engine_v4.py -q`
Expected: PASS (all). If `test_v4_2up_reduction_flips_*` fails because the cap didn't bind, print `r4["market_2up"]["home_fair"]` — it must exceed the ceiling (~1.47); if not, the constructed prob/odds need the fair odd longer (lower the binding side's win prob).

- [ ] **Step 6: Commit**

```bash
git add src/odds_scraper/pricer/engine_v4.py tests/test_pricer_engine_v4.py
git commit -m "feat(pricer): add v4 engine (latest Java port)

DP-direct 1UP level score (regression removed), deactivate a side whose
1X2 cap reference is invalid, and odds-based favourite/near-even for the
margin (2UP coefficient blend stays probability-based). Goldens
cross-checked against XupMarginTest.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Verify `configs.py` needs no change (regression guard)

**Files:**
- Test: `tests/test_pricer_engine_v4.py` (append)

v4 reuses v3's tunable keys and defines no new ones, so `configs.py` is untouched. Lock that assumption with a test so a future key addition fails loudly.

- [ ] **Step 1: Append the guard test**

```python
def test_v4_tunables_are_subset_of_v3_only_names():
    """v4 defines no config key outside the existing V3_ONLY set, so
    configs.py needs no v4-specific handling and with_v4_coefficients'
    hasattr filter covers it. (The removed regression-model names must be
    absent on engine_v4.)"""
    from odds_scraper.pricer import configs
    for name in configs.V3_ONLY_TUNABLE_NAMES:
        assert hasattr(ep_v4, name), f"v4 missing expected tunable {name}"
    # The 1UP regression models are gone in v4.
    assert not hasattr(ep_v4, "ONEUP_FAVORITE_MODEL")
    assert not hasattr(ep_v4, "ONEUP_UNDERDOG_MODEL")
```

- [ ] **Step 2: Run it**

Run: `.venv\Scripts\python -m pytest tests/test_pricer_engine_v4.py::test_v4_tunables_are_subset_of_v3_only_names -q`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_pricer_engine_v4.py
git commit -m "test(pricer): lock v4 tunables as a subset of V3_ONLY (no configs change)"
```

---

## Task 3: Wire v4 into the CSV columns and the dual runner (one commit)

**Files:**
- Modify: `src/odds_scraper/pricer/csv_export.py`
- Modify: `src/odds_scraper/pricer/runner_v2.py`
- Test: `tests/test_pricer_csv.py`, `tests/test_pricer_runner_v2.py`

> These land together: the runner row tuple is positional against `CSV_COLUMNS`. Adding one without the other misaligns CSV rows and breaks existing tests.

- [ ] **Step 1: Add v4 + pB_v4 column blocks to `csv_export.py`**

Edit 1 — after the v3 main block. OLD (the v3 block, ending at `"v3_our_2up_away_fair", ...`):

```python
    "v3_p_home_2", "v3_p_away_2",
    "v3_our_2up_home_fair", "v3_our_2up_home_capped", "v3_our_2up_home_capped_ev",
    "v3_our_2up_away_fair", "v3_our_2up_away_capped", "v3_our_2up_away_capped_ev",
    # BP / SB carry per-selection true prob + odds + EV. EV uses OUR
```

NEW:

```python
    "v3_p_home_2", "v3_p_away_2",
    "v3_our_2up_home_fair", "v3_our_2up_home_capped", "v3_our_2up_home_capped_ev",
    "v3_our_2up_away_fair", "v3_our_2up_away_capped", "v3_our_2up_away_capped_ev",
    # ===== V4 engine block — same layout as V3's, prefixed v4_. Blank
    # unless V4 (or a selection including it) ran. V4 is the latest Java
    # port: DP-direct level 1UP, invalid-ref deactivation, odds-based
    # favourite/near-even for the margin. =====
    "v4_p_home_1", "v4_p_away_1",
    "v4_our_1up_home_fair", "v4_our_1up_home_capped", "v4_our_1up_home_capped_ev",
    "v4_our_1up_away_fair", "v4_our_1up_away_capped", "v4_our_1up_away_capped_ev",
    "v4_p_home_2", "v4_p_away_2",
    "v4_our_2up_home_fair", "v4_our_2up_home_capped", "v4_our_2up_home_capped_ev",
    "v4_our_2up_away_fair", "v4_our_2up_away_capped", "v4_our_2up_away_capped_ev",
    # BP / SB carry per-selection true prob + odds + EV. EV uses OUR
```

Edit 2 — after the pB_v3 block. OLD:

```python
    "pB_v3_p_home_2", "pB_v3_p_away_2",
    "pB_v3_our_2up_home_fair", "pB_v3_our_2up_home_capped", "pB_v3_our_2up_home_capped_ev",
    "pB_v3_our_2up_away_fair", "pB_v3_our_2up_away_capped", "pB_v3_our_2up_away_capped_ev",
    "pB_bp_1up_home_ev", "pB_bp_1up_away_ev",
```

NEW:

```python
    "pB_v3_p_home_2", "pB_v3_p_away_2",
    "pB_v3_our_2up_home_fair", "pB_v3_our_2up_home_capped", "pB_v3_our_2up_home_capped_ev",
    "pB_v3_our_2up_away_fair", "pB_v3_our_2up_away_capped", "pB_v3_our_2up_away_capped_ev",
    "pB_v4_p_home_1", "pB_v4_p_away_1",
    "pB_v4_our_1up_home_fair", "pB_v4_our_1up_home_capped", "pB_v4_our_1up_home_capped_ev",
    "pB_v4_our_1up_away_fair", "pB_v4_our_1up_away_capped", "pB_v4_our_1up_away_capped_ev",
    "pB_v4_p_home_2", "pB_v4_p_away_2",
    "pB_v4_our_2up_home_fair", "pB_v4_our_2up_home_capped", "pB_v4_our_2up_home_capped_ev",
    "pB_v4_our_2up_away_fair", "pB_v4_our_2up_away_capped", "pB_v4_our_2up_away_capped_ev",
    "pB_bp_1up_home_ev", "pB_bp_1up_away_ev",
```

- [ ] **Step 2: Wire v4 into `runner_v2.py`**

Edit 1 — import. OLD:

```python
from . import (
    engine, engine_v2, engine_v3, inputs as input_extract, configs as config_mod,
    csv_export, score_state,
)
```

NEW:

```python
from . import (
    engine, engine_v2, engine_v3, engine_v4, inputs as input_extract, configs as config_mod,
    csv_export, score_state,
)
```

Edit 2 — `VALID_ENGINES`. OLD: `VALID_ENGINES = ("v1", "v2", "v3")` → NEW: `VALID_ENGINES = ("v1", "v2", "v3", "v4")`

Edit 3 — add `with_v4_coefficients` right after the `with_v3_coefficients` function (after its closing `setattr(engine_v3, k, v)` finally block). Insert:

```python


@contextmanager
def with_v4_coefficients(overrides: dict) -> Iterator[None]:
    """Mirror of `with_v3_coefficients` but targeting engine_v4. The hasattr
    filter skips keys engine_v4 doesn't define (the removed 1UP regression
    models, V1/V2 trailing margins) — V4 reads the same ONEUP/TWOUP margin /
    boost / reduction / near-even constants V3 does."""
    applicable = {k: v for k, v in overrides.items() if hasattr(engine_v4, k)}
    saved = {k: getattr(engine_v4, k) for k in applicable}
    try:
        for k, v in applicable.items():
            setattr(engine_v4, k, v)
        yield
    finally:
        for k, v in saved.items():
            setattr(engine_v4, k, v)
```

Edit 4 — `_run_engines`. OLD:

```python
    def _run_engines(inputs: dict) -> tuple[Optional[dict], Optional[dict], Optional[dict]]:
        """Call whichever engines are selected — caller's
        `with_*_coefficients` context decides which profile's
        coefficients are in force."""
        r1 = None
        r2 = None
        r3 = None
        if "v1" in eng:
            try:
                r1 = engine.price_early_payout_markets(**inputs)
            except Exception as exc:  # noqa: BLE001
                log.warning("v1 engine crashed on event=%s ts=%s — skipping (%s)",
                            inputs.get("_event_id"), inputs.get("_ts_utc"), exc)
        if "v2" in eng:
            try:
                r2 = engine_v2.price_early_payout_markets(**inputs)
            except Exception as exc:  # noqa: BLE001
                log.warning("v2 engine crashed on event=%s ts=%s — skipping (%s)",
                            inputs.get("_event_id"), inputs.get("_ts_utc"), exc)
        if "v3" in eng:
            try:
                r3 = engine_v3.price_early_payout_markets(**inputs)
            except Exception as exc:  # noqa: BLE001
                log.warning("v3 engine crashed on event=%s ts=%s — skipping (%s)",
                            inputs.get("_event_id"), inputs.get("_ts_utc"), exc)
        return r1, r2, r3
```

NEW:

```python
    def _run_engines(inputs: dict) -> tuple[Optional[dict], Optional[dict], Optional[dict], Optional[dict]]:
        """Call whichever engines are selected — caller's
        `with_*_coefficients` context decides which profile's
        coefficients are in force."""
        r1 = None
        r2 = None
        r3 = None
        r4 = None
        if "v1" in eng:
            try:
                r1 = engine.price_early_payout_markets(**inputs)
            except Exception as exc:  # noqa: BLE001
                log.warning("v1 engine crashed on event=%s ts=%s — skipping (%s)",
                            inputs.get("_event_id"), inputs.get("_ts_utc"), exc)
        if "v2" in eng:
            try:
                r2 = engine_v2.price_early_payout_markets(**inputs)
            except Exception as exc:  # noqa: BLE001
                log.warning("v2 engine crashed on event=%s ts=%s — skipping (%s)",
                            inputs.get("_event_id"), inputs.get("_ts_utc"), exc)
        if "v3" in eng:
            try:
                r3 = engine_v3.price_early_payout_markets(**inputs)
            except Exception as exc:  # noqa: BLE001
                log.warning("v3 engine crashed on event=%s ts=%s — skipping (%s)",
                            inputs.get("_event_id"), inputs.get("_ts_utc"), exc)
        if "v4" in eng:
            try:
                r4 = engine_v4.price_early_payout_markets(**inputs)
            except Exception as exc:  # noqa: BLE001
                log.warning("v4 engine crashed on event=%s ts=%s — skipping (%s)",
                            inputs.get("_event_id"), inputs.get("_ts_utc"), exc)
        return r1, r2, r3, r4
```

Edit 5 — Profile A context manager + call. OLD:

```python
    with with_v1_coefficients(engine_overrides_v1), \
         with_v2_coefficients(engine_overrides), \
         with_v3_coefficients(engine_overrides):
```

NEW:

```python
    with with_v1_coefficients(engine_overrides_v1), \
         with_v2_coefficients(engine_overrides), \
         with_v3_coefficients(engine_overrides), \
         with_v4_coefficients(engine_overrides):
```

Edit 6 — Profile A `_run_engines` unpack. OLD:

```python
            r_v1, r_v2, r_v3 = _run_engines(
                {k: v for k, v in engine_inputs.items() if not k.startswith("_")}
            )
```

NEW:

```python
            r_v1, r_v2, r_v3, r_v4 = _run_engines(
                {k: v for k, v in engine_inputs.items() if not k.startswith("_")}
            )
```

Edit 7 — Profile B init + context + call. OLD:

```python
            r_v1_b = None
            r_v2_b = None
            r_v3_b = None
            if engine_overrides_b is not None:
                with with_v1_coefficients(engine_overrides_b_v1), \
                     with_v2_coefficients(engine_overrides_b), \
                     with_v3_coefficients(engine_overrides_b):
                    r_v1_b, r_v2_b, r_v3_b = _run_engines(
                        {k: v for k, v in engine_inputs.items() if not k.startswith("_")}
                    )
```

NEW:

```python
            r_v1_b = None
            r_v2_b = None
            r_v3_b = None
            r_v4_b = None
            if engine_overrides_b is not None:
                with with_v1_coefficients(engine_overrides_b_v1), \
                     with_v2_coefficients(engine_overrides_b), \
                     with_v3_coefficients(engine_overrides_b), \
                     with_v4_coefficients(engine_overrides_b):
                    r_v1_b, r_v2_b, r_v3_b, r_v4_b = _run_engines(
                        {k: v for k, v in engine_inputs.items() if not k.startswith("_")}
                    )
```

Edit 8 — success detection. OLD:

```python
            a_succeeded = (
                ("v1" in eng and r_v1 is not None)
                or ("v2" in eng and r_v2 is not None)
                or ("v3" in eng and r_v3 is not None)
            )
            a_failed = not a_succeeded
            b_succeeded = (
                ("v1" in eng and r_v1_b is not None)
                or ("v2" in eng and r_v2_b is not None)
                or ("v3" in eng and r_v3_b is not None)
            )
```

NEW:

```python
            a_succeeded = (
                ("v1" in eng and r_v1 is not None)
                or ("v2" in eng and r_v2 is not None)
                or ("v3" in eng and r_v3 is not None)
                or ("v4" in eng and r_v4 is not None)
            )
            a_failed = not a_succeeded
            b_succeeded = (
                ("v1" in eng and r_v1_b is not None)
                or ("v2" in eng and r_v2_b is not None)
                or ("v3" in eng and r_v3_b is not None)
                or ("v4" in eng and r_v4_b is not None)
            )
```

Edit 9 — Profile A `ev_src` fallback. OLD:

```python
            ev_src = r_v1 if r_v1 is not None else (r_v2 if r_v2 is not None else r_v3)
```

NEW:

```python
            ev_src = r_v1 if r_v1 is not None else (
                r_v2 if r_v2 is not None else (r_v3 if r_v3 is not None else r_v4))
```

Edit 10 — add `v4_block` after `v3_block`. OLD:

```python
            v3_block = _our_block(
                r_v3,
                r_v3["p_home_1"] if r_v3 else None,
                r_v3["p_away_1"] if r_v3 else None,
                r_v3["p_home_2"] if r_v3 else None,
                r_v3["p_away_2"] if r_v3 else None,
            )

            lambdas_src = r_v1 if r_v1 is not None else (r_v2 if r_v2 is not None else r_v3)
```

NEW:

```python
            v3_block = _our_block(
                r_v3,
                r_v3["p_home_1"] if r_v3 else None,
                r_v3["p_away_1"] if r_v3 else None,
                r_v3["p_home_2"] if r_v3 else None,
                r_v3["p_away_2"] if r_v3 else None,
            )
            v4_block = _our_block(
                r_v4,
                r_v4["p_home_1"] if r_v4 else None,
                r_v4["p_away_1"] if r_v4 else None,
                r_v4["p_home_2"] if r_v4 else None,
                r_v4["p_away_2"] if r_v4 else None,
            )

            lambdas_src = r_v1 if r_v1 is not None else (
                r_v2 if r_v2 is not None else (r_v3 if r_v3 is not None else r_v4))
```

Edit 11 — add `pB_v4_block` after `pB_v3_block`, extend `pB_ev_src`. OLD:

```python
            pB_v3_block = _our_block(
                r_v3_b,
                r_v3_b["p_home_1"] if r_v3_b else None,
                r_v3_b["p_away_1"] if r_v3_b else None,
                r_v3_b["p_home_2"] if r_v3_b else None,
                r_v3_b["p_away_2"] if r_v3_b else None,
            )
            pB_ev_src = r_v1_b if r_v1_b is not None else (r_v2_b if r_v2_b is not None else r_v3_b)
```

NEW:

```python
            pB_v3_block = _our_block(
                r_v3_b,
                r_v3_b["p_home_1"] if r_v3_b else None,
                r_v3_b["p_away_1"] if r_v3_b else None,
                r_v3_b["p_home_2"] if r_v3_b else None,
                r_v3_b["p_away_2"] if r_v3_b else None,
            )
            pB_v4_block = _our_block(
                r_v4_b,
                r_v4_b["p_home_1"] if r_v4_b else None,
                r_v4_b["p_away_1"] if r_v4_b else None,
                r_v4_b["p_home_2"] if r_v4_b else None,
                r_v4_b["p_away_2"] if r_v4_b else None,
            )
            pB_ev_src = r_v1_b if r_v1_b is not None else (
                r_v2_b if r_v2_b is not None else (r_v3_b if r_v3_b is not None else r_v4_b))
```

Edit 12 — row unpacking, main block. OLD:

```python
                *v1_block,
                *v2_block,
                *v3_block,
                bp["1up_home"][1], bp["1up_home"][0], _ev(p_h1, bp["1up_home"][0]),
```

NEW:

```python
                *v1_block,
                *v2_block,
                *v3_block,
                *v4_block,
                bp["1up_home"][1], bp["1up_home"][0], _ev(p_h1, bp["1up_home"][0]),
```

Edit 13 — row unpacking, pB block. OLD:

```python
                *pB_v1_block,
                *pB_v2_block,
                *pB_v3_block,
                _ev(pB_p_h1, bp["1up_home"][0]), _ev(pB_p_a1, bp["1up_away"][0]),
```

NEW:

```python
                *pB_v1_block,
                *pB_v2_block,
                *pB_v3_block,
                *pB_v4_block,
                _ev(pB_p_h1, bp["1up_home"][0]), _ev(pB_p_a1, bp["1up_away"][0]),
```

- [ ] **Step 3: Add v4 defaults + column-position test to `tests/test_pricer_csv.py`**

Edit 1 — in `_build_row` defaults, after the `v3_*` block (`"v3_our_2up_away_fair": "", ...`), add:

```python
        "v4_p_home_1": "", "v4_p_away_1": "",
        "v4_our_1up_home_fair": "", "v4_our_1up_home_capped": "", "v4_our_1up_home_capped_ev": "",
        "v4_our_1up_away_fair": "", "v4_our_1up_away_capped": "", "v4_our_1up_away_capped_ev": "",
        "v4_p_home_2": "", "v4_p_away_2": "",
        "v4_our_2up_home_fair": "", "v4_our_2up_home_capped": "", "v4_our_2up_home_capped_ev": "",
        "v4_our_2up_away_fair": "", "v4_our_2up_away_capped": "", "v4_our_2up_away_capped_ev": "",
```

Edit 2 — in `_build_row` defaults, after the `pB_v3_*` block, add:

```python
        "pB_v4_p_home_1": "", "pB_v4_p_away_1": "",
        "pB_v4_our_1up_home_fair": "", "pB_v4_our_1up_home_capped": "", "pB_v4_our_1up_home_capped_ev": "",
        "pB_v4_our_1up_away_fair": "", "pB_v4_our_1up_away_capped": "", "pB_v4_our_1up_away_capped_ev": "",
        "pB_v4_p_home_2": "", "pB_v4_p_away_2": "",
        "pB_v4_our_2up_home_fair": "", "pB_v4_our_2up_home_capped": "", "pB_v4_our_2up_home_capped_ev": "",
        "pB_v4_our_2up_away_fair": "", "pB_v4_our_2up_away_capped": "", "pB_v4_our_2up_away_capped_ev": "",
```

Edit 3 — append a column-position test:

```python
def test_csv_columns_include_v4_block_after_v3():
    """The v4 OUR block sits strictly after the v3 block and before the
    bookmaker columns; pB_v4 sits after pB_v3."""
    cols = csv_export.CSV_COLUMNS
    v3_end = cols.index("v3_our_2up_away_capped_ev")
    bp_start = cols.index("bp_p_1up_home")
    for c in (
        "v4_p_home_1", "v4_p_away_1",
        "v4_our_1up_home_fair", "v4_our_1up_home_capped", "v4_our_1up_home_capped_ev",
        "v4_our_1up_away_fair", "v4_our_1up_away_capped", "v4_our_1up_away_capped_ev",
        "v4_p_home_2", "v4_p_away_2",
        "v4_our_2up_home_fair", "v4_our_2up_home_capped", "v4_our_2up_home_capped_ev",
        "v4_our_2up_away_fair", "v4_our_2up_away_capped", "v4_our_2up_away_capped_ev",
    ):
        assert c in cols, f"missing {c}"
        assert v3_end < cols.index(c) < bp_start, f"{c} out of position"
    assert cols.index("pB_v4_p_home_1") > cols.index("pB_v3_our_2up_away_capped_ev")
```

- [ ] **Step 4: Add runner v4 tests to `tests/test_pricer_runner_v2.py`**

Append:

```python
def test_dual_runner_v4_fills_v4_block(db, tmp_path):
    """`engines=('v4',)` populates the v4_* OUR block and leaves v1/v2/v3 blank."""
    _seed_event_with_priced_snapshot(db, "E")
    default = configs.load_default(db)
    p = tmp_path / "sim" / "v4_only.csv"
    runner_v2.run_simulation_dual(
        db, config=default, regime="any", density="all",
        scope=_BASE_SCOPE,
        csv_path=p, engines=("v4",),
    )
    rows = _read_csv(p)
    assert len(rows) == 1
    assert rows[0]["engines"] == "v4"
    assert rows[0]["v4_p_home_1"] != ""
    assert rows[0]["v4_our_1up_home_capped"] != ""
    assert rows[0]["our_p_home_1"] == ""
    assert rows[0]["v2_p_home_1"] == ""
    assert rows[0]["v3_p_home_1"] == ""


def test_dual_runner_all_four_engines(db, tmp_path):
    """`engines=('v1','v2','v3','v4')` fills all four OUR blocks."""
    _seed_event_with_priced_snapshot(db, "E")
    default = configs.load_default(db)
    p = tmp_path / "sim" / "all4.csv"
    runner_v2.run_simulation_dual(
        db, config=default, regime="any", density="all",
        scope=_BASE_SCOPE,
        csv_path=p, engines=("v1", "v2", "v3", "v4"),
    )
    rows = _read_csv(p)
    assert len(rows) == 1
    assert rows[0]["engines"] == "v1,v2,v3,v4"
    assert rows[0]["our_p_home_1"] != ""
    assert rows[0]["v2_p_home_1"] != ""
    assert rows[0]["v3_p_home_1"] != ""
    assert rows[0]["v4_p_home_1"] != ""
```

- [ ] **Step 5: Run the CSV + runner tests**

Run: `.venv\Scripts\python -m pytest tests/test_pricer_csv.py tests/test_pricer_runner_v2.py -q`
Expected: PASS (all, including the existing v1/v2/v3 tests — proves row/column alignment held).

- [ ] **Step 6: Commit**

```bash
git add src/odds_scraper/pricer/csv_export.py src/odds_scraper/pricer/runner_v2.py tests/test_pricer_csv.py tests/test_pricer_runner_v2.py
git commit -m "feat(pricer): wire v4 into CSV columns + dual runner

Adds v4_* and pB_v4_* 16-cell blocks after v3, registers v4 in
VALID_ENGINES, with_v4_coefficients, _run_engines, the EV/lambda
fallback chains, and the row tuples.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Make v4 the latest engine in the web simulator

**Files:**
- Modify: `src/odds_scraper/web/pricer_routes.py`
- Modify: `src/odds_scraper/web/templates/simulator.html`

- [ ] **Step 1: Bump `LATEST_ENGINE` and the engine-list comment in `pricer_routes.py`**

OLD:

```python
# The simulator page submits zero or more `engine` checkbox values, each
# one of runner_v2.VALID_ENGINES ("v1"/"v2"/"v3"). We normalise to canonical
# (v1, v2, v3) order so CSV columns and the run-history string are stable
# regardless of checkbox order. Empty selection falls back to the latest
# engine. A lone "v1" with no profile B stays on the lean pre-V2 runner
# (byte-identical layout); anything else routes through the dual runner.
LATEST_ENGINE = "v3"
```

NEW:

```python
# The simulator page submits zero or more `engine` checkbox values, each
# one of runner_v2.VALID_ENGINES ("v1"/"v2"/"v3"/"v4"). We normalise to
# canonical (v1, v2, v3, v4) order so CSV columns and the run-history string
# are stable regardless of checkbox order. Empty selection falls back to the
# latest engine. A lone "v1" with no profile B stays on the lean pre-V2 runner
# (byte-identical layout); anything else routes through the dual runner.
LATEST_ENGINE = "v4"
```

- [ ] **Step 2: Add the v4 checkbox (checked) and uncheck v3 in `simulator.html`**

OLD:

```html
            <label><input type="checkbox" name="engine" value="v3" checked>
              <b>V3</b> <span class="filter-lbl">— latest · logit-linear margin + odds boost</span></label>
          </div>
```

NEW:

```html
            <label><input type="checkbox" name="engine" value="v3">
              <b>V3</b> <span class="filter-lbl">— logit-linear margin + odds boost</span></label>
            <label><input type="checkbox" name="engine" value="v4" checked>
              <b>V4</b> <span class="filter-lbl">— latest · DP-direct 1UP, odds-based margin favourite</span></label>
          </div>
```

- [ ] **Step 3: Smoke-test the routes import and engine validation**

Run: `.venv\Scripts\python -c "from odds_scraper.web import pricer_routes; from odds_scraper.pricer import runner_v2; assert pricer_routes.LATEST_ENGINE == 'v4'; assert 'v4' in runner_v2.VALID_ENGINES; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 4: Run the simulator route tests (if v4 affects them)**

Run: `.venv\Scripts\python -m pytest tests/test_simulator_routes.py -q`
Expected: PASS. If a test pins the default-checked engine to "v3", update that assertion to "v4" (the default latest changed by design).

- [ ] **Step 5: Commit**

```bash
git add src/odds_scraper/web/pricer_routes.py src/odds_scraper/web/templates/simulator.html
git commit -m "feat(web/sim): make v4 the latest engine (checked by default)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Final verification

**Files:** none (verification only).

- [ ] **Step 1: Run the full relevant test set**

Run:

```bash
.venv\Scripts\python -m pytest tests/test_pricer_engine_v4.py tests/test_pricer_runner_v2.py tests/test_pricer_csv.py -q
```

Expected: all PASS, 0 failures.

- [ ] **Step 2: Guard against regressions in the broader pricer + web suites**

Run:

```bash
.venv\Scripts\python -m pytest tests/test_pricer_configs.py tests/test_pricer_engine_v3.py tests/test_simulator_routes.py tests/test_web_app.py -q
```

Expected: all PASS (v4 added columns/engine should not break existing behaviour). Investigate any failure before claiming done.

- [ ] **Step 3: Report results**

State the exact pass/fail counts from Steps 1–2. Do not claim completion unless both runs are green.

---

## Self-Review (completed by plan author)

**Spec coverage:** Delta 1 (DP-direct level 1UP) → Edits F/H + `test_v4_level_1up_differs_from_v3`. Delta 2 (invalid-ref deactivation) → Edits E/G/I + `test_v4_invalid_ref_deactivates_*`. Delta 3 (odds favourite/near-even for margin only; prob favourite for coefficient blend) → Edits D/E/G/I + the two `_2up_reduction_flips_*` tests; coefficient blend untouched (verified — Edit H leaves `home_coeff`/`away_coeff` and `_blend_boost` as-is). Config reuse of `V3_ONLY` → Task 2 guard test. Wiring (runner/csv/routes/html, v4 = latest, fallback last) → Tasks 3–4. Phase 2 (live_writer/schema) → explicitly out of scope. Goldens vs Java → Task 1 Step 4. `P(1UP) >= P(2UP)` invariant → `test_v4_oneup_prob_ge_twoup_prob`.

**Placeholder scan:** none — every code/edit step shows complete content; commands have expected output.

**Type/name consistency:** `_run_engines` 4-tuple matches both unpack sites and the type hint; `v4_block`/`pB_v4_block` use the existing `_our_block`; column names match between `csv_export.py`, `_build_row` defaults, and the runner row order; `margin_home_is_fav`/`margin_away_is_fav`/`home_ref`/`away_ref` are defined in Edit E before first use in Edits F/G/I.

**Known sensitivity:** the `_2up_reduction_flips_*` tests assume the binding side's 2UP fair odd exceeds its ~1.47 ceiling. Step 5 of Task 1 documents the fallback if a constructed case doesn't bind.
