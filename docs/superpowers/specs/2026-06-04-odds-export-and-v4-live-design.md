# Design — Odds CSV Export + V4 in the Live Pipeline

Date: 2026-06-04
Status: Draft for review
Author: Lorenzo (with Claude)

## Goal

Add a way to **extract odds from the live-comparison web tool into CSV**, letting the
user choose tournaments, matches, markets, snapshot density, pre-match/in-play, and
whether to include OUR simulated prices.

Investigating "what are we simulating" surfaced a prerequisite: the live pipeline
stores **V2 + V3**, not the **V3 + V4** the user wants. So the work splits into two
sequential phases.

- **Phase 1 — Live pipeline → V3 + V4.** Compute and store V4 each tick; surface
  V3 + V4 in the cards/detail history; stop computing/showing V2.
- **Phase 2 — `/export` page.** Stream filtered raw odds (and optionally the stored
  V3/V4 simulated prices) as CSV.

Phase 2 depends on Phase 1 (stored V4). Build Phase 1 first.

---

## Background — how the data actually flows today

- `snapshots` — one row per (event, bookmaker) per scraper tick. `status` is
  `UPCOMING` (pre-match) / `STARTED` (in-play) / `ENDED`.
- `prices` — `(snapshot_id, event_id, bookmaker, market_id, line, side, odds,
  probability, ts_utc)`. The raw observed market. `line = 0.0` is the **sentinel**
  for non-parameterized markets (the 1x2 family); real parameterized lines are
  `> 0`.
- `pricer_live_results` — one row per `(event_id, ts_utc)`, written by
  `live_writer.compute_and_write` on every tick. **Stores V2 (`v2_*`) and V3
  (`v3_*`); the `our_*` (V1) columns exist but are written `NULL`; there are no
  `v4_*` columns.** This is the authoritative on-tick OUR record the detail page
  reads.
- The **home-page card SIM cell** and **detail-page history** read OUR from here
  (`get_our_history_for_event`) and also recompute live (`app.py:427` uses
  `engine_v2`; `app.py:439` uses `engine_v3`). That is why the UI shows **V2 + V3**.
- The **simulator** (`/simulator`, `runner_v2`) is a separate *re-pricing* what-if
  tool; it already supports V1–V4 (`VALID_ENGINES`, `with_v4_coefficients`) and
  emits a pricing CSV. V4 has only ever existed there, never in the live record.
- **No V4-specific profile fields** exist: `configs.py` defines V1-shared, V2-only,
  and V3-only tunables but no `V4_ONLY` set. V4 reads the shared ONEUP/TWOUP margin
  params via `with_v4_coefficients`'s `hasattr` filter. (Out of scope to change.)

---

## Phase 1 — V3 + V4 in the live pipeline

### Decision: V3 + V4 only across the whole tool; non-destructive data

The system going forward is **V3 + V4 only**. V1 and V2 are removed from the **entire
UI** (home cards, detail history, simulator engine picker, and profile config panels)
— not just the live comparison views. The tool offers only V3 and V4 everywhere.

**Data is handled non-destructively — no `DROP COLUMN`:**

- **Scraped data is never touched.** The scraped bookmaker odds live in `prices` /
  `snapshots`; this work does not modify them in any way. (They are exactly what the
  Phase 2 export reads out.)
- In `pricer_live_results`, the `our_*` (V1) and `v2_*` (V2) columns are **computed**
  engine output, not scraped. We **stop writing `v2_*`** (and `our_*` stays NULL as
  today) and **stop reading them**, but we **leave the columns in place** — an
  additive migration is the proven pattern here (v9 added V2, v10 added V3
  identically), and keeping the columns avoids a destructive migration while
  preserving historical values. The UI and export simply never reference V1/V2.
- Stored profile coefficients keep their V2-only tunable fields (optional,
  backfilled on load) for back-compat; those fields are just **hidden** from the
  profile form. No profile-schema change.

### 1.1 Schema — migration v11 (`db_schema.py`)

Add `11:` to `_MIGRATIONS`, mirroring v10 exactly:

```python
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

Nullable, so pre-v11 rows keep loading until backfilled.

### 1.2 Writer — `live_writer.compute_and_write`

- Compute **V3 (primary)** and **V4 (best-effort)**; remove the `engine_v2` call.
- **V3 is the must-succeed engine**: it supplies the shared `basis_used` /
  `lambda_home` / `lambda_away` (lambda derivation is engine-independent — v10's
  comment confirms it's identical across engines) and the `v3_*` block. If V3
  crashes, return `False` (skip the tick, as V2 does today).
- **V4 is best-effort**: on crash, log a warning and store `v4_*` as `NULL` (mirrors
  today's V3 handling). One bad V4 tick never drops the row or affects V3.
- INSERT writes `v3_*` and `v4_*`; `our_*` and `v2_*` are written `NULL`.

### 1.3 Backfill

- Add `backfill_v4(conn)` mirroring `backfill_v3`: target rows where all `v4_*` are
  `NULL`, re-extract inputs from `prices`, run `engine_v4`, `UPDATE` only `v4_*`.
- Update `backfill_all` to compute **V3 (primary) + V4** instead of V2 + V3 (the
  shared `lambda`/`basis` come from V3).
- Provide a runnable entry mirroring `scripts/backfill_v3_live.py`
  (`scripts/backfill_v4_live.py`) so existing history gets V4.

### 1.4 Read path — `queries.get_our_history_for_event`

Restructure the returned shape from "V2-primary + V3-beside" to explicit
**V3 + V4** keys:

```
{ts_utc: {
    "home_odds_v3": _, "away_odds_v3": _, "home_prob_v3": _, "away_prob_v3": _,
    "home_odds_v4": _, "away_odds_v4": _, "home_prob_v4": _, "away_prob_v4": _,
}}
```

(Drop the V1-fallback path — V3 is always present on a stored row by construction.)

### 1.5 Render path — `app.py`

- Card SIM cell + detail history: compute **`engine_v3` + `engine_v4`** live (drop
  the `engine_v2` call at `app.py:427`).
- Build `sim_v3` and `sim_v4` cells from the new query keys;
  `history_books = ("betpawa", "sportybet", "sim_v3", "sim_v4", "bet9ja", "betway")`.
- `show_sim_col` gate unchanged (1UP/2UP markets only).

### 1.6 Templates / UI — V3 + V4 only across the whole tool

- `event_detail.html` — SIM columns/headers become **V3** and **V4**; add a
  `--c-v4` colour var; remove the V2 (and V1) columns from this view.
- `_event_card.html` — card SIM cell shows **V3 + V4** (no V1/V2).
- `simulator.html` — engine picker offers **V3 + V4 only** (remove the V1 and V2
  checkboxes); default V4. The `runner_v2` backend may keep V1/V2 support
  internally (harmless); only the UI is restricted. `pricer_routes` already
  validates against `VALID_ENGINES`, so trimming the checkboxes needs no backend
  change.
- `_profile_fields.html` — show only the **V3/V4-relevant** tunable panels; hide the
  V1/V2 panels and V2-only fields (trailing margins). Stored profiles keep those
  fields (optional/backfilled) — they're just not rendered.

### 1.7 Phase 1 success criteria

- New ticks write non-NULL `v3_*` and `v4_*`, NULL `v2_*`/`our_*`.
- `backfill_v4` fills V4 on all historical rows that can price.
- No V1/V2 anywhere in the UI: cards, detail, simulator engine picker, and profile
  config panels all show **V3 + V4 only**.
- Scraped `prices`/`snapshots` untouched; no `DROP COLUMN` runs.
- `pytest` green.

---

## Phase 2 — `/export` page (raw odds → CSV)

### 2.1 Components

- **`web/export_service.py`** — the testable core (no FastAPI imports):
  - `select_ticks(conn, regime, density, scope)` → ordered ticks with metadata;
    `latest` uses a deterministic tiebreak (`MAX(ts_utc)`, then `MAX(snapshot_id)`).
  - `collapse_onchange(ticks, conn, markets)` — fingerprints **only the selected
    markets** (odds normalized to avoid float drift); drops a tick whose fingerprint
    equals the previous *kept* tick for that event.
  - `limit_first_last(ticks, first_n, last_n)` — applied **after** density.
  - `iter_long_rows(conn, ticks, markets, books, sim_engines)` — generator yielding
    LONG dict rows (memory-safe streaming).
  - `to_wide_rows(long_rows)` — deterministic post-transform, frozen sorted columns.
  - `csv_safe(value)` — escapes leading `= + - @` (spreadsheet formula injection).
- **`web/export_routes.py`** — `register_export_routes(app, templates, *, db_path,
  conn)` (mirrors `register_pricer_routes`):
  - `GET /export` — the page.
  - `GET /export/markets` — HTMX `(market_id, line)` checkboxes for in-scope events
    (1x2 family + parameterized lines via `get_available_lines`).
  - `GET /export/count` — HTMX badge: events · snapshots · estimated rows.
  - `GET /export.csv` — `StreamingResponse`; filters as query params;
    `Content-Disposition` filename gains a `_with_simulated` suffix when sim
    engines are included.
- **`web/templates/export.html`** + nav link in `base.html`. Reuses the simulator's
  country/league/event/date widgets, **relabeled in plain language**:
  - regime → "Match state: pre-match / in-play / any"
  - density → "Snapshots: all / only when odds changed / latest per match"

### 2.2 Filters (UI → query)

- **Scope:** country, league, event, date, search (reuse simulator vocabulary).
- **regime:** `any` / `prematch` (`UPCOMING`) / `live` (`STARTED`).
- **density:** `all` / `onchange` / `latest`.
- **markets:** multi-select of `(market_id, line)` present for in-scope events;
  default all. The 1x2 family carries `line = 0.0` (kept — it's the legitimate
  sentinel); parameterized markets carry `line > 0`. A selection is keyed
  `"{market_id}|{line}"` so parameterized lines never collapse together.
- **bookmakers:** multi-select (betpawa/sportybet/bet9ja/betway); default all.
- **first-N / last-N snapshots per event:** optional, applied after density.
- **include simulated prices:** checkbox + engine checkboxes **V3, V4** (default
  V4), read from stored `pricer_live_results`.

### 2.3 Output shapes (UI toggle, LONG default)

- **LONG (default):** one row per `(event, ts, bookmaker, market, line, side)`:
  `event_id, country_name, league_name, home, away, kickoff_utc, snapshot_id,
  ts_utc, status, match_minute, score_home, score_away, bookmaker, market_id,
  line, side, odds, probability, is_simulated, engine`.
  Simulated prices appear as extra rows: `bookmaker = OUR`, `engine = v4` (or v3),
  `is_simulated = 1`, `market_id ∈ {1x2_1up_ft, 1x2_2up_ft}`, side home/away,
  `odds = *_capped`, `probability = *_p_*`.
- **WIDE:** metadata once per `(event, ts)` + a **frozen, sorted** column set
  `{book}__{market}__{line}__{side}__odds` / `__prob`; simulated as
  `our_v4_1up_home_odds`-style suffix columns. Built purely from the LONG rows
  (one query, two serializers).

### 2.4 Simulated-price join (correctness)

`LEFT JOIN pricer_live_results ON (event_id, ts_utc)`, one engine block per row —
**never an inner join** (that would delete every non-1UP/2UP market). Sim values
exist only for the 1UP/2UP markets; all other markets keep their real rows with
blank sim. The join key `(event_id, ts_utc)` is unique per row, so no row
multiplication.

### 2.5 Delivery & limits

- Synchronous `StreamingResponse` lazily generating CSV rows from a DB cursor — no
  re-pricing happens (export reads stored values only), so there is no CPU work to
  background.
- Safety: `/export/count` shows an estimated row count before download; `/export.csv`
  enforces a configurable hard cap (default 2,000,000 rows) — a streamed counter
  stops cleanly and the response is finalized (logged) rather than runaway.
- Zero matches → header-only, parseable CSV.

### 2.6 Error handling & auth

- Bad `regime`/`density`/`format` → `400`. Unknown market selections ignored; if
  none valid → header-only CSV.
- **Auth: explicit non-goal** (trusted internal/local tool). CSV formula-injection
  escaping is still applied to text cells.

### 2.7 Phase 2 success criteria

- `/export` page renders with working scope/market/format controls and a live
  count badge.
- `/export.csv` streams a correct LONG CSV for a given filter set; WIDE toggle
  produces a stable-column transform of the same data.
- Including sim engines adds V3/V4 rows (LONG) / columns (WIDE) for 1UP/2UP only,
  with non-UP markets retained.
- `onchange` over a market subset drops only ticks unchanged *in the selected
  markets*.
- `pytest` green.

---

## Testing strategy (TDD, in-memory SQLite fixture)

Seed `events` / `snapshots` / `prices` / `pricer_live_results`, then test:

**Phase 1**
- `compute_and_write` stores non-NULL `v3_*` + `v4_*`, NULL `v2_*`; tick survives a
  V4 crash (V4 NULL, V3 present); tick is skipped on V3 crash.
- `backfill_v4` fills only `v4_*`, idempotent.
- `get_our_history_for_event` returns V3 + V4 keys.

**Phase 2**
- LONG row projection: regime + scope + market filter → exact expected rows.
- density: `latest` tiebreak; `onchange` over a subset (a hidden-market change does
  **not** keep the tick).
- `limit_first_last` ordering (after density).
- WIDE column stability + values.
- sim LEFT join: non-UP markets retained; sim rows/cols only for UP; no row
  multiplication.
- `csv_safe` escaping; `0.0` sentinel handling.
- Route smoke tests via FastAPI `TestClient` (`/export` 200; `/export.csv`
  content-type + filename; `/export/count`, `/export/markets`).

---

## Out of scope / non-goals

- V4-specific profile tunables (V4 reuses shared margin params).
- Physically dropping `v2_*` / `our_*` columns (functional drop only).
- Auth / API versioning / content negotiation on the export endpoint.
- Re-pricing in the export path (stored values only).
- Refactoring the simulator's `_select_ticks` into shared code (keep export
  self-contained until a real third caller appears).
