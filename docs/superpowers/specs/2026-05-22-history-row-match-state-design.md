# History-row match state column — design

**Status:** approved 2026-05-22
**Touches:** `src/odds_scraper/web/queries.py`, `src/odds_scraper/web/app.py`, `src/odds_scraper/web/templates/event_detail.html`, `tests/test_web_queries.py`, `tests/test_web_app.py`.
**Untouched:** anything outside `web/`.

## Motivation

The detail-page history table currently shows only `ts_utc` per row. For live-odds analysis you need to read each row alongside the match state at that exact tick — was that a 0-0 at minute 22 or a 1-0 at minute 34? The data is already in the `snapshots` table (`match_minute`, `score_home`, `score_away`); we just don't surface it on history rows.

The main-card meta line and detail-page header already show minute + score for the *current* tick. This change extends the same information to *every* historical row.

## Settled inputs

| Decision | Value |
|---|---|
| Where the new info appears | One new column in the history table, immediately after `TIME (UTC)`. Header: `STATE`. |
| Cell content (live event row) | `34' · 1–0` |
| Cell content (ended event row) | `FT · 1–0` (we render `FT` whenever `match_minute is None` AND `status` indicates ENDED, otherwise minute). |
| Cell content (upcoming-status row, or pre-kickoff sentinel) | `—` (single em-dash). |
| Column always rendered | Yes — for upcoming events the column still appears, just filled with `—`. Keeps the layout stable when an event transitions from upcoming → live and the user is already on the detail page. |
| Per-bookmaker variance | None expected (all four bookmakers' snapshots at the same `ts_utc` extract minute/score from the same BetPawa detail). Bucket per `ts_utc` and pick any one bookmaker's snapshot for the (minute, score). |
| Sport scope | Soccer only (current scope). |
| Layout impact | One extra `<th>` in the header row, one extra `<td>` per `<tr>`. The bookmaker column headers' `colspan` is unchanged because they already span `event.sides|length` columns, which is a separate concern. |

## Architecture

### Query change (`queries.py`)

`get_market_history_for_event` currently returns `(ts_utc, bookmaker, side, odds, probability)` rows. Extend with a JOIN to `snapshots` so each row also carries `match_minute`, `score_home`, `score_away`, and `snapshot_status` (the snapshot's `status` column — needed to distinguish ENDED from UPCOMING/STARTED for the `FT` rendering rule).

New SQL:

```sql
SELECT p.ts_utc, p.bookmaker, p.side, p.odds, p.probability,
       s.match_minute, s.score_home, s.score_away, s.status
FROM prices p
JOIN snapshots s
  ON s.event_id  = p.event_id
 AND s.ts_utc    = p.ts_utc
 AND s.bookmaker = p.bookmaker
WHERE p.event_id = ?
  AND p.market_id = ?
  AND p.line      = ?
  AND p.odds IS NOT NULL
ORDER BY p.ts_utc DESC, p.bookmaker, p.side
```

The `prices` table has no `status` column, so `s.status` doesn't collide with any existing alias and no `AS …` is needed.

The JOIN columns `(event_id, ts_utc, bookmaker)` uniquely identify a snapshot. No duplication.

### `HistoryRow` dataclass (`app.py`)

```python
@dataclass
class HistoryRow:
    ts_utc: str
    match_minute: Optional[int]
    score_home: Optional[int]
    score_away: Optional[int]
    status: str  # snapshot.status: "STARTED" | "UPCOMING" | "ENDED" | ""
    cells: dict[str, dict[str, PriceCell]]
```

`_build_event_detail` populates the new fields when bucketing per `ts_utc` by picking the first encountered snapshot's values (they're identical across bookmakers at the same ts).

### Template (`event_detail.html`)

In the history table head, add a `STATE` column header between `TIME (UTC)` and the bookmaker `<th>`s. The bookmaker `<th colspan="...">` blocks stay as-is.

In the history table sub-head row (the one with `H/D/A` short labels), add a `<th class="state-col"></th>` placeholder cell to keep the column count aligned.

In each `<tr>`, add `<td class="state-col">` after the `ts_utc` cell. Cell content is computed by a small Jinja macro or inline template logic:

```jinja
{% if row.match_minute is not none and row.status == 'STARTED' %}
  {{ row.match_minute }}' · {{ row.score_home }}–{{ row.score_away }}
{% elif row.status == 'ENDED' %}
  FT · {{ row.score_home }}–{{ row.score_away }}
{% else %}
  —
{% endif %}
```

(The `if … is not none` guard covers the case where a snapshot has `STARTED` status but no minute yet — rare but the writer can produce sentinel rows where status is set but other fields are null.)

### Edge cases

- Snapshot with `status=ENDED` but `score_home is none` → shows `FT · –` (literal en-dash between two empty score positions). Should be vanishingly rare since ENDED implies a final score, but defensive.
- A history row with no matching snapshot — shouldn't happen since the JOIN is required — falls back to `—` because all fields are absent.

## Tests

| File | New / changed tests |
|---|---|
| `tests/test_web_queries.py` | New: `test_get_market_history_for_event_includes_minute_and_score` — seed prices + snapshot with `match_minute=34, score_home=1, score_away=0`; assert returned row has those columns. New: `test_get_market_history_for_event_status_ended_carries_status` — seed ENDED snapshot; assert `status == "ENDED"`. |
| `tests/test_web_app.py` | New: `test_event_detail_history_row_renders_live_state` — seed live snapshot + price; assert response contains `"34' · 1–0"` in the table body. New: `test_event_detail_history_row_renders_ended_state` — seed ENDED snapshot; assert `"FT · 1–0"`. New: `test_event_detail_history_row_renders_dash_for_upcoming` — default fixture is UPCOMING; assert `class="state-col"` cells contain the em-dash for those rows. |

No collector / writer / models changes; no test changes outside the two web test files.

## Out of scope

- No new per-row UI for ts_utc reformatting (keep ISO-8601 in the cell).
- No goal-event highlighting / row coloring on score changes. (Could be a future polish.)
- No tooltip on the state cell.
- No per-bookmaker snapshot picking logic; we trust that all four bookmakers' snapshots at the same `ts_utc` carry identical minute/score (they share the same BetPawa detail extraction).
- Sport scope stays soccer.
