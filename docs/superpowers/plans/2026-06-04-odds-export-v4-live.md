# Odds CSV Export + V4 Live Pipeline — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the live pipeline to V3 + V4 only (clean UI, non-destructive), then add a `/export` page that streams filtered raw scraped odds (+ optional stored V3/V4 sim prices) to CSV.

**Architecture:** Phase 1 adds `v4_*` columns to `pricer_live_results` (additive migration), makes `live_writer` compute V3 (primary) + V4 (best-effort), backfills V4, and strips V1/V2 from the whole UI. Phase 2 adds a self-contained `export_service` (pure, TDD'd) + `export_routes` streaming CSV; export reads stored values only, never re-prices.

**Tech Stack:** Python 3, FastAPI, Jinja2, SQLite (read-only conn for web), pytest.

**Spec:** `docs/superpowers/specs/2026-06-04-odds-export-and-v4-live-design.md`
**Branch:** `feat/odds-export-v4-live` (already created)

**Engine contract (confirmed):** `engine_v3` and `engine_v4` both expose
`price_early_payout_markets(**kwargs)` returning
`{"lambda_home","lambda_away","p_home_1","p_away_1","p_home_2","p_away_2",
"market_1up":{"home_fair","home_margin","away_fair","away_margin"},
"market_2up":{...}}`. `*_margin` is the capped odds; `*_fair` the fair odds.

**Test command (Windows):** `python -m pytest <path> -v`

---

# PHASE 1 — V3 + V4 in the live pipeline

### Task 1: Schema migration v11 — add `v4_*` columns

**Files:**
- Modify: `src/odds_scraper/db_schema.py` (the `_MIGRATIONS` dict, after key `10`)
- Test: `tests/test_db_schema.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_db_schema.py`:

```python
def test_schema_v11_adds_v4_columns(tmp_path):
    import sqlite3
    from odds_scraper.db_schema import init_schema, SCHEMA_VERSION
    conn = sqlite3.connect(str(tmp_path / "x.db"), isolation_level=None)
    init_schema(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(pricer_live_results)")}
    for c in (
        "v4_p_home_1", "v4_p_away_1",
        "v4_1up_home_fair", "v4_1up_home_capped",
        "v4_1up_away_fair", "v4_1up_away_capped",
        "v4_p_home_2", "v4_p_away_2",
        "v4_2up_home_fair", "v4_2up_home_capped",
        "v4_2up_away_fair", "v4_2up_away_capped",
    ):
        assert c in cols, f"missing {c}"
    assert SCHEMA_VERSION >= 11
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_db_schema.py::test_schema_v11_adds_v4_columns -v`
Expected: FAIL (columns missing / SCHEMA_VERSION < 11).

- [ ] **Step 3: Add migration 11**

In `src/odds_scraper/db_schema.py`, inside `_MIGRATIONS`, immediately after the `10:` entry, add:

```python
    # v11: per-tick V4 engine output beside V3. live_writer writes V3 + V4
    # every tick (V2 retired from the live pipeline; its columns remain but
    # are no longer written). Nullable so pre-v11 rows keep loading until
    # backfill_v4 fills them.
    11: lambda conn: _add_columns_if_missing(conn, "pricer_live_results", [
        ("v4_p_home_1",        "REAL"),
        ("v4_p_away_1",        "REAL"),
        ("v4_1up_home_fair",   "REAL"),
        ("v4_1up_home_capped", "REAL"),
        ("v4_1up_away_fair",   "REAL"),
        ("v4_1up_away_capped", "REAL"),
        ("v4_p_home_2",        "REAL"),
        ("v4_p_away_2",        "REAL"),
        ("v4_2up_home_fair",   "REAL"),
        ("v4_2up_home_capped", "REAL"),
        ("v4_2up_away_fair",   "REAL"),
        ("v4_2up_away_capped", "REAL"),
    ]),
```

`SCHEMA_VERSION` is derived from `max(_MIGRATIONS)` — confirm by reading the
constant near the dict; if it's a hardcoded literal, bump it to `11`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_db_schema.py::test_schema_v11_adds_v4_columns -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/odds_scraper/db_schema.py tests/test_db_schema.py
git commit -m "feat(schema): v11 adds v4_* columns to pricer_live_results"
```

---

### Task 2: `live_writer` computes V3 (primary) + V4 (best-effort), drops V2

**Files:**
- Modify: `src/odds_scraper/pricer/live_writer.py`
- Test: `tests/test_pricer_live_writer.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_pricer_live_writer.py` (reuses the module's existing
`_tick_snapshot` helper):

```python
def test_live_writer_persists_v3_v4_not_v2(tmp_path):
    import sqlite3
    from odds_scraper.db_schema import init_schema
    from odds_scraper.models import Bookmaker
    from odds_scraper.pricer import live_writer
    conn = sqlite3.connect(str(tmp_path / "v4.db"), isolation_level=None)
    init_schema(conn); conn.row_factory = sqlite3.Row
    rows = [
        _tick_snapshot(Bookmaker.BETPAWA, with_prob=True),
        _tick_snapshot(Bookmaker.SPORTYBET, with_prob=False),
    ]
    assert live_writer.compute_and_write_from_snapshots(
        conn, "E1", "2026-05-22T18:30:00Z", rows, (0, 0))
    row = conn.execute(
        "SELECT v2_p_home_1, v3_p_home_1, v4_p_home_1, "
        "       v3_1up_home_capped, v4_1up_home_capped "
        "FROM pricer_live_results WHERE event_id='E1'").fetchone()
    assert row["v2_p_home_1"] is None     # V2 retired
    assert row["v3_p_home_1"] is not None  # V3 primary
    assert row["v4_p_home_1"] is not None  # V4 computed
    assert row["v3_1up_home_capped"] is not None
    assert row["v4_1up_home_capped"] is not None
    conn.close()


def test_live_writer_v4_crash_keeps_row_with_v3(tmp_path, monkeypatch):
    import sqlite3
    from odds_scraper.db_schema import init_schema
    from odds_scraper.models import Bookmaker
    from odds_scraper.pricer import live_writer
    conn = sqlite3.connect(str(tmp_path / "v4crash.db"), isolation_level=None)
    init_schema(conn); conn.row_factory = sqlite3.Row
    monkeypatch.setattr(live_writer.engine_v4, "price_early_payout_markets",
                        lambda **kw: (_ for _ in ()).throw(RuntimeError("v4 down")))
    rows = [
        _tick_snapshot(Bookmaker.BETPAWA, with_prob=True),
        _tick_snapshot(Bookmaker.SPORTYBET, with_prob=False),
    ]
    assert live_writer.compute_and_write_from_snapshots(
        conn, "E2", "2026-05-22T18:30:00Z", rows, (0, 0))   # row still written
    row = conn.execute(
        "SELECT v3_2up_home_capped, v4_2up_home_capped "
        "FROM pricer_live_results WHERE event_id='E2'").fetchone()
    assert row["v3_2up_home_capped"] is not None  # V3 present
    assert row["v4_2up_home_capped"] is None      # V4 nulled on crash
    conn.close()
```

Also UPDATE the existing V2-asserting tests in this file to the new reality:
- `test_compute_and_write_inserts_row`: change the `SELECT` to
  `v3_p_home_1, v3_1up_home_capped, v4_p_home_1` and assert all three non-None;
  drop the `v2_p_home_1`/`v2_1up_home_capped` non-None asserts (now NULL).
- `test_live_writer_persists_v2_columns`: rename to
  `test_live_writer_v2_columns_now_null`; assert `v2_p_home_1 is None` and
  `v3_p_home_1 is not None`.
- `test_live_writer_persists_v3_matching_direct_call`: unchanged (still valid).
- `test_v3_crash_still_persists_row_with_v2`: rename to
  `test_v3_crash_skips_tick` and assert `compute_and_write_from_snapshots(...)`
  returns `False` and no row exists (V3 is now the must-succeed primary).
- `test_live_writer_v2_trailing_produces_output`: change the final asserts to
  read `v3_1up_away_capped`/`v4_1up_away_capped` (both non-None);
  `v2_1up_away_capped` is now NULL.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_pricer_live_writer.py -v`
Expected: the new tests FAIL (V4 not written; V3 not primary).

- [ ] **Step 3: Rewrite `compute_and_write`**

In `src/odds_scraper/pricer/live_writer.py`:

Change the import line:
```python
from . import engine_v3, engine_v4, inputs as input_extract, score_state
```

Replace the body from the `res_v2 = ...` block through the `conn.execute(...INSERT...)`
call (currently lines ~73–148) with:

```python
    # V3 is the must-succeed primary: it supplies the shared basis/lambda and
    # the v3_* block. A V3 crash drops the tick (returns False) — same policy
    # V2 had before it was retired.
    try:
        res_v3 = engine_v3.price_early_payout_markets(**engine_kwargs)
    except Exception as exc:  # noqa: BLE001
        log.warning("v3 engine crashed on event=%s ts=%s — skipping (%s)",
                    event_id, ts_utc, exc)
        return False

    # V4 is best-effort: a crash stores NULL v4 and never drops the tick.
    try:
        res_v4 = engine_v4.price_early_payout_markets(**engine_kwargs)
    except Exception as exc:  # noqa: BLE001
        log.warning("v4 engine crashed on event=%s ts=%s — storing NULL v4 (%s)",
                    event_id, ts_utc, exc)
        res_v4 = None

    def _v4(market, key):
        return res_v4[market][key] if res_v4 is not None else None

    def _v4p(key):
        return res_v4[key] if res_v4 is not None else None

    conn.execute(
        """
        INSERT OR REPLACE INTO pricer_live_results (
            event_id, ts_utc, basis_used,
            lambda_home, lambda_away,
            v3_p_home_1, v3_p_away_1,
            v3_1up_home_fair, v3_1up_home_capped,
            v3_1up_away_fair, v3_1up_away_capped,
            v3_p_home_2, v3_p_away_2,
            v3_2up_home_fair, v3_2up_home_capped,
            v3_2up_away_fair, v3_2up_away_capped,
            v4_p_home_1, v4_p_away_1,
            v4_1up_home_fair, v4_1up_home_capped,
            v4_1up_away_fair, v4_1up_away_capped,
            v4_p_home_2, v4_p_away_2,
            v4_2up_home_fair, v4_2up_home_capped,
            v4_2up_away_fair, v4_2up_away_capped
        ) VALUES (?, ?, ?, ?, ?,
                  ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                  ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id, ts_utc, basis,
            res_v3["lambda_home"], res_v3["lambda_away"],
            res_v3["p_home_1"], res_v3["p_away_1"],
            res_v3["market_1up"]["home_fair"],   res_v3["market_1up"]["home_margin"],
            res_v3["market_1up"]["away_fair"],   res_v3["market_1up"]["away_margin"],
            res_v3["p_home_2"], res_v3["p_away_2"],
            res_v3["market_2up"]["home_fair"],   res_v3["market_2up"]["home_margin"],
            res_v3["market_2up"]["away_fair"],   res_v3["market_2up"]["away_margin"],
            _v4p("p_home_1"), _v4p("p_away_1"),
            _v4("market_1up", "home_fair"),   _v4("market_1up", "home_margin"),
            _v4("market_1up", "away_fair"),   _v4("market_1up", "away_margin"),
            _v4p("p_home_2"), _v4p("p_away_2"),
            _v4("market_2up", "home_fair"),   _v4("market_2up", "home_margin"),
            _v4("market_2up", "away_fair"),   _v4("market_2up", "away_margin"),
        ),
    )
    return True
```

(The `our_*` and `v2_*` columns are simply omitted from the INSERT — they default
to NULL on the new row.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_pricer_live_writer.py -v`
Expected: PASS (the rewritten + new tests).

- [ ] **Step 5: Commit**

```bash
git add src/odds_scraper/pricer/live_writer.py tests/test_pricer_live_writer.py
git commit -m "feat(live): compute V3 (primary) + V4, retire V2 from live pipeline"
```

---

### Task 3: `backfill_all` → V3+V4; add `backfill_v4`

**Files:**
- Modify: `src/odds_scraper/pricer/live_writer.py`
- Test: `tests/test_pricer_live_writer.py`

- [ ] **Step 1: Write the failing test**

```python
def test_backfill_v4_fills_existing_rows_and_is_idempotent(tmp_path):
    import asyncio, sqlite3
    from odds_scraper.models import Bookmaker
    from odds_scraper.pricer import live_writer
    from odds_scraper.writer import SqliteWriter
    db = tmp_path / "odds.db"
    rows = [_tick_snapshot(b, with_prob=(b == Bookmaker.BETPAWA), event_id="EV1")
            for b in Bookmaker]

    async def seed():
        async with SqliteWriter(db) as w:
            await w.append(rows)
    asyncio.run(seed())

    conn = sqlite3.connect(str(db), isolation_level=None)
    conn.row_factory = sqlite3.Row
    assert live_writer.backfill_all(conn)[0] == 1          # writes v3 + v4
    conn.execute(
        "UPDATE pricer_live_results SET "
        "v4_p_home_1=NULL, v4_p_away_1=NULL, v4_1up_home_fair=NULL, "
        "v4_1up_home_capped=NULL, v4_1up_away_fair=NULL, v4_1up_away_capped=NULL, "
        "v4_p_home_2=NULL, v4_p_away_2=NULL, v4_2up_home_fair=NULL, "
        "v4_2up_home_capped=NULL, v4_2up_away_fair=NULL, v4_2up_away_capped=NULL")
    v3_before = conn.execute(
        "SELECT v3_2up_home_capped FROM pricer_live_results").fetchone()[0]
    updated, _ = live_writer.backfill_v4(conn)
    assert updated == 1
    row = conn.execute(
        "SELECT v4_2up_home_capped, v3_2up_home_capped "
        "FROM pricer_live_results").fetchone()
    assert row["v4_2up_home_capped"] is not None
    assert row["v3_2up_home_capped"] == v3_before          # v3 untouched
    assert live_writer.backfill_v4(conn)[0] == 0           # idempotent
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_pricer_live_writer.py::test_backfill_v4_fills_existing_rows_and_is_idempotent -v`
Expected: FAIL (`backfill_v4` undefined).

- [ ] **Step 3: Add `backfill_v4` and confirm `backfill_all` works via `compute_and_write`**

`backfill_all` already delegates to `compute_and_write` (rewritten in Task 2), so it
now writes V3+V4 automatically — no change needed there. Append `backfill_v4` to
`live_writer.py`, mirroring `backfill_v3` but targeting `v4_*` and calling
`engine_v4`:

```python
def backfill_v4(conn: sqlite3.Connection) -> tuple[int, int]:
    """Fill v4_* on existing pricer_live_results rows that lack it. Mirrors
    backfill_v3: re-extracts inputs from `prices`, runs engine_v4, UPDATEs only
    v4_* columns. V3 values untouched. Idempotent. Returns (updated, skipped)."""
    targets = conn.execute(
        """
        SELECT r.event_id, r.ts_utc,
               MAX(s.score_home) AS sh, MAX(s.score_away) AS sa
        FROM pricer_live_results r
        JOIN snapshots s ON s.event_id = r.event_id AND s.ts_utc = r.ts_utc
        WHERE r.v4_p_home_1 IS NULL AND r.v4_p_away_1 IS NULL
          AND r.v4_1up_home_fair IS NULL AND r.v4_1up_home_capped IS NULL
          AND r.v4_1up_away_fair IS NULL AND r.v4_1up_away_capped IS NULL
          AND r.v4_p_home_2 IS NULL AND r.v4_p_away_2 IS NULL
          AND r.v4_2up_home_fair IS NULL AND r.v4_2up_home_capped IS NULL
          AND r.v4_2up_away_fair IS NULL AND r.v4_2up_away_capped IS NULL
        GROUP BY r.event_id, r.ts_utc
        ORDER BY r.event_id, r.ts_utc
        """
    ).fetchall()
    leads_by_tick = score_state.max_leads_for_events(conn, {t[0] for t in targets})
    updated = 0
    skipped = 0
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
        score = (int(sh), int(sa)) if sh is not None and sa is not None else (0, 0)
        engine_inputs["score"] = score
        leads = leads_by_tick.get((ev_id, ts), (0, 0))
        engine_inputs["max_home_lead"] = leads[0]
        engine_inputs["max_away_lead"] = leads[1]
        kw = {k: v for k, v in engine_inputs.items() if not k.startswith("_")}
        try:
            r4 = engine_v4.price_early_payout_markets(**kw)
        except Exception as exc:  # noqa: BLE001
            log.warning("v4 backfill crashed event=%s ts=%s (%s)", ev_id, ts, exc)
            skipped += 1
            continue
        conn.execute(
            """
            UPDATE pricer_live_results SET
                v4_p_home_1=?, v4_p_away_1=?,
                v4_1up_home_fair=?, v4_1up_home_capped=?,
                v4_1up_away_fair=?, v4_1up_away_capped=?,
                v4_p_home_2=?, v4_p_away_2=?,
                v4_2up_home_fair=?, v4_2up_home_capped=?,
                v4_2up_away_fair=?, v4_2up_away_capped=?
            WHERE event_id=? AND ts_utc=?
            """,
            (
                r4["p_home_1"], r4["p_away_1"],
                r4["market_1up"]["home_fair"], r4["market_1up"]["home_margin"],
                r4["market_1up"]["away_fair"], r4["market_1up"]["away_margin"],
                r4["p_home_2"], r4["p_away_2"],
                r4["market_2up"]["home_fair"], r4["market_2up"]["home_margin"],
                r4["market_2up"]["away_fair"], r4["market_2up"]["away_margin"],
                ev_id, ts,
            ),
        )
        updated += 1
    return updated, skipped
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_pricer_live_writer.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/odds_scraper/pricer/live_writer.py tests/test_pricer_live_writer.py
git commit -m "feat(live): add backfill_v4 (mirrors backfill_v3)"
```

---

### Task 4: `backfill_v4_live.py` runnable script

**Files:**
- Create: `scripts/backfill_v4_live.py` (copy the shape of `scripts/backfill_v3_live.py`)

- [ ] **Step 1: Read the existing V3 script**

Run: `python -c "print(open('scripts/backfill_v3_live.py').read())"`
Note its CLI (argparse db path), schema-init call, and how it prints results.

- [ ] **Step 2: Create the V4 script**

Create `scripts/backfill_v4_live.py` identical to `backfill_v3_live.py` except it
calls `live_writer.backfill_v4(conn)` and prints "V4" in its messages. Keep the same
argparse arguments and the `init_schema(conn)` call (so the v11 migration runs
before backfill).

- [ ] **Step 3: Smoke-run against a throwaway DB**

Run: `python scripts/backfill_v4_live.py --help`
Expected: usage text prints, exit 0.

- [ ] **Step 4: Commit**

```bash
git add scripts/backfill_v4_live.py
git commit -m "feat(live): backfill_v4_live.py one-shot script"
```

---

### Task 5: `get_our_history_for_event` returns V3 + V4

**Files:**
- Modify: `src/odds_scraper/web/queries.py:287-338`
- Test: `tests/test_web_queries.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_web_queries.py` (follow the file's existing in-memory seed
helpers; if it seeds via a helper, reuse it — otherwise insert a
`pricer_live_results` row directly):

```python
def test_get_our_history_returns_v3_and_v4(tmp_path):
    import sqlite3
    from odds_scraper.db_schema import init_schema
    from odds_scraper.web import queries
    conn = sqlite3.connect(str(tmp_path / "q.db"), isolation_level=None)
    init_schema(conn); conn.row_factory = sqlite3.Row
    conn.execute(
        "INSERT INTO pricer_live_results (event_id, ts_utc, basis_used, "
        "v3_1up_home_capped, v3_1up_away_capped, v3_p_home_1, v3_p_away_1, "
        "v4_1up_home_capped, v4_1up_away_capped, v4_p_home_1, v4_p_away_1) "
        "VALUES ('E1','2026-05-22T18:30:00Z','bp', 2.1,3.2,0.5,0.3, 2.0,3.0,0.52,0.31)")
    out = queries.get_our_history_for_event(conn, "E1", "1x2_1up_ft")
    row = out["2026-05-22T18:30:00Z"]
    assert row["home_odds_v3"] == 2.1 and row["away_odds_v3"] == 3.2
    assert row["home_prob_v3"] == 0.5
    assert row["home_odds_v4"] == 2.0 and row["away_odds_v4"] == 3.0
    assert row["home_prob_v4"] == 0.52
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_web_queries.py::test_get_our_history_returns_v3_and_v4 -v`
Expected: FAIL (KeyError on `home_odds_v3`/`home_odds_v4` — current shape uses
`home_odds`/`home_odds_v3`).

- [ ] **Step 3: Rewrite `get_our_history_for_event`**

Replace the function body (`src/odds_scraper/web/queries.py:287-338`) with a
V3+V4 version:

```python
def get_our_history_for_event(
    conn: sqlite3.Connection, event_id: str, market_id: str,
) -> dict[str, dict[str, float | None]]:
    """OUR engine output per tick for a 1UP/2UP market, V3 + V4.

    Shape: {ts_utc: {
        "home_odds_v3","away_odds_v3","home_prob_v3","away_prob_v3",
        "home_odds_v4","away_odds_v4","home_prob_v4","away_prob_v4"}}.
    Empty dict if not a UP market or no rows. (V1/V2 retired from the live
    pipeline; their columns remain but are no longer read.)
    """
    if market_id == "1x2_1up_ft":
        v3_oh, v3_oa, v3_ph, v3_pa = (
            "v3_1up_home_capped", "v3_1up_away_capped", "v3_p_home_1", "v3_p_away_1")
        v4_oh, v4_oa, v4_ph, v4_pa = (
            "v4_1up_home_capped", "v4_1up_away_capped", "v4_p_home_1", "v4_p_away_1")
    elif market_id == "1x2_2up_ft":
        v3_oh, v3_oa, v3_ph, v3_pa = (
            "v3_2up_home_capped", "v3_2up_away_capped", "v3_p_home_2", "v3_p_away_2")
        v4_oh, v4_oa, v4_ph, v4_pa = (
            "v4_2up_home_capped", "v4_2up_away_capped", "v4_p_home_2", "v4_p_away_2")
    else:
        return {}
    rows = conn.execute(
        f"SELECT ts_utc, {v3_oh}, {v3_oa}, {v3_ph}, {v3_pa}, "
        f"       {v4_oh}, {v4_oa}, {v4_ph}, {v4_pa} "
        f"FROM pricer_live_results WHERE event_id = ?",
        (event_id,),
    ).fetchall()
    return {
        r["ts_utc"]: {
            "home_odds_v3": r[v3_oh], "away_odds_v3": r[v3_oa],
            "home_prob_v3": r[v3_ph], "away_prob_v3": r[v3_pa],
            "home_odds_v4": r[v4_oh], "away_odds_v4": r[v4_oa],
            "home_prob_v4": r[v4_ph], "away_prob_v4": r[v4_pa],
        }
        for r in rows
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_web_queries.py::test_get_our_history_returns_v3_and_v4 -v`
Expected: PASS. (The detail-page consumer is updated in Task 6 — expect
`test_web_app.py` failures until then; that's fine, fixed next task.)

- [ ] **Step 5: Commit**

```bash
git add src/odds_scraper/web/queries.py tests/test_web_queries.py
git commit -m "feat(web): get_our_history_for_event returns V3 + V4 keys"
```

---

### Task 6: Card + detail render V3 + V4 (drop V2)

**Files:**
- Modify: `src/odds_scraper/web/app.py` (EventView dataclass ~129-160; build_event_view ~414-472; _build_event_detail ~504-555; import line 13-16)
- Modify: `src/odds_scraper/web/templates/_event_card.html` (~51-120)
- Modify: `src/odds_scraper/web/templates/event_detail.html` (~71-72)
- Test: `tests/test_web_app.py`

- [ ] **Step 1: Update the import**

`src/odds_scraper/web/app.py:13-16` — change the pricer import to drop `engine_v2`,
add `engine_v4`:

```python
from odds_scraper.pricer import (
    engine_v3, engine_v4, inputs as pricer_inputs,
    score_state as pricer_score_state,
)
```

- [ ] **Step 2: Rename EventView fields V2/V3 → V3/V4**

In the `EventView` dataclass (~129-160), rename the two OUR blocks so the names
match the engines they now carry:
- `our_1up_home/away`, `our_2up_home/away`, `our_p_1up_home/away`,
  `our_p_2up_home/away` → prefix `our_v3_...` (these become **V3**).
- the existing `our_v3_1up_home/...`/`our_v3_p_...` block → prefix `our_v4_...`
  (these become **V4**).

Net: EventView exposes `our_v3_1up_home, our_v3_1up_away, our_v3_2up_home,
our_v3_2up_away, our_v3_p_1up_home, our_v3_p_1up_away, our_v3_p_2up_home,
our_v3_p_2up_away` and the same eight with `our_v4_` prefix. Update the field
comments to "V3 engine (primary)" / "V4 engine (latest)".

- [ ] **Step 3: Update build_event_view to compute V3 + V4**

Replace the compute block (`src/odds_scraper/web/app.py:415-449`):

```python
    our_v3_1up_home = our_v3_1up_away = our_v3_2up_home = our_v3_2up_away = None
    our_v3_p_1up_home = our_v3_p_1up_away = our_v3_p_2up_home = our_v3_p_2up_away = None
    our_v4_1up_home = our_v4_1up_away = our_v4_2up_home = our_v4_2up_away = None
    our_v4_p_1up_home = our_v4_p_1up_away = our_v4_p_2up_home = our_v4_p_2up_away = None
    if engine_inputs is not None:
        score = (row["score_home"] or 0, row["score_away"] or 0)
        engine_inputs["score"] = (int(score[0]), int(score[1]))
        engine_inputs["max_home_lead"] = max_leads[0]
        engine_inputs["max_away_lead"] = max_leads[1]
        engine_kwargs = {k: v for k, v in engine_inputs.items() if not k.startswith("_")}
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
        try:
            r4 = engine_v4.price_early_payout_markets(**engine_kwargs)
            our_v4_1up_home = r4["market_1up"]["home_margin"]
            our_v4_1up_away = r4["market_1up"]["away_margin"]
            our_v4_2up_home = r4["market_2up"]["home_margin"]
            our_v4_2up_away = r4["market_2up"]["away_margin"]
            our_v4_p_1up_home = r4["p_home_1"]
            our_v4_p_1up_away = r4["p_away_1"]
            our_v4_p_2up_home = r4["p_home_2"]
            our_v4_p_2up_away = r4["p_away_2"]
        except Exception:  # noqa: BLE001
            pass
```

And update the `EventView(...)` constructor (~463-470) to pass the renamed
`our_v3_*` / `our_v4_*` fields.

- [ ] **Step 4: Update _build_event_detail sim cells (V3 + V4)**

Replace the `for ts, our in our_by_ts.items():` loop (`app.py:527-549`) so it reads
the new query keys and builds `sim_v3` + `sim_v4` cells:

```python
    for ts, our in our_by_ts.items():
        v3_cells: dict[str, PriceCell] = {}
        if our["home_odds_v3"] is not None:
            v3_cells["home"] = PriceCell(odds=our["home_odds_v3"], probability=our["home_prob_v3"])
        if our["away_odds_v3"] is not None:
            v3_cells["away"] = PriceCell(odds=our["away_odds_v3"], probability=our["away_prob_v3"])
        if v3_cells and ts in bucket:
            bucket[ts]["cells"]["sim_v3"] = v3_cells
        v4_cells: dict[str, PriceCell] = {}
        if our["home_odds_v4"] is not None:
            v4_cells["home"] = PriceCell(odds=our["home_odds_v4"], probability=our["home_prob_v4"])
        if our["away_odds_v4"] is not None:
            v4_cells["away"] = PriceCell(odds=our["away_odds_v4"], probability=our["away_prob_v4"])
        if v4_cells and ts in bucket:
            bucket[ts]["cells"]["sim_v4"] = v4_cells
```

And update `history_books` (`app.py:553`):

```python
        history_books = ("betpawa", "sportybet", "sim_v3", "sim_v4", "bet9ja", "betway")
```

- [ ] **Step 5: Update templates**

`event_detail.html:71-72` — change the label map + sim_books tuple:

```jinja
      {% set book_label = {"betpawa":"BetPawa","sportybet":"SportyBet","bet9ja":"Bet9ja","betway":"Betway","sim_v3":"V3","sim_v4":"V4"} %}
      {% set sim_books = ("sim_v3", "sim_v4") %}
```

Also update the two later `{% if bm in ("betpawa", "sportybet", "sim", "sim_v3") ...`
occurrences (the prob-rendering guards, ~line 108) to
`("betpawa", "sportybet", "sim_v3", "sim_v4")`.

`_event_card.html:51-120` — rename the jinja vars and tags:
- `our_value`/`our_prob` ← `event.our_v3_*` (tag text **V3**, cell key `sim_v3`).
- `our_v3_value`/`our_v3_prob` ← `event.our_v4_*` (tag text **V4**, cell key `sim_v4`).
  Concretely: change each `event.our_1up_home`→`event.our_v3_1up_home`,
  `event.our_p_1up_home`→`event.our_v3_p_1up_home`,
  `event.our_v3_1up_home`→`event.our_v4_1up_home`,
  `event.our_v3_p_1up_home`→`event.our_v4_p_1up_home` (and the away/2up analogues),
  and the `<span class="sim-tag">V2</span>`→`V3`, `V3`→`V4`,
  `data-bookmaker="sim"`→`sim_v3`, `data-bookmaker="sim_v3"`→`sim_v4`.

- [ ] **Step 6: Update/add web app tests**

In `tests/test_web_app.py`, find assertions referencing `our_1up_*`/`our_v3_*`
EventView fields or the `sim`/`sim_v3` cell keys and update them to
`our_v3_*`/`our_v4_*` and `sim_v3`/`sim_v4`. Add one assertion that a detail page
for a UP market with a seeded `pricer_live_results` row renders both a `V3` and a
`V4` column header.

- [ ] **Step 7: Run the web tests**

Run: `python -m pytest tests/test_web_app.py tests/test_web_queries.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/odds_scraper/web/app.py src/odds_scraper/web/templates/_event_card.html src/odds_scraper/web/templates/event_detail.html tests/test_web_app.py
git commit -m "feat(web): cards + detail show V3 + V4 (drop V2)"
```

---

### Task 7: Simulator engine picker + profile panel → V3/V4 only

**Files:**
- Modify: `src/odds_scraper/web/templates/simulator.html` (engine checkboxes, ~165-175)
- Modify: `src/odds_scraper/web/templates/_profile_fields.html` (panels ~40-90, CSS ~180-205)
- Test: `tests/test_simulator_routes.py`

- [ ] **Step 1: Trim the simulator engine checkboxes**

In `simulator.html`, remove the `value="v1"` and `value="v2"` engine checkboxes,
leaving only V3 and V4; make V4 checked by default. The backend
(`pricer_routes.post_run`) already validates against `runner_v2.VALID_ENGINES` and
falls back to `LATEST_ENGINE` ("v4"), so no backend change is needed.

- [ ] **Step 2: Collapse the profile panel to a shared V3/V4 panel**

In `_profile_fields.html`, hide the V1 and V2 `cfg-panel`/`cfg-leg` blocks and the
V2-only fields (`ONEUP_TRAILING_*_MARGIN`). Relabel the remaining tunable panel
legend to "V3 / V4". Keep all field `name=` attributes unchanged so stored profiles
still round-trip (the values just aren't shown for V1/V2). Update the CSS so the
remaining panel uses the V3/V4 colour vars; leave unused `--c-v2` rules in place
(harmless) or delete the now-unreferenced `[data-eng="v2"]` rules.

- [ ] **Step 3: Update simulator route tests**

In `tests/test_simulator_routes.py`, if any test posts `engine=v2`/`engine=v1` to
assert behaviour, change it to `engine=v3`/`engine=v4`. Add a test asserting the
`/simulator` page HTML no longer contains `value="v2"` for an engine checkbox:

```python
def test_simulator_page_offers_only_v3_v4_engines(client):
    html = client.get("/simulator").text
    assert 'name="engine" value="v3"' in html
    assert 'name="engine" value="v4"' in html
    assert 'name="engine" value="v2"' not in html
    assert 'name="engine" value="v1"' not in html
```

(Use the module's existing `client` fixture; match its exact checkbox markup when
writing the asserts — adjust the substring to the real attribute order.)

- [ ] **Step 4: Run simulator tests**

Run: `python -m pytest tests/test_simulator_routes.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/odds_scraper/web/templates/simulator.html src/odds_scraper/web/templates/_profile_fields.html tests/test_simulator_routes.py
git commit -m "feat(web): simulator + profiles show V3/V4 only"
```

---

### Task 8: Phase 1 verification

- [ ] **Step 1: Full test run**

Run: `python -m pytest -q`
Expected: all green. Fix any remaining V2-referencing test fallout.

- [ ] **Step 2: Manual smoke (optional but recommended)**

Launch the web app (see project README / `web/__main__.py`), open a live UP-market
event detail page, and confirm the history shows **V3** and **V4** columns and no
V2. Confirm `/simulator` shows only V3/V4 engine checkboxes.

- [ ] **Step 3: Commit any fixes**

```bash
git add -A && git commit -m "test(live): green after V3/V4 migration"
```

---

# PHASE 2 — `/export` page (raw odds → CSV)

### Task 9: `export_service.select_ticks`

**Files:**
- Create: `src/odds_scraper/web/export_service.py`
- Test: `tests/test_export_service.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_export_service.py`:

```python
import sqlite3
import pytest
from odds_scraper.db_schema import init_schema
from odds_scraper.web import export_service as ex


def _conn():
    c = sqlite3.connect(":memory:", isolation_level=None)
    c.row_factory = sqlite3.Row
    init_schema(c)
    return c


def _seed_event(c, eid="E1", home="A", away="B",
                country=("ng", "Nigeria"), league=("npl", "NPL")):
    c.execute(
        "INSERT INTO events (id, home, away, kickoff_utc, country_id, "
        "country_name, league_id, league_name) VALUES (?,?,?,?,?,?,?,?)",
        (eid, home, away, "2026-05-22T18:00:00Z", country[0], country[1],
         league[0], league[1]))


def _seed_tick(c, eid, ts, status, *, book="betpawa", minute=0,
               sh=0, sa=0, prices=()):
    """Insert one snapshot + its prices. `prices` = [(market_id,line,side,odds,prob)]."""
    cur = c.execute(
        "INSERT INTO snapshots (event_id, bookmaker, ts_utc, status, "
        "match_minute, score_home, score_away, fetch_status) "
        "VALUES (?,?,?,?,?,?,?, 'ok')",
        (eid, book, ts, status, minute, sh, sa))
    snap_id = cur.lastrowid
    for market_id, line, side, odds, prob in prices:
        c.execute(
            "INSERT INTO prices (snapshot_id, event_id, bookmaker, ts_utc, "
            "market_id, line, side, odds, probability) VALUES (?,?,?,?,?,?,?,?,?)",
            (snap_id, eid, book, ts, market_id, line, side, odds, prob))
    return snap_id


def test_select_ticks_regime_filters_status():
    c = _conn(); _seed_event(c)
    _seed_tick(c, "E1", "2026-05-22T17:00:00Z", "UPCOMING")
    _seed_tick(c, "E1", "2026-05-22T18:10:00Z", "STARTED")
    pre = ex.select_ticks(c, "prematch", "all", {})
    live = ex.select_ticks(c, "live", "all", {})
    allr = ex.select_ticks(c, "any", "all", {})
    assert [t["ts_utc"] for t in pre] == ["2026-05-22T17:00:00Z"]
    assert [t["ts_utc"] for t in live] == ["2026-05-22T18:10:00Z"]
    assert len(allr) == 2


def test_select_ticks_latest_tiebreak_uses_max_snapshot_id():
    c = _conn(); _seed_event(c)
    # two snapshots same ts — latest must pick the row, deterministically
    _seed_tick(c, "E1", "2026-05-22T18:00:00Z", "STARTED")
    _seed_tick(c, "E1", "2026-05-22T18:30:00Z", "STARTED")
    latest = ex.select_ticks(c, "any", "latest", {})
    assert [t["ts_utc"] for t in latest] == ["2026-05-22T18:30:00Z"]


def test_select_ticks_scope_country_and_invalid_regime():
    c = _conn(); _seed_event(c)
    _seed_tick(c, "E1", "2026-05-22T18:00:00Z", "STARTED")
    assert ex.select_ticks(c, "any", "all", {"country": "zz"}) == []
    assert len(ex.select_ticks(c, "any", "all", {"country": "ng"})) == 1
    with pytest.raises(ValueError):
        ex.select_ticks(c, "bogus", "all", {})
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_export_service.py -v`
Expected: FAIL (module/function missing).

- [ ] **Step 3: Implement `select_ticks`**

Create `src/odds_scraper/web/export_service.py`:

```python
from __future__ import annotations

import sqlite3
from typing import Iterable, Iterator

VALID_REGIMES = ("any", "prematch", "live")
VALID_DENSITIES = ("all", "latest", "onchange")
SIM_ENGINES = ("v3", "v4")
_STATUS_BY_REGIME = {"prematch": "UPCOMING", "live": "STARTED"}


def _regime_status(regime: str) -> str | None:
    if regime not in VALID_REGIMES:
        raise ValueError(f"unknown regime {regime!r}")
    return _STATUS_BY_REGIME.get(regime)


def select_ticks(
    conn: sqlite3.Connection, regime: str, density: str, scope: dict,
) -> list[dict]:
    """One row per (event_id, ts_utc) tick with event + snapshot metadata.

    regime: any/prematch/live (snapshot status filter).
    density: all (every tick) / latest (last per event, MAX ts then MAX
             snapshot_id tiebreak) / onchange (same set as 'all'; the actual
             collapse is applied later by collapse_onchange).
    scope: country/league/event/date/search (all optional).
    """
    if density not in VALID_DENSITIES:
        raise ValueError(f"unknown density {density!r}")
    status = _regime_status(regime)

    where = ["e.home != '' AND e.away != ''"]
    params: list = []
    if status:
        where.append("s.status = ?"); params.append(status)
    if scope.get("country"):
        where.append("e.country_id = ?"); params.append(scope["country"])
    if scope.get("league"):
        where.append("e.league_id = ?"); params.append(scope["league"])
    if scope.get("event_id"):
        where.append("s.event_id = ?"); params.append(scope["event_id"])
    if scope.get("date"):
        where.append("DATE(e.kickoff_utc) = ?"); params.append(scope["date"])
    if scope.get("search"):
        where.append("(LOWER(e.home) LIKE ? OR LOWER(e.away) LIKE ?)")
        like = f"%{scope['search'].lower()}%"; params += [like, like]
    where_sql = " AND ".join(where)

    base = """
        SELECT MIN(s.id)           AS snapshot_id,
               s.event_id, s.ts_utc,
               MAX(s.status)       AS status,
               MAX(s.match_minute) AS match_minute,
               MAX(s.score_home)   AS score_home,
               MAX(s.score_away)   AS score_away,
               MAX(e.home)         AS home,
               MAX(e.away)         AS away,
               MAX(e.kickoff_utc)  AS kickoff_utc,
               MAX(e.country_name) AS country_name,
               MAX(e.league_name)  AS league_name
        FROM snapshots s JOIN events e ON e.id = s.event_id
    """
    if density == "latest":
        regime_filter = f"WHERE status = '{status}'" if status else ""
        sql = f"""
            WITH latest AS (
                SELECT event_id, MAX(ts_utc) AS max_ts
                FROM snapshots {regime_filter}
                GROUP BY event_id
            )
            {base}
            JOIN latest l ON l.event_id = s.event_id AND l.max_ts = s.ts_utc
            WHERE {where_sql}
            GROUP BY s.event_id, s.ts_utc
            ORDER BY s.event_id, s.ts_utc, MAX(s.id)
        """
    else:
        sql = f"""
            {base}
            WHERE {where_sql}
            GROUP BY s.event_id, s.ts_utc
            ORDER BY s.event_id, s.ts_utc
        """
    cur = conn.cursor(); cur.row_factory = sqlite3.Row
    return [dict(r) for r in cur.execute(sql, params).fetchall()]
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_export_service.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/odds_scraper/web/export_service.py tests/test_export_service.py
git commit -m "feat(export): select_ticks (regime/density/scope)"
```

---

### Task 10: `_load_tick_prices` + `collapse_onchange` over selected markets

**Files:**
- Modify: `src/odds_scraper/web/export_service.py`
- Test: `tests/test_export_service.py`

- [ ] **Step 1: Write the failing test**

```python
def test_collapse_onchange_uses_only_selected_markets():
    c = _conn(); _seed_event(c)
    # tick1 and tick2: 1x2 odds identical; an over_under line changes between them
    _seed_tick(c, "E1", "2026-05-22T18:00:00Z", "STARTED", prices=[
        ("1x2_ft", 0.0, "home", 1.80, None),
        ("over_under_ft", 2.5, "over", 1.90, None)])
    _seed_tick(c, "E1", "2026-05-22T18:01:00Z", "STARTED", prices=[
        ("1x2_ft", 0.0, "home", 1.80, None),
        ("over_under_ft", 2.5, "over", 2.10, None)])  # OU moved, 1x2 didn't
    ticks = ex.select_ticks(c, "any", "onchange", {})
    # Selecting ONLY 1x2: second tick is unchanged → dropped.
    kept_1x2 = ex.collapse_onchange(c, ticks, [("1x2_ft", 0.0)])
    assert [t["ts_utc"] for t in kept_1x2] == ["2026-05-22T18:00:00Z"]
    # Selecting the OU line: it changed → both kept.
    kept_ou = ex.collapse_onchange(c, ticks, [("over_under_ft", 2.5)])
    assert len(kept_ou) == 2
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_export_service.py::test_collapse_onchange_uses_only_selected_markets -v`
Expected: FAIL (`collapse_onchange` undefined).

- [ ] **Step 3: Implement the helpers**

Append to `export_service.py`:

```python
def load_tick_prices(
    conn: sqlite3.Connection, event_id: str, ts_utc: str,
    markets: Iterable[tuple[str, float]] | None = None,
    books: Iterable[str] | None = None,
) -> list[dict]:
    """All price rows for one (event, ts) tick, optionally filtered to the
    selected (market_id, line) pairs and bookmakers. Ordered deterministically."""
    sql = ("SELECT bookmaker, market_id, line, side, odds, probability "
           "FROM prices WHERE event_id = ? AND ts_utc = ?")
    params: list = [event_id, ts_utc]
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    if markets is not None:
        sel = {(m, float(l)) for m, l in markets}
        rows = [r for r in rows if (r["market_id"], float(r["line"])) in sel]
    if books is not None:
        bset = set(books)
        rows = [r for r in rows if r["bookmaker"] in bset]
    rows.sort(key=lambda r: (r["bookmaker"], r["market_id"], r["line"], r["side"]))
    return rows


def _fingerprint(rows: list[dict]) -> frozenset:
    """Hashable identity of a tick's selected price set. Odds rounded to 4 dp
    so float storage drift never reads as a 'change'."""
    return frozenset(
        (r["bookmaker"], r["market_id"], float(r["line"]), r["side"],
         None if r["odds"] is None else round(float(r["odds"]), 4))
        for r in rows
    )


def collapse_onchange(
    conn: sqlite3.Connection, ticks: list[dict],
    markets: Iterable[tuple[str, float]] | None,
) -> list[dict]:
    """Drop a tick whose selected-market price set equals the previous KEPT
    tick for the same event. Fingerprint is over the SELECTED markets only."""
    kept: list[dict] = []
    last_fp: dict[str, frozenset] = {}
    for t in ticks:
        fp = _fingerprint(load_tick_prices(conn, t["event_id"], t["ts_utc"], markets))
        if last_fp.get(t["event_id"]) == fp:
            continue
        last_fp[t["event_id"]] = fp
        kept.append(t)
    return kept
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_export_service.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/odds_scraper/web/export_service.py tests/test_export_service.py
git commit -m "feat(export): collapse_onchange over selected markets"
```

---

### Task 11: `limit_first_last`

**Files:**
- Modify: `src/odds_scraper/web/export_service.py`
- Test: `tests/test_export_service.py`

- [ ] **Step 1: Write the failing test**

```python
def _ticks(eid, tss):
    return [{"event_id": eid, "ts_utc": ts} for ts in tss]


def test_limit_first_last_per_event():
    ts = [f"2026-05-22T18:0{i}:00Z" for i in range(5)]  # 5 ticks
    rows = _ticks("E1", ts)
    assert [r["ts_utc"] for r in ex.limit_first_last(rows, 2, 0)] == ts[:2]
    assert [r["ts_utc"] for r in ex.limit_first_last(rows, 0, 2)] == ts[-2:]
    # first 2 + last 2 = union, no dupes, original order
    assert [r["ts_utc"] for r in ex.limit_first_last(rows, 2, 2)] == [ts[0], ts[1], ts[3], ts[4]]
    assert [r["ts_utc"] for r in ex.limit_first_last(rows, 0, 0)] == ts  # no-op
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_export_service.py::test_limit_first_last_per_event -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

```python
def limit_first_last(ticks: list[dict], first_n: int, last_n: int) -> list[dict]:
    """Per event, keep the first N and/or last N ticks (union, original order).
    0/0 is a no-op. Assumes `ticks` already ordered by (event_id, ts_utc)."""
    if not first_n and not last_n:
        return ticks
    by_event: dict[str, list[dict]] = {}
    for t in ticks:
        by_event.setdefault(t["event_id"], []).append(t)
    keep_ids: set[int] = set()
    for evs in by_event.values():
        picked = []
        if first_n:
            picked += evs[:first_n]
        if last_n:
            picked += evs[-last_n:]
        for t in picked:
            keep_ids.add(id(t))
    return [t for t in ticks if id(t) in keep_ids]
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_export_service.py::test_limit_first_last_per_event -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/odds_scraper/web/export_service.py tests/test_export_service.py
git commit -m "feat(export): limit_first_last per event"
```

---

### Task 12: `csv_safe` + LONG columns + `iter_long_rows` (real odds, no sim)

**Files:**
- Modify: `src/odds_scraper/web/export_service.py`
- Test: `tests/test_export_service.py`

- [ ] **Step 1: Write the failing test**

```python
def test_csv_safe_escapes_formula_chars():
    assert ex.csv_safe("=cmd()") == "'=cmd()"
    assert ex.csv_safe("+1") == "'+1"
    assert ex.csv_safe("@x") == "'@x"
    assert ex.csv_safe("FC -Home") == "FC -Home"   # dash not leading → untouched
    assert ex.csv_safe(1.85) == 1.85               # non-str passthrough


def test_iter_long_rows_real_prices():
    c = _conn(); _seed_event(c)
    _seed_tick(c, "E1", "2026-05-22T18:00:00Z", "STARTED", minute=10, prices=[
        ("1x2_ft", 0.0, "home", 1.80, 0.55),
        ("1x2_ft", 0.0, "away", 4.20, 0.20)])
    ticks = ex.select_ticks(c, "any", "all", {})
    rows = list(ex.iter_long_rows(c, ticks, markets=[("1x2_ft", 0.0)],
                                  books=None, sim_engines=()))
    assert len(rows) == 2
    r = next(r for r in rows if r["side"] == "home")
    assert r["event_id"] == "E1" and r["bookmaker"] == "betpawa"
    assert r["market_id"] == "1x2_ft" and r["odds"] == 1.80
    assert r["is_simulated"] == 0 and r["engine"] == ""
    assert set(ex.LONG_COLUMNS) == set(r.keys())
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_export_service.py -k "csv_safe or iter_long_rows_real" -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

```python
LONG_COLUMNS = (
    "event_id", "country_name", "league_name", "home", "away", "kickoff_utc",
    "snapshot_id", "ts_utc", "status", "match_minute", "score_home", "score_away",
    "bookmaker", "market_id", "line", "side", "odds", "probability",
    "is_simulated", "engine",
)


def csv_safe(value):
    """Prefix a leading =,+,-,@ with an apostrophe so spreadsheets don't
    execute the cell as a formula. Non-strings pass through unchanged."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        return "'" + value
    return value


def _meta(t: dict) -> dict:
    return {
        "event_id": t["event_id"], "country_name": t.get("country_name"),
        "league_name": t.get("league_name"), "home": t.get("home"),
        "away": t.get("away"), "kickoff_utc": t.get("kickoff_utc"),
        "snapshot_id": t.get("snapshot_id"), "ts_utc": t["ts_utc"],
        "status": t.get("status"), "match_minute": t.get("match_minute"),
        "score_home": t.get("score_home"), "score_away": t.get("score_away"),
    }


def iter_long_rows(
    conn, ticks, *, markets, books, sim_engines=(),
) -> Iterator[dict]:
    """Yield one LONG dict per (tick, bookmaker, market, line, side). Real
    scraped rows first; then stored sim rows (V3/V4) for 1UP/2UP if requested."""
    for t in ticks:
        meta = _meta(t)
        for p in load_tick_prices(conn, t["event_id"], t["ts_utc"], markets, books):
            yield {
                **meta,
                "bookmaker": p["bookmaker"], "market_id": p["market_id"],
                "line": p["line"], "side": p["side"],
                "odds": p["odds"], "probability": p["probability"],
                "is_simulated": 0, "engine": "",
            }
        if sim_engines:
            yield from _sim_rows(conn, t, meta, markets, sim_engines)
```

For now add a stub so imports resolve; the real body lands in Task 13:

```python
def _sim_rows(conn, t, meta, markets, sim_engines) -> Iterator[dict]:
    return iter(())  # implemented in Task 13
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_export_service.py -k "csv_safe or iter_long_rows_real" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/odds_scraper/web/export_service.py tests/test_export_service.py
git commit -m "feat(export): LONG columns, csv_safe, real-price row generator"
```

---

### Task 13: Stored V3/V4 sim rows in LONG (LEFT-join semantics)

**Files:**
- Modify: `src/odds_scraper/web/export_service.py` (`_sim_rows`)
- Test: `tests/test_export_service.py`

- [ ] **Step 1: Write the failing test**

```python
def _seed_sim(c, eid, ts):
    c.execute(
        "INSERT INTO pricer_live_results (event_id, ts_utc, basis_used, "
        "v3_1up_home_capped, v3_1up_away_capped, v3_p_home_1, v3_p_away_1, "
        "v4_1up_home_capped, v4_1up_away_capped, v4_p_home_1, v4_p_away_1) "
        "VALUES (?,?, 'bp', 2.1,3.2,0.5,0.3, 2.0,3.0,0.52,0.31)", (eid, ts))


def test_sim_rows_v3_v4_for_up_markets_only():
    c = _conn(); _seed_event(c)
    ts = "2026-05-22T18:00:00Z"
    _seed_tick(c, "E1", ts, "STARTED", prices=[
        ("1x2_ft", 0.0, "home", 1.80, None)])          # non-UP real market
    _seed_sim(c, "E1", ts)
    ticks = ex.select_ticks(c, "any", "all", {})
    rows = list(ex.iter_long_rows(
        c, ticks, markets=[("1x2_ft", 0.0), ("1x2_1up_ft", 0.0)],
        books=None, sim_engines=("v3", "v4")))
    # real 1x2 row retained (LEFT-join semantics — not deleted by the sim join)
    assert any(r["market_id"] == "1x2_ft" and r["is_simulated"] == 0 for r in rows)
    sim = [r for r in rows if r["is_simulated"] == 1]
    engines = {r["engine"] for r in sim}
    assert engines == {"v3", "v4"}
    # sim rows are 1UP home/away, bookmaker OUR, carry capped odds + prob
    v4_home = next(r for r in sim if r["engine"] == "v4" and r["side"] == "home")
    assert v4_home["market_id"] == "1x2_1up_ft" and v4_home["bookmaker"] == "OUR"
    assert v4_home["odds"] == 2.0 and v4_home["probability"] == 0.52
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_export_service.py::test_sim_rows_v3_v4_for_up_markets_only -v`
Expected: FAIL (stub yields nothing).

- [ ] **Step 3: Implement `_sim_rows`**

Replace the `_sim_rows` stub:

```python
# (market_id, engine) -> (home_odds_col, away_odds_col, home_prob_col, away_prob_col)
_SIM_COLS = {
    ("1x2_1up_ft", "v3"): ("v3_1up_home_capped", "v3_1up_away_capped", "v3_p_home_1", "v3_p_away_1"),
    ("1x2_2up_ft", "v3"): ("v3_2up_home_capped", "v3_2up_away_capped", "v3_p_home_2", "v3_p_away_2"),
    ("1x2_1up_ft", "v4"): ("v4_1up_home_capped", "v4_1up_away_capped", "v4_p_home_1", "v4_p_away_1"),
    ("1x2_2up_ft", "v4"): ("v4_2up_home_capped", "v4_2up_away_capped", "v4_p_home_2", "v4_p_away_2"),
}
_SIM_MARKETS = ("1x2_1up_ft", "1x2_2up_ft")


def _sim_rows(conn, t, meta, markets, sim_engines) -> Iterator[dict]:
    """Stored V3/V4 OUR prices for the in-scope UP markets as LONG rows.
    LEFT-join semantics: if no pricer_live_results row exists for this tick,
    yields nothing (real rows are unaffected)."""
    if markets is not None:
        sel = {m for m, _ in markets}
        up_markets = [m for m in _SIM_MARKETS if m in sel]
    else:
        up_markets = list(_SIM_MARKETS)
    if not up_markets:
        return
    row = conn.execute(
        "SELECT * FROM pricer_live_results WHERE event_id=? AND ts_utc=?",
        (t["event_id"], t["ts_utc"]),
    ).fetchone()
    if row is None:
        return
    keys = row.keys() if hasattr(row, "keys") else None
    for market_id in up_markets:
        for engine in sim_engines:
            cols = _SIM_COLS.get((market_id, engine))
            if not cols:
                continue
            oh, oa, ph, pa = cols
            for side, ocol, pcol in (("home", oh, ph), ("away", oa, pa)):
                odds = row[ocol]
                if odds is None:
                    continue
                yield {
                    **meta, "bookmaker": "OUR", "market_id": market_id,
                    "line": 0.0, "side": side,
                    "odds": odds, "probability": row[pcol],
                    "is_simulated": 1, "engine": engine,
                }
```

(The `pricer_live_results` connection must have `row_factory = sqlite3.Row` so
`row[ocol]` works — the route opens it that way; the test's `_conn()` already does.)

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_export_service.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/odds_scraper/web/export_service.py tests/test_export_service.py
git commit -m "feat(export): stored V3/V4 sim rows (LEFT-join, UP markets only)"
```

---

### Task 14: `to_wide_rows` (frozen, sorted columns)

**Files:**
- Modify: `src/odds_scraper/web/export_service.py`
- Test: `tests/test_export_service.py`

- [ ] **Step 1: Write the failing test**

```python
def test_to_wide_rows_stable_columns():
    long_rows = [
        {"event_id": "E1", "ts_utc": "T1", "country_name": "NG", "league_name": "L",
         "home": "A", "away": "B", "kickoff_utc": "K", "snapshot_id": 1,
         "status": "STARTED", "match_minute": 5, "score_home": 0, "score_away": 0,
         "bookmaker": "betpawa", "market_id": "1x2_ft", "line": 0.0, "side": "home",
         "odds": 1.80, "probability": 0.55, "is_simulated": 0, "engine": ""},
        {"event_id": "E1", "ts_utc": "T1", "country_name": "NG", "league_name": "L",
         "home": "A", "away": "B", "kickoff_utc": "K", "snapshot_id": 1,
         "status": "STARTED", "match_minute": 5, "score_home": 0, "score_away": 0,
         "bookmaker": "OUR", "market_id": "1x2_1up_ft", "line": 0.0, "side": "home",
         "odds": 2.0, "probability": 0.52, "is_simulated": 1, "engine": "v4"},
    ]
    cols, wide = ex.to_wide_rows(long_rows)
    assert wide[0]["event_id"] == "E1"
    assert wide[0]["betpawa__1x2_ft__0.0__home__odds"] == 1.80
    assert wide[0]["our_v4__1x2_1up_ft__0.0__home__odds"] == 2.0
    # columns deterministic + sorted, metadata first
    assert cols.index("event_id") < cols.index("betpawa__1x2_ft__0.0__home__odds")
    assert cols == sorted(cols[len(ex.WIDE_META):]) and True  # value cols sorted
```

(The final assert is loose; the real invariant — value columns sorted — is enforced
in the implementation. Keep the test focused on presence + determinism.)

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_export_service.py::test_to_wide_rows_stable_columns -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

```python
WIDE_META = (
    "event_id", "country_name", "league_name", "home", "away", "kickoff_utc",
    "snapshot_id", "ts_utc", "status", "match_minute", "score_home", "score_away",
)


def _wide_book(row: dict) -> str:
    return f"our_{row['engine']}" if row["is_simulated"] else row["bookmaker"]


def to_wide_rows(long_rows: Iterable[dict]) -> tuple[list[str], list[dict]]:
    """Pivot LONG rows to one row per (event, ts). Value columns are
    '{book}__{market}__{line}__{side}__{odds|prob}', sorted for a stable,
    frozen header. Returns (columns, rows)."""
    long_rows = list(long_rows)
    value_cols: set[str] = set()
    by_key: dict[tuple, dict] = {}
    for r in long_rows:
        key = (r["event_id"], r["ts_utc"])
        bucket = by_key.setdefault(key, {m: r.get(m) for m in WIDE_META})
        book = _wide_book(r)
        stem = f"{book}__{r['market_id']}__{r['line']}__{r['side']}"
        ocol, pcol = f"{stem}__odds", f"{stem}__prob"
        bucket[ocol] = r["odds"]; bucket[pcol] = r["probability"]
        value_cols.add(ocol); value_cols.add(pcol)
    columns = list(WIDE_META) + sorted(value_cols)
    rows = [by_key[k] for k in sorted(by_key)]
    return columns, rows
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_export_service.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/odds_scraper/web/export_service.py tests/test_export_service.py
git commit -m "feat(export): to_wide_rows with frozen sorted columns"
```

---

### Task 15: `available_markets` helper (for the picker)

**Files:**
- Modify: `src/odds_scraper/web/export_service.py`
- Test: `tests/test_export_service.py`

- [ ] **Step 1: Write the failing test**

```python
def test_available_markets_for_scope():
    c = _conn(); _seed_event(c)
    _seed_tick(c, "E1", "2026-05-22T18:00:00Z", "STARTED", prices=[
        ("1x2_ft", 0.0, "home", 1.8, None),
        ("over_under_ft", 2.5, "over", 1.9, None),
        ("over_under_ft", 3.5, "over", 2.7, None)])
    pairs = ex.available_markets(c, {})
    assert ("1x2_ft", 0.0) in pairs
    assert ("over_under_ft", 2.5) in pairs and ("over_under_ft", 3.5) in pairs
    # sorted, deterministic
    assert pairs == sorted(pairs)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_export_service.py::test_available_markets_for_scope -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

```python
def available_markets(conn, scope: dict) -> list[tuple[str, float]]:
    """Distinct (market_id, line) pairs present for in-scope events. Includes
    the 0.0 sentinel line for the 1x2 family (it's a real selectable market)."""
    where = ["e.home != '' AND e.away != ''"]
    params: list = []
    if scope.get("country"):
        where.append("e.country_id = ?"); params.append(scope["country"])
    if scope.get("league"):
        where.append("e.league_id = ?"); params.append(scope["league"])
    if scope.get("event_id"):
        where.append("p.event_id = ?"); params.append(scope["event_id"])
    if scope.get("date"):
        where.append("DATE(e.kickoff_utc) = ?"); params.append(scope["date"])
    sql = (
        "SELECT DISTINCT p.market_id, p.line FROM prices p "
        "JOIN events e ON e.id = p.event_id WHERE " + " AND ".join(where) +
        " ORDER BY p.market_id, p.line"
    )
    return [(r[0], float(r[1])) for r in conn.execute(sql, params).fetchall()]
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_export_service.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/odds_scraper/web/export_service.py tests/test_export_service.py
git commit -m "feat(export): available_markets for the picker"
```

---

### Task 16: `export_routes` — page, count, markets, CSV stream

**Files:**
- Create: `src/odds_scraper/web/export_routes.py`
- Create: `src/odds_scraper/web/templates/export.html`
- Modify: `src/odds_scraper/web/app.py` (register routes + nav)
- Modify: `src/odds_scraper/web/templates/base.html` (nav link)
- Test: `tests/test_export_routes.py`

- [ ] **Step 1: Write the failing route tests**

Create `tests/test_export_routes.py`. Mirror `tests/test_simulator_routes.py`'s app
construction (it builds the FastAPI app + `TestClient` + a seeded temp DB — reuse the
exact same setup helper/fixture; read that file first and copy its `client` fixture):

```python
import csv, io


def test_export_page_renders(export_client):
    r = export_client.get("/export")
    assert r.status_code == 200
    assert "Export" in r.text


def test_export_csv_long_streams_rows(export_client):
    r = export_client.get("/export.csv", params={
        "regime": "any", "density": "all", "format": "long"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    reader = list(csv.DictReader(io.StringIO(r.text)))
    assert "bookmaker" in reader[0] and "odds" in reader[0]


def test_export_csv_with_sim_filename_suffix(export_client):
    r = export_client.get("/export.csv", params={
        "regime": "any", "density": "all", "format": "long",
        "sim": "1", "engine": ["v4"]})
    assert "_with_simulated" in r.headers["content-disposition"]


def test_export_csv_bad_regime_400(export_client):
    r = export_client.get("/export.csv", params={"regime": "bogus"})
    assert r.status_code == 400
```

Add an `export_client` fixture in this file that builds the app the same way
`test_simulator_routes.py` does and seeds at least one event + a couple of ticks
with prices (reuse the seed helpers from `test_export_service.py` by importing them,
or inline a minimal seed).

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_export_routes.py -v`
Expected: FAIL (no `/export` route).

- [ ] **Step 3: Implement `export_routes.py`**

```python
from __future__ import annotations

import csv
import io
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from . import export_service as ex, queries

_MAX_ROWS = 2_000_000


def register_export_routes(
    app: FastAPI, templates: Jinja2Templates, *, db_path: Path, conn,
) -> None:
    """Attach /export routes. `conn` is the long-lived read-only connection."""

    def _scope(country, league, event_id, date, search) -> dict:
        return {"country": country, "league": league, "event_id": event_id,
                "date": date, "search": search}

    def _parse_markets(market: list[str]) -> list[tuple[str, float]] | None:
        """'market_id|line' tokens → pairs; None means 'all'."""
        if not market:
            return None
        out: list[tuple[str, float]] = []
        for tok in market:
            mid, _, line = tok.partition("|")
            try:
                out.append((mid, float(line or 0.0)))
            except ValueError:
                continue
        return out or None

    def _build_ticks(regime, density, scope, markets, first_n, last_n):
        ticks = ex.select_ticks(conn, regime, density, scope)
        if density == "onchange":
            ticks = ex.collapse_onchange(conn, ticks, markets)
        ticks = ex.limit_first_last(ticks, first_n, last_n)
        return ticks

    @app.get("/export", response_class=HTMLResponse)
    async def export_page(request: Request):
        return templates.TemplateResponse(request, "export.html", {
            "country_league_index": queries.get_country_league_index(conn),
            "markets": ex.available_markets(conn, {}),
            "sim_engines": ex.SIM_ENGINES,
        })

    @app.get("/export/markets", response_class=HTMLResponse)
    async def export_markets(country: str = "", league: str = "",
                             event_id: str = "", date: str = ""):
        scope = _scope(country, league, event_id, date, "")
        from html import escape as e
        parts = []
        for mid, line in ex.available_markets(conn, scope):
            val = f"{mid}|{line}"
            lbl = mid if line == 0.0 else f"{mid} @ {line}"
            parts.append(
                f'<label><input type="checkbox" name="market" value="{e(val)}" checked> {e(lbl)}</label>')
        return HTMLResponse("".join(parts) or "<span>no markets in scope</span>")

    @app.get("/export/count", response_class=HTMLResponse)
    async def export_count(regime: str = "any", density: str = "all",
                           country: str = "", league: str = "", event_id: str = "",
                           date: str = "", search: str = "",
                           market: list[str] = Query(default=[])):
        try:
            markets = _parse_markets(market)
            ticks = _build_ticks(regime, density,
                                 _scope(country, league, event_id, date, search),
                                 markets, 0, 0)
        except ValueError:
            return HTMLResponse("<span class='filter-lbl'>invalid scope</span>")
        n_ev = len({t["event_id"] for t in ticks})
        return HTMLResponse(
            f"<span class='filter-lbl'><b>{n_ev:,}</b> events &middot; "
            f"<b>{len(ticks):,}</b> snapshots in scope</span>")

    @app.get("/export.csv")
    async def export_csv(
        regime: str = "any", density: str = "all", format: str = "long",
        country: str = "", league: str = "", event_id: str = "",
        date: str = "", search: str = "",
        market: list[str] = Query(default=[]),
        book: list[str] = Query(default=[]),
        first_n: int = 0, last_n: int = 0,
        sim: int = 0, engine: list[str] = Query(default=[]),
    ):
        if regime not in ex.VALID_REGIMES:
            raise HTTPException(400, f"unknown regime {regime!r}")
        if density not in ex.VALID_DENSITIES:
            raise HTTPException(400, f"unknown density {density!r}")
        if format not in ("long", "wide"):
            raise HTTPException(400, f"unknown format {format!r}")
        markets = _parse_markets(market)
        books = book or None
        sim_engines = tuple(e for e in ex.SIM_ENGINES if e in set(engine)) if sim else ()
        scope = _scope(country, league, event_id, date, search)
        ticks = _build_ticks(regime, density, scope, markets, first_n, last_n)

        long_iter = ex.iter_long_rows(conn, ticks, markets=markets, books=books,
                                      sim_engines=sim_engines)

        suffix = "_with_simulated" if sim_engines else ""
        fname = f"odds_export_{regime}_{density}{suffix}.csv"

        def _emit_long():
            buf = io.StringIO()
            w = csv.DictWriter(buf, fieldnames=ex.LONG_COLUMNS, extrasaction="ignore")
            w.writeheader(); yield buf.getvalue(); buf.seek(0); buf.truncate(0)
            n = 0
            for row in long_iter:
                if n >= _MAX_ROWS:
                    break
                w.writerow({k: ex.csv_safe(v) for k, v in row.items()})
                yield buf.getvalue(); buf.seek(0); buf.truncate(0); n += 1

        def _emit_wide():
            cols, rows = ex.to_wide_rows(long_iter)
            buf = io.StringIO()
            w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
            w.writeheader(); yield buf.getvalue(); buf.seek(0); buf.truncate(0)
            for row in rows[:_MAX_ROWS]:
                w.writerow({k: ex.csv_safe(v) for k, v in row.items()})
                yield buf.getvalue(); buf.seek(0); buf.truncate(0)

        gen = _emit_wide() if format == "wide" else _emit_long()
        return StreamingResponse(
            gen, media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{fname}"'})
```

- [ ] **Step 4: Create `export.html`**

Create `src/odds_scraper/web/templates/export.html` extending `base.html`. It needs:
a `<form>` with `GET` action `/export.csv`; scope controls (reuse the country/league
select markup from `simulator.html`); regime radios labelled "Match state:
any / pre-match / in-play"; density radios labelled "Snapshots: all / only when odds
changed / latest per match"; a `format` radio (long default / wide); `first_n` /
`last_n` number inputs; a `sim` checkbox + `engine` checkboxes (V3, V4 — V4 checked);
the market checkbox list (HTMX `hx-get="/export/markets"` refreshing on scope change,
default rendered from `markets`); a live count badge (`hx-get="/export/count"`); and
a submit button. Read `simulator.html` and copy its control styling/HTMX patterns so
the page is visually consistent.

- [ ] **Step 5: Register routes + nav link**

In `src/odds_scraper/web/app.py`, near the existing
`from .pricer_routes import register_pricer_routes`, add
`from .export_routes import register_export_routes`, and in `create_app` (where
`register_pricer_routes(...)` is called) add:

```python
    register_export_routes(app, templates, db_path=db_path, conn=conn)
```

(Match the exact arg names `create_app` uses for `db_path`/`conn`.)

In `base.html`, add an `<a href="/export">Export</a>` nav link beside the existing
nav links.

- [ ] **Step 6: Run the route tests**

Run: `python -m pytest tests/test_export_routes.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/odds_scraper/web/export_routes.py src/odds_scraper/web/templates/export.html src/odds_scraper/web/app.py src/odds_scraper/web/templates/base.html tests/test_export_routes.py
git commit -m "feat(export): /export page + streaming CSV routes"
```

---

### Task 17: Phase 2 verification

- [ ] **Step 1: Full test run**

Run: `python -m pytest -q`
Expected: all green.

- [ ] **Step 2: Manual smoke**

Launch the app, open `/export`, pick a country/league, toggle markets, watch the
count badge, download LONG then WIDE, and download once with "include simulated
prices / V4" — confirm the filename gains `_with_simulated` and the CSV opens cleanly
in a spreadsheet (no formula execution on team names).

- [ ] **Step 3: Final commit**

```bash
git add -A && git commit -m "test(export): green end-to-end"
```

---

## Self-Review notes (for the implementer)

- **Spec coverage:** Phase 1 tasks 1–8 cover the V3+V4 migration, writer, backfill,
  read path, card/detail, simulator + profile UI. Phase 2 tasks 9–17 cover
  select_ticks, onchange-over-subset, first/last-N, LONG/WIDE, csv_safe, sim
  LEFT-join, markets picker, routes, streaming, and the row cap.
- **0.0 sentinel:** the 1x2 family is selectable at `line=0.0` (kept); parameterized
  markets are distinct `(market_id, line>0)` pairs — verified in
  `test_available_markets_for_scope` and `test_collapse_onchange_*`.
- **No inner-join deletion:** `_sim_rows` returns early when no
  `pricer_live_results` row exists, so real rows for non-UP markets always survive
  (`test_sim_rows_v3_v4_for_up_markets_only`).
- **Type consistency:** `LONG_COLUMNS` keys match `iter_long_rows`/`_sim_rows`
  output; `WIDE_META` ⊂ wide rows; `SIM_ENGINES=("v3","v4")` used by service + route.
- **Existing-test fallout:** Tasks 2, 5, 6, 7 explicitly update the V2-asserting
  tests in `test_pricer_live_writer.py`, `test_web_app.py`, `test_simulator_routes.py`.
