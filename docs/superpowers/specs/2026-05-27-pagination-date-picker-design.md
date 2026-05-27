# Pagination + Date Picker — Design Spec

**Date:** 2026-05-27
**Status:** Draft

## Goal

Add scroll-based infinite pagination (20 events per batch) across all tabs (live/upcoming/ended), remove the 24h ended cutoff, and add a date picker to the ended tab. Filters query the full dataset for available options; only event cards load in batches.

## Changes

### 1. Backend — `queries.py`

**`get_events_by_status`:**
- Add params: `offset: int = 0`, `limit: int = 20`, `date_from: str = ""`, `date_to: str = ""`.
- Remove the hardcoded `timedelta(hours=24)` cutoff for ended events entirely.
- When `date_from` is set, add `AND s.ts_utc >= :date_from` clause.
- When `date_to` is set, add `AND s.ts_utc < :date_to_next` clause (date_to + 1 day to include the full day).
- Append `LIMIT :limit OFFSET :offset` to the SQL.
- Return type stays `list[sqlite3.Row]`.

**New `get_filter_options(conn) -> dict`:**
- Returns `{"countries": [...], "date_range": {"min": "2026-05-21", "max": "2026-05-27"}}`.
- Countries/leagues come from the existing `get_country_league_index` (already queries full dataset).
- Date range: `SELECT MIN(DATE(kickoff_utc)), MAX(DATE(kickoff_utc)) FROM events`.
- Called once on page load to populate dropdowns/date picker bounds. Not paginated.

### 2. Backend — `app.py`

**Modify `/events` route:**
- Accept query params: `offset`, `limit`, `date_from`, `date_to` (in addition to existing `status`, `country`, `league`).
- Pass them through to `get_events_by_status`.
- Detect HTMX partial requests via `HX-Request` header. When present, return only the `_events_list.html` fragment (no full page wrapper). When absent, return the full `index.html` as today.
- Pass `get_filter_options` result to template on full page loads.

### 3. Frontend — Infinite Scroll via HTMX

**`_events_list.html`:**
- After the last event card in each batch, render a sentinel div with HTMX attributes:
  ```html
  {% if events|length == limit %}
  <div hx-get="/events?status={{ status }}&offset={{ offset + limit }}&country={{ country }}&league={{ league }}&date_from={{ date_from }}&date_to={{ date_to }}"
       hx-trigger="revealed"
       hx-swap="outerHTML"
       class="scroll-sentinel">
  </div>
  {% endif %}
  ```
- When the sentinel scrolls into view, HTMX fetches the next batch. The server returns more cards + a new sentinel (if there are more). When fewer than `limit` cards come back, no sentinel — scroll ends naturally.

**`index.html` — Ended tab filter bar:**
- Add two `<input type="date">` fields (From / To) in the filter area, visible only on the ended tab.
- `min`/`max` attributes set from `date_range` in filter options.
- On change, trigger HTMX request that replaces the events list with page 1 of filtered results:
  ```html
  <input type="date" name="date_from"
         hx-get="/events" hx-target="#events-list" hx-swap="innerHTML"
         hx-include="[name='status'],[name='country'],[name='league'],[name='date_from'],[name='date_to']"
         hx-trigger="change" />
  ```

**`app.js`:**
- Wire date picker visibility to the ended tab (hide on live/upcoming).
- Existing country/league filter logic unchanged — it already triggers HTMX re-fetches.

### 4. Per-Tab Behavior

| Tab | Pagination | Date Picker | Sort |
|-----|-----------|-------------|------|
| **Live** | 20 per scroll | No | By match_minute DESC |
| **Upcoming** | 20 per scroll | No | By kickoff_utc ASC |
| **Ended** | 20 per scroll | Yes (from/to) | By ts_utc DESC |

### 5. Filter + Scroll Interaction

- Changing any filter (country, league, date) resets to offset=0 and replaces the event list.
- Scrolling appends more events matching the current filter state.
- Filter dropdowns always show all available options (full dataset query), regardless of pagination state.

## What Does NOT Change

- Event detail page — no pagination needed (single event view).
- Simulator — independent routing, not affected.
- Scraper pipeline — no changes.
- Database schema — no migrations.

## Testing

- Query tests: verify offset/limit params work, date filtering returns correct events, empty results at end of pagination.
- HTMX integration: verify partial responses return only event cards (no full page), sentinel div appears when more events exist, doesn't appear on last batch.
- Date picker: verify ended tab shows date inputs, other tabs don't, date changes trigger re-fetch.
- Backward compat: verify full-page loads still work when no HX-Request header present.
