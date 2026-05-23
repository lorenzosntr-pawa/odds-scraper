# Pricer engine integration — design

**Status:** approved 2026-05-23
**Touches:** new `src/odds_scraper/pricer/` package; `web/app.py` + `web/queries.py` + `web/templates/_event_card.html` + `web/templates/index.html` + `web/static/app.css` (SIM column); new `web/templates/simulator.html` + simulator routes; `db_schema.py` (schema v4); new tests
**Untouched:** scraper runtime (`collector.py`, `watcher.py`, `writer.py`, `main.py`), `MARKET_MANIFEST`, bookieskit integration, `resolution.py`

## Motivation

We already monitor four books' 1UP / 2UP prices side-by-side on the home cards and on the event detail page. We don't yet see what **our own** engine would price for those markets at the same tick. That gap matters for two reasons:

1. **Port verification** — the Python `engine_prod_v1.py` is a hand-port of the Java `ThreeWay1UPCalculatorImpl + Threeway2UpCalculatorImpl`. Until we see OUR computed prices alongside BP's quoted ones in the same UI we use every day, we can't tell whether the port drifted.
2. **Config tuning workflow** — pricing-team needs to sweep margin / reduction / boost coefficients against real historical snapshots and see the deltas vs the four books in one place, then export the diff for a teammate. The existing standalone `run_*_comparison.py` scripts produce CSVs but require running a script and reading the file; the workflow we want is interactive.

Both surfaces share the engine and the input contract. Building them together avoids forking the integration twice.

## Settled inputs

| Decision | Value |
|---|---|
| Placement | Both: live "OUR" column on event cards **and** dedicated `/simulator` page. |
| Engine source | Verbatim copy of `engine_prod_v1.py` into `pricer/engine.py`. No edits to the math. |
| Basis book (card OUR column) | BP-first with per-input fallback to SB. B9J / BW never supply probability inputs because `prices.probability` is NULL for them by design. |
| Probability source | `prices.probability` as-is. Scraped pre-devigged at source; never re-devigged client-side. |
| Cap-step source odds | BP's 1X2 decimal odds when present, else SB's. Matches the "compare-against-BP" framing. |
| Live regime | Engine handles both prematch (score 0-0) and live (trailing-team paths) — OUR column renders for both, no special-casing. |
| SIM column position | Between BP and SB → `MARKET · OUTCOME | BP | SIM | SB | B9J | BW`. |
| SIM marker style | Strong yellow background + "SIM" pill on the cell. |
| SIM cell rule (BP has UP odds) | BP cell shows BP quote (plain), SIM cell shows OUR with strong marker. |
| SIM cell rule (BP missing UP odds) | BP cell *itself* shows OUR with strong marker ("what BP would quote"). SIM cell is blank. |
| SIM cell rule (non-UP rows) | Always blank — SIM only applies to `1x2_1up_ft` and `1x2_2up_ft`. |
| Tuning scope | Margins + reductions + boost coefficients — 13 named constants, 17 scalar values (margins are `(slope, intercept)` tuples). Model intercepts stay pinned to engine source. |
| Named profiles | Yes. Profiles are persisted; one read-only `default` is seeded from the engine source. |
| Sim coverage modes | `all` (every snapshot), `latest` (most recent snapshot per event), `prematch`, `live`. |
| Sim scope | Reuses the event filter row: status tab + country + league + date + search. |
| Storage for sim runs | New tables `pricer_configs`, `pricer_runs`, `pricer_results` in the existing SQLite DB. |
| CSV export | Per-run wide CSV at `data/sim/run_<id>.csv`; download link served from `/simulator/runs/<id>/csv`. |
| In-card OUR persistence | None. Computed on-the-fly per render (~1ms × ~50 events = negligible). |

## Architecture

### Package layout

```
src/odds_scraper/pricer/
├── __init__.py
├── engine.py        # verbatim copy of engine_prod_v1.py
├── inputs.py        # extract() with BP-first per-input SB fallback
├── runner.py        # iterate snapshots → engine → result rows, plus with_coefficients()
├── configs.py       # load/save/list named coefficient profiles; "default" is seeded read-only
```

### Card "OUR" column

`web/queries.py` already loads per-event latest prices via `get_latest_prices_for_events`. Extend the row-builder pipeline so each event view has:

- `our_1up_home`, `our_1up_away`, `our_2up_home`, `our_2up_away` — the engine output (capped odds).
- A flag `bp_has_1up` / `bp_has_2up` so the template can decide whether OUR goes in the SIM cell or replaces the BP cell.

The Jinja card template gains:
- A new column header `SIM` between `BP` and `SB`.
- A new cell per row, populated according to the rules table above.
- A `.sim` CSS class delivering the yellow background and "SIM" pill.

When no engine output can be produced (no 1X2 + no OU from either BP or SB, or FTTS missing for 1UP), the cell falls back to em-dash exactly like a missing book quote.

### `/simulator` page

Top-down five-section single page (no SPA / no fragments). All five render server-side.

1. **Event scope** — same filter conventions as the home page (status tab, country, league, date, search). On change it re-queries `/simulator?…` to refresh the live "matches in scope" count.
2. **Config** — profile selector + form for the ~14 tunable scalars. "Save as…" persists a new named profile. "Reset to default" reloads the seeded read-only one.
3. **Run** — coverage radio (`all` / `latest` / `prematch` / `live`) + the big run button. The form POSTs to `/simulator/runs`.
4. **Results** — most recent run's summary (id, profile, n events, n rows, timing) + preview of the top N rows + "Download CSV".
5. **History** — last 20 runs as a table with `Run #id · ts · profile · n_events / n_rows · csv link`.

Running is synchronous in-request — engine pass is ~1 ms × the snapshot count, expected sub-second for a typical day's scope. If a sweep is wide enough to feel slow, we'll revisit with a background task later (out of scope here).

### Data model

Three new tables, added via the existing `_MIGRATIONS` dict, bumping `SCHEMA_VERSION` to 4:

```sql
CREATE TABLE pricer_configs (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL UNIQUE,
    created_at   TEXT NOT NULL,
    is_default   INTEGER NOT NULL DEFAULT 0,  -- 1 = pinned, can't edit/delete
    coefficients TEXT NOT NULL                -- JSON map of {name: value-or-tuple}
);

CREATE TABLE pricer_runs (
    id          INTEGER PRIMARY KEY,
    created_at  TEXT NOT NULL,
    config_id   INTEGER NOT NULL REFERENCES pricer_configs(id),
    coverage    TEXT NOT NULL,                -- 'all' | 'latest' | 'prematch' | 'live'
    scope_json  TEXT NOT NULL,                -- JSON of the filter selections at run time
    n_events    INTEGER NOT NULL,
    n_rows      INTEGER NOT NULL,
    csv_path    TEXT NOT NULL                 -- relative path under data/, e.g. 'sim/run_0042.csv'
);
CREATE INDEX idx_pricer_runs_created ON pricer_runs(created_at DESC);

CREATE TABLE pricer_results (
    run_id              INTEGER NOT NULL REFERENCES pricer_runs(id),
    snapshot_id         INTEGER NOT NULL REFERENCES snapshots(id),
    event_id            TEXT    NOT NULL,
    ts_utc              TEXT    NOT NULL,
    basis_used          TEXT    NOT NULL,     -- 'bp' | 'bp+sb' | 'sb'
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
    -- Quoted at the same tick. NULL if the book didn't quote the market.
    bp_1up_home_odds    REAL, bp_1up_away_odds REAL,
    bp_2up_home_odds    REAL, bp_2up_away_odds REAL,
    sb_1up_home_odds    REAL, sb_1up_away_odds REAL,
    sb_2up_home_odds    REAL, sb_2up_away_odds REAL,
    b9j_1up_home_odds   REAL, b9j_1up_away_odds REAL,
    b9j_2up_home_odds   REAL, b9j_2up_away_odds REAL,
    bw_1up_home_odds    REAL, bw_1up_away_odds REAL,
    bw_2up_home_odds    REAL, bw_2up_away_odds REAL
);
CREATE INDEX idx_pricer_results_run     ON pricer_results(run_id);
CREATE INDEX idx_pricer_results_event   ON pricer_results(event_id, ts_utc);
```

The seeded `default` config row carries the FeatureProperties.java baseline values as JSON. Adding it is part of the same migration.

### Engine integration mechanics

`engine.py` keeps its module-level constants exactly as imported. Coefficient overrides happen via a context manager in `runner.py`:

```python
from contextlib import contextmanager
from . import engine

@contextmanager
def with_coefficients(overrides: dict):
    saved = {k: getattr(engine, k) for k in overrides}
    try:
        for k, v in overrides.items():
            setattr(engine, k, v)
        yield
    finally:
        for k, v in saved.items():
            setattr(engine, k, v)
```

Engine calls are synchronous and the web app is single-process asyncio — no concurrent engine calls within one event loop, so the in-place mutation is safe. The card OUR column always runs under the seeded `default` config (no override), so the contextmanager is only entered by the simulator runner.

### Input fallback contract (`pricer/inputs.py`)

```python
def extract(prices_by_book: dict[str, list[Row]]) -> tuple[Optional[dict], str]:
    """
    Build the engine input dict from per-book price rows.

    Inputs and per-input "available" semantics (BP-first, SB fallback):
      - 1X2: all three sides (home, draw, away) must have non-null prob.
      - total_ou / home_ou / away_ou: at least one half-line (X.5) with
        non-null over prob — the whole list is taken from one book, no
        cross-book line merging (avoids basis ambiguity).
      - ftts: both home and away sides with non-null prob.

    For each input, take it from BP if available; otherwise from SB.
    Returns (inputs, basis_used) where basis_used is "bp" | "bp+sb" | "sb"
    summarising which books supplied at least one input. Returns
    (None, "") if lambdas can't be derived (no OU from either book).

    `prices.probability` is consumed as-is — pre-devigged at source.
    """
```

The basis-used string is recorded on each `pricer_results` row for audit.

## Out of scope

- Modifying the engine math itself — `engine.py` is the verbatim port.
- Showing OUR for markets other than 1UP / 2UP — the SIM column is hidden for `1x2_ft`, `over_under_*`, `next_goal_ft`.
- Background sim runs / job queue — runs are sync in-request; if a sweep grows past acceptable latency we'll add a queue later.
- Editing the read-only `default` profile from the UI.
- Comparing two profiles inside one run / overlay charts of two runs — separate runs + CSV diff for now.
- CSV export of the card view — only `/simulator` runs export CSVs.
- Live-streaming sim results to the browser during a run — render after completion.

## Testing approach

- **Engine** — copy `tests/test_engine_prod_v1.py` from the original project verbatim. Asserts the port hasn't drifted. Pure math, no fixtures.
- **Inputs** — table-driven cases on the fallback ladder: BP-full, BP-missing-FTTS-SB-has-it, BP-missing-everything, both-missing-OU (returns `None`). Regression guard against accidental re-devigging.
- **Runner** — fixture DB with one event × two snapshots → `run()` writes N rows + a CSV with matching row count.
- **Configs** — create / load / delete a profile; delete on `default` raises.
- **Card SIM column** — extend `tests/test_web_app.py`:
  - BP has 1UP odds → BP cell plain, SIM cell has `.sim` class + "SIM" pill.
  - BP missing 1UP odds → BP cell has `.sim` class + "SIM" pill, SIM cell blank.
  - `1x2_ft` / OU rows → SIM cell always blank.
  - OUR not computable → SIM cell em-dash, no pill.
- **`/simulator` routes**:
  - `GET /simulator` → 200, has form, has run-history table.
  - `POST /simulator/runs` → returns the run id, persists rows + CSV.
  - `GET /simulator/runs/<id>/csv` → 200, `text/csv`, row count matches.
- **`with_coefficients` context manager** — set → `getattr` reflects override → exit → `getattr` reflects original. No engine call in this test (isolation).
