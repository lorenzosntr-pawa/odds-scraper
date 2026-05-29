# Pricer engine v4 — design

**Date:** 2026-05-29
**Status:** approved (brainstorming) → ready for plan

## Goal

Add a fourth pricing engine `v4` to the `check_merging` simulator that
faithfully reproduces the **latest** SourceSportradar Java pricing code
(branch `up-markets-pricing-rewrite`), so its 1UP/2UP odds can be A/B'd
against v1/v2/v3 and the bookmakers in the web simulator and the CSV
export.

## Source of truth

Repo: `C:\Users\loren\Desktop\betpawa\1UP_PR\SourceSportradar` (branch
`up-markets-pricing-rewrite`). Key files mirrored:

- `docs/xup_margin_spec.md` — margin spec (logit/sigmoid margin, near-even
  boost, upside-only cap, deactivation).
- `synthetic/XupMargin.java` — `offeredOdds`, `cap`, `isFavorite`,
  `nearEven`, `isCapReferenceValid`.
- `synthetic/oneup/ThreeWay1UPCalculatorImpl.java` —
  `calculate1upLevelProbabilities` (DP-direct), `calculate1upTrailingProbabilities`
  (inclusion-exclusion), `priceSide` (deactivation).
- `synthetic/twoup/Threeway2UpCalculatorImpl.java` — 2UP inclusion-exclusion
  + boost-coefficient blend, `priceSide`.
- `synthetic/EverLeadsProbability.java` — the DP (`Stats` 8-tuple).
- `synthetic/ThreeWayCommon.java` — probability-based `isHomeFavorite`,
  `favoriteStrength`.
- Java tests: `XupMarginTest.java`, and the oneup/twoup
  `...CalculatorImplTest.java` — golden numeric values.

## Architecture

`src/odds_scraper/pricer/engine_v4.py` is created by **copying
`engine_v3.py`** and applying only the three deltas below. Everything else
stays byte-identical to v3:

- lambda derivation (`derive_lambda_pair` and helpers),
- the `ever_leads_probability` DP and its helpers,
- `_fair_prob_to_odds` (logit level/tilt margin), `_apply_boost`
  (near-even suppression), `_cap_selection` (odds-space, upside-only,
  reduction %),
- 2UP inclusion-exclusion + boost-coefficient blend (`_blend_boost`),
- `_favorite_strength`,
- leading-side and max-lead deactivation rules,
- the return-dict shape (incl. legacy flat fields).

The module is **isolated** (its own module-level constants) so
`with_*_coefficients` overrides on one engine never cross-contaminate the
others — same pattern as engine_v2/engine_v3.

> Note on naming: an existing `test_db_schema_v4.py` / `SCHEMA_VERSION >= 4`
> refer to the **database schema version**, which is unrelated to this
> **pricing engine v4**. This work adds no schema bump.

## The three deltas (the whole job)

### Delta 1 — 1UP level score is DP-direct (next-goal regression removed)

The latest Java `calculate1upLevelProbabilities` no longer uses FTTS or the
`ONEUP_FAVORITE/UNDERDOG` regression or the fav/dog blend. At level score
(`goal_difference == 0`) it reads the DP directly:

```python
stats = ever_leads_probability(lambda_home, lambda_away, 0)
home_1up_prob = _clamp_prob(stats[0])   # p_home_ever_1  (win mass already included)
away_1up_prob = _clamp_prob(stats[1])   # p_away_ever_1
```

(Java: `clampToProbability(stats.pHomeEver1())` / `pAwayEver1()`.)

- **Delete** `ONEUP_FAVORITE_MODEL`, `ONEUP_UNDERDOG_MODEL`, `_apply_model`,
  and the entire `ftts_*` level-score branch.
- `ftts_home_prob` / `ftts_away_prob` stay in the function signature (runner
  compatibility) but are **unused**.
- **Trailing 1UP** (`goal_difference != 0`) and **all of 2UP** keep v3's
  inclusion-exclusion math unchanged.

### Delta 2 — deactivate a side whose 1X2 win odd can't anchor the cap

Java now deactivates (emits no odds) for a side when its OWN 1X2 win odd is
missing/None or `<= 1.0`, instead of pricing it to the 1.01 floor. Mirror
`XupMargin.isCapReferenceValid` + the calculators' `priceSide`
(which returns `DEACTIVATED` before `offeredOdds`/`cap` runs):

```python
def _valid_ref(o):
    return o is not None and o > 1.0

home_ref = home_1x2_odds if _valid_ref(home_1x2_odds) else None   # same for away
```

- If `home_ref is None` → **both** `home_1up` **and** `home_2up` are
  deactivated (`odds=None`, `prob=None`). Same for away.
- This replaces v3's "active side with `source_odds=None` → floor to 1.01"
  path. The DP / win probabilities are still computed internally; only the
  priced output for that side is suppressed.

### Delta 3 — favourite & near-even for the MARGIN come from ODDS, not probability

v3 uses `home_is_favorite = p_home >= p_away` and
`near_even = abs(p_home - p_away) < threshold` for everything. Java splits
this in two — replicate exactly:

- **MARGIN side selection** (which boost % and which reduction % apply) uses
  `XupMargin.isFavorite` = **lower valid 1X2 win odds**, on the
  validity-normalised refs:
  - `this is None` → `False`,
  - else `other is None` → `True`,
  - else `this <= other` (tie → favourite).
- **near_even** uses `XupMargin.nearEven`:
  `abs(1/home_ref - 1/away_ref) < NEAR_EVEN_THRESHOLD`, **False** if either
  ref is None / `<= 0`.
- **Unchanged (still probability-based):** the 2UP boost-COEFFICIENT blend
  (`home_coeff` / `away_coeff` via `_blend_boost`) and `_favorite_strength`
  — these keep using the PROBABILITY-based favourite
  (`home_is_favorite = p_home >= p_away`), exactly as v3
  (Java `ThreeWayCommon.isHomeFavorite` / `favoriteStrength`).

So: a new **odds-based favourite** drives each side's boost % and reduction %
in the margin/cap; the existing **probability-based favourite** keeps driving
the 2UP coefficient blend and favorite-strength. This matters because 2UP
reductions are asymmetric (fav 2.0 % / dog 0.5 %): picking the reduction side
by odds vs probability changes the capped odd when the two favourites
disagree.

## Config

v4 reuses v3's tunable key **names** verbatim:
`ONEUP/TWOUP_MARGIN_LEVEL/TILT`, `*_ODDS_BOOST_PCT`, `NEAR_EVEN_THRESHOLD`,
`*_REDUCTION_PCT`, `TWOUP_*_BOOST_COEFFICIENT`. Defaults already match the
Java spec table (1UP reductions 2.0/2.0, 2UP 2.0/0.5, level/tilt 0.1324/0.9922
and 0.0352/1.0030, near-even 0.03, boosts 0.0).

v4 does **not** define the 1UP regression-model keys
(`ONEUP_FAVORITE/UNDERDOG_MODEL`) or any trailing-margin keys.

**Decision:** reuse `V3_ONLY_TUNABLE_NAMES` rather than add a `V4_ONLY` set.
v4's tunable keys are a subset of v3's, so they are already in `V3_ONLY` (and
thus correctly skipped by the v1 runner). `with_v4_coefficients` filters by
`hasattr(engine_v4, k)`, so the regression keys that v4 doesn't define are
silently skipped. No `V4_ONLY_TUNABLE_NAMES` is needed.

## Wiring seams (v4 = new latest)

Product decision: **v4 becomes the new latest engine.**

- `src/odds_scraper/pricer/runner_v2.py`:
  - `import engine_v4`;
  - `VALID_ENGINES = ("v1", "v2", "v3", "v4")`;
  - add `with_v4_coefficients()` mirroring `with_v3_coefficients()`
    (`hasattr(engine_v4, k)` filter);
  - `_run_engines` returns `r4` (guarded try/except like the others);
  - build `v4_block` + `pB_v4_block` via `_our_block`;
  - append `r4` **last** in the `ev_src` / `lambdas_src` fallback chains
    (v1 → v2 → v3 → v4) and in the success-detection;
  - extend `_v1_skip` is unchanged (still `V2_ONLY | V3_ONLY`, which already
    covers v4's keys);
  - unpack `*v4_block` / `*pB_v4_block` into the row tuples in the same
    positions v3 uses.
- `src/odds_scraper/pricer/csv_export.py` (+ any `models/build_csv_header`):
  add the v4 16-cell OUR block and the pB_v4 16-cell block to `CSV_COLUMNS`,
  immediately after the v3 / pB_v3 blocks (same position pattern).
- `src/odds_scraper/web/pricer_routes.py`: `LATEST_ENGINE = "v4"`.
- `src/odds_scraper/web/templates/simulator.html`: add a v4 checkbox
  (**checked by default**) in the engine selector group, with a short label;
  leave v3 present but unchecked.
- **Phase 2 (skipped):** `src/odds_scraper/pricer/live_writer.py` and a DB
  schema migration are out of scope per the request — the goal is the
  simulator A/B + CSV only.

## Testing (TDD — financial pricing, no shortcuts)

New `tests/test_pricer_engine_v4.py`:

**Margin goldens** (cross-checked against `XupMarginTest.java`):
- no-margin (level=0, tilt=1) → `odds = 1/p`: `p=0.5 → 2.0`, `p=0.8 → 1.25`.
- cap binds to `source * (1 - red%)`: `p=0.5, ref=1.5 → 1.5`; with 10 %
  reduction → `1.35`.
- `p=1.0` floors to `MIN_ODDS = 1.01`.
- near-even strict-`<` boundary (gap exactly at threshold is NOT near-even).
- boost lengthens then is suppressed near-even: fav `2.0 → 2.2`, dog
  `2.0 → 2.4`; near-even → stays `2.0`.

**v4-vs-v3 differential tests:**
- **level-score 1UP differs:** v4 (DP-direct) ≠ v3 (regression) on a level
  tick that supplies FTTS — assert not-equal.
- **trailing 1UP and all 2UP equal:** v4 == v3 within float tolerance,
  EXCEPT a constructed case where the odds-vs-probability favourite flips the
  2UP reduction side (one case each way: prob-fav = home but odds-fav = away,
  and vice-versa) — assert the capped 2UP odd diverges there.
- **invalid 1X2 odds:** for `home_1x2_odds in (None, 0, 1.0, 0.99)` v4
  deactivates that side (1UP and 2UP odds/prob both None); v3 floors to
  1.01 — assert the divergence.
- **core invariant:** `P(1UP) >= P(2UP)` per side at every score.

Plus:
- `tests/test_pricer_runner_v2.py`: a `v4`-only run fills the `v4_*` block and
  leaves others blank; an all-four-engines run (`v1,v2,v3,v4`) fills all four
  blocks.
- `tests/test_pricer_csv.py`: add v4 / pB_v4 blocks to the `_build_row`
  defaults so the column-layout assertions hold.

Run only the relevant tests:

```
.venv\Scripts\python -m pytest tests/test_pricer_engine_v4.py tests/test_pricer_runner_v2.py tests/test_pricer_csv.py -q
```

## Out of scope

- `live_writer.py` and DB schema changes (phase 2).
- Any change to v1/v2/v3 engine math.
- Re-fitting margin coefficients (defaults already match the Java spec table).
