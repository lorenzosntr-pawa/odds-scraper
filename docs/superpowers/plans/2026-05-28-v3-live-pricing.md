# V3 Live Pricing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compute the V3 pricing engine on every live tick, persist it in `pricer_live_results` beside V2, backfill history, and show V3 next to V2 in the event-detail history and the home cards.

**Architecture:** Additive mirror of the V2 live path. Schema migration v10 adds 12 `v3_*` columns; `live_writer` runs `engine_v3` on the same extracted inputs as `engine_v2` and writes both; a `backfill_v3` UPDATE-only pass fills existing rows; the history query and two templates render V3 alongside V2. The scraper, simulator, `engine_v2.py`, and `engine.py` are untouched. V3 uses its hardcoded module defaults (no profile), exactly like V2 live.

**Tech Stack:** Python 3.13, SQLite, FastAPI + Jinja2, pytest. Run tests with `.venv\Scripts\python.exe -m pytest`.

**Spec:** `docs/superpowers/specs/2026-05-28-v3-live-pricing-design.md`

---

### Task 1: Schema migration v10 — add `v3_*` columns

**Files:**
- Modify: `src/odds_scraper/db_schema.py` (`SCHEMA_VERSION` line 7; `_MIGRATIONS` dict ends line 261)
- Test: `tests/test_db_schema.py` (create if absent; otherwise add to it)

- [ ] **Step 1: Write the failing test**

In `tests/test_db_schema.py`:

```python
import sqlite3
from odds_scraper.db_schema import init_schema, SCHEMA_VERSION


def _cols(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_v10_adds_v3_columns(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "x.db"), isolation_level=None)
    init_schema(conn)
    cols = _cols(conn, "pricer_live_results")
    for c in (
        "v3_p_home_1", "v3_p_away_1",
        "v3_1up_home_fair", "v3_1up_home_capped",
        "v3_1up_away_fair", "v3_1up_away_capped",
        "v3_p_home_2", "v3_p_away_2",
        "v3_2up_home_fair", "v3_2up_home_capped",
        "v3_2up_away_fair", "v3_2up_away_capped",
    ):
        assert c in cols, f"missing {c}"
    assert SCHEMA_VERSION >= 10
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_db_schema.py::test_v10_adds_v3_columns -v`
Expected: FAIL (columns missing / SCHEMA_VERSION == 9).

- [ ] **Step 3: Implement the migration**

In `src/odds_scraper/db_schema.py` change `SCHEMA_VERSION = 9` to `SCHEMA_VERSION = 10`. Add this entry to `_MIGRATIONS` (after the `9:` entry, before the closing `}` on line 261):

```python
    # v10: per-tick V3 engine output beside V2. live_writer writes both
    # every tick; backfill_v3 fills existing rows. Nullable so pre-v10
    # rows keep loading until backfilled. Lambdas/basis are shared (V3's
    # lambda derivation is identical to V2's) so they are not duplicated.
    10: lambda conn: _add_columns_if_missing(conn, "pricer_live_results", [
        ("v3_p_home_1",        "REAL"),
        ("v3_p_away_1",        "REAL"),
        ("v3_1up_home_fair",   "REAL"),
        ("v3_1up_home_capped", "REAL"),
        ("v3_1up_away_fair",   "REAL"),
        ("v3_1up_away_capped", "REAL"),
        ("v3_p_home_2",        "REAL"),
        ("v3_p_away_2",        "REAL"),
        ("v3_2up_home_fair",   "REAL"),
        ("v3_2up_home_capped", "REAL"),
        ("v3_2up_away_fair",   "REAL"),
        ("v3_2up_away_capped", "REAL"),
    ]),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_db_schema.py::test_v10_adds_v3_columns -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/odds_scraper/db_schema.py tests/test_db_schema.py
git commit -m "feat(db): schema v10 adds v3_* columns to pricer_live_results"
```

---

### Task 2: `live_writer` computes + persists V3

**Files:**
- Modify: `src/odds_scraper/pricer/live_writer.py` (import line 17; `compute_and_write` body lines 40-119)
- Test: `tests/test_pricer_live_writer.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_pricer_live_writer.py` (reuse the module's existing fixtures/helpers for building `prices_by_book` and a schema-initialized `conn`; mirror an existing V2 persistence test for input construction):

```python
def test_compute_and_write_persists_v3_matching_direct_call(conn):
    from odds_scraper.pricer import engine_v3, inputs as input_extract
    prices_by_book = _sample_prices_by_book()  # existing helper in this test module
    ok = live_writer.compute_and_write(conn, "EV1", "2026-03-20T12:00:00Z", prices_by_book)
    assert ok
    row = conn.execute(
        "SELECT v3_2up_home_capped, v3_1up_home_capped, v3_p_home_2 "
        "FROM pricer_live_results WHERE event_id='EV1'"
    ).fetchone()
    # Direct engine_v3 call on the same extracted inputs.
    engine_inputs, _ = input_extract.extract(prices_by_book)
    engine_inputs["score"] = (0, 0)
    engine_inputs["max_home_lead"] = 0
    engine_inputs["max_away_lead"] = 0
    kw = {k: v for k, v in engine_inputs.items() if not k.startswith("_")}
    direct = engine_v3.price_early_payout_markets(**kw)
    assert row[0] == direct["market_2up"]["home_margin"]
    assert row[2] == direct["p_home_2"]


def test_v3_crash_still_persists_row_with_v2(conn, monkeypatch):
    def boom(**kw):
        raise RuntimeError("v3 down")
    monkeypatch.setattr(live_writer.engine_v3, "price_early_payout_markets", boom)
    ok = live_writer.compute_and_write(conn, "EV2", "2026-03-20T12:00:00Z", _sample_prices_by_book())
    assert ok  # row still written
    row = conn.execute(
        "SELECT v2_2up_home_capped, v3_2up_home_capped FROM pricer_live_results WHERE event_id='EV2'"
    ).fetchone()
    assert row[0] is not None      # V2 present
    assert row[1] is None          # V3 nulled on crash
```

> If `_sample_prices_by_book` / `conn` fixture names differ in this test module, use the module's existing equivalents — match the file's current patterns.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pricer_live_writer.py -k v3 -v`
Expected: FAIL (no `v3_*` written / `engine_v3` not imported in live_writer).

- [ ] **Step 3: Implement**

In `src/odds_scraper/pricer/live_writer.py`:

Change the import (line 17) from:
```python
from . import engine_v2, inputs as input_extract, score_state
```
to:
```python
from . import engine_v2, engine_v3, inputs as input_extract, score_state
```

After the existing V2 call block (lines 73-80), add a V3 call with the same crash contract:
```python
    try:
        res_v3 = engine_v3.price_early_payout_markets(**engine_kwargs)
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "v3 engine crashed on event=%s ts=%s — storing NULL v3 (%s)",
            event_id, ts_utc, exc,
        )
        res_v3 = None

    def _v3(path, key):
        return res_v3[path][key] if res_v3 is not None else None

    def _v3p(key):
        return res_v3[key] if res_v3 is not None else None
```

Extend the `INSERT OR REPLACE` column list (after the `v2_2up_away_capped` column on line 98) with:
```python
            ,
            v3_p_home_1, v3_p_away_1,
            v3_1up_home_fair, v3_1up_home_capped,
            v3_1up_away_fair, v3_1up_away_capped,
            v3_p_home_2, v3_p_away_2,
            v3_2up_home_fair, v3_2up_home_capped,
            v3_2up_away_fair, v3_2up_away_capped
```
and add 12 placeholders to the `VALUES (...)` list. Append these to the parameter tuple after the last V2 value (after line 116, `res_v2["market_2up"]["away_fair"], res_v2["market_2up"]["away_margin"],`):
```python
            _v3p("p_home_1"), _v3p("p_away_1"),
            _v3("market_1up", "home_fair"), _v3("market_1up", "home_margin"),
            _v3("market_1up", "away_fair"), _v3("market_1up", "away_margin"),
            _v3p("p_home_2"), _v3p("p_away_2"),
            _v3("market_2up", "home_fair"), _v3("market_2up", "home_margin"),
            _v3("market_2up", "away_fair"), _v3("market_2up", "away_margin"),
```

> Keep the SQL placeholder count in sync: the statement now binds the existing 29 params + 12 = 41. Count the `?`s.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pricer_live_writer.py -v`
Expected: PASS (all, including pre-existing V2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/odds_scraper/pricer/live_writer.py tests/test_pricer_live_writer.py
git commit -m "feat(live-writer): compute + persist engine_v3 beside v2 per tick"
```

---

### Task 3: `backfill_v3` + CLI script

**Files:**
- Modify: `src/odds_scraper/pricer/live_writer.py` (add `backfill_v3`)
- Create: `scripts/backfill_v3_live.py`
- Test: `tests/test_pricer_live_writer.py`

- [ ] **Step 1: Write the failing test**

```python
def test_backfill_v3_fills_existing_rows_and_is_idempotent(conn):
    from odds_scraper.pricer import engine_v3, inputs as input_extract
    pbb = _sample_prices_by_book()
    # Seed a row, then NULL out its v3 (simulate a pre-v10 / pre-backfill row),
    # also seed `prices` so backfill can re-extract inputs.
    _seed_prices_table(conn, "EV1", "2026-03-20T12:00:00Z", pbb)  # existing/added helper
    live_writer.compute_and_write(conn, "EV1", "2026-03-20T12:00:00Z", pbb)
    conn.execute("UPDATE pricer_live_results SET "
                 "v3_p_home_1=NULL, v3_p_away_1=NULL, v3_1up_home_fair=NULL, "
                 "v3_1up_home_capped=NULL, v3_1up_away_fair=NULL, v3_1up_away_capped=NULL, "
                 "v3_p_home_2=NULL, v3_p_away_2=NULL, v3_2up_home_fair=NULL, "
                 "v3_2up_home_capped=NULL, v3_2up_away_fair=NULL, v3_2up_away_capped=NULL")
    v2_before = conn.execute("SELECT v2_2up_home_capped FROM pricer_live_results").fetchone()[0]

    updated, skipped = live_writer.backfill_v3(conn)
    assert updated == 1
    row = conn.execute("SELECT v3_2up_home_capped, v2_2up_home_capped "
                       "FROM pricer_live_results").fetchone()
    assert row[0] is not None              # v3 filled
    assert row[1] == v2_before             # v2 untouched

    again_updated, _ = live_writer.backfill_v3(conn)
    assert again_updated == 0              # idempotent
```

> Reuse the test module's existing helper for inserting into `prices` (the backfill source). If none exists, add `_seed_prices_table` mirroring the columns `live_writer.backfill_all` reads: `bookmaker, market_id, line, side, odds, probability` plus `event_id, ts_utc`.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pricer_live_writer.py::test_backfill_v3_fills_existing_rows_and_is_idempotent -v`
Expected: FAIL (`backfill_v3` not defined).

- [ ] **Step 3: Implement `backfill_v3`**

Add to `src/odds_scraper/pricer/live_writer.py`:

```python
def backfill_v3(conn: sqlite3.Connection) -> tuple[int, int]:
    """Fill v3_* on existing pricer_live_results rows that don't have V3 yet.

    A row has V3 iff any v3_* column is non-NULL (a fully score-deactivated
    row can't happen — both sides can't be deactivated at once — so "all
    v3_* NULL" reliably means "not computed"). Re-extracts engine inputs from
    `prices` exactly as backfill_all does, runs engine_v3, and UPDATEs ONLY
    the v3_* columns. V2 values are left untouched. Idempotent.

    Returns (updated, skipped); skipped = rows whose inputs can't price.
    """
    targets = conn.execute(
        """
        SELECT r.event_id, r.ts_utc,
               MAX(s.score_home) AS sh, MAX(s.score_away) AS sa
        FROM pricer_live_results r
        JOIN snapshots s ON s.event_id = r.event_id AND s.ts_utc = r.ts_utc
        WHERE r.v3_p_home_1 IS NULL AND r.v3_p_away_1 IS NULL
          AND r.v3_1up_home_fair IS NULL AND r.v3_1up_home_capped IS NULL
          AND r.v3_1up_away_fair IS NULL AND r.v3_1up_away_capped IS NULL
          AND r.v3_p_home_2 IS NULL AND r.v3_p_away_2 IS NULL
          AND r.v3_2up_home_fair IS NULL AND r.v3_2up_home_capped IS NULL
          AND r.v3_2up_away_fair IS NULL AND r.v3_2up_away_capped IS NULL
        GROUP BY r.event_id, r.ts_utc
        ORDER BY r.event_id, r.ts_utc
        """
    ).fetchall()
    leads_by_tick = score_state.max_leads_for_events(conn, {t[0] for t in targets})

    updated = skipped = 0
    for ev_id, ts, sh, sa in targets:
        price_rows = conn.execute(
            "SELECT bookmaker, market_id, line, side, odds, probability "
            "FROM prices WHERE event_id = ? AND ts_utc = ?",
            (ev_id, ts),
        ).fetchall()
        prices_by_book: dict[str, list[dict]] = {}
        for bm, mid, line, side, odds, prob in price_rows:
            prices_by_book.setdefault(bm, []).append({
                "market_id": mid, "line": line if line is not None else 0.0,
                "side": side, "odds": odds, "probability": prob,
            })
        engine_inputs, _basis = input_extract.extract(prices_by_book)
        if engine_inputs is None:
            skipped += 1
            continue
        engine_inputs["score"] = (int(sh), int(sa)) if sh is not None and sa is not None else (0, 0)
        leads = leads_by_tick.get((ev_id, ts), (0, 0))
        engine_inputs["max_home_lead"] = leads[0]
        engine_inputs["max_away_lead"] = leads[1]
        kw = {k: v for k, v in engine_inputs.items() if not k.startswith("_")}
        try:
            r3 = engine_v3.price_early_payout_markets(**kw)
        except Exception as exc:  # noqa: BLE001
            log.warning("v3 backfill crashed event=%s ts=%s (%s)", ev_id, ts, exc)
            skipped += 1
            continue
        conn.execute(
            """
            UPDATE pricer_live_results SET
                v3_p_home_1=?, v3_p_away_1=?,
                v3_1up_home_fair=?, v3_1up_home_capped=?,
                v3_1up_away_fair=?, v3_1up_away_capped=?,
                v3_p_home_2=?, v3_p_away_2=?,
                v3_2up_home_fair=?, v3_2up_home_capped=?,
                v3_2up_away_fair=?, v3_2up_away_capped=?
            WHERE event_id=? AND ts_utc=?
            """,
            (
                r3["p_home_1"], r3["p_away_1"],
                r3["market_1up"]["home_fair"], r3["market_1up"]["home_margin"],
                r3["market_1up"]["away_fair"], r3["market_1up"]["away_margin"],
                r3["p_home_2"], r3["p_away_2"],
                r3["market_2up"]["home_fair"], r3["market_2up"]["home_margin"],
                r3["market_2up"]["away_fair"], r3["market_2up"]["away_margin"],
                ev_id, ts,
            ),
        )
        updated += 1
    return updated, skipped
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pricer_live_writer.py::test_backfill_v3_fills_existing_rows_and_is_idempotent -v`
Expected: PASS.

- [ ] **Step 5: Create the CLI script**

`scripts/backfill_v3_live.py`:

```python
"""One-shot: backfill v3_* on existing pricer_live_results rows.

Run once after deploying schema v10. Idempotent — re-running fills only
rows that still lack V3. Reads the DB path from the same place the app does.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from odds_scraper.db_schema import init_schema
from odds_scraper.pricer import live_writer


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="path to the odds SQLite DB")
    args = ap.parse_args()
    conn = sqlite3.connect(args.db, isolation_level=None)
    init_schema(conn)  # ensure v10 applied
    updated, skipped = live_writer.backfill_v3(conn)
    print(f"v3 backfill: updated {updated}, skipped {skipped}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Commit**

```bash
git add src/odds_scraper/pricer/live_writer.py scripts/backfill_v3_live.py tests/test_pricer_live_writer.py
git commit -m "feat(live-writer): backfill_v3 (UPDATE-only) + CLI script"
```

---

### Task 4: history query returns V3 beside V2

**Files:**
- Modify: `src/odds_scraper/web/queries.py` (`get_our_history_for_event` lines 287-326)
- Test: `tests/test_web_queries.py`

- [ ] **Step 1: Write the failing test**

```python
def test_get_our_history_returns_v3_beside_v2(conn):
    # Seed a pricer_live_results row with distinct v2 and v3 values.
    conn.execute(
        "INSERT INTO pricer_live_results (event_id, ts_utc, basis_used, "
        "v2_2up_home_capped, v2_2up_away_capped, v2_p_home_2, v2_p_away_2, "
        "v3_2up_home_capped, v3_2up_away_capped, v3_p_home_2, v3_p_away_2) "
        "VALUES ('E','T','betpawa', 2.0, 3.0, 0.5, 0.3, 2.2, 3.3, 0.45, 0.28)"
    )
    out = queries.get_our_history_for_event(conn, "E", "1x2_2up_ft")
    assert out["T"]["home_odds"] == 2.0      # v2 unchanged
    assert out["T"]["home_odds_v3"] == 2.2   # v3 added
    assert out["T"]["away_prob_v3"] == 0.28
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_queries.py::test_get_our_history_returns_v3_beside_v2 -v`
Expected: FAIL (`home_odds_v3` KeyError).

- [ ] **Step 3: Implement**

In `get_our_history_for_event`, add the V3 column names per branch and extend the SELECT + returned dict. Replace the body (lines 300-326) with:

```python
    if market_id == "1x2_1up_ft":
        odds_h, odds_a = "our_1up_home_capped", "our_1up_away_capped"
        prob_h, prob_a = "our_p_home_1", "our_p_away_1"
        v2_odds_h, v2_odds_a = "v2_1up_home_capped", "v2_1up_away_capped"
        v2_prob_h, v2_prob_a = "v2_p_home_1", "v2_p_away_1"
        v3_odds_h, v3_odds_a = "v3_1up_home_capped", "v3_1up_away_capped"
        v3_prob_h, v3_prob_a = "v3_p_home_1", "v3_p_away_1"
    elif market_id == "1x2_2up_ft":
        odds_h, odds_a = "our_2up_home_capped", "our_2up_away_capped"
        prob_h, prob_a = "our_p_home_2", "our_p_away_2"
        v2_odds_h, v2_odds_a = "v2_2up_home_capped", "v2_2up_away_capped"
        v2_prob_h, v2_prob_a = "v2_p_home_2", "v2_p_away_2"
        v3_odds_h, v3_odds_a = "v3_2up_home_capped", "v3_2up_away_capped"
        v3_prob_h, v3_prob_a = "v3_p_home_2", "v3_p_away_2"
    else:
        return {}
    rows = conn.execute(
        f"SELECT ts_utc, {odds_h}, {odds_a}, {prob_h}, {prob_a}, "
        f"       {v2_odds_h}, {v2_odds_a}, {v2_prob_h}, {v2_prob_a}, "
        f"       {v3_odds_h}, {v3_odds_a}, {v3_prob_h}, {v3_prob_a} "
        f"FROM pricer_live_results WHERE event_id = ?",
        (event_id,),
    ).fetchall()
    return {
        r["ts_utc"]: {
            "home_odds": r[v2_odds_h] if r[v2_odds_h] is not None else r[odds_h],
            "away_odds": r[v2_odds_a] if r[v2_odds_a] is not None else r[odds_a],
            "home_prob": r[v2_prob_h] if r[v2_prob_h] is not None else r[prob_h],
            "away_prob": r[v2_prob_a] if r[v2_prob_a] is not None else r[prob_a],
            "home_odds_v3": r[v3_odds_h],
            "away_odds_v3": r[v3_odds_a],
            "home_prob_v3": r[v3_prob_h],
            "away_prob_v3": r[v3_prob_a],
        }
        for r in rows
    }
```

Also update the docstring's "Returned shape" to include the `_v3` keys.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_queries.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add src/odds_scraper/web/queries.py tests/test_web_queries.py
git commit -m "feat(web/queries): history query returns v3 beside v2"
```

---

### Task 5: detail-page UI — `V2 | V3` columns

**Files:**
- Modify: `src/odds_scraper/web/app.py` (`_build_event_detail`, the OUR-merge block lines 500-517)
- Modify: `src/odds_scraper/web/templates/event_detail.html` (lines 71-72, 108)
- Test: `tests/test_web_app.py`

- [ ] **Step 1: Write the failing test**

```python
def test_event_detail_page_shows_v2_and_v3_columns(client, seed_live_v2_v3):
    # seed_live_v2_v3: fixture/helper that inserts an event + a 2UP
    # pricer_live_results row with both v2_* and v3_* set (reuse patterns
    # from existing detail-page tests in this module).
    r = client.get(f"/events/{seed_live_v2_v3}?market=1x2_2up_ft")
    assert r.status_code == 200
    body = r.text
    assert ">V2<" in body and ">V3<" in body          # both column headers
    assert 'data-bookmaker="sim_v3"' in body          # v3 pseudo-book column
```

> If existing detail tests already seed a V2 live row, extend that helper to also set the `v3_*` columns rather than adding a new fixture.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_app.py::test_event_detail_page_shows_v2_and_v3_columns -v`
Expected: FAIL (no `sim_v3` column / no `>V3<` header).

- [ ] **Step 3: Implement the builder change**

In `src/odds_scraper/web/app.py`, replace the OUR-merge loop + column list (lines 500-517) with:

```python
    for ts, our in our_by_ts.items():
        sim_cells: dict[str, PriceCell] = {}
        if our["home_odds"] is not None:
            sim_cells["home"] = PriceCell(odds=our["home_odds"], probability=our["home_prob"])
        if our["away_odds"] is not None:
            sim_cells["away"] = PriceCell(odds=our["away_odds"], probability=our["away_prob"])
        if sim_cells and ts in bucket:
            bucket[ts]["cells"]["sim"] = sim_cells
        sim_cells_v3: dict[str, PriceCell] = {}
        if our["home_odds_v3"] is not None:
            sim_cells_v3["home"] = PriceCell(odds=our["home_odds_v3"], probability=our["home_prob_v3"])
        if our["away_odds_v3"] is not None:
            sim_cells_v3["away"] = PriceCell(odds=our["away_odds_v3"], probability=our["away_prob_v3"])
        if sim_cells_v3 and ts in bucket:
            bucket[ts]["cells"]["sim_v3"] = sim_cells_v3

    show_sim_col = bool(our_by_ts) and market_id in ("1x2_1up_ft", "1x2_2up_ft")
    if show_sim_col:
        history_books = ("betpawa", "sportybet", "sim", "sim_v3", "bet9ja", "betway")
    else:
        history_books = ("betpawa", "sportybet", "bet9ja", "betway")
```

- [ ] **Step 4: Implement the template change**

In `src/odds_scraper/web/templates/event_detail.html`:

Line 71-72 — extend the label map and sim-book set:
```jinja
      {% set book_label = {"betpawa":"BetPawa","sportybet":"SportyBet","bet9ja":"Bet9ja","betway":"Betway","sim":"V2","sim_v3":"V3"} %}
      {% set sim_books = ("sim", "sim_v3") %}
```

Line 108 — include `sim_v3` in the prob-rendering set:
```jinja
                      {% if bm in ("betpawa", "sportybet", "sim", "sim_v3") and p.probability is not none %}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_app.py -v`
Expected: PASS (all).

- [ ] **Step 6: Commit**

```bash
git add src/odds_scraper/web/app.py src/odds_scraper/web/templates/event_detail.html tests/test_web_app.py
git commit -m "feat(web/detail): show V3 OUR column beside V2 in history"
```

---

### Task 6: home-card UI — V3 stacked under V2 in SIM cell

**Files:**
- Modify: `src/odds_scraper/web/app.py` (`EventView` dataclass lines 140-148; `_build_event_view` lines 405-445)
- Modify: `src/odds_scraper/web/templates/_event_card.html` (SIM cell lines 97-103; macro `our_value` block lines 50-76)
- Test: `tests/test_web_app.py`

- [ ] **Step 1: Write the failing test**

```python
def test_home_card_shows_v3_under_v2_in_sim(client, seed_live_event_with_up_quote):
    # Fixture seeds an event whose latest snapshot has BP-quoted 2UP + the
    # full 1X2/OU/FTTS inputs so both engines price. Reuse the existing
    # home-card SIM test's fixture if present.
    r = client.get("/")
    assert r.status_code == 200
    assert 'data-bookmaker="sim_v3"' in r.text   # V3 sub-cell rendered in the card
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_app.py::test_home_card_shows_v3_under_v2_in_sim -v`
Expected: FAIL.

- [ ] **Step 3: Extend `EventView`**

In `src/odds_scraper/web/app.py`, after `our_p_2up_away` (line 148) add:
```python
    # OUR-engine output for the V3 sub-cell (logit-margin engine).
    our_v3_1up_home: Optional[float]
    our_v3_1up_away: Optional[float]
    our_v3_2up_home: Optional[float]
    our_v3_2up_away: Optional[float]
    our_v3_p_1up_home: Optional[float]
    our_v3_p_1up_away: Optional[float]
    our_v3_p_2up_home: Optional[float]
    our_v3_p_2up_away: Optional[float]
```

- [ ] **Step 4: Compute V3 in `_build_event_view`**

Add `engine_v3` to the app's imports (alongside `engine_v2`). In `_build_event_view`, inside the `if engine_inputs is not None:` block after the V2 `try/except` (after line 426), add:
```python
        our_v3_1up_home = our_v3_1up_away = our_v3_2up_home = our_v3_2up_away = None
        our_v3_p_1up_home = our_v3_p_1up_away = our_v3_p_2up_home = our_v3_p_2up_away = None
        try:
            r3 = engine_v3.price_early_payout_markets(**engine_kwargs)
            our_v3_1up_home = r3["market_1up"]["home_margin"]
            our_v3_1up_away = r3["market_1up"]["away_margin"]
            our_v3_2up_home = r3["market_2up"]["home_margin"]
            our_v3_2up_away = r3["market_2up"]["away_margin"]
            our_v3_p_1up_home = r3["p_home_1"]
            our_v3_p_1up_away = r3["p_away_1"]
            our_v3_p_2up_home = r3["p_home_2"]
            our_v3_p_2up_away = r3["p_away_2"]
        except Exception:  # noqa: BLE001
            pass
```

Initialize the eight `our_v3_*` names to `None` next to the V2 initializers (line 406-407) so they're defined when `engine_inputs is None`:
```python
    our_v3_1up_home = our_v3_1up_away = our_v3_2up_home = our_v3_2up_away = None
    our_v3_p_1up_home = our_v3_p_1up_away = our_v3_p_2up_home = our_v3_p_2up_away = None
```
(Remove the duplicate initialization inside the `try` block — keep the single set before the `if`.)

Pass them into the `EventView(...)` constructor (after the `our_p_2up_away=...` line 443):
```python
        our_v3_1up_home=our_v3_1up_home, our_v3_1up_away=our_v3_1up_away,
        our_v3_2up_home=our_v3_2up_home, our_v3_2up_away=our_v3_2up_away,
        our_v3_p_1up_home=our_v3_p_1up_home, our_v3_p_1up_away=our_v3_p_1up_away,
        our_v3_p_2up_home=our_v3_p_2up_home, our_v3_p_2up_away=our_v3_p_2up_away,
```

- [ ] **Step 5: Template — set V3 values + render V3 under V2**

In `src/odds_scraper/web/templates/_event_card.html`, inside `render_group` extend the per-row value selection (after line 75, mirroring the V2 `our_value` logic) to also set `our_v3_value` / `our_v3_prob`:
```jinja
          {% set our_v3_value = none %}
          {% set our_v3_prob = none %}
          {% if is_up_row %}
            {% if group.group_key == '1x2_1up_ft' %}
              {% if row.side_short == 'H' %}{% set our_v3_value = event.our_v3_1up_home %}{% set our_v3_prob = event.our_v3_p_1up_home %}{% endif %}
              {% if row.side_short == 'A' %}{% set our_v3_value = event.our_v3_1up_away %}{% set our_v3_prob = event.our_v3_p_1up_away %}{% endif %}
            {% else %}
              {% if row.side_short == 'H' %}{% set our_v3_value = event.our_v3_2up_home %}{% set our_v3_prob = event.our_v3_p_2up_home %}{% endif %}
              {% if row.side_short == 'A' %}{% set our_v3_value = event.our_v3_2up_away %}{% set our_v3_prob = event.our_v3_p_2up_away %}{% endif %}
            {% endif %}
          {% endif %}
```

Replace the SIM cell (lines 97-103) so V2 and V3 stack with tiny labels:
```jinja
            {# SIM column: V2 then V3 stacked, for UP rows when BP quoted. #}
            <span data-bookmaker="sim">
              {% if is_up_row and bp_has_quote and our_value is not none %}
                <span class="sim-sub" data-bookmaker="sim">
                  <span class="sim-tag">V2</span>
                  <span class="odds sim">{{ "%.2f"|format(our_value) }}</span>
                  {% if our_prob is not none %}<span class="prob">.{{ "%02d"|format([(our_prob * 100)|round|int, 99]|min) }}</span>{% endif %}
                </span>
                {% if our_v3_value is not none %}
                <span class="sim-sub" data-bookmaker="sim_v3">
                  <span class="sim-tag">V3</span>
                  <span class="odds sim">{{ "%.2f"|format(our_v3_value) }}</span>
                  {% if our_v3_prob is not none %}<span class="prob">.{{ "%02d"|format([(our_v3_prob * 100)|round|int, 99]|min) }}</span>{% endif %}
                </span>
                {% endif %}
              {% else %}<span class="text-gray-700">—</span>{% endif %}
            </span>
```

Add minimal CSS for the stacked sub-cells (in the card's existing `<style>` block, or the shared stylesheet): `.sim-sub{display:block;}` `.sim-tag{font-size:9px;color:#6b7280;margin-right:3px;}`.

> The card header stays one "SIM" column (line 39 unchanged) — the V2/V3 split is inside that cell, so no `.card-grid` column-count/CSS change is needed.

- [ ] **Step 6: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_app.py -v`
Expected: PASS (all).

- [ ] **Step 7: Commit**

```bash
git add src/odds_scraper/web/app.py src/odds_scraper/web/templates/_event_card.html tests/test_web_app.py
git commit -m "feat(web/card): show V3 OUR under V2 in the SIM cell"
```

---

### Task 7: Full suite + manual UI check

- [ ] **Step 1:** Run the whole suite: `.venv\Scripts\python.exe -m pytest -q` — expect all green.
- [ ] **Step 2:** Start the web app, open the home page and an event-detail page for a live event with 1UP/2UP, and confirm V3 renders beside V2 (card SIM cell shows V2+V3; detail history shows V2 and V3 columns). If the UI can't be exercised, say so explicitly.
- [ ] **Step 3:** (Deploy step, run by the user) After deploy applies schema v10, run `python scripts/backfill_v3_live.py --db <path>` once and confirm `updated > 0`.

---

## Notes for the executor

- V3 uses `engine_v3` module defaults — do NOT thread a profile/config into the live path.
- Never let a V3 failure drop a tick or change V2: V3 is always wrapped so a crash stores `NULL` V3.
- Keep `engine_v2.py` and `engine.py` untouched.
- Match existing test-module fixtures/helpers (`conn`, `client`, price-seeding helpers) rather than inventing new ones; the snippets above name them generically.
