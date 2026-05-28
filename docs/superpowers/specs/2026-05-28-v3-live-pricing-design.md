# V3 Live Pricing (real matches, side-by-side with V2) — Design Spec

**Date:** 2026-05-28
**Branch:** `feat/v3-live-pricing`
**Status:** Draft

## Context

The V2 pricing engine runs on every live scraper tick: its 1UP/2UP odds and
probabilities are persisted per `(event_id, ts_utc)` and shown in the web UI as
an "OUR / SIM" column next to the bookmaker odds. V3 (the logit-margin engine
with the finalized odds-space upside-only cap, already on `main`) currently runs
**only in the simulator**.

We want V3 to run on **real matches too**, persisted in the DB, and displayed in
the UI **side-by-side with V2** — full parity with how V2 is computed, stored,
and rendered. This lets us watch V2 vs V3 diverge on live activity without
running a simulation.

## Current V2 live path (what we mirror)

- **Compute + persist (per tick):** `watcher.py` → `writer.append_pricer_live`
  → `live_writer.compute_and_write()` calls
  `engine_v2.price_early_payout_markets(**engine_kwargs)` and `INSERT OR REPLACE`s
  one row into `pricer_live_results`. No profile/config — the engine's hardcoded
  module defaults are used.
- **DB:** `pricer_live_results`, one row per `(event_id, ts_utc)`. Holds
  `lambda_home/away`, `basis_used`, the 12 `v2_*` columns (1UP/2UP × home/away ×
  prob/fair/capped), and dead `our_*` (V1) columns written as `NULL`.
- **Detail-page history:** reads the **persisted** `v2_*` columns via
  `queries.get_our_history_for_event` and renders them as a "sim" pseudo-book
  column in `event_detail.html`.
- **Home-card listing:** **recomputes** V2 live per render in
  `app._build_event_view` (cheap, avoids a JOIN) and shows it in the card SIM
  cell (`_event_card.html`).
- **Backfill:** `live_writer.backfill_all()` re-extracts engine inputs from the
  `prices` table per tick and writes rows that don't yet exist. Confirms the raw
  inputs needed to recompute are retained in `prices` (+ score in `snapshots`).

## Decisions (locked)

1. **Config:** V3 live uses `engine_v3`'s **hardcoded module defaults** (the
   finalized margin + odds-space cap). No profile — identical to V2 live.
2. **Backfill:** **all history** — fill V3 for every existing
   `pricer_live_results` row from its stored inputs.
3. **UI:** show V3 **both** in the event-detail history table and on the home
   cards, beside V2.

## Architecture

One new schema migration, one extra engine call in the existing writer, one
backfill entry point, and additive UI rendering. The scraper, the simulator, and
`engine_v2.py` / `engine.py` are untouched. V3 is **additive** everywhere —
no V2 behaviour, column, or value changes.

### 1. Schema migration v10 — `db_schema.py`

Bump `SCHEMA_VERSION` 9 → 10. Add `_MIGRATIONS[10]` calling
`_add_columns_if_missing(conn, "pricer_live_results", [...])` for 12 nullable
`REAL` columns mirroring the `v2_*` set:

```
v3_p_home_1, v3_p_away_1,
v3_1up_home_fair, v3_1up_home_capped, v3_1up_away_fair, v3_1up_away_capped,
v3_p_home_2, v3_p_away_2,
v3_2up_home_fair, v3_2up_home_capped, v3_2up_away_fair, v3_2up_away_capped
```

`lambda_home/away` and `basis_used` are shared (V3's lambda derivation is
identical to V2's) and are **not** duplicated. `_add_columns_if_missing` is
PRAGMA-guarded, so the migration is safe against partial completion. Existing
rows get `NULL` V3 until backfilled.

### 2. Live writer — `live_writer.compute_and_write()`

After `engine_kwargs` is built (unchanged), add a second engine call on the
**same** kwargs and extend the single `INSERT OR REPLACE` to write the `v3_*`
columns alongside `v2_*`:

```python
res_v3 = engine_v3.price_early_payout_markets(**engine_kwargs)
```

Wrapped in the same try/except contract as the V2 call: if V3 raises, log a
warning and write `NULL` V3 for that tick (the row + V2 still persist — a V3
crash must never drop a tick or block V2). One input extraction, two engine
calls per tick (negligible cost; the DP dominates and is independent per engine).

### 3. Backfill — `live_writer.backfill_v3()` + `scripts/backfill_v3_live.py`

`backfill_all()` only writes **missing** rows, so it won't touch existing rows
that already have V2 but `NULL` V3. Add a dedicated path:

- `backfill_v3(conn) -> tuple[int, int]`: select `pricer_live_results` rows where
  V3 has **not** been computed — i.e. **all 12 `v3_*` columns are `NULL`**. (A
  single per-side capped column is not a safe sentinel: a score-deactivated side
  legitimately stores `NULL` even when V3 ran. But any priceable tick yields at
  least one non-`NULL` V3 field — both sides can never be fully deactivated at
  once — so "all `v3_*` NULL" unambiguously means "not yet computed".) For each
  such row, re-extract engine inputs from `prices` for `(event_id, ts_utc)`
  exactly as `backfill_all` does (same score-from-`snapshots`, same bulk
  max-leads lookup), run `engine_v3`, and `UPDATE` **only** the `v3_*` columns
  for that row.
- **V2 columns are left untouched** — historical V2 values stay immutable; the
  backfill is purely additive. Idempotent (re-running skips rows that already
  have V3). Returns `(updated, skipped)`; skipped = ticks whose inputs can't
  price (matching V2's existing skip behaviour).
- `scripts/backfill_v3_live.py`: thin CLI that opens the DB, runs `init_schema`
  (applies v10), calls `backfill_v3`, prints `(updated, skipped)`. Run once after
  deploy.

### 4. History query — `queries.get_our_history_for_event`

Return V3 next to V2 per timestamp. The function currently yields a per-`ts`
dict of V2 OUR odds/probs (with a V1 fallback). Extend each entry to also carry
the V3 1UP/2UP capped odds + true probs from the `v3_*` columns. Shape stays a
plain dict keyed by `ts_utc`; V3 fields are `None` where not backfilled.

### 5. Detail-page UI — `event_detail.html` (+ its route builder)

Today the OUR block is a single "SIM" pseudo-book column (= V2). Render the OUR
block as **two columns, `V2` and `V3`**, side by side, each with the existing
odds + true-prob sub-cells, for the active market (1UP/2UP). A tick with no V3
(un-backfilled / engine-skip) shows blank V3 cells; V2 is unaffected. The
bookmaker columns are unchanged.

### 6. Home-card UI — `app._build_event_view` + `_event_card.html`

`_build_event_view` already recomputes V2 live for the latest snapshot. Add a
parallel `engine_v3.price_early_payout_markets(**engine_kwargs)` call on the same
inputs and surface `our_v3_1up_home/away`, `our_v3_2up_home/away` (+ probs)
beside the existing `our_*` (V2) fields. `_event_card.html` SIM area shows V2 and
V3 compactly (e.g. two small stacked rows under the SIM label: `V2` then `V3`).
V3 cells appear only when V3 priced this snapshot; otherwise the card looks as it
does today.

## Data flow

```
live tick  → watcher → live_writer.compute_and_write
                         ├─ extract inputs (once)
                         ├─ engine_v2 → v2_* columns      (unchanged)
                         └─ engine_v3 → v3_* columns      (new)
                         INSERT OR REPLACE pricer_live_results

backfill   → backfill_v3 → per existing row: extract from `prices`,
                            engine_v3 → UPDATE v3_* only

detail page → get_our_history_for_event → reads v2_* AND v3_* → V2 | V3 columns
home card   → _build_event_view → recompute engine_v2 AND engine_v3 (latest) → SIM V2/V3
```

## Error handling

- A V3 engine exception on a tick logs a warning and stores `NULL` V3; the row,
  its V2 values, and the watcher loop are unaffected.
- Backfill skips un-priceable ticks (insufficient 1X2/OU/FTTS) exactly as the V2
  backfill does, and never throws on a single bad tick.
- The migration is idempotent and partial-completion-safe (PRAGMA-guarded ALTERs
  in their own transaction with the version bump).

## Testing

- **Migration:** v10 adds the 12 `v3_*` columns; an existing v9 DB upgrades and
  pre-existing rows read with `NULL` V3.
- **Writer:** a tick writes both `v2_*` and `v3_*`; persisted `v3_*` equals a
  direct `engine_v3.price_early_payout_markets` call on the same extracted
  inputs; a forced V3 exception still persists the row with V2 set and V3 `NULL`.
- **Backfill:** `backfill_v3` fills `v3_*` for rows that had `NULL`; leaves
  `v2_*` byte-identical; is idempotent (second run updates 0); skips
  un-priceable ticks.
- **Query:** `get_our_history_for_event` returns V3 beside V2, with `None` V3
  where absent.
- **Web render:** event-detail page shows `V2` and `V3` OUR columns; home card
  shows V2 and V3 in the SIM area; both pages render when V3 is present and when
  it is `NULL` (no template error).

## Out of scope

- No scraper, simulator, `engine_v2.py`, or `engine.py` changes.
- No profile/config selection on the live path (V3 uses module defaults).
- No removal of the dead V1 `our_*` columns (separate cleanup if ever wanted).
- No new charts/analytics beyond showing V3 next to V2.
