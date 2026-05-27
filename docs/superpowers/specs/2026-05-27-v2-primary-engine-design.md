# V2 as Primary Engine — Design Spec

**Date:** 2026-05-27
**Status:** Draft

## Goal

Make V2 the only engine shown in the web UI and the only engine running in the live scraper pipeline. V1 remains available exclusively in the simulator for A/B comparison runs.

## Background

Currently both V1 and V2 run on every scraper tick (via `live_writer.compute_and_write`), and the UI shows them side-by-side: stacked in event card SIM cells with `v1`/`v2` labels, and as separate "SIM v1" / "SIM v2" columns in the detail history table. V2 has proven stable and is the preferred model — the dual display is no longer needed outside the simulator.

## Changes

### 1. Live Pipeline — V2 Only

**File: `src/odds_scraper/pricer/live_writer.py`**

- `compute_and_write`: Remove the V1 engine call (`engine.price_early_payout_markets`). Only run V2.
- Write V2 results into the `v2_*` columns as today. Set all `our_*` columns (V1) to NULL on new inserts.
- Keep the V1 columns in the INSERT — they stay in the schema for historical data and simulator backfills. Just stop populating them from live ticks.
- `backfill_all`: Same change — V2 engine only.
- `snapshots_to_prices_by_book`: Unchanged (deals with raw bookmaker prices, not engine output).

**File: `src/odds_scraper/web/app.py`**

- Home page event card builder: Remove the V1 engine call that runs on card render. Only run V2. Populate the card's SIM fields from V2 output.

### 2. Event Cards — Single SIM Column

**File: `src/odds_scraper/web/templates/_event_card.html`**

- Remove the stacked v1/v2 layout (the `sim-tag` spans labeling "v1" and "v2").
- SIM cell shows a single value per side, sourced from V2 fields (`v2_1up_home`, `v2_1up_away`, etc.).
- Rename template variables: the card builder in `app.py` should populate the existing `our_*` view fields from V2 engine output so the template reads a single set of values (no `v2_*` prefix needed in the template).

### 3. Detail History — Single SIM Column

**File: `src/odds_scraper/web/app.py` (`_build_event_detail`)**

- Map V2 fields from `get_our_history_for_event` into the `"sim"` pseudo-bookmaker slot (currently V1 goes to `"sim"` and V2 goes to `"sim_v2"`).
- Drop the `"sim_v2"` pseudo-bookmaker entirely.
- Remove the `show_sim_v2_col` conditional logic.
- The `books` tuple always uses `"sim"` (no `"sim_v2"`).

**File: `src/odds_scraper/web/templates/event_detail.html`**

- Remove `"sim_v2"` from the book labels map.
- Rename `"sim"` label from `"SIM v1"` to `"SIM"`.
- The column renders identically to today's V2 column, just without the version suffix.

**File: `src/odds_scraper/web/queries.py`**

- `get_our_history_for_event`: Return V2 fields as the primary `home_odds`/`away_odds`/`home_prob`/`away_prob` keys. Drop or rename the `v2_*` prefixed keys since there's no longer a need to distinguish.
- For historical rows where V2 is NULL (pre-schema-v9), fall back to V1 values so the history table isn't empty for old events.

### 4. Simulator — No Changes

- Engine radio buttons (v1 / v2 / both) remain as-is.
- `runner.py` (V1-only), `runner_v2.py` (dual), `csv_export.py` all unchanged.
- Profile management (`configs.py`) unchanged — V1-specific and V2-specific tuning knobs stay.
- Simulator routes (`pricer_routes.py`) unchanged.

## What Does NOT Change

- **Database schema**: No migrations. V1 columns stay in `pricer_live_results` for historical data.
- **Engine modules**: `engine.py` and `engine_v2.py` untouched.
- **Simulator**: Full V1/V2/both capability preserved.
- **Config system**: All tunable knobs remain.

## Testing

- Existing tests for `live_writer` updated to verify only V2 runs.
- Event card tests (`test_web_app.py`) updated: SIM cell shows single value, no v1/v2 tags.
- Detail history tests: single "SIM" column with V2 data.
- Verify historical events (pre-V2) still show V1 data as fallback in history.
- Simulator tests unchanged — they already test V1/V2/both independently.
