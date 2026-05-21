# UX consumers: country/league filter + new-market expander + two-stage detail pills — design

**Status:** approved 2026-05-22
**Touches:** `src/odds_scraper/web/app.py`, `src/odds_scraper/web/queries.py`, templates under `src/odds_scraper/web/templates/`, `src/odds_scraper/web/static/app.js`, plus tests under `tests/test_web_app.py` and `tests/test_web_queries.py`.
**Untouched:** `models.py`, `collector.py`, `writer.py`, `watcher.py`, `db_schema.py`, `event_resolver.py`, `registry.py`, `resolution*.py`, `status.py`, `config.py`, `main.py`. No schema migration. No new collected data.

## Motivation

Sub-projects 1 and 2 added country/league capture and three new markets (`next_goal_ft`, `home_over_under_ft`, `away_over_under_ft`) to the data pipeline but intentionally deferred all UX consumers. This sub-project lands the UX side in a single coherent pass:

1. The web UI cannot currently render `next_goal_ft` at all — its `none` outcome string is missing from `_SIDE_LABEL` / `_SIDE_SHORT` and would raise `KeyError` at template render time.
2. Country and league are captured but not displayed anywhere or used as filter dimensions.
3. The home-page card expander only knows about `over_under_ft`; the three new markets need to appear in order.
4. The detail-page pill row was designed for 12 pills (3 × 1x2 + 9 OU); the new markets push that to ~36, which calls for a two-stage chip selector.

## Settled inputs

| Decision | Value |
|---|---|
| Filter row UX | Cascading single-select dropdowns: Country → League. Picking "All" on Country resets League to "All". |
| Detail-page pills | Two-stage: family-chip row, then line-chip row visible only when a parameterized family is active. |
| Line-chip filtering | Only show lines that have at least one priced outcome in this event's price history. |
| Card expander order | After 1x2 family: `next_goal_ft` → `over_under_ft` → `home_over_under_ft` → `away_over_under_ft`. |
| Family-chip disabled state | A parameterized family with zero priced lines for the event is greyed out and non-clickable. |
| Detail-page subtitle | `{country_name} · {league_name}`. Renders just the present field if one is empty; omitted entirely if both empty. |
| Persistence | Country + league selections persist in the existing localStorage filter key alongside kickoff/search. |
| Default market on detail page | Unchanged: `1x2_2up_ft`. |
| URL slug scheme | Same `{column_prefix}_{line}` pattern as today; new slug values added (`ng_*`, `ou_home_*`, `ou_away_*`). Existing bookmarks (`1x2_ft`, `ou_2.5`, …) keep resolving unchanged. |
| Country/league index endpoint | None — embedded as JSON in `index.html` on initial page load. |
| Multi-sport | Out of scope (today everything is soccer). |
| Backfill | None — events with empty country/league appear in "All / All" only, as today. |

## Architecture

### Source-of-truth hookup

A single ordered table in `web/app.py` declares the expander/family-chip display order and labels:

```python
_EXPANDER_MARKETS: tuple[tuple[str, str], ...] = (
    ("next_goal_ft",       "Next Goal"),
    ("over_under_ft",      "Match O/U"),
    ("home_over_under_ft", "Home O/U"),
    ("away_over_under_ft", "Away O/U"),
)
```

The 1x2 family display order stays in the existing `_MARKET_LABELS` / `_COLLAPSED_ORDER` / `queries.COLLAPSED_MARKETS` table — unchanged.

For everything else, the UX reads from `MARKET_MANIFEST` directly via a small lookup:

```python
_spec_by_id: dict[str, MarketSpec] = {s.canonical_id: s for s in MARKET_MANIFEST}

def _sides_for(market_id: str) -> tuple[str, ...]:
    return _spec_by_id[market_id].sides
```

This replaces the existing hardcoded `_SIDES_1X2` and `_SIDES_OU` constants. Adding a future market with new sides only requires updating `_SIDE_LABEL` / `_SIDE_SHORT` if it introduces new outcome strings; the rest follows automatically.

### Side labels

```python
_SIDE_LABEL = {
    "home": "Home", "draw": "Draw", "away": "Away",
    "over": "Over", "under": "Under",
    "none": "None",
}
_SIDE_SHORT = {
    "home": "H", "draw": "D", "away": "A",
    "over": "O", "under": "U",
    "none": "N",
}
```

The added `"none"` entry is the single safety net that makes `next_goal_ft` renderable.

### Card view — `_build_event_view`

Replace the existing inline `_OU_LINES` loop with a single loop over `_EXPANDER_MARKETS`. For each `(canonical_id, ui_label_prefix)`:

```python
spec = _spec_by_id[canonical_id]
for line in spec.lines or (None,):
    rows = []
    for side in spec.sides:
        line_key = line if line is not None else 0.0
        prices = bucket.get((canonical_id, line_key, side), {})
        rows.append(OutcomeRow(
            market_label=_short_label(ui_label_prefix, line),
            side_label=_SIDE_LABEL[side],
            side_short=_SIDE_SHORT[side],
            prices=prices,
        ))
    if any(r.prices for r in rows):
        groups.append(MarketGroup(
            label=_group_label(ui_label_prefix, line),
            rows=rows,
            is_extra=True,
        ))
```

Helpers:
- `_short_label("Next Goal", 1.0) -> "NG 1"`, `_short_label("Match O/U", 2.5) -> "OU 2.5"`, `_short_label("Home O/U", 1.5) -> "H-OU 1.5"`, `_short_label("Away O/U", 0.5) -> "A-OU 0.5"`.
- `_group_label` uses the full prefix: `Next Goal 1`, `Match O/U 2.5`, `Home O/U 1.5`, `Away O/U 0.5`.

All four parameterized markets carry `is_extra=True`. The single existing "expand" toggle in the template reveals all four groups together; the button copy changes from "Show OU lines" to "Show more odds".

### Filter row — country/league cascading dropdowns

New query in `queries.py`:

```python
def get_country_league_index(conn) -> list[dict]:
    """Return [{country_id, country_name, leagues: [{league_id, league_name}]}, ...]
    sorted by country_name, leagues sorted by league_name. Excludes rows with
    empty country_name."""
```

Implementation: one `SELECT DISTINCT country_id, country_name, league_id, league_name FROM events WHERE country_name IS NOT NULL AND country_name != '' ORDER BY country_name, league_name` then group in Python. Cheap because cardinality is bounded.

`queries.get_events_by_status(conn, status, *, country_id="", league_id="")` gains the two optional filter kwargs. Empty string = no filter. Non-empty splices `AND country_id = ?` / `AND league_id = ?` into the existing WHERE clause with bound parameters (no string interpolation).

The `/events` FastAPI route accepts `country: str = Query("")` and `league: str = Query("")` and passes them through.

The `/` (index) route fetches the country/league index and embeds it in the response as:

```html
<script type="application/json" id="country-league-index">
{"items": [{"country_id": "242", "country_name": "Germany", "leagues": [...]}, ...]}
</script>
```

The client reads this on boot, populates the Country `<select>`, and updates the League `<select>` whenever the Country selection changes.

### Detail page — subtitle + available-lines query

`_build_event_detail` consumes two new pieces of data:

1. `country_name` and `league_name` from the `events` row join. `queries.get_event_meta` is extended to add `e.country_name, e.league_name` to its SELECT (the columns already exist in the table since sub-project 1; the query just doesn't surface them today). Template renders `{{ country_name }} · {{ league_name }}` with conditional joiner logic.

2. New query `queries.get_available_lines(conn, event_id) -> dict[str, list[float]]`:

   ```sql
   SELECT DISTINCT market_id, line FROM prices
   WHERE event_id = ? AND line > 0
   ORDER BY market_id, line
   ```

   The Python wrapper groups by `market_id`. The `line > 0` clause filters out the `0.0` sentinel that `SqliteWriter` stores for non-parameterized markets (1x2 family); valid parameterized lines like `0.5` are kept (`0.5 > 0` is true). The returned dict shape is `{"over_under_ft": [2.5, 3.5], "next_goal_ft": [1.0, 2.0], …}`.

The pill builder constructs:

- **Family pills** (always rendered): one per entry in `_EXPANDER_MARKETS` plus the three 1x2 family entries. A parameterized family pill is `disabled=True` when its `market_id` is missing from `available_lines` (no priced lines for this event).
- **Line pills** (rendered only when active family is parameterized): one per line in `available_lines[active_market_id]`, sorted ascending.

Active-family default for a parameterized family in URL navigation is `min(available_lines[market_id])` — i.e., clicking "Match O/U" with available lines `[2.5, 3.5]` lands you on `?market=ou_2.5`.

### Templates

- `templates/index.html`: filter row gains two `<select>` elements before the existing kickoff selector. Embedded `<script id="country-league-index">` JSON tag.
- `templates/_events_list.html`: toggle button label changes; otherwise unchanged (it already iterates `is_extra` groups generically).
- `templates/event_detail.html`: header gains the subtitle `<div>`. Pill row block restructured into family row + conditional line row. Family pills use `disabled` class when the family has no available lines.

### Static JS (`static/app.js`)

- Parse `#country-league-index` JSON on boot. Populate Country `<select>`.
- On Country change: filter to that country's leagues, repopulate League `<select>`, reset to "All".
- On either Country or League change: trigger HTMX request to `/events?status=…&country=…&league=…` (the existing tab `status` lives in client state already).
- Persist `{country_id, league_id}` to localStorage alongside existing filter keys. Rehydrate on page load. If a stored value isn't in the current index, reset to "All".

## Tests

| File | Change |
|---|---|
| `tests/test_web_queries.py` | New: `test_get_country_league_index_groups_by_country` (two countries, three leagues total). New: `test_get_country_league_index_skips_empty_countries`. New: `test_get_events_by_status_filters_by_country_id`. New: `test_get_events_by_status_filters_by_league_id`. New: `test_get_available_lines_returns_only_lines_with_data` (insert prices for over_under_ft 2.5 and 3.5; assert result dict). New: `test_get_available_lines_excludes_sentinel_zero_lines` (insert 1x2_ft snapshot with line=0; assert 1x2_ft absent from result). Helper: `_with_country_league(...)` to insert an event row with given country/league. |
| `tests/test_web_app.py` | New: `test_index_embeds_country_league_index_json` (asserts script tag presence + parseable JSON). New: `test_events_fragment_filters_by_country_and_league`. New: `test_events_card_includes_next_goal_group_when_priced`. New: `test_events_card_includes_per_team_ou_groups_when_priced`. New: `test_events_card_omits_market_groups_with_no_data` (per-team OU with no prices → no group rendered). New: `test_event_detail_subtitle_renders_country_and_league`. New: `test_event_detail_subtitle_omits_when_both_empty`. New: `test_event_detail_pills_family_row_includes_new_markets`. New: `test_event_detail_pills_line_row_filters_to_available_lines`. New: `test_event_detail_disables_family_pill_when_no_lines_available`. Existing `test_event_detail_pills_include_ou_lines` updated to assert against the new family + line two-stage structure. |
| Other test files | Unchanged. |

No new integration tests; the scraper-side test coverage is unchanged.

## Out of scope

- **Schema changes.** `events.country_*` / `league_*` already exist; `prices.market_id` / `line` are already indexable for the available-lines query.
- **New collected data.** Sub-project 3 is purely consumer-side.
- **Multi-sport support.** All UX assumes soccer.
- **Prematch vs live differentiation** for `next_goal_ft` line ("which is the current active goal number"). Whatever bookmakers price, we show; no live-state inference.
- **Multi-select country filter.** Cascading single-select per design decision.
- **CSV export / ClickHouse export.** Deferred per prior conversation.
- **Backfill** of legacy events lacking country/league. They appear in "All / All" only.
- **Server-side rendering of the country/league dropdowns.** Client populates from embedded JSON; no separate `/countries` endpoint.
