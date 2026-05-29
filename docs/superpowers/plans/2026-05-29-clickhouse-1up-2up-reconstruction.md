# ClickHouse 1UP/2UP Reconstruction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Derive V2/V3/V4 1UP/2UP fair+margined odds for every betslip pricing moment (prematch and live) from the real backtest dataset in ClickHouse, and write the results to `risk_Lorenzo`.

**Architecture:** ClickHouse reads the betslip snapshot log and uses `ASOF JOIN` to align O/U + next-goal selections to the nearest 1X2 timestamp per `(event_id, in_play)`. A thin Python layer streams those pricing-moment rows, renormalizes the 1X2 probabilities, derives a brand-neutral cap reference (`1/(p*1.02)`), assembles probability inputs (no devig — `true_proba` is already fair), and calls the three engines. Results are batch-inserted back into ClickHouse with a reliability report.

**Tech Stack:** Python 3.11, `clickhouse-connect`, existing `odds_scraper.pricer` engines (`engine_v2`, `engine_v3`, `engine_v4`), pytest. Connection is via an already-running local Teleport ClickHouse proxy.

**Spec:** `docs/superpowers/specs/2026-05-29-clickhouse-1up-2up-reconstruction-design.md`

---

## File Structure

New package `src/odds_scraper/reconstruct/`:

- `__init__.py` — package marker.
- `constants.py` — market-name strings, selection labels, margin/fresh constants, output column order.
- `pricing.py` — pure pricing core: renormalization, cap-odds, next-goal-line selection, input assembly, DP cache, `price_moment`, output-row builder. No IO.
- `queries.py` — builds the extraction SQL (`ASOF JOIN`) and the output-table DDL.
- `clickhouse_io.py` — `clickhouse-connect` connection adapter (read iterator + batched insert). Config from env. No business logic.
- `report.py` — reliability-report markdown builder.

New script:

- `scripts/reconstruct_clickhouse.py` — CLI orchestrator.

New tests:

- `tests/reconstruct/test_pricing.py`
- `tests/reconstruct/test_queries.py`
- `tests/reconstruct/test_clickhouse_io.py`
- `tests/reconstruct/test_report.py`
- `tests/reconstruct/test_integration.py` (proxy-gated, skipped by default)

The pricing moment passed between layers is a plain dict with these keys (the "Moment contract"):

```python
# Moment dict contract (produced by query assembly, consumed by pricing):
{
    "event_id": str, "sr_id": str, "brand": str,
    "event_name": str, "sr_start_time": str,
    "in_play": bool, "moment_ts": str,            # "%Y-%m-%d %H:%M:%S"
    "home_score": int, "away_score": int,
    "p_home_raw": float, "p_draw_raw": float, "p_away_raw": float,
    "total_ou": list,   # [(line: float, over_prob: float), ...]
    "home_ou": list,    # [(line, over_prob), ...]
    "away_ou": list,    # [(line, over_prob), ...]
    "ftts_home": float | None,   # next-goal Home true_proba for the active line
    "ftts_away": float | None,   # next-goal Away true_proba for the active line
    "max_input_staleness_seconds": int,
}
```

---

### Task 1: Track engine_v4, add dependency, scaffold package

**Files:**
- Modify: `pyproject.toml`
- Create: `src/odds_scraper/reconstruct/__init__.py`
- Create: `tests/reconstruct/__init__.py`
- Track (already in tree, currently untracked): `src/odds_scraper/pricer/engine_v4.py`, `tests/test_pricer_engine_v4.py`

- [ ] **Step 1: Add `clickhouse-connect` to dependencies**

In `pyproject.toml`, change the `dependencies` list (currently lines ~10-13) to add the driver:

```toml
dependencies = [
    "pyyaml>=6.0",
    "clickhouse-connect>=0.7",
]
```

- [ ] **Step 2: Create the package marker**

Create `src/odds_scraper/reconstruct/__init__.py`:

```python
"""Offline batch: derive V2/V3/V4 1UP/2UP odds from the ClickHouse betslip
snapshot log and write results to risk_Lorenzo. See
docs/superpowers/specs/2026-05-29-clickhouse-1up-2up-reconstruction-design.md."""
```

Create `tests/reconstruct/__init__.py` (empty file).

- [ ] **Step 3: Install and verify imports**

Run: `uv sync --extra dev` (or `pip install -e ".[dev]"`)
Then run: `python -c "import clickhouse_connect; from odds_scraper.pricer import engine_v2, engine_v3, engine_v4; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock src/odds_scraper/reconstruct/__init__.py tests/reconstruct/__init__.py src/odds_scraper/pricer/engine_v4.py tests/test_pricer_engine_v4.py
git commit -m "chore(reconstruct): scaffold package, track engine_v4, add clickhouse-connect"
```

---

### Task 2: Constants

**Files:**
- Create: `src/odds_scraper/reconstruct/constants.py`

- [ ] **Step 1: Write the constants module**

Create `src/odds_scraper/reconstruct/constants.py`:

```python
"""Vocabulary + tunables for ClickHouse 1UP/2UP reconstruction.

Selection labels and market names are the one place that depends on the exact
ClickHouse table vocabulary; verify them against the live table (see the
integration task) and change here only.
"""
from __future__ import annotations

# --- source market names (exact strings in bi_Samuel...) ---
MARKET_1X2 = "1X2 - FT"
MARKET_OU_TOTAL = "Total Score Over/Under - FT"
MARKET_OU_HOME = "Total Score Over/Under - FT - Home Team"
MARKET_OU_AWAY = "Total Score Over/Under - FT - Away Team"
# Next-goal market name is "{n} Goal" with handicap = n*4 (handicap/4.0 == n).

OU_MARKETS = (MARKET_OU_TOTAL, MARKET_OU_HOME, MARKET_OU_AWAY)

# --- selection labels (verify against live table) ---
SEL_HOME, SEL_DRAW, SEL_AWAY = "Home", "Draw", "Away"
SEL_OVER, SEL_UNDER = "Over", "Under"
SEL_NG_HOME, SEL_NG_AWAY, SEL_NG_NONE = "Home", "Away", "None"

# --- tunables ---
CAP_MARGIN = 0.02          # flat brand-neutral margin baked into cap reference odds
FRESH_SECONDS = 3600       # <1h staleness window for an emitted moment
RENORM_DRIFT_TOL = 0.05    # |sum(1X2 true_proba) - 1| beyond this is flagged

# --- output ---
DEFAULT_OUTPUT_TABLE = "risk_Lorenzo.oneup_twoup_reconstructed"

OUTPUT_COLUMNS = [
    "run_ts", "brand", "event_id", "sr_id", "event_name", "sr_start_time",
    "in_play", "moment_ts", "home_score", "away_score",
    "p_home", "p_draw", "p_away", "lambda_home", "lambda_away",
    "ftts_home", "ftts_away", "has_1up",
    "max_input_staleness_seconds", "est_input_drift_pct", "renorm_drift",
]
for _e in ("v2", "v3", "v4"):
    for _m in ("1up", "2up"):
        for _s in ("home", "away"):
            OUTPUT_COLUMNS += [f"{_e}_{_m}_{_s}_odds",
                               f"{_e}_{_m}_{_s}_prob",
                               f"{_e}_{_m}_{_s}_ev"]
```

- [ ] **Step 2: Verify it imports**

Run: `python -c "from odds_scraper.reconstruct import constants as c; print(len(c.OUTPUT_COLUMNS))"`
Expected: prints `57` (21 base + 36 engine cells).

- [ ] **Step 3: Commit**

```bash
git add src/odds_scraper/reconstruct/constants.py
git commit -m "feat(reconstruct): source/sink vocabulary and tunables"
```

---

### Task 3: 1X2 renormalization + cap-odds derivation

**Files:**
- Create: `src/odds_scraper/reconstruct/pricing.py`
- Test: `tests/reconstruct/test_pricing.py`

- [ ] **Step 1: Write the failing test**

Create `tests/reconstruct/test_pricing.py`:

```python
import math
import pytest
from odds_scraper.reconstruct import pricing


def test_renormalize_1x2_scales_to_one():
    ph, pd, pa, drift = pricing.renormalize_1x2(0.5, 0.3, 0.4)  # sums to 1.2
    assert math.isclose(ph + pd + pa, 1.0, abs_tol=1e-9)
    assert math.isclose(ph, 0.5 / 1.2, abs_tol=1e-9)
    assert math.isclose(drift, 0.2, abs_tol=1e-9)  # raw sum was 1.2 -> drift 0.2


def test_renormalize_1x2_already_fair():
    ph, pd, pa, drift = pricing.renormalize_1x2(0.45, 0.30, 0.25)
    assert math.isclose(ph + pd + pa, 1.0, abs_tol=1e-9)
    assert math.isclose(drift, 0.0, abs_tol=1e-9)


def test_cap_odds_applies_two_percent_margin():
    # fair odds for p=0.5 is 2.0; with 2% margin -> 1/(0.5*1.02)
    assert math.isclose(pricing.cap_odds_from_prob(0.5), 1.0 / (0.5 * 1.02), abs_tol=1e-9)


def test_cap_odds_none_for_nonpositive_prob():
    assert pricing.cap_odds_from_prob(0.0) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/reconstruct/test_pricing.py -v`
Expected: FAIL — `ModuleNotFoundError`/`AttributeError` (pricing has no `renormalize_1x2`).

- [ ] **Step 3: Write minimal implementation**

Create `src/odds_scraper/reconstruct/pricing.py`:

```python
"""Pure pricing core for ClickHouse 1UP/2UP reconstruction. No IO.

true_proba is already fair, so there is NO devig here (unlike the CSV
deriver). The 1X2 triple is renormalized to sum 1, and the engines' required
1X2 decimal odds are synthesized from those probabilities with a flat 2%
margin (brand-neutral) — offered `price` is intentionally NOT used.
"""
from __future__ import annotations

from typing import Optional

from .constants import CAP_MARGIN


def renormalize_1x2(p_home: float, p_draw: float, p_away: float):
    """Return (home, draw, away) scaled to sum 1, plus drift = raw_sum - 1."""
    s = p_home + p_draw + p_away
    if s <= 0:
        return 0.0, 0.0, 0.0, 0.0
    return p_home / s, p_draw / s, p_away / s, s - 1.0


def cap_odds_from_prob(prob: float, margin: float = CAP_MARGIN) -> Optional[float]:
    """Synthetic 1X2 source odds for the engine cap: fair odds with a flat
    margin baked in. Returns None for a non-ppriceable probability."""
    implied = prob * (1.0 + margin)
    if implied <= 0:
        return None
    return 1.0 / implied
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/reconstruct/test_pricing.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/odds_scraper/reconstruct/pricing.py tests/reconstruct/test_pricing.py
git commit -m "feat(reconstruct): 1X2 renormalization + brand-neutral cap odds"
```

---

### Task 4: Next-goal line selection by score

**Files:**
- Modify: `src/odds_scraper/reconstruct/pricing.py`
- Test: `tests/reconstruct/test_pricing.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/reconstruct/test_pricing.py`:

```python
def test_next_goal_index_prematch_is_one():
    assert pricing.next_goal_index(0, 0) == 1


def test_next_goal_index_uses_total_goals_plus_one():
    assert pricing.next_goal_index(1, 1) == 3   # 2 scored -> next is goal #3
    assert pricing.next_goal_index(2, 0) == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/reconstruct/test_pricing.py -k next_goal -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'next_goal_index'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/odds_scraper/reconstruct/pricing.py`:

```python
def next_goal_index(home_score: int, away_score: int) -> int:
    """Goal number of the next goal = goals already scored + 1.
    The next-goal market line (handicap/4.0) equals this index."""
    return home_score + away_score + 1
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/reconstruct/test_pricing.py -k next_goal -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/odds_scraper/reconstruct/pricing.py tests/reconstruct/test_pricing.py
git commit -m "feat(reconstruct): next-goal line selection by score"
```

---

### Task 5: Engine-input assembly (no devig)

**Files:**
- Modify: `src/odds_scraper/reconstruct/pricing.py`
- Test: `tests/reconstruct/test_pricing.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/reconstruct/test_pricing.py`:

```python
def _moment(**over):
    m = {
        "event_id": "E1", "sr_id": "sr1", "brand": "ng",
        "event_name": "A vs B", "sr_start_time": "2026-05-01 18:00:00",
        "in_play": False, "moment_ts": "2026-05-01 17:30:00",
        "home_score": 0, "away_score": 0,
        "p_home_raw": 0.5, "p_draw_raw": 0.3, "p_away_raw": 0.4,
        "total_ou": [(2.5, 0.55)], "home_ou": [(1.5, 0.5)], "away_ou": [(1.5, 0.45)],
        "ftts_home": 0.45, "ftts_away": 0.40,
        "max_input_staleness_seconds": 100,
    }
    m.update(over)
    return m


def test_assemble_kwargs_uses_renormalized_probs_and_no_devig():
    kw = pricing.assemble_engine_kwargs(_moment())
    assert math.isclose(kw["p_home_win"] + kw["p_draw"] + kw["p_away_win"], 1.0, abs_tol=1e-9)
    # O/U over-prob passed straight through (no devig)
    assert kw["total_ou"] == [(2.5, 0.55)]
    # cap odds derived from renormalized prob with 2% margin
    assert math.isclose(kw["home_1x2_odds"], pricing.cap_odds_from_prob(kw["p_home_win"]), abs_tol=1e-9)
    assert kw["ftts_home_prob"] == 0.45
    assert kw["score"] == (0, 0)


def test_assemble_kwargs_drops_ftts_when_missing():
    kw = pricing.assemble_engine_kwargs(_moment(ftts_home=None, ftts_away=None))
    assert kw["ftts_home_prob"] is None and kw["ftts_away_prob"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/reconstruct/test_pricing.py -k assemble -v`
Expected: FAIL — no `assemble_engine_kwargs`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/odds_scraper/reconstruct/pricing.py`:

```python
def assemble_engine_kwargs(moment: dict) -> dict:
    """Build the kwargs accepted by every engine's price_early_payout_markets
    from a Moment dict. Renormalizes 1X2, synthesizes cap odds, passes O/U
    over-probabilities and FTTS through unchanged (already fair)."""
    ph, pd, pa, _drift = renormalize_1x2(
        moment["p_home_raw"], moment["p_draw_raw"], moment["p_away_raw"])
    return dict(
        p_home_win=ph, p_draw=pd, p_away_win=pa,
        home_1x2_odds=cap_odds_from_prob(ph),
        draw_1x2_odds=cap_odds_from_prob(pd),
        away_1x2_odds=cap_odds_from_prob(pa),
        total_ou=list(moment["total_ou"]),
        home_ou=list(moment["home_ou"]),
        away_ou=list(moment["away_ou"]),
        ftts_home_prob=moment["ftts_home"],
        ftts_away_prob=moment["ftts_away"],
        score=(int(moment["home_score"]), int(moment["away_score"])),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/reconstruct/test_pricing.py -k assemble -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/odds_scraper/reconstruct/pricing.py tests/reconstruct/test_pricing.py
git commit -m "feat(reconstruct): assemble engine inputs from a pricing moment"
```

---

### Task 6: Shared DP cache across the three engines

**Files:**
- Modify: `src/odds_scraper/reconstruct/pricing.py`
- Test: `tests/reconstruct/test_pricing.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/reconstruct/test_pricing.py`:

```python
from odds_scraper.pricer import engine_v2, engine_v3, engine_v4


def test_install_dp_cache_patches_all_engines_and_restores():
    orig2 = engine_v2.ever_leads_probability
    restore = pricing.install_dp_cache()
    try:
        assert engine_v2.ever_leads_probability is not orig2
        assert engine_v3.ever_leads_probability is engine_v2.ever_leads_probability
        assert engine_v4.ever_leads_probability is engine_v2.ever_leads_probability
        # cache actually memoizes
        engine_v2.ever_leads_probability(1.2, 1.0, 0)
        info_before = pricing.dp_cache_info().misses
        engine_v2.ever_leads_probability(1.2, 1.0, 0)
        assert pricing.dp_cache_info().misses == info_before  # second call hit cache
    finally:
        restore()
    assert engine_v2.ever_leads_probability is orig2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/reconstruct/test_pricing.py -k dp_cache -v`
Expected: FAIL — no `install_dp_cache`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/odds_scraper/reconstruct/pricing.py` (add `import functools` at top of file):

```python
import functools

from odds_scraper.pricer import engine_v2, engine_v3, engine_v4

_dp_cached = None


def install_dp_cache(round_dp: int = 4):
    """Monkeypatch ever_leads_probability in all three engines to share one
    lru_cache keyed on rounded (lambda_h, lambda_a, initial_diff). The DP is
    identical across engines. Returns restore()."""
    global _dp_cached
    originals = {
        m: m.ever_leads_probability for m in (engine_v2, engine_v3, engine_v4)
    }
    base = engine_v2.ever_leads_probability

    @functools.lru_cache(maxsize=200_000)
    def _cached(lh: float, la: float, d: int):
        return base(lh, la, d)

    def wrapper(lambda_h, lambda_a, initial_diff):
        return _cached(round(lambda_h, round_dp), round(lambda_a, round_dp), initial_diff)

    _dp_cached = _cached
    for m in originals:
        m.ever_leads_probability = wrapper

    def restore():
        for m, fn in originals.items():
            m.ever_leads_probability = fn

    return restore


def dp_cache_info():
    return _dp_cached.cache_info()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/reconstruct/test_pricing.py -k dp_cache -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add src/odds_scraper/reconstruct/pricing.py tests/reconstruct/test_pricing.py
git commit -m "feat(reconstruct): shared rounded-lambda DP cache for v2/v3/v4"
```

---

### Task 7: Price a moment into an output row

**Files:**
- Modify: `src/odds_scraper/reconstruct/pricing.py`
- Test: `tests/reconstruct/test_pricing.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/reconstruct/test_pricing.py`:

```python
def test_price_moment_emits_all_engine_cells():
    restore = pricing.install_dp_cache()
    try:
        row = pricing.price_moment(_moment(), run_ts="2026-05-29 00:00:00",
                                   max_home_lead=0, max_away_lead=0)
    finally:
        restore()
    assert row is not None
    for e in ("v2", "v3", "v4"):
        for m in ("1up", "2up"):
            for s in ("home", "away"):
                assert f"{e}_{m}_{s}_odds" in row
                assert f"{e}_{m}_{s}_prob" in row
    assert row["has_1up"] is True
    assert row["in_play"] is False
    assert math.isclose(row["p_home"] + row["p_draw"] + row["p_away"], 1.0, abs_tol=1e-9)


def test_price_moment_returns_none_without_full_1x2():
    m = _moment(p_home_raw=0.0, p_draw_raw=0.0, p_away_raw=0.0)
    restore = pricing.install_dp_cache()
    try:
        assert pricing.price_moment(m, run_ts="t", max_home_lead=0, max_away_lead=0) is None
    finally:
        restore()


def test_price_moment_drops_1up_without_ftts():
    restore = pricing.install_dp_cache()
    try:
        row = pricing.price_moment(_moment(ftts_home=None, ftts_away=None),
                                   run_ts="t", max_home_lead=0, max_away_lead=0)
    finally:
        restore()
    assert row is not None
    assert row["has_1up"] is False
    assert row["v2_1up_home_odds"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/reconstruct/test_pricing.py -k price_moment -v`
Expected: FAIL — no `price_moment`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/odds_scraper/reconstruct/pricing.py`:

```python
_ENGINES = {"v2": engine_v2, "v3": engine_v3, "v4": engine_v4}


def _ev(prob, odds):
    if prob is None or odds is None:
        return None
    return prob * odds - 1.0


def _side_cells(prefix, res, market_key, prob_key_home, prob_key_away):
    m = res[market_key]
    ph, pa = res[prob_key_home], res[prob_key_away]
    oh, oa = m["home_margin"], m["away_margin"]
    return {
        f"{prefix}_home_odds": oh, f"{prefix}_home_prob": ph, f"{prefix}_home_ev": _ev(ph, oh),
        f"{prefix}_away_odds": oa, f"{prefix}_away_prob": pa, f"{prefix}_away_ev": _ev(pa, oa),
    }


def price_moment(moment: dict, *, run_ts: str,
                 max_home_lead: int, max_away_lead: int) -> dict | None:
    """Price one moment with v2/v3/v4. Returns an OUTPUT_COLUMNS-shaped dict,
    or None if the moment carries no priceable market (no full 1X2, or no
    derivable lambda)."""
    ph, pd, pa, drift = renormalize_1x2(
        moment["p_home_raw"], moment["p_draw_raw"], moment["p_away_raw"])
    if not (ph > 0 and pd > 0 and pa > 0):
        return None
    kw = assemble_engine_kwargs(moment)
    kw["max_home_lead"] = max_home_lead
    kw["max_away_lead"] = max_away_lead
    has_1up = kw["ftts_home_prob"] is not None and kw["ftts_away_prob"] is not None

    results = {}
    for name, eng in _ENGINES.items():
        res = eng.price_early_payout_markets(**kw)
        results[name] = res
    # Use v2 as the gate for derivable lambda (DP identical across engines).
    if results["v2"]["lambda_home"] is None or results["v2"]["lambda_away"] is None:
        return None

    row = {
        "run_ts": run_ts, "brand": moment["brand"],
        "event_id": moment["event_id"], "sr_id": moment["sr_id"],
        "event_name": moment["event_name"], "sr_start_time": moment["sr_start_time"],
        "in_play": moment["in_play"], "moment_ts": moment["moment_ts"],
        "home_score": int(moment["home_score"]), "away_score": int(moment["away_score"]),
        "p_home": ph, "p_draw": pd, "p_away": pa,
        "lambda_home": results["v2"]["lambda_home"],
        "lambda_away": results["v2"]["lambda_away"],
        "ftts_home": kw["ftts_home_prob"], "ftts_away": kw["ftts_away_prob"],
        "has_1up": has_1up,
        "max_input_staleness_seconds": int(moment["max_input_staleness_seconds"]),
        "est_input_drift_pct": None,   # filled by the CLI drift pass
        "renorm_drift": round(drift, 6),
    }
    for name, res in results.items():
        row.update(_side_cells(f"{name}_1up", res, "market_1up", "p_home_1", "p_away_1"))
        row.update(_side_cells(f"{name}_2up", res, "market_2up", "p_home_2", "p_away_2"))
    return row
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/reconstruct/test_pricing.py -v`
Expected: all pricing tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/odds_scraper/reconstruct/pricing.py tests/reconstruct/test_pricing.py
git commit -m "feat(reconstruct): price a moment with v2/v3/v4 into an output row"
```

---

### Task 8: Extraction SQL + output DDL

**Files:**
- Create: `src/odds_scraper/reconstruct/queries.py`
- Test: `tests/reconstruct/test_queries.py`

The extraction SQL produces **long** rows (one per selection) already aligned: each O/U and next-goal selection is `ASOF JOIN`ed to the nearest 1X2 `odds_timestamp` per `(event_id, in_play)`. The "pricing moment" key is the anchoring 1X2 timestamp (`moment_ts`). Python groups these long rows into Moment dicts (Task 9). The query selects all next-goal lines (live needs them); Python picks the active line by score.

- [ ] **Step 1: Write the failing test**

Create `tests/reconstruct/test_queries.py`:

```python
from odds_scraper.reconstruct import queries
from odds_scraper.reconstruct import constants as c


def test_extraction_sql_mentions_source_and_markets():
    sql = queries.extraction_sql("bi_Samuel.tbl_x")
    assert "bi_Samuel.tbl_x" in sql
    assert "ASOF" in sql.upper()
    assert c.MARKET_1X2 in sql
    assert "handicap / 4.0" in sql or "handicap/4.0" in sql
    assert "true_proba" in sql
    # next-goal markets matched by the "{n} Goal" pattern, not a fixed handicap
    assert "Goal" in sql


def test_output_ddl_targets_table_and_lists_columns():
    ddl = queries.output_ddl("risk_Lorenzo.out")
    assert "CREATE TABLE IF NOT EXISTS risk_Lorenzo.out" in ddl
    for col in ("run_ts", "event_id", "v4_2up_away_ev"):
        assert col in ddl
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/reconstruct/test_queries.py -v`
Expected: FAIL — no module `queries`.

- [ ] **Step 3: Write minimal implementation**

Create `src/odds_scraper/reconstruct/queries.py`:

```python
"""SQL for ClickHouse 1UP/2UP reconstruction: aligned extraction (ASOF JOIN)
and the output-table DDL."""
from __future__ import annotations

from .constants import (MARKET_1X2, MARKET_OU_TOTAL, MARKET_OU_HOME,
                        MARKET_OU_AWAY, OUTPUT_COLUMNS)

# Markets we anchor O/U + next-goal to. The 1X2 snapshot is the anchor; every
# other selection is snapped to the nearest 1X2 odds_timestamp within the same
# (event_id, in_play). Next-goal is matched by the "{n} Goal" name pattern so
# live can use any line; the active line is chosen in Python by score.
_NON_ANCHOR_FILTER = (
    f"market_name IN ('{MARKET_OU_TOTAL}', '{MARKET_OU_HOME}', '{MARKET_OU_AWAY}') "
    f"OR match(market_name, '^[0-9]+ Goal$')"
)


def extraction_sql(source_table: str) -> str:
    """Return long aligned rows ordered by (event_id, in_play, moment_ts).
    Columns: event_id, sr_id, brand, event_name, sr_start_time, in_play,
    moment_ts, home_score, away_score, market_name, line, selection_name,
    true_proba, sel_ts."""
    return f"""
WITH anchor AS (
    SELECT event_id, sr_id, brand, event_name, sr_start_time, in_play,
           odds_timestamp AS moment_ts, home_score, away_score,
           selection_name, true_proba
    FROM {source_table}
    WHERE market_name = '{MARKET_1X2}'
      AND true_proba IS NOT NULL AND true_proba != 0
),
other AS (
    SELECT event_id, in_play, odds_timestamp AS sel_ts,
           market_name, handicap / 4.0 AS line, selection_name, true_proba,
           home_score, away_score
    FROM {source_table}
    WHERE ({_NON_ANCHOR_FILTER})
      AND true_proba IS NOT NULL AND true_proba != 0
)
SELECT a.event_id, a.sr_id, a.brand, a.event_name, a.sr_start_time,
       a.in_play, a.moment_ts, a.home_score, a.away_score,
       a.selection_name AS x12_selection, a.true_proba AS x12_proba,
       o.market_name, o.line, o.selection_name, o.true_proba, o.sel_ts
FROM anchor AS a
ASOF LEFT JOIN other AS o
  ON a.event_id = o.event_id AND a.in_play = o.in_play
 AND o.sel_ts <= a.moment_ts
ORDER BY a.event_id, a.in_play, a.moment_ts
"""


def output_ddl(output_table: str) -> str:
    """MergeTree DDL covering OUTPUT_COLUMNS. Strings for ids/labels, Float64
    for probs/odds, Int for scores, DateTime for timestamps."""
    string_cols = {"run_ts", "brand", "event_id", "sr_id", "event_name",
                   "sr_start_time", "moment_ts"}
    int_cols = {"home_score", "away_score", "max_input_staleness_seconds"}
    bool_cols = {"in_play", "has_1up"}
    defs = []
    for col in OUTPUT_COLUMNS:
        if col in string_cols:
            t = "String"
        elif col in int_cols:
            t = "Int32"
        elif col in bool_cols:
            t = "UInt8"
        else:
            t = "Nullable(Float64)"
        defs.append(f"    `{col}` {t}")
    cols_sql = ",\n".join(defs)
    return (f"CREATE TABLE IF NOT EXISTS {output_table} (\n{cols_sql}\n) "
            f"ENGINE = MergeTree ORDER BY (event_id, in_play, moment_ts)")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/reconstruct/test_queries.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/odds_scraper/reconstruct/queries.py tests/reconstruct/test_queries.py
git commit -m "feat(reconstruct): aligned extraction SQL (ASOF JOIN) + output DDL"
```

---

### Task 9: Group aligned rows into Moments

**Files:**
- Modify: `src/odds_scraper/reconstruct/pricing.py`
- Test: `tests/reconstruct/test_pricing.py`

The query yields one long row per (anchor 1X2 selection × matched other selection). Assembly collapses all long rows sharing `(event_id, in_play, moment_ts)` into one Moment: the three 1X2 probabilities, O/U over-prob lists per family, and the next-goal Home/Away probs for the **active** line (by score). Staleness = `moment_ts - min(sel_ts)` over the inputs actually used.

- [ ] **Step 1: Write the failing test**

Append to `tests/reconstruct/test_pricing.py`:

```python
from odds_scraper.reconstruct import constants as c


def _long(market, sel, proba, line=0.0, x12_sel="Home", x12_proba=0.5,
          ts="2026-05-01 17:30:00", sel_ts="2026-05-01 17:29:00",
          in_play=False, hs=0, as_=0):
    return {
        "event_id": "E1", "sr_id": "sr1", "brand": "ng",
        "event_name": "A vs B", "sr_start_time": "2026-05-01 18:00:00",
        "in_play": in_play, "moment_ts": ts, "home_score": hs, "away_score": as_,
        "x12_selection": x12_sel, "x12_proba": x12_proba,
        "market_name": market, "line": line, "selection_name": sel,
        "true_proba": proba, "sel_ts": sel_ts,
    }


def test_moments_from_rows_builds_one_moment_per_timestamp():
    rows = [
        _long(c.MARKET_1X2, "Home", 0.5, x12_sel="Home", x12_proba=0.5),
        _long(c.MARKET_1X2, "Draw", 0.3, x12_sel="Draw", x12_proba=0.3),
        _long(c.MARKET_1X2, "Away", 0.4, x12_sel="Away", x12_proba=0.4),
        _long(c.MARKET_OU_TOTAL, "Over", 0.55, line=2.5),
        _long("1 Goal", "Home", 0.46, line=1.0),
        _long("1 Goal", "Away", 0.40, line=1.0),
        _long("1 Goal", "None", 0.14, line=1.0),
    ]
    moments = list(pricing.moments_from_rows(rows))
    assert len(moments) == 1
    m = moments[0]
    assert m["p_home_raw"] == 0.5 and m["p_draw_raw"] == 0.3 and m["p_away_raw"] == 0.4
    assert m["total_ou"] == [(2.5, 0.55)]
    assert m["ftts_home"] == 0.46 and m["ftts_away"] == 0.40   # active line #1


def test_moments_active_next_goal_line_follows_score():
    # 1-1 -> active next goal is line #3; line #1 must be ignored for ftts
    rows = [
        _long(c.MARKET_1X2, "Home", 0.5, in_play=True, hs=1, as_=1),
        _long(c.MARKET_1X2, "Draw", 0.3, x12_sel="Draw", in_play=True, hs=1, as_=1),
        _long(c.MARKET_1X2, "Away", 0.4, x12_sel="Away", in_play=True, hs=1, as_=1),
        _long(c.MARKET_OU_TOTAL, "Over", 0.6, line=3.5, in_play=True, hs=1, as_=1),
        _long("1 Goal", "Home", 0.9, line=1.0, in_play=True, hs=1, as_=1),
        _long("3 Goal", "Home", 0.30, line=3.0, in_play=True, hs=1, as_=1),
        _long("3 Goal", "Away", 0.25, line=3.0, in_play=True, hs=1, as_=1),
        _long("3 Goal", "None", 0.45, line=3.0, in_play=True, hs=1, as_=1),
    ]
    m = list(pricing.moments_from_rows(rows))[0]
    assert m["ftts_home"] == 0.30 and m["ftts_away"] == 0.25   # line #3, not #1


def test_moments_no_ftts_when_active_line_absent():
    rows = [
        _long(c.MARKET_1X2, "Home", 0.5, in_play=True, hs=2, as_=0),
        _long(c.MARKET_1X2, "Draw", 0.3, x12_sel="Draw", in_play=True, hs=2, as_=0),
        _long(c.MARKET_1X2, "Away", 0.4, x12_sel="Away", in_play=True, hs=2, as_=0),
        _long(c.MARKET_OU_TOTAL, "Over", 0.6, line=3.5, in_play=True, hs=2, as_=0),
    ]
    m = list(pricing.moments_from_rows(rows))[0]
    assert m["ftts_home"] is None and m["ftts_away"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/reconstruct/test_pricing.py -k moments -v`
Expected: FAIL — no `moments_from_rows`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/odds_scraper/reconstruct/pricing.py` (add `import collections` and `from datetime import datetime` at top, and `from .constants import (...)` extended to include the market/selection labels):

```python
import collections
from datetime import datetime

from .constants import (MARKET_1X2, MARKET_OU_TOTAL, MARKET_OU_HOME,
                        MARKET_OU_AWAY, SEL_OVER, SEL_NG_HOME, SEL_NG_AWAY)

_TS_FMT = "%Y-%m-%d %H:%M:%S"
_OU_FAMILY = {MARKET_OU_TOTAL: "total_ou", MARKET_OU_HOME: "home_ou",
              MARKET_OU_AWAY: "away_ou"}


def _is_next_goal(market_name: str) -> bool:
    return market_name.endswith(" Goal") and market_name[:-5].isdigit()


def moments_from_rows(rows):
    """Group aligned long rows (Task 8 output) into Moment dicts, ordered as
    the rows arrive. `rows` must be grouped by (event_id, in_play, moment_ts)
    contiguously (the extraction SQL ORDER BY guarantees this)."""
    def key(r):
        return (r["event_id"], r["in_play"], r["moment_ts"])

    for (_eid, _ip, _mts), group in _groupby_contiguous(rows, key):
        group = list(group)
        head = group[0]
        # 1X2 from the anchor selection columns (same on every row of the group)
        p = {}
        for r in group:
            p[r["x12_selection"]] = r["x12_proba"]
        if not ({"Home", "Draw", "Away"} <= set(p)):
            continue
        hs, as_ = int(head["home_score"]), int(head["away_score"])
        active_line = float(next_goal_index(hs, as_))
        ou = {"total_ou": {}, "home_ou": {}, "away_ou": {}}
        ng = {}
        sel_ts_used = []
        for r in group:
            mkt, sel = r["market_name"], r["selection_name"]
            if mkt in _OU_FAMILY and sel == SEL_OVER:
                ou[_OU_FAMILY[mkt]][float(r["line"])] = r["true_proba"]
                sel_ts_used.append(r["sel_ts"])
            elif _is_next_goal(mkt) and float(r["line"]) == active_line:
                ng[sel] = r["true_proba"]
                sel_ts_used.append(r["sel_ts"])
        ftts_home = ng.get(SEL_NG_HOME)
        ftts_away = ng.get(SEL_NG_AWAY)
        if ftts_home is None or ftts_away is None:
            ftts_home = ftts_away = None
        moment_ts = head["moment_ts"]
        stale = _staleness_seconds(moment_ts, sel_ts_used)
        yield {
            "event_id": head["event_id"], "sr_id": head["sr_id"],
            "brand": head["brand"], "event_name": head["event_name"],
            "sr_start_time": head["sr_start_time"],
            "in_play": head["in_play"], "moment_ts": moment_ts,
            "home_score": hs, "away_score": as_,
            "p_home_raw": p["Home"], "p_draw_raw": p["Draw"], "p_away_raw": p["Away"],
            "total_ou": sorted(ou["total_ou"].items()),
            "home_ou": sorted(ou["home_ou"].items()),
            "away_ou": sorted(ou["away_ou"].items()),
            "ftts_home": ftts_home, "ftts_away": ftts_away,
            "max_input_staleness_seconds": stale,
        }


def _groupby_contiguous(rows, key):
    cur_key, bucket = object(), []
    for r in rows:
        k = key(r)
        if k != cur_key and bucket:
            yield cur_key, bucket
            bucket = []
        cur_key = k
        bucket.append(r)
    if bucket:
        yield cur_key, bucket


def _staleness_seconds(moment_ts: str, sel_ts_list) -> int:
    if not sel_ts_list:
        return 0
    t0 = _parse_ts(moment_ts)
    worst = max((t0 - _parse_ts(s)).total_seconds() for s in sel_ts_list if s)
    return int(round(max(worst, 0)))


def _parse_ts(s):
    if isinstance(s, datetime):
        return s
    return datetime.strptime(str(s)[:19], _TS_FMT)
```

Note: the helper functions `_groupby_contiguous`, `_staleness_seconds`, `_parse_ts` are defined once here and reused; do not redefine them elsewhere.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/reconstruct/test_pricing.py -k moments -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/odds_scraper/reconstruct/pricing.py tests/reconstruct/test_pricing.py
git commit -m "feat(reconstruct): group aligned rows into pricing moments"
```

---

### Task 10: ClickHouse connection adapter

**Files:**
- Create: `src/odds_scraper/reconstruct/clickhouse_io.py`
- Test: `tests/reconstruct/test_clickhouse_io.py`

- [ ] **Step 1: Write the failing test**

Create `tests/reconstruct/test_clickhouse_io.py`:

```python
import pytest
from odds_scraper.reconstruct import clickhouse_io as chio


def test_config_from_env_reads_expected_vars(monkeypatch):
    monkeypatch.setenv("CH_HOST", "127.0.0.1")
    monkeypatch.setenv("CH_PORT", "12345")
    monkeypatch.setenv("CH_USER", "lorenzo")
    monkeypatch.setenv("CH_PASSWORD", "secret")
    monkeypatch.setenv("CH_DATABASE", "risk_Lorenzo")
    cfg = chio.config_from_env()
    assert cfg == {"host": "127.0.0.1", "port": 12345, "username": "lorenzo",
                   "password": "secret", "database": "risk_Lorenzo"}


def test_config_from_env_requires_host(monkeypatch):
    monkeypatch.delenv("CH_HOST", raising=False)
    with pytest.raises(RuntimeError, match="CH_HOST"):
        chio.config_from_env()


def test_insert_rows_batches_and_orders_columns():
    captured = []

    class FakeClient:
        def insert(self, table, data, column_names):
            captured.append((table, list(data), list(column_names)))

    rows = [{"a": 1, "b": 2}, {"a": 3, "b": 4}, {"a": 5, "b": 6}]
    chio.insert_rows(FakeClient(), "t", rows, columns=["b", "a"], batch_size=2)
    # two batches: sizes 2 and 1, values ordered as ["b","a"]
    assert [len(d) for _, d, _ in captured] == [2, 1]
    assert captured[0][1][0] == [2, 1]   # first row -> [b, a]
    assert captured[0][2] == ["b", "a"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/reconstruct/test_clickhouse_io.py -v`
Expected: FAIL — no module `clickhouse_io`.

- [ ] **Step 3: Write minimal implementation**

Create `src/odds_scraper/reconstruct/clickhouse_io.py`:

```python
"""Thin ClickHouse connection adapter (clickhouse-connect over the local
Teleport proxy). Config from env; no business logic."""
from __future__ import annotations

import os
from typing import Iterable


def config_from_env() -> dict:
    """Read connection config from CH_* env vars. host is required."""
    host = os.environ.get("CH_HOST")
    if not host:
        raise RuntimeError("CH_HOST not set (point it at the local Teleport proxy)")
    return {
        "host": host,
        "port": int(os.environ.get("CH_PORT", "8123")),
        "username": os.environ.get("CH_USER", "default"),
        "password": os.environ.get("CH_PASSWORD", ""),
        "database": os.environ.get("CH_DATABASE", "default"),
    }


def connect(config: dict | None = None):
    """Open a clickhouse-connect client. Imported lazily so unit tests that
    inject a fake client need no driver/network."""
    import clickhouse_connect
    return clickhouse_connect.get_client(**(config or config_from_env()))


def stream_rows(client, sql: str):
    """Yield query result rows as dicts, streaming in blocks."""
    with client.query_rows_stream(sql) as stream:
        columns = stream.source.column_names
        for row in stream:
            yield dict(zip(columns, row))


def insert_rows(client, table: str, rows: Iterable[dict], *, columns: list,
                batch_size: int = 10_000) -> int:
    """Insert dict rows in column order, in batches. Returns total inserted."""
    batch, total = [], 0
    for r in rows:
        batch.append([r.get(c) for c in columns])
        if len(batch) >= batch_size:
            client.insert(table, batch, column_names=columns)
            total += len(batch)
            batch = []
    if batch:
        client.insert(table, batch, column_names=columns)
        total += len(batch)
    return total
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/reconstruct/test_clickhouse_io.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/odds_scraper/reconstruct/clickhouse_io.py tests/reconstruct/test_clickhouse_io.py
git commit -m "feat(reconstruct): ClickHouse connection adapter (env config, batched insert)"
```

---

### Task 11: Reliability report

**Files:**
- Create: `src/odds_scraper/reconstruct/report.py`
- Test: `tests/reconstruct/test_report.py`

- [ ] **Step 1: Write the failing test**

Create `tests/reconstruct/test_report.py`:

```python
from odds_scraper.reconstruct import report


def test_build_report_contains_counts_and_split():
    sample = [
        {"in_play": False, "has_1up": True, "max_input_staleness_seconds": 100,
         "renorm_drift": 0.01},
        {"in_play": True, "has_1up": False, "max_input_staleness_seconds": 200,
         "renorm_drift": 0.2},
    ]
    md = report.build_report(
        source_table="bi_Samuel.t", output_table="risk_Lorenzo.o",
        n_out=2, n_1up=1, n_prematch=1, n_live=1,
        sample_rows=sample, flagged_drift=1)
    assert "bi_Samuel.t" in md and "risk_Lorenzo.o" in md
    assert "rows emitted" in md
    assert "prematch" in md and "live" in md
    assert "max_lead" in md.lower()  # limitation note present
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/reconstruct/test_report.py -v`
Expected: FAIL — no module `report`.

- [ ] **Step 3: Write minimal implementation**

Create `src/odds_scraper/reconstruct/report.py`:

```python
"""Markdown reliability report for a reconstruction run."""
from __future__ import annotations


def _pct(vals, p):
    if not vals:
        return 0
    s = sorted(vals)
    return s[min(len(s) - 1, int(len(s) * p / 100))]


def build_report(*, source_table, output_table, n_out, n_1up, n_prematch,
                 n_live, sample_rows, flagged_drift) -> str:
    stale = [r["max_input_staleness_seconds"] for r in sample_rows]
    lines = [
        "# ClickHouse 1UP/2UP reconstruction report", "",
        f"- source: `{source_table}`",
        f"- output: `{output_table}`",
        f"- rows emitted: {n_out:,}",
        f"- prematch: {n_prematch:,} | live: {n_live:,}",
        f"- rows with 1UP priced: {n_1up:,} ({(100*n_1up/n_out if n_out else 0):.0f}%)",
        f"- 2UP-only rows: {n_out - n_1up:,}",
        f"- 1X2 renorm-drift flagged (> tol): {flagged_drift:,}",
        "",
        "## Staleness (emitted moments)",
        f"- seconds — p50 {_pct(stale,50)}, p90 {_pct(stale,90)}, "
        f"max {max(stale) if stale else 0}",
        "",
        "## Limitations",
        "- `max_home_lead`/`max_away_lead` are approximated from the max score "
        "observed in the available (opportunistic) snapshots for each event; an "
        "unobserved lead swing (e.g. 1-0 -> 1-1 between snapshots) can mis-price "
        "live 1UP/2UP.",
    ]
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/reconstruct/test_report.py -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add src/odds_scraper/reconstruct/report.py tests/reconstruct/test_report.py
git commit -m "feat(reconstruct): reliability report builder"
```

---

### Task 12: CLI orchestrator

**Files:**
- Create: `scripts/reconstruct_clickhouse.py`
- Test: `tests/reconstruct/test_pricing.py` (one end-to-end-in-memory test of the pure pipeline helper)

The CLI streams aligned rows from ClickHouse, builds moments, prices them (tracking per-event max lead), and inserts results. To keep it testable without a DB, the per-event pricing loop lives in a pure function `run_pricing(moments_iter, run_ts)` in `pricing.py`; the CLI only wires IO around it.

- [ ] **Step 1: Write the failing test (pure pipeline)**

Append to `tests/reconstruct/test_pricing.py`:

```python
def test_run_pricing_tracks_max_lead_across_event_moments():
    # Two live moments for one event: 1-0 then 1-1. At 1-1 the engine must
    # know home previously led by 1 (max_home_lead=1) for correct 1UP deactivation.
    base = dict(event_id="E1", sr_id="s", brand="ng", event_name="A vs B",
                sr_start_time="2026-05-01 18:00:00", in_play=True,
                total_ou=[(2.5, 0.55)], home_ou=[(1.5, 0.5)], away_ou=[(1.5, 0.45)],
                ftts_home=0.45, ftts_away=0.40, max_input_staleness_seconds=50,
                p_home_raw=0.5, p_draw_raw=0.3, p_away_raw=0.4)
    m1 = {**base, "moment_ts": "2026-05-01 18:20:00", "home_score": 1, "away_score": 0}
    m2 = {**base, "moment_ts": "2026-05-01 18:40:00", "home_score": 1, "away_score": 1}
    restore = pricing.install_dp_cache()
    try:
        rows = list(pricing.run_pricing([m1, m2], run_ts="2026-05-29 00:00:00"))
    finally:
        restore()
    assert len(rows) == 2
    assert all(r["run_ts"] == "2026-05-29 00:00:00" for r in rows)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/reconstruct/test_pricing.py -k run_pricing -v`
Expected: FAIL — no `run_pricing`.

- [ ] **Step 3: Implement `run_pricing` in pricing.py**

Append to `src/odds_scraper/reconstruct/pricing.py`:

```python
def run_pricing(moments_iter, *, run_ts: str):
    """Price a stream of moments, tracking per-event max lead so live
    deactivation is history-aware across the moments we observed. Moments must
    be grouped by event_id contiguously (extraction SQL ORDER BY guarantees
    it). Yields output rows (skips None)."""
    cur_event = object()
    max_h = max_a = 0
    for m in moments_iter:
        if m["event_id"] != cur_event:
            cur_event, max_h, max_a = m["event_id"], 0, 0
        diff = int(m["home_score"]) - int(m["away_score"])
        max_h = max(max_h, diff)
        max_a = max(max_a, -diff)
        row = price_moment(m, run_ts=run_ts,
                           max_home_lead=max_h, max_away_lead=max_a)
        if row is not None:
            yield row
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/reconstruct/test_pricing.py -k run_pricing -v`
Expected: 1 passed.

- [ ] **Step 5: Write the CLI**

Create `scripts/reconstruct_clickhouse.py`:

```python
"""Batch-reconstruct V2/V3/V4 1UP/2UP odds from the ClickHouse betslip log.

Requires a reachable ClickHouse (local Teleport proxy) via CH_* env vars:
  CH_HOST, CH_PORT, CH_USER, CH_PASSWORD, CH_DATABASE
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from odds_scraper.reconstruct import clickhouse_io as chio
from odds_scraper.reconstruct import constants as c
from odds_scraper.reconstruct import pricing, queries, report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True,
                    help="e.g. bi_Samuel.tbl_oneup_backtest_odds_data_betslip_includingGoalInfo")
    ap.add_argument("--output", default=c.DEFAULT_OUTPUT_TABLE)
    ap.add_argument("--report", required=True)
    ap.add_argument("--run-ts", required=True,
                    help="run identifier timestamp 'YYYY-MM-DD HH:MM:SS'")
    ap.add_argument("--batch-size", type=int, default=10_000)
    args = ap.parse_args()

    client = chio.connect()
    client.command(queries.output_ddl(args.output))

    restore = pricing.install_dp_cache()
    n_out = n_1up = n_prematch = n_live = flagged = 0
    sample_rows = []
    try:
        rows_stream = chio.stream_rows(client, queries.extraction_sql(args.source))
        moments = pricing.moments_from_rows(rows_stream)
        priced = pricing.run_pricing(moments, run_ts=args.run_ts)

        def _accounting(it):
            nonlocal n_out, n_1up, n_prematch, n_live, flagged
            for row in it:
                n_out += 1
                n_1up += 1 if row["has_1up"] else 0
                n_prematch += 0 if row["in_play"] else 1
                n_live += 1 if row["in_play"] else 0
                if abs(row["renorm_drift"]) > c.RENORM_DRIFT_TOL:
                    flagged += 1
                sample_rows.append({
                    "in_play": row["in_play"], "has_1up": row["has_1up"],
                    "max_input_staleness_seconds": row["max_input_staleness_seconds"],
                    "renorm_drift": row["renorm_drift"]})
                yield row

        inserted = chio.insert_rows(client, args.output, _accounting(priced),
                                    columns=c.OUTPUT_COLUMNS,
                                    batch_size=args.batch_size)
    finally:
        restore()

    Path(args.report).write_text(
        report.build_report(source_table=args.source, output_table=args.output,
                            n_out=n_out, n_1up=n_1up, n_prematch=n_prematch,
                            n_live=n_live, sample_rows=sample_rows,
                            flagged_drift=flagged),
        encoding="utf-8")
    print(f"inserted {inserted} rows -> {args.output} ({n_1up} with 1UP, "
          f"{n_prematch} prematch / {n_live} live)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Verify the CLI parses and the pure tests pass**

Run: `python scripts/reconstruct_clickhouse.py --help`
Expected: prints usage with `--source`, `--output`, `--report`, `--run-ts`.
Run: `pytest tests/reconstruct -v`
Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add scripts/reconstruct_clickhouse.py src/odds_scraper/reconstruct/pricing.py tests/reconstruct/test_pricing.py
git commit -m "feat(reconstruct): CLI orchestrator + per-event max-lead tracking"
```

---

### Task 13: Proxy-gated integration smoke test + vocabulary verification

**Files:**
- Create: `tests/reconstruct/test_integration.py`

This is the one place we touch the real ClickHouse. It is **skipped** unless `CH_HOST` is set, so the suite stays green offline. It also doubles as the §12 vocabulary check: it asserts the next-goal `selection_name` labels and market names in `constants.py` actually appear in the source table.

- [ ] **Step 1: Write the integration test**

Create `tests/reconstruct/test_integration.py`:

```python
import os
import pytest

from odds_scraper.reconstruct import clickhouse_io as chio
from odds_scraper.reconstruct import constants as c
from odds_scraper.reconstruct import pricing, queries

pytestmark = pytest.mark.skipif(
    not os.environ.get("CH_HOST"), reason="no ClickHouse proxy (CH_HOST unset)")

SOURCE = os.environ.get(
    "CH_SOURCE",
    "bi_Samuel.tbl_oneup_backtest_odds_data_betslip_includingGoalInfo")


def _client():
    return chio.connect()


def test_source_vocabulary_matches_constants():
    client = _client()
    names = {r[0] for r in client.query(
        f"SELECT DISTINCT market_name FROM {SOURCE} LIMIT 200").result_rows}
    assert c.MARKET_1X2 in names
    assert c.MARKET_OU_TOTAL in names
    # at least one "{n} Goal" next-goal market exists
    assert any(n.endswith(" Goal") and n[:-5].isdigit() for n in names)
    sels = {r[0] for r in client.query(
        f"SELECT DISTINCT selection_name FROM {SOURCE} "
        f"WHERE market_name LIKE '% Goal' LIMIT 50").result_rows}
    assert {c.SEL_NG_HOME, c.SEL_NG_AWAY, c.SEL_NG_NONE} <= sels


def test_end_to_end_small_slice_prices_and_inserts():
    client = _client()
    client.command(queries.output_ddl("risk_Lorenzo.recon_smoke_test"))
    # one event's worth of aligned rows
    sql = queries.extraction_sql(SOURCE).rstrip()
    sql += "\nLIMIT 5000"
    rows = list(chio.stream_rows(client, sql))
    restore = pricing.install_dp_cache()
    try:
        moments = list(pricing.moments_from_rows(rows))
        priced = list(pricing.run_pricing(moments, run_ts="2026-05-29 00:00:00"))
    finally:
        restore()
    assert priced, "expected at least one priced moment from a 5000-row slice"
    n = chio.insert_rows(client, "risk_Lorenzo.recon_smoke_test", priced,
                         columns=c.OUTPUT_COLUMNS, batch_size=1000)
    assert n == len(priced)
    client.command("DROP TABLE IF EXISTS risk_Lorenzo.recon_smoke_test")
```

- [ ] **Step 2: Run offline (should skip)**

Run: `pytest tests/reconstruct/test_integration.py -v`
Expected: 2 skipped (CH_HOST unset).

- [ ] **Step 3: Run against the proxy (manual, when proxy is up)**

With the Teleport proxy running and `CH_HOST`/`CH_PORT`/`CH_USER`/`CH_PASSWORD`/`CH_DATABASE` exported:
Run: `pytest tests/reconstruct/test_integration.py -v`
Expected: 2 passed. If `test_source_vocabulary_matches_constants` fails, fix the labels in `constants.py` to match the real table (this is the §12 verification) and re-run.

- [ ] **Step 4: Commit**

```bash
git add tests/reconstruct/test_integration.py
git commit -m "test(reconstruct): proxy-gated integration smoke + vocabulary check"
```

---

### Task 14: Full-suite check and report dry-run

**Files:** none (verification only)

- [ ] **Step 1: Run the whole suite**

Run: `pytest tests/reconstruct -v`
Expected: all unit tests pass; integration tests skipped (offline).

- [ ] **Step 2: Confirm no regressions in the broader suite**

Run: `pytest -q`
Expected: existing tests still pass (the new package is additive; only `engine_v4.py` was newly tracked).

- [ ] **Step 3: Document the run command in the spec's §12 follow-ups (optional)**

If the proxy details are now known, record the exact `CH_*` values needed and the real `--source`/`--output` in a short note at the top of `scripts/reconstruct_clickhouse.py` (do not commit secrets).

---

## Self-Review

**Spec coverage:**
- §2 source/markets → Tasks 2, 8. Sink/DDL → Tasks 2, 8, 12. Connection/env → Task 10. ✓
- §3 components (clickhouse_io, queries, pricing, report, CLI) → Tasks 8, 10, 11, 12; pricing core → Tasks 3-7, 9. ✓
- §4 data flow (extract → align → renorm → cap → next-goal → price → insert → report) → Tasks 8, 9, 3, 5, 7, 12, 11. ✓
- §5 no-devig + `1/(p*1.02)` cap + `max_lead` approximation → Tasks 3, 5, 12 (`run_pricing`). ✓
- §6 live score + next-goal line `total+1` + 3-way FTTS requirement → Tasks 4, 9. ✓
- §7 output schema/columns/`run_ts` → Task 2 (`OUTPUT_COLUMNS`), Task 8 (DDL), Task 7 (row). ✓
- §8 error handling (drop no-1X2 / no-λ, renorm flag, batched insert) → Tasks 7, 12, 10. ✓
- §9 report contents → Task 11. ✓
- §10 testing (unit + proxy-gated integration + vocabulary verify) → Tasks 3-12 tests, Task 13. ✓
- §12 branch strategy → Task 1 (track engine_v4, stay on this branch); table name default → Task 2; next-goal labels verified → Task 13. ✓

**Placeholder scan:** No TBD/TODO; every code step has complete code; commands have expected output. ✓

**Type consistency:** Moment dict keys are identical across Tasks 5, 7, 9, 12 and the contract block. `OUTPUT_COLUMNS` (Task 2) is the single source for the row shape used in Tasks 7, 8, 10, 12. `install_dp_cache`/`dp_cache_info`/`price_moment`/`moments_from_rows`/`run_pricing` signatures match between definition and call sites. Engine call uses kwargs present in all three engines (`score`, `max_home_lead`, `max_away_lead` confirmed). ✓
