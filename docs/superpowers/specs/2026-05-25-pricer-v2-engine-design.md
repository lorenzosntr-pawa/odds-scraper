# Pricer Engine V2 — Side-by-side A/B Design

**Goal.** Add a second pricing engine (`engine_v2.py`) that mirrors the May 2026 Java rewrite ("Rebuild 1UP and 2UP pricing model and cap mechanism", commit `10351fd1` on `up-markets-pricing-rewrite`) and a runner path that can emit both V1 and V2 columns on the same CSV row. The existing V1 engine, runner, and routes must remain untouched so V1 is the unmodified A/B baseline.

**Non-goal.** No DB schema changes. No new profile tables. No new tunable names. The two engines share profiles.

---

## Why V2

V1's 1UP trailing branch (live, `goal_difference ≠ 0`) used a heuristic `TrailingSelection.calculate(...)` based on `offered = source_1x2 × (1 - reductionFactor)`. That math was independent of the 2UP DP, so on ~11.6% of in-play (`STARTED`) rows V1 produced `1UP_odds > 2UP_odds` for the trailing side — a **hard invariant violation** because reaching +2 implies passing through +1.

V2 unifies 1UP and 2UP behind a single DP (`ever_leads_probability`) that tracks `{ever±1, ever±2}` together, so the invariant `P(1UP) ≥ P(2UP) ⇒ 1UP_odds ≤ 2UP_odds` holds by construction.

V2 also tightens the default 2UP dog margin (intercept `0.008 → 0.014`) — expected to drop mid-dog Δ vs Sportybet from `~+0.29` to `~+0.15`.

---

## Architecture

Three new modules, one route change. Existing V1 surfaces stay byte-identical.

```
src/odds_scraper/pricer/
  engine.py           # V1 — UNCHANGED
  engine_v2.py        # NEW — Java port of the rewrite
  runner.py           # V1 — UNCHANGED
  runner_v2.py        # NEW — dual-engine runner emitting v1_* + v2_* columns
  csv_export.py       # extended: new CSV_COLUMNS_V2 layout (V1 layout kept as alias)
  configs.py          # one default-value change; otherwise schema unchanged
  score_state.py      # UNCHANGED — both engines consume the same lead-state lookup

src/odds_scraper/web/
  pricer_routes.py    # adds an `engine` form field; dispatches v1/v2/both
  templates/simulator.html  # adds engine selector radio
```

No new tables. No new profile schema. No new migration.

---

## Engine V2

### Public surface

`engine_v2.price_early_payout_markets(...)` keeps the same kwarg signature as V1 — same inputs (1X2, OU, FTTS, `score`, `max_home_lead`, `max_away_lead`) and the same dict shape on the return (`lambda_home`, `lambda_away`, `p_home_1`, `p_away_1`, `p_home_2`, `p_away_2`, `market_1up`, `market_2up`, legacy flat fields). This is the contract `runner_v2` relies on to call both engines with one set of inputs.

### Module-level constants

Copy the constant block from `engine.py` verbatim, **including** `ONEUP_TRAILING_MIN_REDUCTION` / `ONEUP_TRAILING_MAX_REDUCTION` and the matching `MARGIN_BLEND_ENABLED` / `BOOST_BLEND_ENABLED` flags. V2 does not reference the two `ONEUP_TRAILING_*` constants anymore (the trailing path uses the DP), but they stay so that `with_coefficients(overrides)` applies cleanly with the same dict shape and shared profiles round-trip without raising on missing keys. Mark them dormant in a docstring at the top of `engine_v2.py`.

The one default-value change: `TWOUP_UNDERDOG_MARGIN = (0.994, 0.014)` (intercept `0.008 → 0.014`).

### Math change 1 — `ever_leads_probability`

Add a new function `ever_leads_probability(lambda_h, lambda_a, initial_diff) -> tuple` returning an 8-tuple in the order Java's `Stats` record uses:

```
(p_home_ever_1, p_away_ever_1, p_home_ever_1_and_wins, p_away_ever_1_and_wins,
 p_home_ever_2, p_away_ever_2, p_home_ever_2_and_wins, p_away_ever_2_and_wins)
```

Implementation mirrors `EverLeadsProbability.java` exactly:
- 4 hit flags packed in one int: `F_LOW2 = 1`, `F_LOW1 = 2`, `F_HIGH1 = 4`, `F_HIGH2 = 8` → 16 flag combinations.
- DP state is `[2 * (MAX_GOALS + |initial_diff| + 2) + 1][16]`.
- Per-step transitions: home goal moves `diff → diff+1` with probability `p = λH / (λH + λA)`, OR's in `F_HIGH1` / `F_HIGH2` as the new diff crosses thresholds. Away goal symmetric.
- Accumulate across Poisson-weighted goal counts up to `MAX_GOALS = 40`, with early exit on `prob_n < 1e-12 && n > λ_total`.
- Returns `(0,)*8` when `λH ≤ 0` or `λA ≤ 0` (matches Java `Stats.ZERO`).

Keep `ever_2up_probability` in `engine_v2.py` removed/deleted — V2's 2UP path uses the new 8-tuple too.

### Math change 2 — 1UP trailing path

In `engine_v2.price_early_payout_markets`, when `goal_difference != 0`, replace the V1 trailing branch (which called `_trailing_selection`) with:

```python
stats = ever_leads_probability(lambda_home, lambda_away, goal_difference)
p_h_ever_1, p_a_ever_1, p_h_ever_1_wins, p_a_ever_1_wins, *_ = stats

home_residual = max(0.0, p_h_ever_1 - p_h_ever_1_wins)
away_residual = max(0.0, p_a_ever_1 - p_a_ever_1_wins)
home_1up_prob = clamp_non_negative(p_home + home_residual)
away_1up_prob = clamp_non_negative(p_away + away_residual)
# Apply fav/dog blended margin (same code path as the level-score 1UP)
# Apply standard SelectionCapping cap step
# DEACTIVATE the leading side (set its odds + prob + capped to None)
```

The margin blend, cap, and final-odds computation use the same helpers V1 uses — only the probability source changed.

### Math change 3 — 2UP level / one-goal path

Same shape as V1, but feed it from the new 8-tuple instead of `ever_2up_probability`:

```python
stats = ever_leads_probability(lambda_home, lambda_away, goal_difference)
p_h_ever_2 = stats[4]; p_a_ever_2 = stats[5]
p_h_ever_2_wins = stats[6]; p_a_ever_2_wins = stats[7]
# Then the V1 inclusion-exclusion + boost-coefficient blend continues unchanged.
```

### Unchanged from V1

- 1UP level-score (`goal_difference == 0`): linear-regression `_apply_model` with FTTS + lambdas, FAV/DOG model blend by `favorite_strength`.
- 2UP trailing (`|goal_difference| ≥ 2`): keeps the heuristic `_trailing_selection(...)` — Java did not rewrite this branch.
- Margin blending (`_blend_margins`), boost blending (`_blend_boost`), favorite-strength math, fair-prob-to-odds, scaled probability gap, cap mechanism (`_cap_selection` with 1.01 floor + 10% relative gap limit).
- History-aware deactivation (`max_home_lead` / `max_away_lead`) — applied as final post-pricing override, identical to V1.

---

## Configs

`configs.DEFAULT_COEFFICIENTS["TWOUP_UNDERDOG_MARGIN"] = [0.994, 0.014]` — one-line bump. The default profile seeded into the DB on first init now reflects the v2 value. **Existing custom profiles in the DB are not migrated** — they keep whatever values their author saved. This is the right behaviour for A/B: a user comparing engines on a saved custom profile sees engine-of-record behaviour, not "v1 behaviour with my tunes". A UI tooltip near the profile selector will make the contract explicit:

> "Profiles apply to whichever engine version is selected. The default profile uses the latest engine defaults; custom profiles use the values they were saved with."

`TUNABLE_NAMES`, `FLAG_NAMES`, `DEFAULT_FLAGS`, and the `coefficients_to_engine_overrides` mapping stay **unchanged**. The `ONEUP_TRAILING_*` constants remain in the override list — V2 simply doesn't reference them at runtime.

---

## Runner V2

`runner_v2.py` provides:

```python
def run_simulation_dual(
    conn, *, config, regime, density, scope, csv_path,
    engines: Sequence[str] = ("v1", "v2"),  # any of "v1", "v2"
    on_progress=None,
) -> tuple[int, int]
```

For each `(event_id, ts_utc)` tick in scope:

1. Build engine inputs once (shared between engines).
2. Compute max-lead pair once via `score_state.max_leads_for_events` — pass into both engine calls.
3. If `"v1"` in engines: call `engine.price_early_payout_markets(**inputs)`.
4. If `"v2"` in engines: call `engine_v2.price_early_payout_markets(**inputs)`.
5. Materialise a single CSV row containing whichever blocks are active. Missing blocks emit empty cells (column header is always present).
6. Progress callback fires per V1 batch unchanged.

`with_coefficients(overrides)` applies to whichever engine module is being called. The override dict is the same coefficients dict produced by `configs.coefficients_to_engine_overrides(profile.coefficients)` for both engines.

When `engines == ("v1",)`, output is byte-identical to today's `runner.run_simulation` (verified via test).

---

## CSV layout

`csv_export.py` extends `CSV_COLUMNS` to a new tuple `CSV_COLUMNS_V2`. The V1 block (current `CSV_COLUMNS`) is preserved at the front; a v2 block follows immediately after the V1 OUR block, before the bookmaker columns. Bookmaker EV columns (`bp_*_ev`, `sb_*_ev`) keep using V1's `our_p_*` — V1 is the "current of record" engine for live EVs.

New v2 columns, in this order:

```
v2_p_home_1, v2_p_away_1,
v2_our_1up_home_fair, v2_our_1up_home_capped, v2_our_1up_home_capped_ev,
v2_our_1up_away_fair, v2_our_1up_away_capped, v2_our_1up_away_capped_ev,
v2_p_home_2, v2_p_away_2,
v2_our_2up_home_fair, v2_our_2up_home_capped, v2_our_2up_home_capped_ev,
v2_our_2up_away_fair, v2_our_2up_away_capped, v2_our_2up_away_capped_ev,
```

Pattern matches V1: each `_capped_ev` = `v2_prob × v2_capped − 1` (engine-self EV; surfaces V2's embedded margin per selection, blank when settled).

CSV runs with only one engine selected still write the full header — the unused engine's columns stay blank for that run.

An optional `engines` column at the front (e.g. `"v1,v2"` or `"v2"`) makes downstream filtering trivial. Including it.

---

## Routes + UI

`POST /simulator/runs` gains an `engine: str = Form("both")` field — values: `"v1"`, `"v2"`, `"both"`. The route validates the value, then dispatches to either V1's existing `run_simulation` (`engine == "v1"`) or V2's `run_simulation_dual` (`engine in {"v2", "both"}`). The simpler path keeps V1's runner untouched.

The simulator page adds a small radio group in the "Run dimensions" section, between Regime and Density:

```
Engine:
  ( ) V1 only      — current pricing model
  ( ) V2 only      — rewritten 1UP/2UP DP
  (•) Both (A/B)   — emit both side-by-side
```

`GET /simulator/scope` is unchanged — scope count doesn't depend on engine.

`GET /simulator/runs/{id}/status` and `GET /simulator/runs/{id}/csv` are unchanged — the run record carries `csv_name` and the file already has whatever columns were written.

The simulator's "History (this session)" table gains an `engines` column showing which engine(s) ran.

---

## Tests

**Engine V2 tests** (new file `tests/test_pricer_engine_v2.py`):
- `ever_leads_probability` returns 8-tuple with monotonic guarantees: `p_home_ever_1 ≥ p_home_ever_2`, same for away.
- Bit-flag layout matches Java: `initial_diff = +1` initializes with `F_HIGH1` set, `+2` adds `F_HIGH2`, etc.
- Symmetry: swapping λH/λA exchanges home/away outputs.
- `ZERO` path: λH=0 returns all-zero stats.
- 1UP level-score path (`goal_difference == 0`): V1 and V2 produce numerically identical `p_home_1`/`p_away_1` (this branch is unchanged).
- 1UP trailing path: V2 produces shorter odds than V1 on a known case (Colorado Rapids-style 1-2 at min 92).
- 2UP level path: V2 numerically matches V1 to within float tolerance on a level prematch fixture (V1's `ever_2up_probability` and V2's `ever_leads_probability` agree on the ever_±2 quantities).
- 2UP trailing path: V2 numerically matches V1 exactly — both still call `_trailing_selection`.
- Invariant: for a battery of random `(λH, λA, goal_diff)` triples, `p_home_1 ≥ p_home_2` and `p_away_1 ≥ p_away_2` strictly. Same for capped odds (after-cap monotonicity may not hold in pathological cases, so this is an `assert` on the probability side, not the odds side).

**Runner V2 tests** (new file `tests/test_pricer_runner_v2.py`):
- `run_simulation_dual(engines=("v1",))` produces output byte-identical to `run_simulation(...)` on the same scope.
- `run_simulation_dual(engines=("v2",))` populates v2 columns and leaves v1 columns blank.
- `run_simulation_dual(engines=("v1","v2"))` populates both blocks; v1 cells match a separate v1-only run; v2 cells match a separate v2-only run.
- Lead-state passthrough: max-leads queried once per scope, passed to both engines (regression guard against double-querying).
- Progress callback contract unchanged.

**Route tests** (extend `tests/test_simulator_routes.py`):
- Form with `engine=v2` dispatches v2; `engine=both` dispatches dual.
- Invalid `engine` value returns 400.
- History row renders the `engines` column.

**Config tests** (extend `tests/test_pricer_configs.py`):
- Default profile loaded from a fresh DB has `TWOUP_UNDERDOG_MARGIN = [0.994, 0.014]`.
- Saved custom profile with the old `0.008` value loads back unchanged (no silent migration).

---

## Validation runs (post-implementation)

Once `runner_v2.py` produces a CSV across the current dataset:

(a) **Invariant check.** Count rows where `v2_our_2up_X_capped < v2_our_1up_X_capped` (X ∈ {home, away}). Target: 0. Same count on the V1 columns should be ~11.6% of STARTED rows.

(b) **Competitiveness vs SB / B9J / BW.** Bucket by competitor odds (≤1.30 / 1.30–1.70 / 1.70–2.20 / 2.20–3.50 / 3.50–6.00 / 6.00–12.00 / >12). Compare mean Δ and win-rate (% rows where our odds ≥ competitor) between v1 and v2. Expect mid-dog Δ vs SB to drop from ~+0.29 to ~+0.15.

(c) **1UP trailing sanity.** Filter `status = STARTED` rows with `goal_difference ≠ 0`; histogram of V1 vs V2 1UP odds should show V2 producing tighter, more model-consistent numbers.

(d) **Prematch no-regression.** Filter `status = UPCOMING`; `v2_p_home_1 == v1_p_home_1` within float tolerance.

These checks are post-implementation work — small ad-hoc Python scripts living under `scripts/` that read the CSV.

---

## Out of scope

- Hot-path (live scraper) integration of V2. `live_writer.py` keeps calling V1. We may wire V2 there later, but for now V2 is simulator-only.
- New profile tunables for V2. Adding any would force a schema migration; deferred until V2's own coefficients diverge.
- A UI for comparing v1 vs v2 on the home-page card SIM column. The detail page already shows OUR history from `pricer_live_results` (V1-only) — V2 results stay in CSVs for now.
- Renaming `engine.py` → `engine_v1.py`. The current name is the V1 contract; mass-renaming breaks every import site and isn't required for parallel A/B.
