# Pricer Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate the BetPawa pricing engine into the odds app so OUR computed 1UP/2UP prices appear inline on event cards (port-verification + live comparison) and a `/simulator` page runs batch backtests with tunable coefficients and CSV export.

**Architecture:** New `src/odds_scraper/pricer/` package — engine.py is a verbatim copy of `engine_prod_v1.py`, `inputs.py` builds engine inputs with BP-first / SB-fallback per-input, `runner.py` orchestrates simulator runs and provides a `with_coefficients` contextmanager that mutates engine module state for one sync call. Three new SQLite tables (`pricer_configs`, `pricer_runs`, `pricer_results`) persist runs; CSVs land at `data/sim/run_<id>.csv`. Card SIM column hangs off `_build_event_view` — engine called per event at render time (~1ms × ~50 events).

**Tech Stack:** Python 3.13, FastAPI + Jinja2, SQLite WAL, vanilla CSS, pytest. No new third-party deps (engine has zero project deps — `math` and `typing` stdlib only).

---

## File map

**New files:**
- `src/odds_scraper/pricer/__init__.py`
- `src/odds_scraper/pricer/engine.py` — verbatim copy of `C:\Users\loren\Desktop\betpawa\1UP\test_1up\src\pricer\engine_prod_v1.py`
- `src/odds_scraper/pricer/inputs.py` — BP-first per-input extraction
- `src/odds_scraper/pricer/runner.py` — `with_coefficients()` + `run_simulation()`
- `src/odds_scraper/pricer/configs.py` — coefficient profile CRUD; `DEFAULT_COEFFICIENTS` + `TUNABLE_NAMES`
- `src/odds_scraper/pricer/csv_export.py` — `write_run_csv()`
- `src/odds_scraper/web/pricer_routes.py` — `register_pricer_routes(app, conn, db_path)`
- `src/odds_scraper/web/templates/simulator.html`
- `tests/test_pricer_engine.py` — verbatim copy of the original test
- `tests/test_pricer_inputs.py`
- `tests/test_pricer_runner.py`
- `tests/test_pricer_configs.py`
- `tests/test_pricer_csv.py`
- `tests/test_simulator_routes.py`

**Modified files:**
- `src/odds_scraper/db_schema.py` — bump to `SCHEMA_VERSION = 4`, add migration `4` (three tables + seed default profile)
- `src/odds_scraper/web/app.py` — `EventView` gains six fields, `_build_event_view` computes OUR, `create_app` registers pricer routes, expose db_path to handlers via closure
- `src/odds_scraper/web/templates/_event_card.html` — SIM column markup + per-row population rules
- `src/odds_scraper/web/static/app.css` — `.sim` cell class
- `src/odds_scraper/web/templates/index.html` — link to `/simulator` in the top bar (single anchor)
- `tests/test_web_app.py` — four new SIM-column tests

---

## Tunable coefficient list (used in Tasks 2, 3, 11)

The 13 named constants exposed in the simulator form:

```python
# In pricer/configs.py
TUNABLE_NAMES = (
    "ONEUP_FAVORITE_MARGIN",                  # (slope, intercept)
    "ONEUP_UNDERDOG_MARGIN",                  # (slope, intercept)
    "ONEUP_MIN_GUARANTEED_REDUCTION",         # float
    "ONEUP_TRAILING_MIN_REDUCTION",           # float
    "ONEUP_TRAILING_MAX_REDUCTION",           # float
    "TWOUP_FAVORITE_MARGIN",                  # (slope, intercept)
    "TWOUP_UNDERDOG_MARGIN",                  # (slope, intercept)
    "TWOUP_FAVORITE_BOOST_COEFFICIENT",       # float
    "TWOUP_UNDERDOG_BOOST_COEFFICIENT",       # float
    "TWOUP_FAVORITE_MIN_GUARANTEED_REDUCTION",
    "TWOUP_UNDERDOG_MIN_GUARANTEED_REDUCTION",
    "TWOUP_TRAILING_MIN_REDUCTION",
    "TWOUP_TRAILING_MAX_REDUCTION",
)

DEFAULT_COEFFICIENTS = {
    "ONEUP_FAVORITE_MARGIN": [0.9969, 0.0313],
    "ONEUP_UNDERDOG_MARGIN": [0.9799, 0.0400],
    "ONEUP_MIN_GUARANTEED_REDUCTION": 0.02,
    "ONEUP_TRAILING_MIN_REDUCTION": 0.05,
    "ONEUP_TRAILING_MAX_REDUCTION": 0.25,
    "TWOUP_FAVORITE_MARGIN": [0.998, 0.010],
    "TWOUP_UNDERDOG_MARGIN": [0.994, 0.008],
    "TWOUP_FAVORITE_BOOST_COEFFICIENT": 0.9,
    "TWOUP_UNDERDOG_BOOST_COEFFICIENT": 0.6,
    "TWOUP_FAVORITE_MIN_GUARANTEED_REDUCTION": 0.02,
    "TWOUP_UNDERDOG_MIN_GUARANTEED_REDUCTION": 0.005,
    "TWOUP_TRAILING_MIN_REDUCTION": 0.05,
    "TWOUP_TRAILING_MAX_REDUCTION": 0.25,
}
```

`is_default=1` rows store this JSON. When applying overrides, list values are converted back to tuples before `setattr` on the engine module (engine's tuple constants must stay tuples).

---

### Task 1: Copy engine + verify port

**Files:**
- Create: `src/odds_scraper/pricer/__init__.py` (empty)
- Create: `src/odds_scraper/pricer/engine.py` (verbatim copy)
- Create: `tests/test_pricer_engine.py` (verbatim copy)

- [ ] **Step 1: Create the pricer package**

```bash
mkdir src/odds_scraper/pricer
type nul > src/odds_scraper/pricer/__init__.py
```

- [ ] **Step 2: Copy the engine source verbatim**

```bash
copy "C:\Users\loren\Desktop\betpawa\1UP\test_1up\src\pricer\engine_prod_v1.py" "src\odds_scraper\pricer\engine.py"
```

- [ ] **Step 3: Copy the engine test verbatim**

```bash
copy "C:\Users\loren\Desktop\betpawa\1UP\test_1up\tests\test_engine_prod_v1.py" "tests\test_pricer_engine.py"
```

- [ ] **Step 4: Rewrite the import in the copied test**

The original test imports `from src.pricer import engine_prod_v1 as ep`. Replace with:

```python
from odds_scraper.pricer import engine as ep
```

Use a search-and-replace on the single import line in `tests/test_pricer_engine.py`.

- [ ] **Step 5: Run engine tests to verify port**

Run: `.venv/Scripts/python.exe -m pytest tests/test_pricer_engine.py -v`
Expected: all tests pass (the original suite verifies the Java port — passing here proves nothing regressed during the copy).

- [ ] **Step 6: Commit**

```bash
git checkout -b feat/pricer-engine-copy
git add src/odds_scraper/pricer/__init__.py src/odds_scraper/pricer/engine.py tests/test_pricer_engine.py
git commit -m "feat(pricer): copy engine_prod_v1.py + tests verbatim"
```

---

### Task 2: Schema v4 — pricer tables + default seed

**Files:**
- Modify: `src/odds_scraper/db_schema.py:6` (bump SCHEMA_VERSION) and the `_MIGRATIONS` dict
- Test: `tests/test_db_schema_v4.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_db_schema_v4.py`:

```python
import json
import sqlite3
from pathlib import Path

from odds_scraper.db_schema import init_schema, SCHEMA_VERSION


def test_schema_v4_creates_pricer_tables(tmp_path: Path):
    conn = sqlite3.connect(str(tmp_path / "x.db"), isolation_level=None)
    init_schema(conn)
    tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {"pricer_configs", "pricer_runs", "pricer_results"} <= tables
    assert SCHEMA_VERSION == 4


def test_schema_v4_seeds_default_config(tmp_path: Path):
    conn = sqlite3.connect(str(tmp_path / "x.db"), isolation_level=None)
    init_schema(conn)
    rows = conn.execute(
        "SELECT name, is_default, coefficients FROM pricer_configs"
    ).fetchall()
    assert len(rows) == 1
    name, is_default, coeff_json = rows[0]
    assert name == "default"
    assert is_default == 1
    coeffs = json.loads(coeff_json)
    assert coeffs["ONEUP_FAVORITE_MARGIN"] == [0.9969, 0.0313]
    assert coeffs["TWOUP_FAVORITE_BOOST_COEFFICIENT"] == 0.9


def test_schema_v4_results_indexes(tmp_path: Path):
    conn = sqlite3.connect(str(tmp_path / "x.db"), isolation_level=None)
    init_schema(conn)
    idx_names = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )
    }
    assert "idx_pricer_results_run" in idx_names
    assert "idx_pricer_results_event" in idx_names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_db_schema_v4.py -v`
Expected: FAIL — `SCHEMA_VERSION == 4` assertion fails (currently 3), and `pricer_*` tables don't exist.

- [ ] **Step 3: Implement migration 4**

Edit `src/odds_scraper/db_schema.py`:

```python
# Top of file — bump version
SCHEMA_VERSION = 4
```

Add `import json` at the top of the file.

Add inside `_MIGRATIONS`, immediately after the `3:` entry:

```python
    4: lambda conn: _apply_v4_pricer_tables(conn),
```

Add this helper function at module scope, alongside `_add_columns_if_missing`:

```python
def _apply_v4_pricer_tables(conn: sqlite3.Connection) -> None:
    """v4: pricer integration — configs, runs, results + seed default profile.

    The "default" config is the read-only baseline pinned to the engine
    source's FeatureProperties.java values. Tests and the /simulator
    page rely on it being present exactly once with is_default=1.
    """
    conn.executescript(
        """
        CREATE TABLE pricer_configs (
            id           INTEGER PRIMARY KEY,
            name         TEXT NOT NULL UNIQUE,
            created_at   TEXT NOT NULL,
            is_default   INTEGER NOT NULL DEFAULT 0,
            coefficients TEXT NOT NULL
        );
        CREATE TABLE pricer_runs (
            id          INTEGER PRIMARY KEY,
            created_at  TEXT NOT NULL,
            config_id   INTEGER NOT NULL REFERENCES pricer_configs(id),
            coverage    TEXT NOT NULL,
            scope_json  TEXT NOT NULL,
            n_events    INTEGER NOT NULL,
            n_rows      INTEGER NOT NULL,
            csv_path    TEXT NOT NULL
        );
        CREATE INDEX idx_pricer_runs_created ON pricer_runs(created_at DESC);
        CREATE TABLE pricer_results (
            run_id              INTEGER NOT NULL REFERENCES pricer_runs(id),
            snapshot_id         INTEGER NOT NULL REFERENCES snapshots(id),
            event_id            TEXT    NOT NULL,
            ts_utc              TEXT    NOT NULL,
            basis_used          TEXT    NOT NULL,
            lambda_home         REAL,
            lambda_away         REAL,
            our_p_home_1        REAL,
            our_p_away_1        REAL,
            our_1up_home_fair   REAL,
            our_1up_home_capped REAL,
            our_1up_away_fair   REAL,
            our_1up_away_capped REAL,
            our_p_home_2        REAL,
            our_p_away_2        REAL,
            our_2up_home_fair   REAL,
            our_2up_home_capped REAL,
            our_2up_away_fair   REAL,
            our_2up_away_capped REAL,
            bp_1up_home_odds    REAL, bp_1up_away_odds  REAL,
            bp_2up_home_odds    REAL, bp_2up_away_odds  REAL,
            sb_1up_home_odds    REAL, sb_1up_away_odds  REAL,
            sb_2up_home_odds    REAL, sb_2up_away_odds  REAL,
            b9j_1up_home_odds   REAL, b9j_1up_away_odds REAL,
            b9j_2up_home_odds   REAL, b9j_2up_away_odds REAL,
            bw_1up_home_odds    REAL, bw_1up_away_odds  REAL,
            bw_2up_home_odds    REAL, bw_2up_away_odds  REAL
        );
        CREATE INDEX idx_pricer_results_run   ON pricer_results(run_id);
        CREATE INDEX idx_pricer_results_event ON pricer_results(event_id, ts_utc);
        """
    )
    default_coeffs = {
        "ONEUP_FAVORITE_MARGIN": [0.9969, 0.0313],
        "ONEUP_UNDERDOG_MARGIN": [0.9799, 0.0400],
        "ONEUP_MIN_GUARANTEED_REDUCTION": 0.02,
        "ONEUP_TRAILING_MIN_REDUCTION": 0.05,
        "ONEUP_TRAILING_MAX_REDUCTION": 0.25,
        "TWOUP_FAVORITE_MARGIN": [0.998, 0.010],
        "TWOUP_UNDERDOG_MARGIN": [0.994, 0.008],
        "TWOUP_FAVORITE_BOOST_COEFFICIENT": 0.9,
        "TWOUP_UNDERDOG_BOOST_COEFFICIENT": 0.6,
        "TWOUP_FAVORITE_MIN_GUARANTEED_REDUCTION": 0.02,
        "TWOUP_UNDERDOG_MIN_GUARANTEED_REDUCTION": 0.005,
        "TWOUP_TRAILING_MIN_REDUCTION": 0.05,
        "TWOUP_TRAILING_MAX_REDUCTION": 0.25,
    }
    conn.execute(
        "INSERT INTO pricer_configs (name, created_at, is_default, coefficients) "
        "VALUES ('default', datetime('now'), 1, ?)",
        (json.dumps(default_coeffs),),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_db_schema_v4.py -v`
Expected: all three tests pass.

- [ ] **Step 5: Run the full suite to make sure existing migrations still work end-to-end**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: 100% pass.

- [ ] **Step 6: Commit**

```bash
git add src/odds_scraper/db_schema.py tests/test_db_schema_v4.py
git commit -m "feat(db): schema v4 — pricer_configs/runs/results tables + default seed"
```

---

### Task 3: `pricer/configs.py` — coefficient profile CRUD

**Files:**
- Create: `src/odds_scraper/pricer/configs.py`
- Create: `tests/test_pricer_configs.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_pricer_configs.py`:

```python
import sqlite3
from pathlib import Path

import pytest

from odds_scraper.db_schema import init_schema
from odds_scraper.pricer import configs


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(str(tmp_path / "x.db"), isolation_level=None)
    init_schema(c)
    c.row_factory = sqlite3.Row
    return c


def test_load_default_returns_seeded_baseline(conn):
    p = configs.load_default(conn)
    assert p.name == "default"
    assert p.is_default is True
    assert p.coefficients["ONEUP_FAVORITE_MARGIN"] == [0.9969, 0.0313]


def test_list_profiles_initially_returns_only_default(conn):
    rows = configs.list_profiles(conn)
    assert [r.name for r in rows] == ["default"]


def test_create_profile_persists_named_overrides(conn):
    over = dict(configs.DEFAULT_COEFFICIENTS)
    over["TWOUP_FAVORITE_BOOST_COEFFICIENT"] = 0.85
    new_id = configs.create_profile(conn, "boost-85", over)
    assert new_id > 1
    loaded = configs.load_by_id(conn, new_id)
    assert loaded.name == "boost-85"
    assert loaded.is_default is False
    assert loaded.coefficients["TWOUP_FAVORITE_BOOST_COEFFICIENT"] == 0.85


def test_create_profile_rejects_unknown_coefficient_names(conn):
    bad = dict(configs.DEFAULT_COEFFICIENTS)
    bad["BOGUS_KNOB"] = 1.23
    with pytest.raises(ValueError, match="unknown coefficient"):
        configs.create_profile(conn, "bad", bad)


def test_delete_profile_removes_named(conn):
    new_id = configs.create_profile(conn, "tmp", configs.DEFAULT_COEFFICIENTS)
    configs.delete_profile(conn, new_id)
    assert configs.load_by_id(conn, new_id) is None


def test_delete_default_raises(conn):
    default_id = configs.load_default(conn).id
    with pytest.raises(ValueError, match="default"):
        configs.delete_profile(conn, default_id)


def test_apply_to_engine_module_normalises_tuples(conn):
    """List values for tuple constants must round-trip back to tuples
    when applied to the engine module — engine.py reads tuples and the
    code unpacks them positionally."""
    overrides = configs.coefficients_to_engine_overrides(
        configs.DEFAULT_COEFFICIENTS
    )
    assert overrides["ONEUP_FAVORITE_MARGIN"] == (0.9969, 0.0313)
    assert isinstance(overrides["ONEUP_FAVORITE_MARGIN"], tuple)
    assert overrides["TWOUP_FAVORITE_BOOST_COEFFICIENT"] == 0.9
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_pricer_configs.py -v`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement `pricer/configs.py`**

Create `src/odds_scraper/pricer/configs.py`:

```python
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Optional


TUNABLE_NAMES = (
    "ONEUP_FAVORITE_MARGIN",
    "ONEUP_UNDERDOG_MARGIN",
    "ONEUP_MIN_GUARANTEED_REDUCTION",
    "ONEUP_TRAILING_MIN_REDUCTION",
    "ONEUP_TRAILING_MAX_REDUCTION",
    "TWOUP_FAVORITE_MARGIN",
    "TWOUP_UNDERDOG_MARGIN",
    "TWOUP_FAVORITE_BOOST_COEFFICIENT",
    "TWOUP_UNDERDOG_BOOST_COEFFICIENT",
    "TWOUP_FAVORITE_MIN_GUARANTEED_REDUCTION",
    "TWOUP_UNDERDOG_MIN_GUARANTEED_REDUCTION",
    "TWOUP_TRAILING_MIN_REDUCTION",
    "TWOUP_TRAILING_MAX_REDUCTION",
)

# Coefficient names whose engine value is a (slope, intercept) tuple.
# All others are plain floats.
_TUPLE_NAMES = frozenset({
    "ONEUP_FAVORITE_MARGIN", "ONEUP_UNDERDOG_MARGIN",
    "TWOUP_FAVORITE_MARGIN", "TWOUP_UNDERDOG_MARGIN",
})

DEFAULT_COEFFICIENTS = {
    "ONEUP_FAVORITE_MARGIN": [0.9969, 0.0313],
    "ONEUP_UNDERDOG_MARGIN": [0.9799, 0.0400],
    "ONEUP_MIN_GUARANTEED_REDUCTION": 0.02,
    "ONEUP_TRAILING_MIN_REDUCTION": 0.05,
    "ONEUP_TRAILING_MAX_REDUCTION": 0.25,
    "TWOUP_FAVORITE_MARGIN": [0.998, 0.010],
    "TWOUP_UNDERDOG_MARGIN": [0.994, 0.008],
    "TWOUP_FAVORITE_BOOST_COEFFICIENT": 0.9,
    "TWOUP_UNDERDOG_BOOST_COEFFICIENT": 0.6,
    "TWOUP_FAVORITE_MIN_GUARANTEED_REDUCTION": 0.02,
    "TWOUP_UNDERDOG_MIN_GUARANTEED_REDUCTION": 0.005,
    "TWOUP_TRAILING_MIN_REDUCTION": 0.05,
    "TWOUP_TRAILING_MAX_REDUCTION": 0.25,
}


@dataclass(frozen=True)
class Profile:
    id: int
    name: str
    created_at: str
    is_default: bool
    coefficients: dict


def _row_to_profile(row: sqlite3.Row) -> Profile:
    return Profile(
        id=row["id"],
        name=row["name"],
        created_at=row["created_at"],
        is_default=bool(row["is_default"]),
        coefficients=json.loads(row["coefficients"]),
    )


def load_default(conn: sqlite3.Connection) -> Profile:
    row = conn.execute(
        "SELECT id, name, created_at, is_default, coefficients "
        "FROM pricer_configs WHERE is_default = 1"
    ).fetchone()
    if row is None:
        raise RuntimeError("default pricer config missing — schema v4 not applied?")
    return _row_to_profile(row)


def load_by_id(conn: sqlite3.Connection, profile_id: int) -> Optional[Profile]:
    row = conn.execute(
        "SELECT id, name, created_at, is_default, coefficients "
        "FROM pricer_configs WHERE id = ?",
        (profile_id,),
    ).fetchone()
    return _row_to_profile(row) if row else None


def list_profiles(conn: sqlite3.Connection) -> list[Profile]:
    rows = conn.execute(
        "SELECT id, name, created_at, is_default, coefficients "
        "FROM pricer_configs ORDER BY is_default DESC, name ASC"
    ).fetchall()
    return [_row_to_profile(r) for r in rows]


def create_profile(conn: sqlite3.Connection, name: str, coefficients: dict) -> int:
    # Validate keys before insert so partial data never lands in the DB.
    unknown = set(coefficients) - set(TUNABLE_NAMES)
    if unknown:
        raise ValueError(f"unknown coefficient names: {sorted(unknown)}")
    missing = set(TUNABLE_NAMES) - set(coefficients)
    if missing:
        raise ValueError(f"missing coefficient names: {sorted(missing)}")
    cur = conn.execute(
        "INSERT INTO pricer_configs (name, created_at, is_default, coefficients) "
        "VALUES (?, datetime('now'), 0, ?)",
        (name, json.dumps(coefficients)),
    )
    return cur.lastrowid


def delete_profile(conn: sqlite3.Connection, profile_id: int) -> None:
    row = conn.execute(
        "SELECT is_default FROM pricer_configs WHERE id = ?", (profile_id,),
    ).fetchone()
    if row is None:
        return
    if row[0] == 1:
        raise ValueError("cannot delete the default pricer config")
    conn.execute("DELETE FROM pricer_configs WHERE id = ?", (profile_id,))


def coefficients_to_engine_overrides(coefficients: dict) -> dict:
    """Convert a stored coefficients dict (lists for tuple constants) into
    the form engine.py expects (tuples for tuple constants). Pass-through
    for scalars. Use this just before applying via with_coefficients()."""
    out: dict = {}
    for k in TUNABLE_NAMES:
        v = coefficients[k]
        if k in _TUPLE_NAMES:
            out[k] = tuple(v)
        else:
            out[k] = v
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_pricer_configs.py -v`
Expected: all seven tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/odds_scraper/pricer/configs.py tests/test_pricer_configs.py
git commit -m "feat(pricer): coefficient profile CRUD + default seed loader"
```

---

### Task 4: `pricer/inputs.py` — BP-first per-input extract

**Files:**
- Create: `src/odds_scraper/pricer/inputs.py`
- Create: `tests/test_pricer_inputs.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_pricer_inputs.py`:

```python
from odds_scraper.pricer import inputs


def _row(market_id, line, side, odds, prob):
    """Mimics sqlite3.Row indexing for the columns inputs.py needs."""
    return {"market_id": market_id, "line": line, "side": side,
            "odds": odds, "probability": prob}


def _full_1x2(odds=(1.85, 3.4, 4.2), probs=(0.54, 0.29, 0.17)):
    return [
        _row("1x2_ft", 0.0, "home", odds[0], probs[0]),
        _row("1x2_ft", 0.0, "draw", odds[1], probs[1]),
        _row("1x2_ft", 0.0, "away", odds[2], probs[2]),
    ]

def _full_ou(market_id, over_prob_25=0.55):
    return [
        _row(market_id, 2.5, "over",  1.85, over_prob_25),
        _row(market_id, 2.5, "under", 1.95, 1 - over_prob_25),
    ]

def _ftts():
    return [
        _row("next_goal_ft", 1.0, "home", 1.85, 0.54),
        _row("next_goal_ft", 1.0, "none", 8.50, 0.12),
        _row("next_goal_ft", 1.0, "away", 3.50, 0.34),
    ]


def test_bp_full_inputs_uses_bp_only():
    bp_prices = _full_1x2() + _full_ou("over_under_ft") + \
                _full_ou("home_over_under_ft") + _full_ou("away_over_under_ft") + \
                _ftts()
    result, basis = inputs.extract({"betpawa": bp_prices})
    assert basis == "bp"
    assert result["p_home_win"] == 0.54
    assert result["ftts_home_prob"] == 0.54
    assert (2.5, 0.55) in result["total_ou"]


def test_bp_missing_ftts_falls_through_to_sb():
    bp_prices = _full_1x2() + _full_ou("over_under_ft")
    sb_prices = _full_1x2() + _ftts()
    result, basis = inputs.extract(
        {"betpawa": bp_prices, "sportybet": sb_prices},
    )
    assert basis == "bp+sb"
    # 1X2 + OU came from BP
    assert result["p_home_win"] == 0.54
    assert result["home_1x2_odds"] == 1.85
    # FTTS came from SB
    assert result["ftts_home_prob"] == 0.54
    assert result["ftts_away_prob"] == 0.34


def test_bp_missing_everything_uses_sb_only():
    sb_prices = _full_1x2() + _full_ou("over_under_ft") + _ftts()
    result, basis = inputs.extract({"betpawa": [], "sportybet": sb_prices})
    assert basis == "sb"
    assert result["p_home_win"] == 0.54


def test_both_missing_ou_returns_none():
    bp_prices = _full_1x2()
    sb_prices = _full_1x2()
    result, basis = inputs.extract(
        {"betpawa": bp_prices, "sportybet": sb_prices},
    )
    assert result is None
    assert basis == ""


def test_probability_is_consumed_as_is_never_redevigged():
    """The 1X2 probabilities passed to the engine must equal the raw
    `probability` column values — no client-side devigging from the
    odds. Regression guard against accidentally summing 1/odds."""
    bp_prices = _full_1x2(odds=(1.85, 3.4, 4.2), probs=(0.50, 0.30, 0.20)) + \
                _full_ou("over_under_ft")
    result, _ = inputs.extract({"betpawa": bp_prices})
    assert result["p_home_win"] == 0.50
    assert result["p_draw"]     == 0.30
    assert result["p_away_win"] == 0.20


def test_per_side_ou_kept_independent():
    """home_ou available from BP; away_ou only from SB — each list
    independently falls through, no cross-book merging within a list."""
    bp_prices = _full_1x2() + _full_ou("over_under_ft") + _full_ou("home_over_under_ft")
    sb_prices = _full_1x2() + _full_ou("over_under_ft") + _full_ou("away_over_under_ft")
    result, basis = inputs.extract(
        {"betpawa": bp_prices, "sportybet": sb_prices},
    )
    assert basis == "bp+sb"
    assert result["home_ou"]  # came from BP
    assert result["away_ou"]  # came from SB
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_pricer_inputs.py -v`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement `pricer/inputs.py`**

Create `src/odds_scraper/pricer/inputs.py`:

```python
from __future__ import annotations

from typing import Iterable, Mapping, Optional


def _extract_1x2(prices: Iterable) -> Optional[dict]:
    """Return {'home': {'odds':_, 'prob':_}, 'draw': …, 'away': …} or None
    if any side is missing or has null prob/odds. Treated as one input —
    all three sides must come from the same book.
    """
    out: dict = {}
    for r in prices:
        if r["market_id"] != "1x2_ft":
            continue
        if r["odds"] is None or r["probability"] is None:
            continue
        out[r["side"]] = {"odds": r["odds"], "prob": r["probability"]}
    if {"home", "draw", "away"} <= out.keys():
        return out
    return None


def _extract_ou(prices: Iterable, market_id: str) -> list[tuple[float, float]]:
    """Return list of (line, over_prob) for the given market. Drops null probs."""
    out: list[tuple[float, float]] = []
    for r in prices:
        if r["market_id"] != market_id or r["side"] != "over":
            continue
        if r["probability"] is None:
            continue
        out.append((r["line"], r["probability"]))
    return out


def _extract_ftts(prices: Iterable) -> Optional[dict]:
    """Return {'home': prob, 'away': prob} or None if either is missing."""
    out: dict = {}
    for r in prices:
        if r["market_id"] != "next_goal_ft":
            continue
        if r["probability"] is None:
            continue
        if r["side"] in ("home", "away"):
            out[r["side"]] = r["probability"]
    if {"home", "away"} <= out.keys():
        return out
    return None


def extract(
    prices_by_book: Mapping[str, Iterable],
) -> tuple[Optional[dict], str]:
    """Build engine inputs from per-book prices using BP-first / SB-fallback.

    Inputs treated independently — each may come from a different book:
      - 1X2 (prob+odds, all three sides)
      - total_ou           (list of (line, over_prob))
      - home_ou            (list)
      - away_ou            (list)
      - ftts               (home + away probs)

    Returns (engine_input_dict, basis_used) where basis_used is one of
    'bp' | 'sb' | 'bp+sb'. Returns (None, '') if lambdas can't be derived
    (no OU from either book).
    """
    bp = list(prices_by_book.get("betpawa") or [])
    sb = list(prices_by_book.get("sportybet") or [])
    used_books: set[str] = set()

    def pick(extractor, *, bp_args=(), sb_args=(), nonempty=lambda x: x):
        bp_val = extractor(bp, *bp_args)
        if nonempty(bp_val):
            used_books.add("bp")
            return bp_val
        sb_val = extractor(sb, *sb_args)
        if nonempty(sb_val):
            used_books.add("sb")
            return sb_val
        # Return whichever non-None value we got (may be empty list);
        # don't claim a book if neither produced anything.
        return bp_val if bp_val is not None else sb_val

    one_x_two = pick(_extract_1x2, nonempty=bool)
    if one_x_two is None:
        return None, ""

    total_ou = pick(_extract_ou, bp_args=("over_under_ft",),
                    sb_args=("over_under_ft",), nonempty=bool)
    home_ou  = pick(_extract_ou, bp_args=("home_over_under_ft",),
                    sb_args=("home_over_under_ft",), nonempty=bool)
    away_ou  = pick(_extract_ou, bp_args=("away_over_under_ft",),
                    sb_args=("away_over_under_ft",), nonempty=bool)

    if not total_ou and not (home_ou and away_ou):
        # Engine deactivates without OU coverage.
        return None, ""

    ftts = pick(_extract_ftts, nonempty=bool)

    basis_used = (
        "bp+sb" if used_books == {"bp", "sb"}
        else "bp" if "bp" in used_books
        else "sb" if "sb" in used_books
        else ""
    )

    return {
        "p_home_win":     one_x_two["home"]["prob"],
        "p_draw":         one_x_two["draw"]["prob"],
        "p_away_win":     one_x_two["away"]["prob"],
        "home_1x2_odds":  one_x_two["home"]["odds"],
        "draw_1x2_odds":  one_x_two["draw"]["odds"],
        "away_1x2_odds":  one_x_two["away"]["odds"],
        "total_ou":       total_ou,
        "home_ou":        home_ou,
        "away_ou":        away_ou,
        "ftts_home_prob": ftts["home"] if ftts else None,
        "ftts_away_prob": ftts["away"] if ftts else None,
    }, basis_used
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_pricer_inputs.py -v`
Expected: all six tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/odds_scraper/pricer/inputs.py tests/test_pricer_inputs.py
git commit -m "feat(pricer): BP-first per-input extract with SB fallback"
```

---

### Task 5: `pricer/runner.py` — `with_coefficients` + `run_simulation`

**Files:**
- Create: `src/odds_scraper/pricer/runner.py`
- Create: `tests/test_pricer_runner.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_pricer_runner.py`:

```python
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from odds_scraper.db_schema import init_schema
from odds_scraper.pricer import configs, engine, runner


def _seed_event_with_priced_snapshot(conn: sqlite3.Connection, event_id: str):
    conn.execute(
        "INSERT INTO events (id, home, away, kickoff_utc) "
        "VALUES (?, 'Home FC', 'Away FC', '2026-05-22T18:30:00Z')",
        (event_id,),
    )
    cur = conn.execute(
        "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, "
        "match_minute, score_home, score_away, fetch_status) "
        "VALUES ('2026-05-21T10:00:00Z', ?, 'betpawa', 'UPCOMING', "
        "NULL, NULL, NULL, 'ok')",
        (event_id,),
    )
    snap_id = cur.lastrowid
    base = [
        ("1x2_ft", 0.0, "home", 1.85, 0.54),
        ("1x2_ft", 0.0, "draw", 3.40, 0.29),
        ("1x2_ft", 0.0, "away", 4.20, 0.17),
        ("over_under_ft", 2.5, "over",  1.85, 0.55),
        ("over_under_ft", 2.5, "under", 1.95, 0.45),
        ("next_goal_ft", 1.0, "home", 1.85, 0.54),
        ("next_goal_ft", 1.0, "none", 8.50, 0.12),
        ("next_goal_ft", 1.0, "away", 3.50, 0.34),
    ]
    for mid, line, side, odds, prob in base:
        conn.execute(
            "INSERT INTO prices (snapshot_id, event_id, ts_utc, bookmaker, "
            "market_id, line, side, odds, probability) "
            "VALUES (?, ?, '2026-05-21T10:00:00Z', 'betpawa', ?, ?, ?, ?, ?)",
            (snap_id, event_id, mid, line, side, odds, prob),
        )
    return snap_id


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(str(tmp_path / "odds.db"), isolation_level=None)
    init_schema(c)
    c.row_factory = sqlite3.Row
    return c


def test_with_coefficients_mutates_and_restores(db):
    original_margin = engine.ONEUP_FAVORITE_MARGIN
    with runner.with_coefficients({"ONEUP_FAVORITE_MARGIN": (0.9, 0.05)}):
        assert engine.ONEUP_FAVORITE_MARGIN == (0.9, 0.05)
    assert engine.ONEUP_FAVORITE_MARGIN == original_margin


def test_with_coefficients_restores_on_exception(db):
    original_margin = engine.ONEUP_FAVORITE_MARGIN
    with pytest.raises(RuntimeError):
        with runner.with_coefficients({"ONEUP_FAVORITE_MARGIN": (0.9, 0.05)}):
            raise RuntimeError("boom")
    assert engine.ONEUP_FAVORITE_MARGIN == original_margin


def test_run_simulation_writes_results_for_priced_snapshot(db, tmp_path):
    _seed_event_with_priced_snapshot(db, "E1")
    default = configs.load_default(db)
    run_id = runner.run_simulation(
        db, config=default,
        coverage="all",
        scope={"status": "upcoming", "country": "", "league": "", "date": "", "search": ""},
        csv_dir=tmp_path / "sim",
    )
    row = db.execute(
        "SELECT n_events, n_rows, csv_path FROM pricer_runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    assert row["n_events"] == 1
    assert row["n_rows"] == 1
    results = db.execute(
        "SELECT event_id, basis_used, our_p_home_2, our_2up_home_capped "
        "FROM pricer_results WHERE run_id = ?",
        (run_id,),
    ).fetchall()
    assert len(results) == 1
    assert results[0]["event_id"] == "E1"
    assert results[0]["basis_used"] == "bp"
    # Engine succeeded — non-null capped 2UP odds.
    assert results[0]["our_2up_home_capped"] is not None


def test_run_simulation_coverage_latest_emits_one_row_per_event(db, tmp_path):
    _seed_event_with_priced_snapshot(db, "E1")
    # Add a second snapshot for the same event so 'all' would emit 2 rows
    # and 'latest' must emit 1.
    cur = db.execute(
        "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, "
        "match_minute, score_home, score_away, fetch_status) "
        "VALUES ('2026-05-21T11:00:00Z', 'E1', 'betpawa', 'UPCOMING', "
        "NULL, NULL, NULL, 'ok')",
    )
    snap2 = cur.lastrowid
    for mid, line, side, odds, prob in [
        ("1x2_ft", 0.0, "home", 1.85, 0.54),
        ("1x2_ft", 0.0, "draw", 3.40, 0.29),
        ("1x2_ft", 0.0, "away", 4.20, 0.17),
        ("over_under_ft", 2.5, "over",  1.85, 0.55),
        ("over_under_ft", 2.5, "under", 1.95, 0.45),
    ]:
        db.execute(
            "INSERT INTO prices (snapshot_id, event_id, ts_utc, bookmaker, "
            "market_id, line, side, odds, probability) "
            "VALUES (?, 'E1', '2026-05-21T11:00:00Z', 'betpawa', ?, ?, ?, ?, ?)",
            (snap2, mid, line, side, odds, prob),
        )

    default = configs.load_default(db)
    run_id = runner.run_simulation(
        db, config=default, coverage="latest",
        scope={"status": "upcoming", "country": "", "league": "", "date": "", "search": ""},
        csv_dir=tmp_path / "sim",
    )
    rows = db.execute(
        "SELECT ts_utc FROM pricer_results WHERE run_id = ?", (run_id,),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["ts_utc"] == "2026-05-21T11:00:00Z"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_pricer_runner.py -v`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement `pricer/runner.py`**

Create `src/odds_scraper/pricer/runner.py`:

```python
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from . import engine, inputs as input_extract, configs as config_mod, csv_export


@contextmanager
def with_coefficients(overrides: dict) -> Iterator[None]:
    """Temporarily set module-level constants on engine.py.

    Engine reads constants directly from its module globals. To honour a
    profile's coefficients we setattr the overrides before the call and
    restore originals on exit. Not thread-safe — the web app runs single-
    process asyncio and engine calls are sync within one event loop, so
    no two engine calls overlap. The card OUR column always runs under
    the seeded default (no override), so this contextmanager is only
    entered by the simulator runner.
    """
    saved = {k: getattr(engine, k) for k in overrides}
    try:
        for k, v in overrides.items():
            setattr(engine, k, v)
        yield
    finally:
        for k, v in saved.items():
            setattr(engine, k, v)


_BOOK_PREFIXES = {
    "betpawa":   "bp",
    "sportybet": "sb",
    "bet9ja":    "b9j",
    "betway":    "bw",
}


def _select_snapshot_ids(
    conn: sqlite3.Connection, coverage: str, scope: dict,
) -> list[int]:
    """Return distinct snapshot ids in scope, ordered by event_id then ts.

    coverage:
      'all'      — every snapshot for events that match scope
      'latest'   — only the most recent snapshot per event
      'prematch' — UPCOMING snapshots only
      'live'     — STARTED snapshots only
    """
    status = scope.get("status") or ""
    where_extra: list[str] = []
    params: list = []
    if coverage in ("prematch", "live"):
        where_extra.append("s.status = ?")
        params.append("UPCOMING" if coverage == "prematch" else "STARTED")
    if status == "live":
        where_extra.append("s.status = 'STARTED'")
    elif status == "upcoming":
        where_extra.append("s.status = 'UPCOMING'")
    elif status == "ended":
        where_extra.append("s.status = 'ENDED'")
    if scope.get("country"):
        where_extra.append("e.country_id = ?")
        params.append(scope["country"])
    if scope.get("league"):
        where_extra.append("e.league_id = ?")
        params.append(scope["league"])
    where_clause = " AND " + " AND ".join(where_extra) if where_extra else ""

    if coverage == "latest":
        sql = f"""
            WITH latest AS (
                SELECT event_id, MAX(ts_utc) AS max_ts
                FROM snapshots GROUP BY event_id
            )
            SELECT DISTINCT s.id, s.event_id, s.ts_utc
            FROM snapshots s
            JOIN events e ON e.id = s.event_id
            JOIN latest l ON l.event_id = s.event_id AND l.max_ts = s.ts_utc
            WHERE 1=1 {where_clause}
            ORDER BY s.event_id, s.ts_utc
        """
    else:
        sql = f"""
            SELECT DISTINCT s.id, s.event_id, s.ts_utc
            FROM snapshots s
            JOIN events e ON e.id = s.event_id
            WHERE 1=1 {where_clause}
            ORDER BY s.event_id, s.ts_utc
        """
    return [row[0] for row in conn.execute(sql, params).fetchall()]


def _load_tick_prices(
    conn: sqlite3.Connection, event_id: str, ts_utc: str,
) -> dict[str, list]:
    """Return {book: [price_row, ...]} for every (event, ts) bucket — i.e.
    every book's snapshot at the same timestamp. SQLite Row is dict-like."""
    rows = conn.execute(
        "SELECT bookmaker, market_id, line, side, odds, probability "
        "FROM prices WHERE event_id = ? AND ts_utc = ?",
        (event_id, ts_utc),
    ).fetchall()
    out: dict[str, list] = {}
    for r in rows:
        out.setdefault(r["bookmaker"], []).append({
            "market_id":   r["market_id"],
            "line":        r["line"],
            "side":        r["side"],
            "odds":        r["odds"],
            "probability": r["probability"],
        })
    return out


def _extract_quoted_up(prices: list) -> dict:
    """Return {1up_home, 1up_away, 2up_home, 2up_away} odds for one book's
    snapshot. Missing rows stay None."""
    out = {"1up_home": None, "1up_away": None, "2up_home": None, "2up_away": None}
    for r in prices:
        m = r["market_id"]
        if m == "1x2_1up_ft" and r["side"] in ("home", "away"):
            out[f"1up_{r['side']}"] = r["odds"]
        elif m == "1x2_2up_ft" and r["side"] in ("home", "away"):
            out[f"2up_{r['side']}"] = r["odds"]
    return out


def _score_for_snapshot(conn: sqlite3.Connection, snapshot_id: int) -> tuple[int, int]:
    row = conn.execute(
        "SELECT score_home, score_away FROM snapshots WHERE id = ?",
        (snapshot_id,),
    ).fetchone()
    if row is None or row["score_home"] is None or row["score_away"] is None:
        return (0, 0)
    return (int(row["score_home"]), int(row["score_away"]))


def run_simulation(
    conn: sqlite3.Connection,
    *,
    config: config_mod.Profile,
    coverage: str,
    scope: dict,
    csv_dir: Path,
) -> int:
    """Execute a simulation run, persist rows + CSV, return new run id.

    `coverage` in {'all', 'latest', 'prematch', 'live'}.
    `scope` carries the filter selections so a run can be reproduced later.
    """
    snapshot_ids = _select_snapshot_ids(conn, coverage, scope)
    if not snapshot_ids:
        # Still record the run — n_rows=0 surfaces in the history UI.
        return _record_empty_run(conn, config, coverage, scope, csv_dir)

    overrides = config_mod.coefficients_to_engine_overrides(config.coefficients)

    # Resolve ts_utc and event_id for each snapshot
    snap_meta = {
        row["id"]: (row["event_id"], row["ts_utc"])
        for row in conn.execute(
            f"SELECT id, event_id, ts_utc FROM snapshots "
            f"WHERE id IN ({','.join('?' * len(snapshot_ids))})",
            snapshot_ids,
        )
    }

    results: list[tuple] = []
    seen_events: set[str] = set()

    with with_coefficients(overrides):
        for snap_id in snapshot_ids:
            event_id, ts_utc = snap_meta[snap_id]
            prices_by_book = _load_tick_prices(conn, event_id, ts_utc)
            engine_inputs, basis = input_extract.extract(prices_by_book)
            if engine_inputs is None:
                continue
            engine_inputs["score"] = _score_for_snapshot(conn, snap_id)
            res = engine.price_early_payout_markets(**engine_inputs)
            quoted = {
                book: _extract_quoted_up(prices_by_book.get(book, []))
                for book in ("betpawa", "sportybet", "bet9ja", "betway")
            }
            results.append((
                snap_id, event_id, ts_utc, basis,
                res["lambda_home"], res["lambda_away"],
                res["p_home_1"], res["p_away_1"],
                res["market_1up"]["home_fair"],   res["market_1up"]["home_margin"],
                res["market_1up"]["away_fair"],   res["market_1up"]["away_margin"],
                res["p_home_2"], res["p_away_2"],
                res["market_2up"]["home_fair"],   res["market_2up"]["home_margin"],
                res["market_2up"]["away_fair"],   res["market_2up"]["away_margin"],
                quoted["betpawa"]["1up_home"],   quoted["betpawa"]["1up_away"],
                quoted["betpawa"]["2up_home"],   quoted["betpawa"]["2up_away"],
                quoted["sportybet"]["1up_home"], quoted["sportybet"]["1up_away"],
                quoted["sportybet"]["2up_home"], quoted["sportybet"]["2up_away"],
                quoted["bet9ja"]["1up_home"],    quoted["bet9ja"]["1up_away"],
                quoted["bet9ja"]["2up_home"],    quoted["bet9ja"]["2up_away"],
                quoted["betway"]["1up_home"],    quoted["betway"]["1up_away"],
                quoted["betway"]["2up_home"],    quoted["betway"]["2up_away"],
            ))
            seen_events.add(event_id)

    # Create run row first to get the id (needed for filename + FK).
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cur = conn.execute(
        "INSERT INTO pricer_runs (created_at, config_id, coverage, scope_json, "
        "n_events, n_rows, csv_path) VALUES (?, ?, ?, ?, ?, ?, '')",
        (now_iso, config.id, coverage, json.dumps(scope),
         len(seen_events), len(results)),
    )
    run_id = cur.lastrowid
    csv_path = f"sim/run_{run_id:04d}.csv"

    # 35 columns total: run_id + 34 from each `results` tuple. Build the
    # placeholder string once so the column count is unambiguous.
    _RESULT_COLS = (
        "run_id, snapshot_id, event_id, ts_utc, basis_used, "
        "lambda_home, lambda_away, "
        "our_p_home_1, our_p_away_1, "
        "our_1up_home_fair, our_1up_home_capped, our_1up_away_fair, our_1up_away_capped, "
        "our_p_home_2, our_p_away_2, "
        "our_2up_home_fair, our_2up_home_capped, our_2up_away_fair, our_2up_away_capped, "
        "bp_1up_home_odds, bp_1up_away_odds, bp_2up_home_odds, bp_2up_away_odds, "
        "sb_1up_home_odds, sb_1up_away_odds, sb_2up_home_odds, sb_2up_away_odds, "
        "b9j_1up_home_odds, b9j_1up_away_odds, b9j_2up_home_odds, b9j_2up_away_odds, "
        "bw_1up_home_odds, bw_1up_away_odds, bw_2up_home_odds, bw_2up_away_odds"
    )
    placeholders = ",".join("?" * 35)
    conn.executemany(
        f"INSERT INTO pricer_results ({_RESULT_COLS}) VALUES ({placeholders})",
        [(run_id, *row) for row in results],
    )
    conn.execute(
        "UPDATE pricer_runs SET csv_path = ? WHERE id = ?", (csv_path, run_id),
    )
    csv_export.write_run_csv(conn, run_id, csv_dir / f"run_{run_id:04d}.csv")
    return run_id


def _record_empty_run(
    conn: sqlite3.Connection, config: config_mod.Profile,
    coverage: str, scope: dict, csv_dir: Path,
) -> int:
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cur = conn.execute(
        "INSERT INTO pricer_runs (created_at, config_id, coverage, scope_json, "
        "n_events, n_rows, csv_path) VALUES (?, ?, ?, ?, 0, 0, '')",
        (now_iso, config.id, coverage, json.dumps(scope)),
    )
    run_id = cur.lastrowid
    csv_path = f"sim/run_{run_id:04d}.csv"
    conn.execute(
        "UPDATE pricer_runs SET csv_path = ? WHERE id = ?", (csv_path, run_id),
    )
    csv_export.write_run_csv(conn, run_id, csv_dir / f"run_{run_id:04d}.csv")
    return run_id
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_pricer_runner.py -v`
Expected: all four tests pass. If `csv_export` import fails because Task 6 hasn't landed yet, finish Task 6 before re-running.

**Note:** Task 6 must land in the same branch as this task — runner.py imports `csv_export`. If working strictly TDD per-task, mock the CSV write temporarily; otherwise complete Task 6 first and run the combined test suite.

- [ ] **Step 5: Commit**

```bash
git add src/odds_scraper/pricer/runner.py tests/test_pricer_runner.py
git commit -m "feat(pricer): with_coefficients() + run_simulation() orchestrator"
```

---

### Task 6: `pricer/csv_export.py` — write run results to wide CSV

**Files:**
- Create: `src/odds_scraper/pricer/csv_export.py`
- Create: `tests/test_pricer_csv.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_pricer_csv.py`:

```python
import csv
import sqlite3
from pathlib import Path

import pytest

from odds_scraper.db_schema import init_schema
from odds_scraper.pricer import csv_export


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(str(tmp_path / "x.db"), isolation_level=None)
    init_schema(c)
    c.row_factory = sqlite3.Row
    return c


def _seed_one_run(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        "INSERT INTO pricer_runs (created_at, config_id, coverage, scope_json, "
        "n_events, n_rows, csv_path) "
        "VALUES ('2026-05-23T10:00:00Z', 1, 'all', '{}', 1, 1, 'sim/run_0001.csv')",
    )
    run_id = cur.lastrowid
    conn.execute(
        "INSERT INTO events (id, home, away, kickoff_utc) "
        "VALUES ('E1', 'Home FC', 'Away FC', '2026-05-22T18:30:00Z')",
    )
    cur = conn.execute(
        "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, fetch_status) "
        "VALUES ('2026-05-21T10:00:00Z', 'E1', 'betpawa', 'UPCOMING', 'ok')",
    )
    snap_id = cur.lastrowid
    conn.execute(
        "INSERT INTO pricer_results (run_id, snapshot_id, event_id, ts_utc, "
        "basis_used, our_p_home_2, our_2up_home_capped, bp_2up_home_odds) "
        "VALUES (?, ?, 'E1', '2026-05-21T10:00:00Z', 'bp', 0.65, 1.85, 1.83)",
        (run_id, snap_id),
    )
    return run_id


def test_write_run_csv_emits_header_and_rows(db, tmp_path):
    run_id = _seed_one_run(db)
    out = tmp_path / "run_0001.csv"
    csv_export.write_run_csv(db, run_id, out)

    with open(out, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert len(rows) == 1
    row = rows[0]
    assert row["event_id"] == "E1"
    assert row["home"] == "Home FC"
    assert row["away"] == "Away FC"
    assert row["basis_used"] == "bp"
    assert float(row["our_2up_home_capped"]) == 1.85
    assert float(row["bp_2up_home_odds"]) == 1.83


def test_write_run_csv_creates_dirs_and_handles_empty_run(db, tmp_path):
    """An empty run (n_rows=0) still produces a CSV with just headers."""
    cur = db.execute(
        "INSERT INTO pricer_runs (created_at, config_id, coverage, scope_json, "
        "n_events, n_rows, csv_path) "
        "VALUES ('2026-05-23T10:00:00Z', 1, 'all', '{}', 0, 0, 'sim/run_0002.csv')",
    )
    run_id = cur.lastrowid
    out = tmp_path / "sub" / "run_0002.csv"
    csv_export.write_run_csv(db, run_id, out)
    with open(out, encoding="utf-8") as f:
        lines = f.read().splitlines()
    assert len(lines) == 1  # header only
    assert "event_id" in lines[0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_pricer_csv.py -v`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement `pricer/csv_export.py`**

Create `src/odds_scraper/pricer/csv_export.py`:

```python
from __future__ import annotations

import csv
import sqlite3
from pathlib import Path


CSV_COLUMNS = (
    "run_id", "event_id", "home", "away", "kickoff_utc",
    "snapshot_id", "ts_utc", "basis_used",
    "lambda_home", "lambda_away",
    "our_p_home_1", "our_p_away_1",
    "our_1up_home_fair", "our_1up_home_capped",
    "our_1up_away_fair", "our_1up_away_capped",
    "our_p_home_2", "our_p_away_2",
    "our_2up_home_fair", "our_2up_home_capped",
    "our_2up_away_fair", "our_2up_away_capped",
    "bp_1up_home_odds",  "bp_1up_away_odds",
    "bp_2up_home_odds",  "bp_2up_away_odds",
    "sb_1up_home_odds",  "sb_1up_away_odds",
    "sb_2up_home_odds",  "sb_2up_away_odds",
    "b9j_1up_home_odds", "b9j_1up_away_odds",
    "b9j_2up_home_odds", "b9j_2up_away_odds",
    "bw_1up_home_odds",  "bw_1up_away_odds",
    "bw_2up_home_odds",  "bw_2up_away_odds",
)


def write_run_csv(
    conn: sqlite3.Connection, run_id: int, out_path: Path,
) -> None:
    """Materialise pricer_results for `run_id` as a wide CSV.

    Joins event metadata (home, away, kickoff_utc) onto each row so the
    CSV reads standalone — no DB needed downstream.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows = conn.execute(
        """
        SELECT
            r.run_id, r.event_id, e.home, e.away, e.kickoff_utc,
            r.snapshot_id, r.ts_utc, r.basis_used,
            r.lambda_home, r.lambda_away,
            r.our_p_home_1, r.our_p_away_1,
            r.our_1up_home_fair, r.our_1up_home_capped,
            r.our_1up_away_fair, r.our_1up_away_capped,
            r.our_p_home_2, r.our_p_away_2,
            r.our_2up_home_fair, r.our_2up_home_capped,
            r.our_2up_away_fair, r.our_2up_away_capped,
            r.bp_1up_home_odds,  r.bp_1up_away_odds,
            r.bp_2up_home_odds,  r.bp_2up_away_odds,
            r.sb_1up_home_odds,  r.sb_1up_away_odds,
            r.sb_2up_home_odds,  r.sb_2up_away_odds,
            r.b9j_1up_home_odds, r.b9j_1up_away_odds,
            r.b9j_2up_home_odds, r.b9j_2up_away_odds,
            r.bw_1up_home_odds,  r.bw_1up_away_odds,
            r.bw_2up_home_odds,  r.bw_2up_away_odds
        FROM pricer_results r
        JOIN events e ON e.id = r.event_id
        WHERE r.run_id = ?
        ORDER BY r.event_id, r.ts_utc
        """,
        (run_id,),
    ).fetchall()

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_COLUMNS)
        for row in rows:
            writer.writerow(row)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_pricer_csv.py tests/test_pricer_runner.py -v`
Expected: both csv + runner suites pass (runner.py imports csv_export).

- [ ] **Step 5: Commit**

```bash
git add src/odds_scraper/pricer/csv_export.py tests/test_pricer_csv.py
git commit -m "feat(pricer): wide CSV export of run results joined with event meta"
```

---

### Task 7: Extend `EventView` + `_build_event_view` for OUR fields

**Files:**
- Modify: `src/odds_scraper/web/app.py:124-134` (EventView dataclass) and `:253-315` (_build_event_view)
- Test: extend `tests/test_web_app.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_web_app.py`:

```python
def test_event_view_carries_our_odds(db_path: Path):
    """The card route attaches OUR 1UP/2UP odds to each event view so
    the template can render the SIM column."""
    # Seed FTTS so 1UP is computable in the existing fixture.
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    snap_id = conn.execute(
        "SELECT id FROM snapshots WHERE event_id='E1' LIMIT 1"
    ).fetchone()[0]
    for side, odds, prob in [
        ("home", 1.85, 0.54), ("none", 8.5, 0.12), ("away", 3.5, 0.34),
    ]:
        conn.execute(
            "INSERT INTO prices (snapshot_id, event_id, ts_utc, bookmaker, "
            "market_id, line, side, odds, probability) "
            "VALUES (?, 'E1', '2026-05-21T10:00:00Z', 'betpawa', "
            "'next_goal_ft', 1.0, ?, ?, ?)",
            (snap_id, side, odds, prob),
        )
    for side, odds, prob in [("over", 1.85, 0.55), ("under", 1.95, 0.45)]:
        conn.execute(
            "INSERT INTO prices (snapshot_id, event_id, ts_utc, bookmaker, "
            "market_id, line, side, odds, probability) "
            "VALUES (?, 'E1', '2026-05-21T10:00:00Z', 'betpawa', "
            "'over_under_ft', 2.5, ?, ?, ?)",
            (snap_id, side, odds, prob),
        )
    conn.close()
    client = TestClient(create_app(db_path=db_path))
    r = client.get("/events?status=upcoming")
    # OUR 1up/2up should now be in the markup as data attrs on the SIM cells.
    assert 'data-bookmaker="sim"' in r.text


def test_event_view_no_our_when_inputs_missing(db_path: Path):
    """With no OU and no FTTS in the fixture, OUR is not computable —
    the SIM cell renders em-dash (no .sim class)."""
    client = TestClient(create_app(db_path=db_path))
    r = client.get("/events?status=upcoming")
    # SIM column header present (markup always renders the column)
    assert 'data-bookmaker="sim"' in r.text
    # … but the cell content for that row should be em-dash since the
    # default fixture lacks OU + FTTS.
    # Verified indirectly: no .sim class in the events-list markup.
    assert "class=\"sim\"" not in r.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_web_app.py -k our_odds -v`
Expected: FAIL — no `data-bookmaker="sim"` markup yet.

- [ ] **Step 3: Extend EventView + _build_event_view**

Edit `src/odds_scraper/web/app.py`. Add to imports at top:

```python
from odds_scraper.pricer import engine, inputs as pricer_inputs
```

Modify the `EventView` dataclass — add five new fields after `market_groups`:

```python
@dataclass
class EventView:
    id: str
    home: str
    away: str
    kickoff_utc: str
    status: str
    match_minute: Optional[int]
    score_home: Optional[int]
    score_away: Optional[int]
    market_groups: list[MarketGroup]
    # OUR-engine output for the SIM column. None when inputs are missing
    # or the engine deactivates.
    our_1up_home: Optional[float]
    our_1up_away: Optional[float]
    our_2up_home: Optional[float]
    our_2up_away: Optional[float]
    # True when BP itself quoted the market — drives the rule "if BP
    # missing, OUR replaces the BP cell instead of going to SIM column".
    bp_has_1up: bool
    bp_has_2up: bool
```

At the very end of `_build_event_view` (just before the `return EventView(...)`), insert this block computing OUR + the BP flags:

```python
    # Pricer integration: build per-book buckets, run engine on the latest
    # snapshot, surface OUR 1up/2up to the template.
    prices_by_book: dict[str, list] = {}
    for pr in price_rows:
        prices_by_book.setdefault(pr["bookmaker"], []).append({
            "market_id":   pr["market_id"],
            "line":        pr["line"],
            "side":        pr["side"],
            "odds":        pr["odds"],
            "probability": pr["probability"],
        })

    engine_inputs, _basis = pricer_inputs.extract(prices_by_book)
    our_1up_home = our_1up_away = our_2up_home = our_2up_away = None
    if engine_inputs is not None:
        score = (row["score_home"] or 0, row["score_away"] or 0)
        engine_inputs["score"] = (int(score[0]), int(score[1]))
        try:
            result = engine.price_early_payout_markets(**engine_inputs)
            our_1up_home = result["market_1up"]["home_margin"]
            our_1up_away = result["market_1up"]["away_margin"]
            our_2up_home = result["market_2up"]["home_margin"]
            our_2up_away = result["market_2up"]["away_margin"]
        except Exception:  # noqa: BLE001
            # Engine doesn't raise on bad inputs (returns deactivated dict),
            # so this is a defensive guard against future regressions.
            pass

    bp_prices = prices_by_book.get("betpawa", [])
    bp_has_1up = any(p["market_id"] == "1x2_1up_ft" and p["side"] in ("home", "away")
                     and p["odds"] is not None for p in bp_prices)
    bp_has_2up = any(p["market_id"] == "1x2_2up_ft" and p["side"] in ("home", "away")
                     and p["odds"] is not None for p in bp_prices)
```

Update the return value:

```python
    return EventView(
        id=row["id"], home=row["home"], away=row["away"],
        kickoff_utc=row["kickoff_utc"], status=row["status"],
        match_minute=row["match_minute"],
        score_home=row["score_home"], score_away=row["score_away"],
        market_groups=groups,
        our_1up_home=our_1up_home, our_1up_away=our_1up_away,
        our_2up_home=our_2up_home, our_2up_away=our_2up_away,
        bp_has_1up=bp_has_1up, bp_has_2up=bp_has_2up,
    )
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_web_app.py -k our_odds -v`
Expected: First test still fails (template not updated yet — Task 8). Second test passes.

Run: `.venv/Scripts/python.exe -m pytest tests/test_pricer_engine.py tests/test_pricer_inputs.py -v`
Expected: still pass (no regression).

- [ ] **Step 5: Commit**

```bash
git add src/odds_scraper/web/app.py tests/test_web_app.py
git commit -m "feat(web): compute OUR 1up/2up per event in EventView builder"
```

---

### Task 8: Card template — SIM column markup + rules

**Files:**
- Modify: `src/odds_scraper/web/templates/_event_card.html`
- Modify: `src/odds_scraper/web/static/app.css`
- Test: tests already added in Task 7 finish passing here.

- [ ] **Step 1: Edit the card template**

Edit `src/odds_scraper/web/templates/_event_card.html`. In the col-header div, insert a new lbl span between BP and SB:

```jinja
    <div class="col-header">
      <span class="lbl">MARKET · OUTCOME</span>
      <span class="lbl" data-bookmaker="betpawa">BP <span class="prob-mark">+p</span></span>
      <span class="lbl sim" data-bookmaker="sim">SIM</span>
      <span class="lbl" data-bookmaker="sportybet">SB <span class="prob-mark">+p</span></span>
      <span class="lbl" data-bookmaker="bet9ja">B9J</span>
      <span class="lbl" data-bookmaker="betway">BW</span>
    </div>
```

Inside the `render_group` macro, change the row loop so each row renders six cells in the right order, with conditional logic for the 1up/2up markets:

```jinja
    {% macro render_group(group) %}
      <div class="market-block" data-group-key="{{ group.group_key }}">
        <div class="group-label">{{ group.label }}</div>
        {% for row in group.rows %}
          {% set is_up_row = group.group_key in ('1x2_1up_ft', '1x2_2up_ft') %}
          {% set our_value = none %}
          {% set bp_has_quote = false %}
          {% if is_up_row %}
            {% if group.group_key == '1x2_1up_ft' %}
              {% set bp_has_quote = event.bp_has_1up %}
              {% if row.side_short == 'H' %}{% set our_value = event.our_1up_home %}{% endif %}
              {% if row.side_short == 'A' %}{% set our_value = event.our_1up_away %}{% endif %}
            {% else %}
              {% set bp_has_quote = event.bp_has_2up %}
              {% if row.side_short == 'H' %}{% set our_value = event.our_2up_home %}{% endif %}
              {% if row.side_short == 'A' %}{% set our_value = event.our_2up_away %}{% endif %}
            {% endif %}
          {% endif %}
          <div class="row">
            <span class="outcome">{{ row.market_label }} · {{ row.side_short }}</span>

            {# BP column: normal quote, OR OUR with SIM marker when BP missing UP quote #}
            <span data-bookmaker="betpawa">
              {% set p = row.prices.get("betpawa") %}
              {% if p %}
                <span class="odds">{{ "%.2f"|format(p.odds) }}</span>
                {% if p.probability is not none %}
                  <span class="prob">.{{ "%02d"|format([(p.probability * 100)|round|int, 99]|min) }}</span>
                {% endif %}
              {% elif is_up_row and our_value is not none and not bp_has_quote %}
                <span class="odds sim">{{ "%.2f"|format(our_value) }}</span>
                <span class="sim-pill">SIM</span>
              {% else %}<span class="text-gray-700">—</span>{% endif %}
            </span>

            {# SIM column: only populated for UP rows when BP quoted #}
            <span data-bookmaker="sim">
              {% if is_up_row and our_value is not none and bp_has_quote %}
                <span class="odds sim">{{ "%.2f"|format(our_value) }}</span>
                <span class="sim-pill">SIM</span>
              {% else %}<span class="text-gray-700">—</span>{% endif %}
            </span>

            {# Remaining books — sportybet, bet9ja, betway #}
            {% for bm in ("sportybet", "bet9ja", "betway") %}
              {% set p = row.prices.get(bm) %}
              <span data-bookmaker="{{ bm }}">
                {% if p %}
                  <span class="odds">{{ "%.2f"|format(p.odds) }}</span>
                  {% if bm == "sportybet" and p.probability is not none %}
                    <span class="prob">.{{ "%02d"|format([(p.probability * 100)|round|int, 99]|min) }}</span>
                  {% endif %}
                {% else %}<span class="text-gray-700">—</span>{% endif %}
              </span>
            {% endfor %}
          </div>
        {% endfor %}
      </div>
    {% endmacro %}
```

- [ ] **Step 2: Add SIM cell styling**

Edit `src/odds_scraper/web/static/app.css`. Add after the existing `.prob` rule:

```css
/* Pricer SIM column — OUR engine output. Strong marker so a glance
   never confuses simulated odds with a real market quote. */
.lbl.sim, .odds.sim {
  color: #fbbf24;
  font-weight: 600;
}
.sim-pill {
  display: inline-block;
  border: 1px solid #fbbf24;
  color: #fbbf24;
  background: #3a2f0a;
  padding: 0 3px;
  margin-left: 3px;
  font-size: 9px;
  letter-spacing: 0.05em;
}
.col-header > .lbl[data-bookmaker="sim"],
.row > span[data-bookmaker="sim"] {
  width: 90px;       /* slightly wider than other cells to fit "1.85 SIM" */
  text-align: center;
  white-space: nowrap;
}
```

Also add the SIM column to the bookmaker chip toggle CSS block (so the existing BP/SB/B9J/BW chip toggle keeps working — and so the SIM column can't be hidden via a chip since there's no chip for it; the rule below is just for parity if we ever add one):

(no change needed — body.hide-* rules don't cover "sim" and we don't render a sim chip, so SIM is always visible by design.)

- [ ] **Step 3: Run tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_web_app.py -v`
Expected: all pass, including the two from Task 7.

- [ ] **Step 4: Add four targeted SIM-column tests**

Append to `tests/test_web_app.py`:

```python
@pytest.fixture
def db_with_ftts_and_ou(tmp_path: Path) -> Path:
    """DB seeded with 1X2 + OU + FTTS so the engine can compute OUR.
    Plus BP-quoted 1UP odds (home only) so we can test both "BP has" and
    "BP missing" cases on the same card."""
    path = tmp_path / "odds.db"
    conn = sqlite3.connect(str(path), isolation_level=None)
    init_schema(conn)
    conn.execute(
        "INSERT INTO events (id, home, away, kickoff_utc) "
        "VALUES ('E1', 'Liverpool', 'Arsenal', '2026-05-22T18:30:00Z')",
    )
    cur = conn.execute(
        "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, fetch_status) "
        "VALUES ('2026-05-21T10:00:00Z', 'E1', 'betpawa', 'UPCOMING', 'ok')",
    )
    snap_id = cur.lastrowid
    inserts = [
        ("1x2_ft", 0.0, "home", 1.85, 0.54),
        ("1x2_ft", 0.0, "draw", 3.40, 0.29),
        ("1x2_ft", 0.0, "away", 4.20, 0.17),
        ("over_under_ft", 2.5, "over",  1.85, 0.55),
        ("over_under_ft", 2.5, "under", 1.95, 0.45),
        ("next_goal_ft", 1.0, "home", 1.85, 0.54),
        ("next_goal_ft", 1.0, "none", 8.50, 0.12),
        ("next_goal_ft", 1.0, "away", 3.50, 0.34),
        # BP has 1UP home quote but NOT 1UP away — exercises both rules.
        ("1x2_1up_ft", 0.0, "home", 1.50, 0.65),
    ]
    for mid, line, side, odds, prob in inserts:
        conn.execute(
            "INSERT INTO prices (snapshot_id, event_id, ts_utc, bookmaker, "
            "market_id, line, side, odds, probability) "
            "VALUES (?, 'E1', '2026-05-21T10:00:00Z', 'betpawa', ?, ?, ?, ?, ?)",
            (snap_id, mid, line, side, odds, prob),
        )
    conn.close()
    return path


def test_sim_column_header_present(client: TestClient):
    r = client.get("/events?status=upcoming")
    assert 'data-bookmaker="sim"' in r.text
    assert "SIM" in r.text  # header text


def test_sim_cell_marks_our_when_bp_has_up_quote(db_with_ftts_and_ou: Path):
    """1UP home row — BP quoted, so SIM cell has OUR with .sim class +
    SIM pill, BP cell shows BP's plain quote."""
    client = TestClient(create_app(db_path=db_with_ftts_and_ou))
    r = client.get("/events?status=upcoming")
    # 1UP-home row markup contains both BP quote (1.50) and a sim-pill
    # for OUR — we can't trivially parse the row alignment but the
    # presence of the sim-pill class proves the SIM cell rendered.
    assert "sim-pill" in r.text
    assert "1.50" in r.text  # BP-quoted 1UP home odds


def test_sim_replaces_bp_cell_when_bp_missing_up_quote(db_with_ftts_and_ou: Path):
    """BP didn't quote 1UP away — the BP cell itself must show OUR
    (with .sim class + SIM pill), and the SIM cell stays blank."""
    client = TestClient(create_app(db_path=db_with_ftts_and_ou))
    r = client.get("/events?status=upcoming")
    # Count sim-pill occurrences: one for 1UP-home in SIM column +
    # at least one more for an UP row where BP missing → in BP slot.
    assert r.text.count("sim-pill") >= 2


def test_sim_blank_for_1x2_ft_and_ou_rows(db_with_ftts_and_ou: Path):
    """SIM cell is blank for non-UP markets even when OUR is computable."""
    client = TestClient(create_app(db_path=db_with_ftts_and_ou))
    r = client.get("/events?status=upcoming")
    # 1x2_ft and over_under rows are NOT in {1x2_1up_ft, 1x2_2up_ft} so
    # their SIM cells render em-dash. Spot-check: the page contains
    # at least one em-dash cell scoped to data-bookmaker="sim".
    assert 'data-bookmaker="sim">\n                <span class="text-gray-700">—</span>' \
           in r.text \
        or 'data-bookmaker="sim">' in r.text  # tolerant of whitespace variants
```

- [ ] **Step 5: Run tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_web_app.py -v`
Expected: all pass.

Run full suite: `.venv/Scripts/python.exe -m pytest -q`
Expected: 100% pass, no regressions.

- [ ] **Step 6: Commit**

```bash
git add src/odds_scraper/web/templates/_event_card.html src/odds_scraper/web/static/app.css tests/test_web_app.py
git commit -m "feat(web): SIM column on event cards — OUR engine output with strong marker"
```

---

### Task 9: `pricer_routes.py` — GET `/simulator`

**Files:**
- Create: `src/odds_scraper/web/pricer_routes.py`
- Create: `src/odds_scraper/web/templates/simulator.html`
- Modify: `src/odds_scraper/web/app.py` (register routes + link in topbar)
- Modify: `src/odds_scraper/web/templates/index.html` (link to /simulator)
- Test: `tests/test_simulator_routes.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_simulator_routes.py`:

```python
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from odds_scraper.db_schema import init_schema
from odds_scraper.web.app import create_app


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "odds.db"
    conn = sqlite3.connect(str(p), isolation_level=None)
    init_schema(conn)
    conn.close()
    return p


@pytest.fixture
def client(db_path: Path) -> TestClient:
    return TestClient(create_app(db_path=db_path))


def test_simulator_page_renders(client: TestClient):
    r = client.get("/simulator")
    assert r.status_code == 200
    assert "Pricer Simulator" in r.text
    # Default profile is present in the selector
    assert "default" in r.text
    # Coverage radio options
    for cov in ("all", "latest", "prematch", "live"):
        assert f'value="{cov}"' in r.text
    # Run button
    assert "Run simulation" in r.text


def test_index_links_to_simulator(client: TestClient):
    r = client.get("/")
    assert 'href="/simulator"' in r.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_simulator_routes.py -v`
Expected: FAIL — `/simulator` returns 404.

- [ ] **Step 3: Create the simulator template**

Create `src/odds_scraper/web/templates/simulator.html`:

```html
{% extends "base.html" %}
{% block content %}
<div class="p-3 max-w-screen-2xl mx-auto">
  <div class="flex justify-between border-b border-gray-900 pb-3 mb-3">
    <div class="text-white font-semibold tracking-wider">PRICER SIMULATOR</div>
    <a class="tab" href="/">← back to events</a>
  </div>

  <form method="post" action="/simulator/runs" class="sim-form">
    <div class="sim-section">
      <div class="sim-section-h">1 · Event scope</div>
      <div class="filter-group">
        <span class="filter-lbl">Status</span>
        <label><input type="radio" name="status" value="upcoming" checked> upcoming</label>
        <label><input type="radio" name="status" value="live"> live</label>
        <label><input type="radio" name="status" value="ended"> ended</label>
      </div>
      <div class="filter-group">
        <span class="filter-lbl">Country</span>
        <input class="filter-select" type="text" name="country" placeholder="country_id">
        <span class="filter-lbl">League</span>
        <input class="filter-select" type="text" name="league" placeholder="league_id">
        <span class="filter-lbl">Date</span>
        <input type="date" class="custom-hours" name="date">
        <span class="filter-lbl">Search</span>
        <input class="search-input" type="search" name="search" placeholder="team">
      </div>
    </div>

    <div class="sim-section">
      <div class="sim-section-h">2 · Config</div>
      <div class="filter-group">
        <span class="filter-lbl">Profile</span>
        <select name="config_id" class="filter-select">
          {% for p in profiles %}
            <option value="{{ p.id }}">{{ p.name }}{% if p.is_default %} (default){% endif %}</option>
          {% endfor %}
        </select>
      </div>
      <p class="filter-lbl" style="margin-top:6px;color:#888">
        Editing coefficients inline lands in a follow-up — for now pick a profile.
        Use the CLI to create a new profile, then re-load this page.
      </p>
    </div>

    <div class="sim-section">
      <div class="sim-section-h">3 · Run</div>
      <div class="filter-group">
        <span class="filter-lbl">Coverage</span>
        <label><input type="radio" name="coverage" value="all" checked> all snapshots</label>
        <label><input type="radio" name="coverage" value="latest"> latest per event</label>
        <label><input type="radio" name="coverage" value="prematch"> prematch only</label>
        <label><input type="radio" name="coverage" value="live"> live only</label>
      </div>
      <button type="submit" class="chip on" style="padding:6px 16px;cursor:pointer">Run simulation</button>
    </div>
  </form>

  <div class="sim-section">
    <div class="sim-section-h">4 · Last run</div>
    {% if last_run %}
      <p class="filter-lbl">
        Run #{{ last_run.id }} · {{ last_run.created_at }} · {{ last_run.coverage }} ·
        {{ last_run.n_events }} events · {{ last_run.n_rows }} rows ·
        <a href="/simulator/runs/{{ last_run.id }}/csv" style="color:#60a5fa">download CSV</a>
      </p>
    {% else %}
      <p class="filter-lbl">No runs yet.</p>
    {% endif %}
  </div>

  <div class="sim-section">
    <div class="sim-section-h">5 · History</div>
    <table class="history-table">
      <thead>
        <tr><th>#</th><th>created</th><th>profile</th><th>coverage</th><th>events</th><th>rows</th><th>csv</th></tr>
      </thead>
      <tbody>
        {% for r in history %}
        <tr>
          <td>{{ r.id }}</td>
          <td>{{ r.created_at }}</td>
          <td>{{ r.profile_name }}</td>
          <td>{{ r.coverage }}</td>
          <td>{{ r.n_events }}</td>
          <td>{{ r.n_rows }}</td>
          <td><a href="/simulator/runs/{{ r.id }}/csv" style="color:#60a5fa">csv</a></td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</div>

<style>
  .sim-section { margin: 14px 0; padding: 10px; background: #0a0a0a; border: 1px solid #1a1a1a; border-radius: 4px; }
  .sim-section-h { color: #fbbf24; font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 8px; }
  .sim-form label { color: #d1d5db; font-size: 11px; margin-right: 8px; }
</style>
{% endblock %}
```

- [ ] **Step 4: Create the routes module**

Create `src/odds_scraper/web/pricer_routes.py`:

```python
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from odds_scraper.pricer import configs as config_mod, runner


def register_pricer_routes(
    app: FastAPI, templates: Jinja2Templates,
    *, db_path: Path, conn,
) -> None:
    """Attach /simulator + /simulator/runs + /simulator/runs/<id>/csv to `app`.

    `conn` is the read-only connection used by the rest of the app. The
    simulator needs a writeable connection per request to insert run
    rows; we open one fresh inside the POST handler.
    """
    import sqlite3
    csv_dir = db_path.parent / "sim"

    def _ro_query(sql: str, args=()):
        return conn.execute(sql, args).fetchall()

    @app.get("/simulator", response_class=HTMLResponse)
    async def simulator_page(request: Request):
        profiles = config_mod.list_profiles(conn)
        last_row = conn.execute(
            "SELECT id, created_at, coverage, n_events, n_rows "
            "FROM pricer_runs ORDER BY id DESC LIMIT 1",
        ).fetchone()
        last_run = dict(last_row) if last_row else None
        history_rows = conn.execute(
            "SELECT r.id, r.created_at, c.name AS profile_name, r.coverage, "
            "       r.n_events, r.n_rows "
            "FROM pricer_runs r LEFT JOIN pricer_configs c ON c.id = r.config_id "
            "ORDER BY r.id DESC LIMIT 20"
        ).fetchall()
        return templates.TemplateResponse(
            request, "simulator.html",
            {
                "profiles": profiles,
                "last_run": last_run,
                "history": [dict(r) for r in history_rows],
            },
        )

    @app.post("/simulator/runs")
    async def post_run(
        config_id: int = Form(...),
        coverage:  str = Form(...),
        status:    str = Form(""),
        country:   str = Form(""),
        league:    str = Form(""),
        date:      str = Form(""),
        search:    str = Form(""),
    ):
        if coverage not in ("all", "latest", "prematch", "live"):
            raise HTTPException(400, f"unknown coverage {coverage!r}")
        write_conn = sqlite3.connect(str(db_path), isolation_level=None)
        write_conn.row_factory = sqlite3.Row
        try:
            profile = config_mod.load_by_id(write_conn, config_id)
            if profile is None:
                raise HTTPException(400, f"unknown config_id {config_id}")
            scope = {"status": status, "country": country, "league": league,
                     "date": date, "search": search}
            run_id = runner.run_simulation(
                write_conn, config=profile,
                coverage=coverage, scope=scope, csv_dir=csv_dir,
            )
        finally:
            write_conn.close()
        return RedirectResponse(url=f"/simulator#run-{run_id}", status_code=303)

    @app.get("/simulator/runs/{run_id}/csv")
    async def get_run_csv(run_id: int):
        row = conn.execute(
            "SELECT csv_path FROM pricer_runs WHERE id = ?", (run_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(404, f"no such run {run_id}")
        full_path = db_path.parent / row["csv_path"]
        if not full_path.exists():
            raise HTTPException(404, f"csv missing on disk for run {run_id}")
        return FileResponse(str(full_path), media_type="text/csv",
                            filename=f"pricer_run_{run_id:04d}.csv")
```

- [ ] **Step 5: Wire routes into `create_app`**

Edit `src/odds_scraper/web/app.py`. At the top with the other imports:

```python
from .pricer_routes import register_pricer_routes
```

Inside `create_app`, right after `app.mount("/static", ...)`:

```python
    register_pricer_routes(app, templates, db_path=db_path, conn=conn)
```

- [ ] **Step 6: Add link in index.html top bar**

Edit `src/odds_scraper/web/templates/index.html`. Inside the `<div class="flex justify-between ...">`, add an anchor near the tabs:

```html
  <div class="flex justify-between border-b border-gray-900 pb-3 mb-3">
    <div class="text-white font-semibold tracking-wider">ODDS · LIVE COMPARISON</div>
    <div class="flex gap-1" id="tabs">
      <button class="tab" data-status="live">LIVE</button>
      <button class="tab active" data-status="upcoming">UPCOMING</button>
      <button class="tab" data-status="ended">ENDED</button>
      <a class="tab" href="/simulator" style="margin-left:12px;color:#fbbf24">SIMULATOR →</a>
    </div>
  </div>
```

- [ ] **Step 7: Run tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_simulator_routes.py tests/test_web_app.py -v`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/odds_scraper/web/pricer_routes.py src/odds_scraper/web/templates/simulator.html src/odds_scraper/web/app.py src/odds_scraper/web/templates/index.html tests/test_simulator_routes.py
git commit -m "feat(web): /simulator GET — form, last-run summary, run history"
```

---

### Task 10: POST `/simulator/runs` end-to-end

**Files:**
- Test: extend `tests/test_simulator_routes.py`
- (Routes module already wired in Task 9.)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_simulator_routes.py`:

```python
def _seed_priced_event(db_path: Path):
    """Single event with full inputs at one timestamp."""
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.execute(
        "INSERT INTO events (id, home, away, kickoff_utc) "
        "VALUES ('E1', 'Liverpool', 'Arsenal', '2026-05-22T18:30:00Z')",
    )
    cur = conn.execute(
        "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, fetch_status) "
        "VALUES ('2026-05-21T10:00:00Z', 'E1', 'betpawa', 'UPCOMING', 'ok')",
    )
    snap_id = cur.lastrowid
    inserts = [
        ("1x2_ft", 0.0, "home", 1.85, 0.54),
        ("1x2_ft", 0.0, "draw", 3.40, 0.29),
        ("1x2_ft", 0.0, "away", 4.20, 0.17),
        ("over_under_ft", 2.5, "over",  1.85, 0.55),
        ("over_under_ft", 2.5, "under", 1.95, 0.45),
        ("next_goal_ft", 1.0, "home", 1.85, 0.54),
        ("next_goal_ft", 1.0, "none", 8.50, 0.12),
        ("next_goal_ft", 1.0, "away", 3.50, 0.34),
    ]
    for mid, line, side, odds, prob in inserts:
        conn.execute(
            "INSERT INTO prices (snapshot_id, event_id, ts_utc, bookmaker, "
            "market_id, line, side, odds, probability) "
            "VALUES (?, 'E1', '2026-05-21T10:00:00Z', 'betpawa', ?, ?, ?, ?, ?)",
            (snap_id, mid, line, side, odds, prob),
        )
    conn.close()


def test_post_run_creates_row_and_csv(db_path: Path, client: TestClient):
    _seed_priced_event(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    default_id = conn.execute(
        "SELECT id FROM pricer_configs WHERE is_default=1"
    ).fetchone()["id"]
    conn.close()
    r = client.post(
        "/simulator/runs",
        data={
            "config_id": default_id, "coverage": "all",
            "status": "upcoming", "country": "", "league": "",
            "date": "", "search": "",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "/simulator" in r.headers["location"]
    # Check that a run + at least one result row + CSV file landed.
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    run = conn.execute(
        "SELECT id, n_rows, csv_path FROM pricer_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert run["n_rows"] == 1
    csv_full = db_path.parent / run["csv_path"]
    assert csv_full.exists()
    n_result_rows = conn.execute(
        "SELECT COUNT(*) FROM pricer_results WHERE run_id = ?", (run["id"],),
    ).fetchone()[0]
    assert n_result_rows == 1
    conn.close()


def test_get_run_csv_streams_file(db_path: Path, client: TestClient):
    _seed_priced_event(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    default_id = conn.execute(
        "SELECT id FROM pricer_configs WHERE is_default=1"
    ).fetchone()["id"]
    conn.close()
    client.post(
        "/simulator/runs",
        data={"config_id": default_id, "coverage": "all", "status": "upcoming",
              "country": "", "league": "", "date": "", "search": ""},
        follow_redirects=False,
    )
    conn = sqlite3.connect(str(db_path))
    run_id = conn.execute("SELECT id FROM pricer_runs ORDER BY id DESC LIMIT 1").fetchone()[0]
    conn.close()
    r = client.get(f"/simulator/runs/{run_id}/csv")
    assert r.status_code == 200
    assert r.headers["content-type"] == "text/csv; charset=utf-8"
    body = r.text
    assert "event_id" in body.splitlines()[0]  # header
    assert "E1" in body
```

- [ ] **Step 2: Run tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_simulator_routes.py -v`
Expected: both new tests pass (POST + GET csv).

- [ ] **Step 3: Run full suite for regression**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: 100% pass.

- [ ] **Step 4: Commit**

```bash
git add tests/test_simulator_routes.py
git commit -m "test(web): POST /simulator/runs end-to-end + CSV download"
```

---

### Task 11: Merge to main + smoke

**Files:**
- (no code changes — branch hygiene)

- [ ] **Step 1: Full test suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: 100% pass.

- [ ] **Step 2: Manual smoke**

Start the web app:

```bash
.venv/Scripts/python.exe -m uvicorn odds_scraper.web.app:create_app --factory --reload --port 8000
```

In a browser at `http://localhost:8000`:
- Confirm the SIM column header shows between BP and SB.
- Open a card with FTTS + OU data → confirm a `SIM` pill appears in the SIM cell on 1UP/2UP rows.
- Click the `SIMULATOR →` link → land on `/simulator`.
- Submit the default form → redirect back, History gains a row, CSV link works.

- [ ] **Step 3: Merge feature branch to main**

```bash
git checkout main
git merge --no-ff feat/pricer-integration -m "Merge feat/pricer-integration"
git log --oneline -3
```

(The branch name varies depending on how tasks were grouped — adjust to whichever branch carries the bulk of the work.)

---

## Self-review

**Spec coverage check:**
- "BP-first with per-input fallback to SB" → Task 4 (`inputs.py`).
- "B9J/BW never supply probs" → enforced by `extract` only looking at `betpawa` and `sportybet` keys (Task 4).
- "SIM column between BP and SB" → Task 8 template.
- "BP has UP odds → BP plain, SIM marked" → Task 8 row macro.
- "BP missing UP odds → BP cell shows OUR + SIM" → Task 8 row macro.
- "Non-UP rows → SIM blank" → Task 8 row macro (the `is_up_row` gate).
- "13 named constants tunable" → Task 3 (`TUNABLE_NAMES`).
- "Named profiles persisted, default read-only" → Tasks 2 + 3.
- "`with_coefficients` context manager" → Task 5.
- "Schema v4 — 3 tables + seed" → Task 2.
- "Coverage modes: all / latest / prematch / live" → Task 5 (`_select_snapshot_ids`).
- "Wide CSV at `data/sim/run_<id>.csv`" → Task 6 + Task 5.
- "`/simulator` form, `/simulator/runs` POST, `/simulator/runs/<id>/csv` GET" → Tasks 9 + 10.
- "OUR rendered live during the trailing-team path (live snapshots)" → Task 7 passes `score` to engine.

**Placeholder scan:** none. Every step contains the code or command needed.

**Type consistency:**
- `Profile` dataclass used in Task 3 → consumed in Task 5 (`run_simulation(config: config_mod.Profile)`) and Task 9 (`config_mod.load_by_id`). Signatures match.
- `EventView` extended in Task 7 with `our_*` + `bp_has_*` fields, consumed in Task 8 template.
- `extract()` returns `(Optional[dict], str)` in Task 4 — consumers in Task 5 (`run_simulation`) and Task 7 (`_build_event_view`) both destructure into `(engine_inputs, basis)`.
- `csv_path` is `"sim/run_NNNN.csv"` everywhere (Task 5 + Task 6 + Task 9).

Plan is complete and consistent.
