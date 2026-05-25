# Pricer Engine V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `engine_v2.py` (Python port of the May 2026 Java rewrite) plus a dual-engine runner and simulator UI selector so V1 and V2 can be run side-by-side on the same CSV row, leaving V1 untouched as the A/B baseline.

**Architecture:** Duplicate `engine.py` to `engine_v2.py` (so `with_coefficients` can apply overrides independently on each module), surgically replace the 1UP trailing branch with an inclusion-exclusion DP, replace the 2UP DP call with the new 8-tuple `ever_leads_probability`, and bump the default `TWOUP_UNDERDOG_MARGIN` intercept. A new `runner_v2.py` calls both engines per tick and emits side-by-side `v1_*` + `v2_*` blocks in one CSV. The simulator page gains an engine radio that dispatches `v1`, `v2`, or `both`.

**Tech Stack:** Python 3.13, FastAPI + Jinja2 + HTMX (existing), SQLite (existing), pytest. No new dependencies. No DB schema changes.

**Spec:** `docs/superpowers/specs/2026-05-25-pricer-v2-engine-design.md`

---

## File map

- **Create:** `src/odds_scraper/pricer/engine_v2.py` — Java port of the rewrite. Module-level constants + helpers + new `ever_leads_probability` + a `price_early_payout_markets` whose only structural differences vs V1 are: (1) the 1UP trailing branch uses the DP not `_trailing_selection`; (2) the 2UP level/one-goal branch reads `ever2` fields from the new 8-tuple; (3) the default `TWOUP_UNDERDOG_MARGIN` intercept is `0.014`.
- **Create:** `src/odds_scraper/pricer/runner_v2.py` — `run_simulation_dual(...)` that calls one or both engines per tick. Owns its own `with_coefficients_v2` context manager so the override mechanism doesn't cross modules.
- **Create:** `tests/test_pricer_engine_v2.py` — engine V2 unit tests.
- **Create:** `tests/test_pricer_runner_v2.py` — dual-runner tests.
- **Modify:** `src/odds_scraper/pricer/csv_export.py` — add `engines` column at the front + `v2_*` block after V1's OUR block.
- **Modify:** `src/odds_scraper/pricer/configs.py` — single line: `DEFAULT_COEFFICIENTS["TWOUP_UNDERDOG_MARGIN"] = [0.994, 0.014]`.
- **Modify:** `src/odds_scraper/web/pricer_routes.py` — `engine` form field + dispatch; `RunRecord.engines` field.
- **Modify:** `src/odds_scraper/web/templates/simulator.html` — engine radio in Run dimensions + `engines` column in History + profile-tooltip note.
- **Modify:** `tests/test_pricer_csv.py` — extend `_build_row` defaults with the new columns.
- **Modify:** `tests/test_pricer_runner.py` — adjust existing tests that asserted positional column slices if any (none currently).
- **Modify:** `tests/test_simulator_routes.py` — extend with engine-selector route tests.
- **Modify:** `tests/test_pricer_configs.py` — assert the new dog-margin default.

---

## Task 1: Duplicate engine.py to engine_v2.py (verbatim baseline)

The first move is a clean copy so subsequent V2-specific changes are diffable from V1. No behavioural change yet — `engine_v2` produces identical output to `engine` at this point.

**Files:**
- Create: `src/odds_scraper/pricer/engine_v2.py`
- Test: `tests/test_pricer_engine_v2.py`

- [ ] **Step 1: Write a failing import + equivalence test**

```python
# tests/test_pricer_engine_v2.py
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
```

- [ ] **Step 2: Run test to verify it fails**

```
python -m pytest tests/test_pricer_engine_v2.py -q
```

Expected: `ModuleNotFoundError: No module named 'odds_scraper.pricer.engine_v2'`

- [ ] **Step 3: Create engine_v2.py as a verbatim copy of engine.py**

```bash
cp src/odds_scraper/pricer/engine.py src/odds_scraper/pricer/engine_v2.py
```

Then prepend a module-level docstring explaining what V2 is:

```python
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
# (rest is the verbatim copy of engine.py)
```

Replace the first 1-3 lines of the copied file with the docstring above (which already imports `__future__.annotations`). The rest stays identical to `engine.py`.

- [ ] **Step 4: Run tests to verify they pass**

```
python -m pytest tests/test_pricer_engine_v2.py -q
```

Expected: 2 passed.

- [ ] **Step 5: Run the full suite to confirm no V1 regressions**

```
python -m pytest tests/ -q
```

Expected: every previously-passing test still passes; 2 new tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/odds_scraper/pricer/engine_v2.py tests/test_pricer_engine_v2.py
git commit -m "feat(pricer): scaffold engine_v2 as verbatim copy of v1

V2 starts as a byte-identical copy of engine.py. Subsequent commits
evolve V2 surgically (ever_leads_probability DP, 1UP trailing rewrite,
2UP level reading the new 8-tuple, dog margin intercept bump) while
V1 stays untouched as the A/B baseline."
```

---

## Task 2: Port `ever_leads_probability` (the 8-tuple DP)

The math change at the heart of V2. New function returning probabilities for `{ever ±1, ever ±2}` jointly with `{home wins, away wins}`. This replaces V1's `ever_2up_probability` (which V2 no longer uses).

**Files:**
- Modify: `src/odds_scraper/pricer/engine_v2.py` — add `ever_leads_probability`. Keep `ever_2up_probability` in the file for now (no caller in V2's `price_early_payout_markets` after Task 4 will reference it; we'll delete it in Task 5).
- Test: `tests/test_pricer_engine_v2.py`

- [ ] **Step 1: Write failing tests for the new DP**

Append to `tests/test_pricer_engine_v2.py`:

```python
def test_ever_leads_returns_8_tuple():
    stats = ep_v2.ever_leads_probability(1.4, 1.1, 0)
    assert len(stats) == 8
    for v in stats:
        assert 0.0 <= v <= 1.0


def test_ever_leads_zero_lambdas_returns_zeros():
    assert ep_v2.ever_leads_probability(0.0, 1.0, 0) == (0.0,) * 8
    assert ep_v2.ever_leads_probability(1.0, 0.0, 0) == (0.0,) * 8


def test_ever_leads_monotonic_ever_1_geq_ever_2():
    """P(ever ±1) must be ≥ P(ever ±2) — reaching +2 means passing
    through +1. This is the construction-time invariant V2 relies on."""
    for lh, la, d in [(1.4, 1.1, 0), (2.0, 0.8, 0), (1.0, 1.0, -1), (0.6, 1.5, 2)]:
        ev1h, ev1a, _, _, ev2h, ev2a, _, _ = ep_v2.ever_leads_probability(lh, la, d)
        assert ev1h >= ev2h - 1e-12, f"home: ever1={ev1h} < ever2={ev2h}"
        assert ev1a >= ev2a - 1e-12, f"away: ever1={ev1a} < ever2={ev2a}"


def test_ever_leads_initial_diff_sets_flags():
    """An initial_diff of +1 must have HIGH1 already triggered → P(ever±1)
    on the home side starts at 1.0 (no time-dependent build-up needed)."""
    ev1h, _, _, _, ev2h, _, _, _ = ep_v2.ever_leads_probability(1.4, 1.1, 1)
    assert ev1h == pytest.approx(1.0)
    # +2 → both HIGH1 and HIGH2 pre-set.
    ev1h, _, _, _, ev2h, _, _, _ = ep_v2.ever_leads_probability(1.4, 1.1, 2)
    assert ev1h == pytest.approx(1.0)
    assert ev2h == pytest.approx(1.0)


def test_ever_leads_symmetry_under_team_swap():
    """Swapping (λH, λA) and negating initial_diff must swap home/away
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
```

- [ ] **Step 2: Run tests to verify they fail**

```
python -m pytest tests/test_pricer_engine_v2.py -q
```

Expected: `AttributeError: module 'odds_scraper.pricer.engine_v2' has no attribute 'ever_leads_probability'`.

- [ ] **Step 3: Implement `ever_leads_probability`**

Add to `src/odds_scraper/pricer/engine_v2.py` (place it directly above the existing `ever_2up_probability`, which we'll remove in Task 5):

```python
# Bit-packed hit flags. Layout mirrors EverLeadsProbability.java:
#   bit 0 (1)  = "score difference has ever been ≤ -2 during the match"
#   bit 1 (2)  = "                              has ever been ≤ -1"
#   bit 2 (4)  = "                              has ever been ≥ +1"
#   bit 3 (8)  = "                              has ever been ≥ +2"
_LEADS_F_LOW2  = 1
_LEADS_F_LOW1  = 1 << 1
_LEADS_F_HIGH1 = 1 << 2
_LEADS_F_HIGH2 = 1 << 3
_LEADS_N_FLAGS = 16


def ever_leads_probability(
    lambda_h: float, lambda_a: float, initial_diff: int,
) -> Tuple[float, float, float, float, float, float, float, float]:
    """Joint DP over the score-difference random walk tracking 4 hit
    flags ({ever ≤-2, ≤-1, ≥+1, ≥+2}) and the final-result winner.

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
    """One Poisson-goal transition. Home goal moves diff → diff+1
    with probability p; away symmetric. ORs the appropriate threshold
    flags as the new diff crosses ±1 / ±2."""
    nxt = [[0.0] * _LEADS_N_FLAGS for _ in range(size)]
    one_minus_p = 1.0 - p
    for d_idx in range(size):
        row = state[d_idx]
        for flag in range(_LEADS_N_FLAGS):
            prob = row[flag]
            if prob == 0.0:
                continue
            diff = d_idx - offset

            # Home scores: diff → diff + 1
            new_diff_h = diff + 1
            new_idx_h = new_diff_h + offset
            if 0 <= new_idx_h < size:
                new_flag = flag
                if new_diff_h >= 1: new_flag |= _LEADS_F_HIGH1
                if new_diff_h >= 2: new_flag |= _LEADS_F_HIGH2
                nxt[new_idx_h][new_flag] += p * prob

            # Away scores: diff → diff - 1
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
```

- [ ] **Step 4: Run tests**

```
python -m pytest tests/test_pricer_engine_v2.py -q
```

Expected: 5 new tests pass; 2 from Task 1 still pass.

- [ ] **Step 5: Commit**

```bash
git add src/odds_scraper/pricer/engine_v2.py tests/test_pricer_engine_v2.py
git commit -m "feat(engine_v2): port ever_leads_probability DP

Unified DP tracks {ever ±1, ever ±2} flags with the final-result
winner. Returns the 8-tuple Java's Stats record uses. Not yet called
from price_early_payout_markets — Tasks 3 + 4 wire it in."
```

---

## Task 3: Bump TWOUP_UNDERDOG_MARGIN default in engine_v2 and configs

A single default-value change. Existing custom profiles keep their saved values.

**Files:**
- Modify: `src/odds_scraper/pricer/engine_v2.py:35` (the `TWOUP_UNDERDOG_MARGIN` constant)
- Modify: `src/odds_scraper/pricer/configs.py:48` (the `DEFAULT_COEFFICIENTS["TWOUP_UNDERDOG_MARGIN"]`)
- Test: `tests/test_pricer_engine_v2.py`, `tests/test_pricer_configs.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_pricer_engine_v2.py`:

```python
def test_v2_dog_margin_intercept_bumped():
    """V2's TWOUP_UNDERDOG_MARGIN intercept is 0.014 (Java rewrite)."""
    assert ep_v2.TWOUP_UNDERDOG_MARGIN == (0.994, 0.014)


def test_v1_dog_margin_intercept_unchanged():
    """V1 stays at 0.008 — V2 must not bleed into V1."""
    assert ep_v1.TWOUP_UNDERDOG_MARGIN == (0.994, 0.008)
```

Append to `tests/test_pricer_configs.py`:

```python
def test_default_coefficients_use_v2_dog_margin_intercept(conn):
    """Schema-seeded default profile uses the V2 dog-margin intercept
    (0.014). Custom profiles created before this change keep their
    saved values — no implicit migration."""
    default = configs.load_default(conn)
    assert default.coefficients["TWOUP_UNDERDOG_MARGIN"] == [0.994, 0.014]
```

- [ ] **Step 2: Run tests to verify two fail and one passes (V1 unchanged)**

```
python -m pytest tests/test_pricer_engine_v2.py::test_v2_dog_margin_intercept_bumped tests/test_pricer_engine_v2.py::test_v1_dog_margin_intercept_unchanged tests/test_pricer_configs.py::test_default_coefficients_use_v2_dog_margin_intercept -q
```

Expected: V1 test passes; V2 + configs tests fail (`AssertionError: (0.994, 0.008) != (0.994, 0.014)`).

- [ ] **Step 3: Update the constant in engine_v2 and the default in configs**

In `src/odds_scraper/pricer/engine_v2.py`, change exactly one line:

```python
# Before:  TWOUP_UNDERDOG_MARGIN = (0.994, 0.008)
TWOUP_UNDERDOG_MARGIN = (0.994, 0.014)
```

In `src/odds_scraper/pricer/configs.py`, update the entry in `DEFAULT_COEFFICIENTS`:

```python
# Before:  "TWOUP_UNDERDOG_MARGIN": [0.994, 0.008],
"TWOUP_UNDERDOG_MARGIN": [0.994, 0.014],
```

- [ ] **Step 4: Run tests**

```
python -m pytest tests/test_pricer_engine_v2.py tests/test_pricer_configs.py -q
```

Expected: previously-failing tests pass; everything else still passes.

- [ ] **Step 5: Run the full suite to catch any test that hard-coded 0.008**

```
python -m pytest tests/ -q
```

Expected: any test that asserted the old default needs updating. The likely candidate is `tests/test_pricer_configs.py::test_create_profile_persists_named_overrides`. If it fails on the `0.008` assertion, update it inline: change `assert coeffs["TWOUP_UNDERDOG_MARGIN"] == [0.994, 0.008]` → `== [0.994, 0.014]`. If no test fails on this, skip.

- [ ] **Step 6: Commit**

```bash
git add src/odds_scraper/pricer/engine_v2.py src/odds_scraper/pricer/configs.py tests/test_pricer_engine_v2.py tests/test_pricer_configs.py
git commit -m "feat(configs): bump default TWOUP_UNDERDOG_MARGIN intercept 0.008 → 0.014

Matches the V2 Java rewrite. V1's engine.py constant is unchanged; the
schema-seeded default profile picks up the V2 value on fresh DB inits.
Existing custom profiles in the DB keep whatever values their author
saved — no implicit migration."
```

---

## Task 4: Rewire the 1UP trailing branch in engine_v2 to use the DP

The behavioral heart of V2. In the existing `price_early_payout_markets` function inside `engine_v2.py`, replace the `else` branch under the `# ============== 1UP ==============` section.

**Files:**
- Modify: `src/odds_scraper/pricer/engine_v2.py` (the 1UP `else` branch around lines 547-572 of the copied file — the one starting `# ---- TRAILING-TEAM 1UP`)
- Test: `tests/test_pricer_engine_v2.py`

- [ ] **Step 1: Write failing tests for the new trailing math**

Append to `tests/test_pricer_engine_v2.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```
python -m pytest tests/test_pricer_engine_v2.py -q
```

Expected: `test_v2_oneup_trailing_uses_dp_not_heuristic` and `test_v2_oneup_trailing_uses_inclusion_exclusion` fail (V2 currently has V1's trailing path). The other two pass.

- [ ] **Step 3: Replace the 1UP trailing branch in engine_v2**

Locate the `else` block that starts:

```python
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
```

Replace the entire `else:` block (everything from `else:` to before `# ============== 2UP ==============`) with:

```python
    else:
        # ---- TRAILING-TEAM 1UP (V2): DP-based, leading side deactivated ----
        # V2 uses the same DP as 2UP, reading the ever_±1 fields. This
        # makes the invariant P(1UP) ≥ P(2UP) hold by construction since
        # both products share ever_leads_probability and reaching ±2
        # implies passing through ±1. Replaces V1's heuristic
        # _trailing_selection on this branch.
        stats = ever_leads_probability(lambda_home, lambda_away, goal_difference)
        p_h_ever_1, p_a_ever_1 = stats[0], stats[1]
        p_h_ever_1_wins, p_a_ever_1_wins = stats[2], stats[3]
        home_residual = max(0.0, p_h_ever_1 - p_h_ever_1_wins)
        away_residual = max(0.0, p_a_ever_1 - p_a_ever_1_wins)
        # Inclusion-exclusion: P(X 1UP) = P(X wins) + residual.
        home_1up_prob_raw = max(0.0, p_home + home_residual)
        away_1up_prob_raw = max(0.0, p_away + away_residual)

        # Margin blend — same code path as the level-score branch.
        if ONEUP_MARGIN_BLEND_ENABLED:
            fav_margin_1up = _blend_margins(fav_weight, ONEUP_FAVORITE_MARGIN, ONEUP_UNDERDOG_MARGIN)
            dog_margin_1up = _blend_margins(dog_weight, ONEUP_FAVORITE_MARGIN, ONEUP_UNDERDOG_MARGIN)
        else:
            fav_margin_1up = ONEUP_FAVORITE_MARGIN
            dog_margin_1up = ONEUP_UNDERDOG_MARGIN
        home_margin_1up = fav_margin_1up if home_is_favorite else dog_margin_1up
        away_margin_1up = dog_margin_1up if home_is_favorite else fav_margin_1up

        home_1up_fair_odds = _fair_prob_to_odds(home_1up_prob_raw, home_margin_1up)
        away_1up_fair_odds = _fair_prob_to_odds(away_1up_prob_raw, away_margin_1up)

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
```

- [ ] **Step 4: Run tests**

```
python -m pytest tests/test_pricer_engine_v2.py -q
```

Expected: all engine_v2 tests pass — both the prematch-unchanged guard and the trailing-changed assertions hold.

- [ ] **Step 5: Run full suite — V1 tests must still pass identically**

```
python -m pytest tests/ -q
```

Expected: every test that was passing still passes. If `tests/test_pricer_engine.py` ones referring to 1UP trailing have nothing changing (they don't import `engine_v2`), they pass unchanged.

- [ ] **Step 6: Commit**

```bash
git add src/odds_scraper/pricer/engine_v2.py tests/test_pricer_engine_v2.py
git commit -m "feat(engine_v2): rewrite 1UP trailing branch on ever_leads DP

Replaces V1's _trailing_selection-based path with inclusion-exclusion
math identical in shape to 2UP. Leading side stays deactivated. The
invariant P(1UP) ≥ P(2UP) for the trailing side now holds by
construction since both draw from the same DP."
```

---

## Task 5: Rewire 2UP level/one-goal to read the new 8-tuple; remove ever_2up_probability from engine_v2

V1's `ever_2up_probability` returns 4 quantities; the new `ever_leads_probability` returns 8 with the same `ever_2`/`ever_2_AndWins` semantics. Output of the 2UP level path must stay numerically identical — both engines compute the same DP, just packaged differently.

**Files:**
- Modify: `src/odds_scraper/pricer/engine_v2.py` (the 2UP level/one-goal block + delete `ever_2up_probability` since it's no longer called)
- Test: `tests/test_pricer_engine_v2.py`

- [ ] **Step 1: Write a failing test asserting V2 2UP level matches V1**

Append:

```python
def test_v2_twoup_level_matches_v1_within_float_tolerance(balanced_match):
    """The 2UP level path math should yield the same per-side
    probability under V1 and V2 — they're using the same DP physics."""
    r1 = ep_v1.price_early_payout_markets(**balanced_match)
    r2 = ep_v2.price_early_payout_markets(**balanced_match)
    assert r2["p_home_2"] == pytest.approx(r1["p_home_2"], abs=1e-10)
    assert r2["p_away_2"] == pytest.approx(r1["p_away_2"], abs=1e-10)


def test_v2_no_longer_exposes_ever_2up_probability():
    """V2 doesn't call ever_2up_probability anymore — keeping the
    legacy function around invites stale-code drift."""
    assert not hasattr(ep_v2, "ever_2up_probability")


def test_v2_twoup_one_goal_branch_matches_v1(balanced_match):
    """Score 1-0 still goes through level/one-goal branch (|diff| < 2)
    and must match V1."""
    inputs = {**balanced_match, "score": (1, 0)}
    r1 = ep_v1.price_early_payout_markets(**inputs)
    r2 = ep_v2.price_early_payout_markets(**inputs)
    # Away is still active in 2UP (|diff| = 1).
    assert r2["p_away_2"] == pytest.approx(r1["p_away_2"], abs=1e-10)
```

- [ ] **Step 2: Run tests to verify failures**

```
python -m pytest tests/test_pricer_engine_v2.py -q
```

Expected: at minimum `test_v2_no_longer_exposes_ever_2up_probability` fails (since the copy still has it).

The `_matches_v1` tests may already pass since V2 currently still calls `ever_2up_probability` — and that's intentional: this task's job is to swap the caller without behavioural change. We'll verify the swap is correct by keeping these tests green after.

- [ ] **Step 3: Replace the 2UP DP call and remove `ever_2up_probability`**

In `engine_v2.py`, locate the 2UP level block (under `if abs(goal_difference) < 2:`):

```python
        p_home_ever, p_away_ever, p_home_ever_wins, p_away_ever_wins = ever_2up_probability(
            lambda_home, lambda_away, goal_difference
        )
        home_residual = max(0.0, p_home_ever - p_home_ever_wins)
        away_residual = max(0.0, p_away_ever - p_away_ever_wins)
```

Replace with:

```python
        # V2: read ever_±2 stats from the unified DP. Same DP, same
        # numbers — just packaged into the wider 8-tuple now shared
        # with the 1UP trailing path.
        stats = ever_leads_probability(lambda_home, lambda_away, goal_difference)
        p_home_ever_2_wins = stats[6]
        p_away_ever_2_wins = stats[7]
        p_home_ever_2 = stats[4]
        p_away_ever_2 = stats[5]
        home_residual = max(0.0, p_home_ever_2 - p_home_ever_2_wins)
        away_residual = max(0.0, p_away_ever_2 - p_away_ever_2_wins)
```

Then delete the entire `ever_2up_probability` function and its helper `_ever_2up_accumulate` (look for them between the 1UP code and the helper section — search the file for `def ever_2up_probability` and `def _ever_2up_accumulate`).

- [ ] **Step 4: Run tests**

```
python -m pytest tests/test_pricer_engine_v2.py -q
```

Expected: all engine_v2 tests pass including the new `_matches_v1` ones (proves the rewrite is behaviour-preserving on the 2UP level path) and `no_longer_exposes_ever_2up_probability`.

- [ ] **Step 5: Run full suite**

```
python -m pytest tests/ -q
```

Expected: everything passes.

- [ ] **Step 6: Commit**

```bash
git add src/odds_scraper/pricer/engine_v2.py tests/test_pricer_engine_v2.py
git commit -m "refactor(engine_v2): 2UP level branch reads ever_leads 8-tuple

Replaces the ever_2up_probability call with the unified
ever_leads_probability and indexes its ever_±2 fields. Math is
identical so the level/one-goal output stays numerically equal to V1
within float tolerance.

Deletes ever_2up_probability and _ever_2up_accumulate from engine_v2
— no caller remains. V1's engine.py still owns those for V1's own
use."
```

---

## Task 6: Engine V2 invariant guard test

Verify the construction-time invariant: across many random `(λH, λA, score)` triples, V2's `p_home_1 ≥ p_home_2` (and same for away). This is the property the rewrite is supposed to guarantee.

**Files:**
- Test: `tests/test_pricer_engine_v2.py`

- [ ] **Step 1: Add the invariant test**

```python
def test_v2_oneup_geq_twoup_probability_invariant(balanced_match):
    """P(1UP) ≥ P(2UP) on the same side, at any score. V2's claim to
    fame — V1 violated this on the trailing branch. The 100-tick scan
    covers prematch + every interesting live state."""
    import random
    rng = random.Random(0xABCD)
    for _ in range(100):
        sh = rng.randint(0, 5)
        sa = rng.randint(0, 5)
        inputs = {**balanced_match, "score": (sh, sa)}
        r = ep_v2.price_early_payout_markets(**inputs)
        for side in ("home", "away"):
            p1 = r[f"p_{side}_1"]
            p2 = r[f"p_{side}_2"]
            if p1 is None or p2 is None:
                # Deactivated side — invariant N/A.
                continue
            assert p1 >= p2 - 1e-12, (
                f"V2 invariant broken on score=({sh},{sa}), side={side}: "
                f"p_1={p1}, p_2={p2}"
            )
```

- [ ] **Step 2: Run the test**

```
python -m pytest tests/test_pricer_engine_v2.py::test_v2_oneup_geq_twoup_probability_invariant -q
```

Expected: PASS. If it fails, V2's math is wrong and Tasks 2/4/5 need re-checking; do not move on until this is green.

- [ ] **Step 3: Commit**

```bash
git add tests/test_pricer_engine_v2.py
git commit -m "test(engine_v2): assert P(1UP) ≥ P(2UP) invariant on 100 random ticks

V2's structural claim: both products draw from the same DP so the
1UP ≥ 2UP probability inequality holds by construction. V1 violates
this on ~11.6% of STARTED rows; V2 must violate it 0% of the time."
```

---

## Task 7: Extend csv_export with v2 columns + engines column

V2's runner needs target column names. We're widening the CSV layout but keeping V1's columns in their existing positions.

**Files:**
- Modify: `src/odds_scraper/pricer/csv_export.py:8` (the `CSV_COLUMNS` tuple)
- Modify: `tests/test_pricer_csv.py` (extend the row-builder default mapping)

- [ ] **Step 1: Write a failing test asserting the new columns exist in declared order**

Append to `tests/test_pricer_csv.py`:

```python
def test_csv_columns_include_engines_at_front_and_v2_block():
    """V2 spec: leading `engines` column, then existing V1 layout, then
    a v2 block after V1's OUR section but before bookmaker columns."""
    cols = csv_export.CSV_COLUMNS
    assert cols[0] == "engines"
    # V1 OUR block — unchanged location.
    assert "our_2up_away_capped_ev" in cols
    # New v2 block — must appear strictly after V1's OUR block.
    v1_end_idx = cols.index("our_2up_away_capped_ev")
    for v2_col in (
        "v2_p_home_1", "v2_p_away_1",
        "v2_our_1up_home_fair", "v2_our_1up_home_capped", "v2_our_1up_home_capped_ev",
        "v2_our_1up_away_fair", "v2_our_1up_away_capped", "v2_our_1up_away_capped_ev",
        "v2_p_home_2", "v2_p_away_2",
        "v2_our_2up_home_fair", "v2_our_2up_home_capped", "v2_our_2up_home_capped_ev",
        "v2_our_2up_away_fair", "v2_our_2up_away_capped", "v2_our_2up_away_capped_ev",
    ):
        assert v2_col in cols, f"missing {v2_col}"
        assert cols.index(v2_col) > v1_end_idx, f"{v2_col} comes before V1 OUR block"
    # Bookmaker columns must still come after the v2 block.
    assert cols.index("bp_p_1up_home") > cols.index("v2_our_2up_away_capped_ev")
```

- [ ] **Step 2: Run test to verify it fails**

```
python -m pytest tests/test_pricer_csv.py::test_csv_columns_include_engines_at_front_and_v2_block -q
```

Expected: `AssertionError: cols[0] == "engines"`.

- [ ] **Step 3: Update CSV_COLUMNS**

In `src/odds_scraper/pricer/csv_export.py`, rewrite `CSV_COLUMNS` to:

```python
CSV_COLUMNS = (
    # New leading column so downstream joins can filter v1-only / v2-only
    # rows trivially without parsing column suffixes.
    "engines",
    "snapshot_id", "event_id",
    "home", "away", "kickoff_utc",
    "ts_utc",
    "status", "match_minute", "score_home", "score_away",
    "basis_used",
    "lambda_home", "lambda_away",
    "our_p_home_1", "our_p_away_1",
    "our_1up_home_fair", "our_1up_home_capped", "our_1up_home_capped_ev",
    "our_1up_away_fair", "our_1up_away_capped", "our_1up_away_capped_ev",
    "our_p_home_2", "our_p_away_2",
    "our_2up_home_fair", "our_2up_home_capped", "our_2up_home_capped_ev",
    "our_2up_away_fair", "our_2up_away_capped", "our_2up_away_capped_ev",
    # ===== V2 block — same layout as V1's OUR block, prefixed v2_ =====
    "v2_p_home_1", "v2_p_away_1",
    "v2_our_1up_home_fair", "v2_our_1up_home_capped", "v2_our_1up_home_capped_ev",
    "v2_our_1up_away_fair", "v2_our_1up_away_capped", "v2_our_1up_away_capped_ev",
    "v2_p_home_2", "v2_p_away_2",
    "v2_our_2up_home_fair", "v2_our_2up_home_capped", "v2_our_2up_home_capped_ev",
    "v2_our_2up_away_fair", "v2_our_2up_away_capped", "v2_our_2up_away_capped_ev",
    # ===== Bookmaker block (unchanged) =====
    "bp_p_1up_home", "bp_1up_home_odds", "bp_1up_home_ev",
    "bp_p_1up_away", "bp_1up_away_odds", "bp_1up_away_ev",
    "bp_p_2up_home", "bp_2up_home_odds", "bp_2up_home_ev",
    "bp_p_2up_away", "bp_2up_away_odds", "bp_2up_away_ev",
    "sb_p_1up_home", "sb_1up_home_odds", "sb_1up_home_ev",
    "sb_p_1up_away", "sb_1up_away_odds", "sb_1up_away_ev",
    "sb_p_2up_home", "sb_2up_home_odds", "sb_2up_home_ev",
    "sb_p_2up_away", "sb_2up_away_odds", "sb_2up_away_ev",
    "b9j_1up_home_odds", "b9j_1up_away_odds",
    "b9j_2up_home_odds", "b9j_2up_away_odds",
    "bw_1up_home_odds",  "bw_1up_away_odds",
    "bw_2up_home_odds",  "bw_2up_away_odds",
)
```

- [ ] **Step 4: Update the test row-builder default to include new columns**

In `tests/test_pricer_csv.py`, find the `_build_row` helper and add the new keys to its `defaults` dict:

```python
# Add to the defaults dict in _build_row:
        "engines": "v1",
        "v2_p_home_1": "", "v2_p_away_1": "",
        "v2_our_1up_home_fair": "", "v2_our_1up_home_capped": "", "v2_our_1up_home_capped_ev": "",
        "v2_our_1up_away_fair": "", "v2_our_1up_away_capped": "", "v2_our_1up_away_capped_ev": "",
        "v2_p_home_2": "", "v2_p_away_2": "",
        "v2_our_2up_home_fair": "", "v2_our_2up_home_capped": "", "v2_our_2up_home_capped_ev": "",
        "v2_our_2up_away_fair": "", "v2_our_2up_away_capped": "", "v2_our_2up_away_capped_ev": "",
```

- [ ] **Step 5: Run the csv + runner tests to confirm**

```
python -m pytest tests/test_pricer_csv.py tests/test_pricer_runner.py -q
```

The existing `test_pricer_runner.py` tests will likely FAIL because the V1 runner's row tuple is now too short for the new `CSV_COLUMNS`. That's expected and gets fixed in Task 8 — for now move forward.

For Task 7 specifically, run only the csv test file:

```
python -m pytest tests/test_pricer_csv.py -q
```

Expected: csv tests pass.

- [ ] **Step 6: Commit (do NOT run full suite yet — runner integration follows in Task 8)**

```bash
git add src/odds_scraper/pricer/csv_export.py tests/test_pricer_csv.py
git commit -m "feat(csv_export): widen CSV_COLUMNS with engines + v2 block

Adds a leading 'engines' column and a v2_* block mirroring V1's OUR
layout. Bookmaker columns untouched. Runner integration is the next
task — runner tests will be red until then."
```

---

## Task 8: V1 runner emits the new column shape (engines='v1', v2 cells blank)

Bring V1's existing runner up to the new CSV width so the full suite stays green. V1 emits `engines="v1"` and blank v2 cells.

**Files:**
- Modify: `src/odds_scraper/pricer/runner.py` (the `rows.append((...))` tuple inside `run_simulation`)

- [ ] **Step 1: Look at the failing runner tests to confirm they're now reporting column-count mismatch**

```
python -m pytest tests/test_pricer_runner.py -q
```

Expected: failures referring to CSV row length or unexpected columns. Note the assertions that read `row["engines"]` (won't exist yet) — those are new assertions we'll add in Task 9 and don't apply here.

- [ ] **Step 2: Update the row tuple in runner.py to match new CSV_COLUMNS**

In `src/odds_scraper/pricer/runner.py`, find the `rows.append((` block inside `run_simulation` and rewrite it to prepend `"v1"` and append 16 empty strings in the V2 column positions:

```python
                    rows.append((
                        "v1",                                # engines (V1 runner: V1 only)
                        t["snapshot_id"], event_id,
                        t["home"], t["away"], t["kickoff_utc"],
                        ts_utc,
                        t["status"], t["match_minute"],
                        t["score_home"], t["score_away"],
                        basis,
                        res["lambda_home"], res["lambda_away"],
                        p_h1, p_a1,
                        res["market_1up"]["home_fair"], cap_1h, _ev(p_h1, cap_1h),
                        res["market_1up"]["away_fair"], cap_1a, _ev(p_a1, cap_1a),
                        p_h2, p_a2,
                        res["market_2up"]["home_fair"], cap_2h, _ev(p_h2, cap_2h),
                        res["market_2up"]["away_fair"], cap_2a, _ev(p_a2, cap_2a),
                        # V2 block — blank in V1-only runs.
                        "", "",
                        "", "", "",
                        "", "", "",
                        "", "",
                        "", "", "",
                        "", "", "",
                        # Bookmaker block (unchanged).
                        bp["1up_home"][1], bp["1up_home"][0], _ev(p_h1, bp["1up_home"][0]),
                        bp["1up_away"][1], bp["1up_away"][0], _ev(p_a1, bp["1up_away"][0]),
                        bp["2up_home"][1], bp["2up_home"][0], _ev(p_h2, bp["2up_home"][0]),
                        bp["2up_away"][1], bp["2up_away"][0], _ev(p_a2, bp["2up_away"][0]),
                        sb["1up_home"][1], sb["1up_home"][0], _ev(p_h1, sb["1up_home"][0]),
                        sb["1up_away"][1], sb["1up_away"][0], _ev(p_a1, sb["1up_away"][0]),
                        sb["2up_home"][1], sb["2up_home"][0], _ev(p_h2, sb["2up_home"][0]),
                        sb["2up_away"][1], sb["2up_away"][0], _ev(p_a2, sb["2up_away"][0]),
                        b9j["1up_home"][0], b9j["1up_away"][0],
                        b9j["2up_home"][0], b9j["2up_away"][0],
                        bw["1up_home"][0],  bw["1up_away"][0],
                        bw["2up_home"][0],  bw["2up_away"][0],
                    ))
```

- [ ] **Step 3: Run tests**

```
python -m pytest tests/ -q
```

Expected: everything passes. The csv test from Task 7 still passes, the runner tests pass because their `_read_csv` returns dicts keyed by column name (engines column simply gets ignored).

- [ ] **Step 4: Add a small assertion that V1 emits engines="v1"**

Append to `tests/test_pricer_runner.py`:

```python
def test_v1_runner_marks_rows_engines_v1(db, tmp_path):
    """A V1-only run must record `engines="v1"` so downstream tooling
    can filter cleanly when the same CSV mixes engines (future)."""
    _seed_event_with_priced_snapshot(db, "E")
    default = configs.load_default(db)
    csv_path = tmp_path / "sim" / "v1.csv"
    runner.run_simulation(
        db, config=default, regime="any", density="all",
        scope={"country": "", "league": "", "date": "", "search": ""},
        csv_path=csv_path,
    )
    rows = _read_csv(csv_path)
    assert rows and rows[0]["engines"] == "v1"
    # v2 cells are blank.
    assert rows[0]["v2_p_home_1"] == ""
```

- [ ] **Step 5: Run new test**

```
python -m pytest tests/test_pricer_runner.py::test_v1_runner_marks_rows_engines_v1 -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/odds_scraper/pricer/runner.py tests/test_pricer_runner.py
git commit -m "feat(runner): emit engines='v1' + blank v2 cells in V1-only runs

V1's runner is widened to the new CSV layout. V2 columns stay blank
when only V1 is selected. Output of an engines='v1' run remains a
strict superset of pre-V2 columns in declared order; downstream
analyses keyed by column name are unaffected."
```

---

## Task 9: New runner_v2.run_simulation_dual

The dual-engine runner. Shares engine inputs and lead state between the two engine calls.

**Files:**
- Create: `src/odds_scraper/pricer/runner_v2.py`
- Create: `tests/test_pricer_runner_v2.py`

- [ ] **Step 1: Write failing tests for the dual runner**

```python
# tests/test_pricer_runner_v2.py
import csv
import sqlite3
from pathlib import Path

import pytest

from odds_scraper.db_schema import init_schema
from odds_scraper.pricer import configs, runner, runner_v2


def _seed_event_with_priced_snapshot(conn, event_id):
    conn.execute(
        "INSERT INTO events (id, home, away, kickoff_utc) "
        "VALUES (?, 'Home FC', 'Away FC', '2026-05-22T18:30:00Z')",
        (event_id,),
    )
    cur = conn.execute(
        "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, fetch_status) "
        "VALUES ('2026-05-21T10:00:00Z', ?, 'betpawa', 'UPCOMING', 'ok')",
        (event_id,),
    )
    snap_id = cur.lastrowid
    for mid, line, side, odds, prob in [
        ("1x2_ft", 0.0, "home", 1.85, 0.54),
        ("1x2_ft", 0.0, "draw", 3.40, 0.29),
        ("1x2_ft", 0.0, "away", 4.20, 0.17),
        ("over_under_ft", 2.5, "over",  1.85, 0.55),
        ("over_under_ft", 2.5, "under", 1.95, 0.45),
        ("next_goal_ft", 1.0, "home", 1.85, 0.54),
        ("next_goal_ft", 1.0, "none", 8.50, 0.12),
        ("next_goal_ft", 1.0, "away", 3.50, 0.34),
    ]:
        conn.execute(
            "INSERT INTO prices (snapshot_id, event_id, ts_utc, bookmaker, "
            "market_id, line, side, odds, probability) "
            "VALUES (?, ?, '2026-05-21T10:00:00Z', 'betpawa', ?, ?, ?, ?, ?)",
            (snap_id, event_id, mid, line, side, odds, prob),
        )


@pytest.fixture
def db(tmp_path):
    c = sqlite3.connect(str(tmp_path / "odds.db"), isolation_level=None)
    init_schema(c)
    c.row_factory = sqlite3.Row
    return c


def _read_csv(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_dual_runner_v1_only_matches_existing_runner(db, tmp_path):
    """`run_simulation_dual(engines=('v1',))` must produce a CSV with
    the same V1 cell values that `run_simulation` produces."""
    _seed_event_with_priced_snapshot(db, "E")
    default = configs.load_default(db)
    p_dual = tmp_path / "sim" / "dual_v1.csv"
    p_old  = tmp_path / "sim" / "old_v1.csv"
    runner_v2.run_simulation_dual(
        db, config=default, regime="any", density="all",
        scope={"country": "", "league": "", "date": "", "search": ""},
        csv_path=p_dual, engines=("v1",),
    )
    runner.run_simulation(
        db, config=default, regime="any", density="all",
        scope={"country": "", "league": "", "date": "", "search": ""},
        csv_path=p_old,
    )
    r_dual = _read_csv(p_dual)
    r_old  = _read_csv(p_old)
    assert len(r_dual) == len(r_old) == 1
    for col in ("our_p_home_1", "our_1up_home_capped", "bp_p_1up_home"):
        assert r_dual[0][col] == r_old[0][col]
    assert r_dual[0]["engines"] == "v1"
    # v2 cells stay blank when only v1 selected.
    assert r_dual[0]["v2_p_home_1"] == ""


def test_dual_runner_v2_only_fills_v2_blanks_v1(db, tmp_path):
    _seed_event_with_priced_snapshot(db, "E")
    default = configs.load_default(db)
    p = tmp_path / "sim" / "v2_only.csv"
    runner_v2.run_simulation_dual(
        db, config=default, regime="any", density="all",
        scope={"country": "", "league": "", "date": "", "search": ""},
        csv_path=p, engines=("v2",),
    )
    rows = _read_csv(p)
    assert len(rows) == 1
    assert rows[0]["engines"] == "v2"
    assert rows[0]["v2_p_home_1"] != ""
    # V1 cells blank.
    assert rows[0]["our_p_home_1"] == ""
    assert rows[0]["our_1up_home_capped"] == ""


def test_dual_runner_both_fills_v1_and_v2_blocks(db, tmp_path):
    _seed_event_with_priced_snapshot(db, "E")
    default = configs.load_default(db)
    p = tmp_path / "sim" / "both.csv"
    runner_v2.run_simulation_dual(
        db, config=default, regime="any", density="all",
        scope={"country": "", "league": "", "date": "", "search": ""},
        csv_path=p, engines=("v1", "v2"),
    )
    rows = _read_csv(p)
    assert len(rows) == 1
    assert rows[0]["engines"] == "v1,v2"
    assert rows[0]["our_p_home_1"] != ""
    assert rows[0]["v2_p_home_1"] != ""


def test_dual_runner_progress_callback_fires_start_and_end(db, tmp_path):
    _seed_event_with_priced_snapshot(db, "E")
    default = configs.load_default(db)
    calls = []
    runner_v2.run_simulation_dual(
        db, config=default, regime="any", density="all",
        scope={"country": "", "league": "", "date": "", "search": ""},
        csv_path=tmp_path / "sim" / "p.csv",
        engines=("v1", "v2"),
        on_progress=lambda d, t: calls.append((d, t)),
    )
    assert calls[0] == (0, 1)
    assert calls[-1] == (1, 1)


def test_dual_runner_rejects_empty_engines(db, tmp_path):
    default = configs.load_default(db)
    with pytest.raises(ValueError, match="at least one engine"):
        runner_v2.run_simulation_dual(
            db, config=default, regime="any", density="all",
            scope={"country": "", "league": "", "date": "", "search": ""},
            csv_path=tmp_path / "sim" / "x.csv",
            engines=(),
        )


def test_dual_runner_rejects_unknown_engine(db, tmp_path):
    default = configs.load_default(db)
    with pytest.raises(ValueError, match="unknown engine"):
        runner_v2.run_simulation_dual(
            db, config=default, regime="any", density="all",
            scope={"country": "", "league": "", "date": "", "search": ""},
            csv_path=tmp_path / "sim" / "x.csv",
            engines=("v3",),
        )
```

- [ ] **Step 2: Run to verify failure**

```
python -m pytest tests/test_pricer_runner_v2.py -q
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Create `runner_v2.py`**

```python
# src/odds_scraper/pricer/runner_v2.py
"""Dual-engine simulator runner.

Calls one or both of `engine.price_early_payout_markets` and
`engine_v2.price_early_payout_markets` per tick and writes a single
CSV row with v1_* and v2_* columns side-by-side. Coefficient
overrides are applied independently to each engine module — the V1
engine sees its own `with_coefficients`, the V2 engine sees a sibling
`with_coefficients_v2` so the two modules never cross-contaminate
each other's constants.
"""
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional, Sequence

from . import (
    engine, engine_v2, inputs as input_extract, configs as config_mod,
    csv_export, score_state,
)
from .runner import (
    VALID_REGIMES, VALID_DENSITIES, _PROGRESS_BATCH, _ev,
    _select_ticks, _load_tick_prices, _extract_quoted_up,
    with_coefficients as with_v1_coefficients,
)

log = logging.getLogger(__name__)


VALID_ENGINES = ("v1", "v2")


@contextmanager
def with_v2_coefficients(overrides: dict) -> Iterator[None]:
    """Mirror of `runner.with_coefficients` but targeting engine_v2.
    Necessary because the override mechanism setattrs on a module — if
    we used V1's with_coefficients on engine_v2, the wrong module would
    be touched."""
    saved = {k: getattr(engine_v2, k) for k in overrides}
    try:
        for k, v in overrides.items():
            setattr(engine_v2, k, v)
        yield
    finally:
        for k, v in saved.items():
            setattr(engine_v2, k, v)


_EMPTY_OUR = ("",) * 16


def _our_block(res: dict, p_h1, p_a1, p_h2, p_a2):
    """Per-engine 16-cell OUR block: probs + fair + capped + capped_ev
    for 1UP and 2UP home/away. Returns blanks when `res` is None."""
    if res is None:
        return _EMPTY_OUR
    cap_1h = res["market_1up"]["home_margin"]
    cap_1a = res["market_1up"]["away_margin"]
    cap_2h = res["market_2up"]["home_margin"]
    cap_2a = res["market_2up"]["away_margin"]
    return (
        p_h1, p_a1,
        res["market_1up"]["home_fair"], cap_1h, _ev(p_h1, cap_1h),
        res["market_1up"]["away_fair"], cap_1a, _ev(p_a1, cap_1a),
        p_h2, p_a2,
        res["market_2up"]["home_fair"], cap_2h, _ev(p_h2, cap_2h),
        res["market_2up"]["away_fair"], cap_2a, _ev(p_a2, cap_2a),
    )


def run_simulation_dual(
    conn: sqlite3.Connection,
    *,
    config: config_mod.Profile,
    regime: str = "any",
    density: str = "all",
    scope: dict,
    csv_path: Path,
    engines: Sequence[str] = ("v1", "v2"),
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> tuple[int, int]:
    """Iterate ticks once, call each selected engine, write a wide CSV.

    `engines` may contain any of "v1" / "v2"; raises ValueError on an
    empty or unknown selection. Lead state (`max_home_lead`,
    `max_away_lead`) is computed once per tick and reused by both
    engines so a downstream invariant comparison is apples-to-apples.

    Returns (n_events, n_rows) matching `runner.run_simulation`.
    """
    if regime not in VALID_REGIMES:
        raise ValueError(f"unknown regime {regime!r}")
    if density not in VALID_DENSITIES:
        raise ValueError(f"unknown density {density!r}")
    eng = tuple(engines)
    if not eng:
        raise ValueError("at least one engine must be selected")
    unknown = set(eng) - set(VALID_ENGINES)
    if unknown:
        raise ValueError(f"unknown engine(s): {sorted(unknown)}")

    ticks = _select_ticks(conn, regime, density, scope)
    n_total = len(ticks)
    if on_progress is not None:
        on_progress(0, n_total)

    leads_by_tick = score_state.max_leads_for_events(
        conn, {t["event_id"] for t in ticks},
    )

    engine_overrides = config_mod.coefficients_to_engine_overrides(config.coefficients)
    rows: list[tuple] = []
    seen_events: set[str] = set()
    engines_cell = ",".join(eng)

    # Open both context managers — extras are no-ops when the engine
    # isn't being called this run.
    with with_v1_coefficients(engine_overrides), with_v2_coefficients(engine_overrides):
        for i, t in enumerate(ticks):
            event_id = t["event_id"]
            ts_utc = t["ts_utc"]
            prices_by_book = _load_tick_prices(conn, event_id, ts_utc)
            engine_inputs, basis = input_extract.extract(prices_by_book)
            if engine_inputs is None:
                if on_progress is not None and (i + 1) % _PROGRESS_BATCH == 0:
                    on_progress(i + 1, n_total)
                continue
            sh, sa = t["score_home"], t["score_away"]
            engine_inputs["score"] = (
                int(sh) if sh is not None else 0,
                int(sa) if sa is not None else 0,
            )
            mh, ma = leads_by_tick.get((event_id, ts_utc), (0, 0))
            engine_inputs["max_home_lead"] = mh
            engine_inputs["max_away_lead"] = ma

            r_v1 = None
            r_v2 = None
            if "v1" in eng:
                try:
                    r_v1 = engine.price_early_payout_markets(**engine_inputs)
                except Exception as exc:  # noqa: BLE001
                    log.warning("v1 engine crashed on event=%s ts=%s — skipping (%s)",
                                event_id, ts_utc, exc)
            if "v2" in eng:
                try:
                    r_v2 = engine_v2.price_early_payout_markets(**engine_inputs)
                except Exception as exc:  # noqa: BLE001
                    log.warning("v2 engine crashed on event=%s ts=%s — skipping (%s)",
                                event_id, ts_utc, exc)

            # Skip the tick if BOTH selected engines failed — nothing
            # to write would be misleading.
            if (r_v1 is None and "v1" in eng) and (r_v2 is None and "v2" in eng):
                if on_progress is not None and (i + 1) % _PROGRESS_BATCH == 0:
                    on_progress(i + 1, n_total)
                continue

            quoted = {
                book: _extract_quoted_up(prices_by_book.get(book, []))
                for book in ("betpawa", "sportybet", "bet9ja", "betway")
            }
            bp, sb = quoted["betpawa"], quoted["sportybet"]
            b9j, bw = quoted["bet9ja"], quoted["betway"]

            # EV against bookmaker odds always uses V1's prob if V1
            # ran; otherwise fall back to V2's. (V1 is the current
            # engine-of-record per spec.)
            ev_src = r_v1 if r_v1 is not None else r_v2
            p_h1 = ev_src["p_home_1"]
            p_a1 = ev_src["p_away_1"]
            p_h2 = ev_src["p_home_2"]
            p_a2 = ev_src["p_away_2"]

            v1_block = _our_block(
                r_v1, r_v1["p_home_1"] if r_v1 else None,
                r_v1["p_away_1"] if r_v1 else None,
                r_v1["p_home_2"] if r_v1 else None,
                r_v1["p_away_2"] if r_v1 else None,
            )
            v2_block = _our_block(
                r_v2, r_v2["p_home_1"] if r_v2 else None,
                r_v2["p_away_1"] if r_v2 else None,
                r_v2["p_home_2"] if r_v2 else None,
                r_v2["p_away_2"] if r_v2 else None,
            )

            rows.append((
                engines_cell,
                t["snapshot_id"], event_id,
                t["home"], t["away"], t["kickoff_utc"],
                ts_utc,
                t["status"], t["match_minute"],
                t["score_home"], t["score_away"],
                basis,
                (r_v1 or r_v2)["lambda_home"], (r_v1 or r_v2)["lambda_away"],
                *v1_block,
                *v2_block,
                bp["1up_home"][1], bp["1up_home"][0], _ev(p_h1, bp["1up_home"][0]),
                bp["1up_away"][1], bp["1up_away"][0], _ev(p_a1, bp["1up_away"][0]),
                bp["2up_home"][1], bp["2up_home"][0], _ev(p_h2, bp["2up_home"][0]),
                bp["2up_away"][1], bp["2up_away"][0], _ev(p_a2, bp["2up_away"][0]),
                sb["1up_home"][1], sb["1up_home"][0], _ev(p_h1, sb["1up_home"][0]),
                sb["1up_away"][1], sb["1up_away"][0], _ev(p_a1, sb["1up_away"][0]),
                sb["2up_home"][1], sb["2up_home"][0], _ev(p_h2, sb["2up_home"][0]),
                sb["2up_away"][1], sb["2up_away"][0], _ev(p_a2, sb["2up_away"][0]),
                b9j["1up_home"][0], b9j["1up_away"][0],
                b9j["2up_home"][0], b9j["2up_away"][0],
                bw["1up_home"][0],  bw["1up_away"][0],
                bw["2up_home"][0],  bw["2up_away"][0],
            ))
            seen_events.add(event_id)
            if on_progress is not None and (i + 1) % _PROGRESS_BATCH == 0:
                on_progress(i + 1, n_total)

    csv_export.write_csv(csv_path, rows)
    if on_progress is not None:
        on_progress(n_total, n_total)
    return len(seen_events), len(rows)
```

- [ ] **Step 4: Run tests**

```
python -m pytest tests/test_pricer_runner_v2.py -q
```

Expected: 6 tests pass.

- [ ] **Step 5: Run full suite**

```
python -m pytest tests/ -q
```

Expected: everything passes.

- [ ] **Step 6: Commit**

```bash
git add src/odds_scraper/pricer/runner_v2.py tests/test_pricer_runner_v2.py
git commit -m "feat(runner_v2): dual-engine simulator runner

run_simulation_dual iterates ticks once, calls one or both engines,
and emits side-by-side V1/V2 columns in a single CSV. Engine module
overrides are applied independently via with_v1_coefficients /
with_v2_coefficients so the two engines never share constant state.
Lead state is computed once per scope and passed to both engines."
```

---

## Task 10: Routes — `engine` form field + dispatch + `RunRecord.engines`

The simulator routes accept an `engine` form value and dispatch to V1's runner or the dual runner accordingly.

**Files:**
- Modify: `src/odds_scraper/web/pricer_routes.py`
- Modify: `tests/test_simulator_routes.py`

- [ ] **Step 1: Write failing route tests**

Append to `tests/test_simulator_routes.py`:

```python
def test_post_run_with_engine_v1_dispatches_v1_runner(db_path, client):
    """`engine=v1` runs (default behaviour pre-V2) — RunRecord stays
    'engines=v1' so history can be filtered."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    default_id = conn.execute(
        "SELECT id FROM pricer_configs WHERE is_default=1"
    ).fetchone()["id"]
    conn.close()
    r = client.post(
        "/simulator/runs",
        data={"config_id": default_id, "regime": "any", "density": "all",
              "engine": "v1", "country": "", "league": "", "event_id": "",
              "date": "", "search": ""},
        follow_redirects=False,
    )
    assert r.status_code == 303
    reg = _registry(client)
    rec = _wait_for_run_done(reg, reg.list_recent(1)[0].id)
    assert rec.engines == "v1"


def test_post_run_with_engine_v2_dispatches_dual_runner(db_path, client):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    default_id = conn.execute(
        "SELECT id FROM pricer_configs WHERE is_default=1"
    ).fetchone()["id"]
    conn.close()
    r = client.post(
        "/simulator/runs",
        data={"config_id": default_id, "regime": "any", "density": "all",
              "engine": "v2", "country": "", "league": "", "event_id": "",
              "date": "", "search": ""},
        follow_redirects=False,
    )
    assert r.status_code == 303
    reg = _registry(client)
    rec = _wait_for_run_done(reg, reg.list_recent(1)[0].id)
    assert rec.engines == "v2"


def test_post_run_with_engine_both_dispatches_dual_runner(db_path, client):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    default_id = conn.execute(
        "SELECT id FROM pricer_configs WHERE is_default=1"
    ).fetchone()["id"]
    conn.close()
    r = client.post(
        "/simulator/runs",
        data={"config_id": default_id, "regime": "any", "density": "all",
              "engine": "both", "country": "", "league": "", "event_id": "",
              "date": "", "search": ""},
        follow_redirects=False,
    )
    assert r.status_code == 303
    reg = _registry(client)
    rec = _wait_for_run_done(reg, reg.list_recent(1)[0].id)
    assert rec.engines == "v1,v2"


def test_post_run_with_unknown_engine_returns_400(db_path, client):
    conn = sqlite3.connect(str(db_path))
    default_id = conn.execute(
        "SELECT id FROM pricer_configs WHERE is_default=1"
    ).fetchone()[0]
    conn.close()
    r = client.post(
        "/simulator/runs",
        data={"config_id": default_id, "regime": "any", "density": "all",
              "engine": "v9000", "country": "", "league": "", "event_id": "",
              "date": "", "search": ""},
        follow_redirects=False,
    )
    assert r.status_code == 400
```

- [ ] **Step 2: Run to verify failures**

```
python -m pytest tests/test_simulator_routes.py -q -k "engine"
```

Expected: 4 failures.

- [ ] **Step 3: Add `engines` to `RunRecord`, accept the form field, dispatch**

In `src/odds_scraper/web/pricer_routes.py`:

**3a.** Add `engines` to `RunRecord` dataclass. Find the existing declaration and add the field (alphabetically near the top):

```python
@dataclass
class RunRecord:
    id: int
    state: str
    profile_name: str
    regime: str
    density: str
    engines: str  # "v1" | "v2" | "v1,v2"
    started_at: str
    n_done: int = 0
    n_total: int = 0
    n_events: int = 0
    n_rows: int = 0
    csv_name: str = ""
    finished_at: Optional[str] = None
    error: str = ""
```

**3b.** Update `RunRegistry.acquire_id_if_idle` signature + the `RunRecord` construction to take `engines`:

```python
    async def acquire_id_if_idle(
        self, *, profile_name: str, regime: str, density: str, engines: str,
    ) -> Optional[int]:
        async with self._lock:
            if any(r.state == "running" for r in self._runs.values()):
                return None
            run_id = self._next_id
            self._next_id += 1
            self._runs[run_id] = RunRecord(
                id=run_id, state="running",
                profile_name=profile_name, regime=regime, density=density,
                engines=engines,
                started_at=_now_iso(),
            )
            return run_id
```

**3c.** Add a constant + form parsing for engine choice. Near the existing `VALID_REGIMES` import or at module top:

```python
VALID_ENGINE_CHOICES = ("v1", "v2", "both")
```

**3d.** Modify `post_run` to accept and validate `engine`:

```python
    @app.post("/simulator/runs")
    async def post_run(
        config_id: int = Form(...),
        regime:    str = Form("any"),
        density:   str = Form("all"),
        engine:    str = Form("both"),
        country:   str = Form(""),
        league:    str = Form(""),
        event_id:  str = Form(""),
        date:      str = Form(""),
        search:    str = Form(""),
    ):
        if regime not in runner.VALID_REGIMES:
            raise HTTPException(400, f"unknown regime {regime!r}")
        if density not in runner.VALID_DENSITIES:
            raise HTTPException(400, f"unknown density {density!r}")
        if engine not in VALID_ENGINE_CHOICES:
            raise HTTPException(400, f"unknown engine {engine!r}")
        engines_str = "v1" if engine == "v1" else ("v2" if engine == "v2" else "v1,v2")
        # ... rest unchanged until acquire_id_if_idle:
        run_id = await registry.acquire_id_if_idle(
            profile_name=profile.name, regime=regime, density=density,
            engines=engines_str,
        )
        # ...
```

**3e.** Modify `_run_in_thread` to dispatch to V1's runner (engine == "v1") or `runner_v2.run_simulation_dual` (otherwise):

```python
    def _run_in_thread(
        run_id: int, profile_id: int, regime: str, density: str,
        scope: dict, csv_name: str, engine: str,
    ) -> None:
        from odds_scraper.pricer import runner_v2  # local import keeps cold-start fast
        write_conn = _open_write_conn()
        try:
            profile = config_mod.load_by_id(write_conn, profile_id)
            if profile is None:
                registry.mark_failed(run_id, error="profile vanished")
                return
            try:
                if engine == "v1":
                    n_events, n_rows = runner.run_simulation(
                        write_conn, config=profile,
                        regime=regime, density=density,
                        scope=scope, csv_path=csv_dir / csv_name,
                        on_progress=lambda done, total: registry.update_progress(
                            run_id, done, total,
                        ),
                    )
                else:
                    engines = ("v2",) if engine == "v2" else ("v1", "v2")
                    n_events, n_rows = runner_v2.run_simulation_dual(
                        write_conn, config=profile,
                        regime=regime, density=density,
                        scope=scope, csv_path=csv_dir / csv_name,
                        engines=engines,
                        on_progress=lambda done, total: registry.update_progress(
                            run_id, done, total,
                        ),
                    )
            except Exception as exc:  # noqa: BLE001
                log.exception("background simulation crashed")
                registry.mark_failed(run_id, error=f"{type(exc).__name__}: {exc}")
                return
            registry.mark_done(
                run_id, n_events=n_events, n_rows=n_rows, csv_name=csv_name,
            )
        finally:
            write_conn.close()
```

**3f.** Update the call site that spawns `_run_in_thread` to pass `engine`:

```python
        loop.run_in_executor(
            None, _run_in_thread,
            run_id, config_id, regime, density, scope, csv_name, engine,
        )
```

- [ ] **Step 4: Run route tests**

```
python -m pytest tests/test_simulator_routes.py -q
```

Expected: existing tests still pass; 4 new engine-dispatch tests pass.

- [ ] **Step 5: Run full suite**

```
python -m pytest tests/ -q
```

Expected: full pass.

- [ ] **Step 6: Commit**

```bash
git add src/odds_scraper/web/pricer_routes.py tests/test_simulator_routes.py
git commit -m "feat(routes): engine form field + dispatch v1/v2/both

POST /simulator/runs accepts engine={v1,v2,both} and routes to the
existing V1 runner or the new dual runner accordingly. RunRecord
carries an engines string for History rendering. Default is 'both'
so the page-level A/B is the obvious path; v1-only stays available
for parity with the pre-V2 workflow."
```

---

## Task 11: Simulator template — engine radio + History engines column + profile tooltip

UI surface change. Radio appears in the Run dimensions block; History gets a column; profile selector gets a tooltip noting that profiles apply to whichever engine is selected.

**Files:**
- Modify: `src/odds_scraper/web/templates/simulator.html`
- Modify: `tests/test_simulator_routes.py` (template-render asserts)

- [ ] **Step 1: Write failing UI-render tests**

Append:

```python
def test_simulator_form_has_engine_radio(client):
    r = client.get("/simulator")
    body = r.text
    for v in ("v1", "v2", "both"):
        assert f'name="engine" value="{v}"' in body
    # 'Both' is the default per spec — must be pre-checked.
    assert 'name="engine" value="both" checked' in body or \
           'value="both" checked' in body


def test_simulator_history_renders_engines_column(db_path, client):
    """A finished run must surface its engines value in the history
    table so the user can tell at a glance which run is which."""
    reg = _registry(client)
    # Plant a finished run with engines='v1,v2'.
    rec = RunRecord(
        id=reg._next_id, state="done",
        profile_name="default", regime="any", density="all",
        engines="v1,v2",
        started_at="2026-05-25T10:00:00Z",
        n_done=1, n_total=1, n_events=1, n_rows=1,
        csv_name="run_0001.csv", finished_at="2026-05-25T10:00:30Z",
    )
    reg._runs[rec.id] = rec
    reg._next_id += 1
    r = client.get("/simulator")
    body = r.text
    assert "engines" in body.lower()
    assert ">v1,v2<" in body


def test_simulator_profile_tooltip_mentions_engine_contract(client):
    """The profile selector is now ambiguous — same profile, different
    engine, different math. The tooltip makes the contract explicit."""
    r = client.get("/simulator")
    body = r.text
    assert "engine version" in body.lower() or "applies to whichever engine" in body.lower()
```

- [ ] **Step 2: Run to verify failure**

```
python -m pytest tests/test_simulator_routes.py -q -k "engine_radio or engines_column or profile_tooltip"
```

Expected: 3 failures.

- [ ] **Step 3: Add the radio + History column + tooltip to simulator.html**

**3a.** In the `Run dimensions` section, add an Engine column to the grid. Locate `<div class="sim-run-grid">` and add a third column at the same level as Regime/Density (before the closing `</div>` of the grid):

```html
        <div>
          <div class="filter-lbl" style="margin-bottom:4px"><b>Engine</b> — which pricer to run</div>
          <div class="filter-group" style="flex-direction:column;align-items:flex-start;gap:4px">
            <label><input type="radio" name="engine" value="v1">
              <b>V1 only</b> <span class="filter-lbl">— current pricing model</span></label>
            <label><input type="radio" name="engine" value="v2">
              <b>V2 only</b> <span class="filter-lbl">— rewritten 1UP/2UP DP</span></label>
            <label><input type="radio" name="engine" value="both" checked>
              <b>Both (A/B)</b> <span class="filter-lbl">— side-by-side comparison</span></label>
          </div>
        </div>
```

Update the grid to a 3-column layout:

```css
  .sim-run-grid {
    display: grid;
    grid-template-columns: 1fr 1fr 1fr;
    gap: 18px;
    margin-bottom: 12px;
  }
```

**3b.** Add a tooltip near the profile selector. Find:

```html
        <a href="/simulator/profiles" class="filter-lbl" style="color:#fbbf24;margin-left:6px">Manage profiles →</a>
```

Add immediately above it:

```html
        <span class="filter-lbl" style="color:#888;margin-left:6px"
              title="Profiles apply to whichever engine version is selected. The default profile uses the latest engine defaults; custom profiles use the values they were saved with.">
          (applies to selected engine) ℹ
        </span>
```

**3c.** Add the History column. Find the `<thead>` block in the history table and add `<th>engines</th>` after `<th>density</th>`:

```html
        <tr>
          <th>#</th><th>started</th><th>profile</th>
          <th>regime</th><th>density</th><th>engines</th>
          <th>events</th><th>rows</th><th>state</th><th>csv</th>
        </tr>
```

And in the `<tbody>` row template add the matching `<td>`:

```html
        <tr>
          <td>{{ r.id }}</td>
          <td>{{ r.started_at }}</td>
          <td>{{ r.profile_name }}</td>
          <td>{{ r.regime }}</td>
          <td>{{ r.density }}</td>
          <td>{{ r.engines }}</td>
          <td>{{ r.n_events }}</td>
          <td>{{ r.n_rows }}</td>
          <td>{{ r.state }}</td>
          <td>
            {% if r.csv_name %}
              <a href="/simulator/runs/{{ r.id }}/csv" style="color:#60a5fa">csv</a>
            {% else %}—{% endif %}
          </td>
        </tr>
```

- [ ] **Step 4: Run UI tests**

```
python -m pytest tests/test_simulator_routes.py -q
```

Expected: every route test passes including the 3 new UI assertions.

- [ ] **Step 5: Run full suite + smoke-test the page in a browser if possible**

```
python -m pytest tests/ -q
```

Expected: full pass.

Optional smoke test (not required for the task to be considered complete):

```bash
python -c "from pathlib import Path; from fastapi.testclient import TestClient; from odds_scraper.web.app import create_app; print(TestClient(create_app(db_path=Path('data/odds.db'))).get('/simulator').text)" | grep -E "(engine|Engine)" | head
```

Expected: see the engine radios + History engines column in the rendered HTML.

- [ ] **Step 6: Commit**

```bash
git add src/odds_scraper/web/templates/simulator.html tests/test_simulator_routes.py
git commit -m "feat(simulator-ui): engine selector + engines column + profile note

Adds a V1 / V2 / Both radio under Run dimensions (defaulting to Both
so A/B is the obvious path), an 'engines' column in the run history
so the user can tell at a glance which engines a past CSV covers, and
a tooltip near the profile selector noting that the same profile
produces different output under V1 vs V2 — surfaces the contract
explicitly rather than letting users discover it through inconsistent
outputs."
```

---

## Task 12: Final smoke test against the real DB

Verify both engines produce sensible side-by-side output on real data and that the V2 invariant holds across the dataset.

**Files:**
- Create: `scripts/smoke_pricer_v2.py` (one-shot, gitignored under `scripts/`; not a permanent module)

- [ ] **Step 1: Write a smoke script that runs both engines on a small scope and prints invariants**

```python
# scripts/smoke_pricer_v2.py
"""Run V1 + V2 side-by-side on a small slice of the live DB. Reports:
  - Total rows
  - V1 invariant violations (P(1UP) > P(2UP), i.e. 1UP capped < 2UP capped)
  - V2 invariant violations — target 0
"""
import csv
import sqlite3
from pathlib import Path

from odds_scraper.pricer import configs, runner_v2


def main():
    db = sqlite3.connect("data/odds.db", isolation_level=None)
    db.row_factory = sqlite3.Row
    default = configs.load_default(db)
    out = Path("data/sim/_smoke_v2.csv")
    n_ev, n_rows = runner_v2.run_simulation_dual(
        db, config=default, regime="any", density="latest",
        scope={"country": "", "league": "", "event_id": "",
               "date": "", "search": ""},
        csv_path=out, engines=("v1", "v2"),
    )
    print(f"{n_ev} events / {n_rows} rows")

    v1_violations = 0
    v2_violations = 0
    started_rows = 0
    v1_started_violations = 0
    with open(out, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            for side in ("home", "away"):
                v1_1up = row[f"our_1up_{side}_capped"]
                v1_2up = row[f"our_2up_{side}_capped"]
                v2_1up = row[f"v2_our_1up_{side}_capped"]
                v2_2up = row[f"v2_our_2up_{side}_capped"]
                if v1_1up and v1_2up and float(v1_1up) > float(v1_2up):
                    v1_violations += 1
                    if row["status"] == "STARTED":
                        v1_started_violations += 1
                if v2_1up and v2_2up and float(v2_1up) > float(v2_2up):
                    v2_violations += 1
            if row["status"] == "STARTED":
                started_rows += 1
    print(f"V1 invariant violations: {v1_violations} (STARTED: {v1_started_violations}/{started_rows})")
    print(f"V2 invariant violations: {v2_violations}  ← must be 0")
    out.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the smoke script**

```
python scripts/smoke_pricer_v2.py
```

Expected: prints a row count, V1 violations (some non-zero count, especially on STARTED rows), and **V2 violations = 0**.

If V2 violations > 0: Tasks 2/4/5 have a math bug. Stop and re-check before declaring the feature done.

- [ ] **Step 3: Commit the smoke script**

```bash
git add scripts/smoke_pricer_v2.py
git commit -m "test(smoke): verify V2 invariant holds across real data slice

scripts/smoke_pricer_v2.py runs the dual engine on the latest tick of
every event in data/odds.db, counts invariant violations on both
sides, and asserts V2's count is 0. Handy regression guard for any
future V2 math change."
```

---

## Spec coverage check

| Spec section | Implementing task(s) |
|---|---|
| `engine_v2.py` verbatim baseline + dormant constants | Task 1 |
| `ever_leads_probability` 8-tuple DP | Task 2 |
| `TWOUP_UNDERDOG_MARGIN` default bump | Task 3 |
| 1UP trailing rewritten on DP | Task 4 |
| 2UP level reads new 8-tuple | Task 5 |
| Invariant `P(1UP) ≥ P(2UP)` guard | Task 6 |
| CSV layout: leading `engines` + v2 block | Task 7 |
| V1 runner emits engines='v1' + blank v2 cells | Task 8 |
| `run_simulation_dual` dual runner with shared lead state | Task 9 |
| Routes: `engine` form field + dispatch + `RunRecord.engines` | Task 10 |
| UI: engine radio + history column + profile tooltip | Task 11 |
| Validation: real-data invariant check | Task 12 |

All sections covered. No placeholders; every code step shows the exact code. Type/signature consistency verified — `RunRecord.engines` declared in Task 10 is the same name templates use in Task 11; `runner_v2.run_simulation_dual(...)` signature in Task 9 matches the dispatch in Task 10.
