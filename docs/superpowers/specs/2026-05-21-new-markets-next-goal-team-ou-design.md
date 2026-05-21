# New markets: next_goal + per-team Over/Under — design

**Status:** approved 2026-05-21
**Touches:** `models.py` (MARKET_MANIFEST extension only), test files that assert on derived counts
**Untouched:** `db_schema.py`, `collector.py`, `writer.py`, `watcher.py`, `event_resolver.py`, `registry.py`, `resolution*.py`, `status.py`, `config.py`, `main.py`, the entire `web/` subpackage

## Motivation

Three new markets that the user added to bookieskit upstream are now available in version `0.14.0` (already installed): `next_goal_ft`, `home_over_under_ft`, `away_over_under_ft`. They cover use-cases the existing manifest doesn't — next-goal pricing (which shifts dynamically in live as goals score) and per-team total-goals over/under (lower lines than the match total).

This sub-project is **pure manifest extension**. Because the architecture is built around the manifest-as-source-of-truth invariant (collector iterates it; writer takes whatever it produces; SQLite schema is normalized), adding three rows to `MARKET_MANIFEST` propagates everywhere automatically. The only collateral is updating tests that assert on derived counts (CSV column total, watcher tick-log denominators).

UX consumers — making the home-page card expander show the new markets, adding pills on the detail page — land in sub-project 3.

## Settled inputs

| Decision | Value |
|---|---|
| Bookieskit version | `0.14.0` (commit `360f24d`). Installed and verified. |
| `next_goal_ft` outcomes | `("home", "none", "away")` — bookieskit's exact strings |
| `next_goal_ft` lines | `(1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0)` — covers prematch (line=1) and live's shifting line up to any realistic scoreline |
| `home_over_under_ft` outcomes | `("over", "under")` |
| `home_over_under_ft` lines | `(0.5, 1.5, 2.5, 3.5, 4.5, 5.5)` |
| `away_over_under_ft` outcomes | `("over", "under")` |
| `away_over_under_ft` lines | `(0.5, 1.5, 2.5, 3.5, 4.5, 5.5)` |
| Column-prefix for csv/serialisation | `"ng"`, `"ou_home"`, `"ou_away"` — must be unique across the manifest |
| Schema migrations | **None.** `prices` table is normalized; new market_id values land as new rows. |
| Registry patches | **None.** Bookieskit 0.14.0 already maps all three across all 4 bookmakers (BP/SB/B9J/BW). |
| Default-visible (collapsed card view) | **Unchanged.** `web/queries.COLLAPSED_MARKETS` stays as the 1x2 family. New markets appear in expanded/detail views (sub-project 3). |

## Architecture

### Source-of-truth change

Single edit to `MARKET_MANIFEST` in `src/odds_scraper/models.py`. The full new manifest:

```python
MARKET_MANIFEST: tuple[MarketSpec, ...] = (
    MarketSpec("1x2_ft",        "1x2_ft",      ("home", "draw", "away"), None),
    MarketSpec("1x2_1up_ft",    "1x2_1up_ft",  ("home", "draw", "away"), None),
    MarketSpec("1x2_2up_ft",    "1x2_2up_ft",  ("home", "draw", "away"), None),
    MarketSpec(
        # column_prefix shortened to "ou"; must remain unique across manifest
        "over_under_ft", "ou", ("over", "under"),
        (1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5, 8.5, 9.5),
    ),
    MarketSpec(
        # next-goal: 3-way (home / none / away), line = goal number
        "next_goal_ft", "ng", ("home", "none", "away"),
        (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0),
    ),
    MarketSpec(
        # per-team O/U — home team's goals
        "home_over_under_ft", "ou_home", ("over", "under"),
        (0.5, 1.5, 2.5, 3.5, 4.5, 5.5),
    ),
    MarketSpec(
        # per-team O/U — away team's goals
        "away_over_under_ft", "ou_away", ("over", "under"),
        (0.5, 1.5, 2.5, 3.5, 4.5, 5.5),
    ),
)
```

### What flows through automatically

- **`collector._extract_prices_for_manifest`** iterates `MARKET_MANIFEST`. New markets get extracted from any `NormalizedMarket` returned by bookieskit's `parse_markets()` with one of the new canonical_ids.
- **`watcher._price_cell_count`** derives the per-bookmaker denominator from the manifest. The tick-log line updates automatically.
- **`writer._write_batch`** writes whatever `Snapshot.prices` contains. New rows just have new `market_id` values.
- **SQLite `prices` table** stores any `(snapshot_id, market_id, line, side)` tuple. No DDL change.

### Out-of-manifest line behaviour

Already implemented in `_extract_prices_for_manifest`: any line returned by bookieskit that isn't in the manifest's `lines` tuple is silently dropped. For `next_goal_ft` line=10 (hypothetical 10-goal match), the row is skipped. Same as today's `over_under_ft` handling.

### Per-bookmaker counts after manifest expansion

| Market | Lines | Sides | Outcomes |
|---|---|---|---|
| `1x2_ft` | 1 | 3 | 3 |
| `1x2_1up_ft` | 1 | 3 | 3 |
| `1x2_2up_ft` | 1 | 3 | 3 |
| `over_under_ft` | 9 | 2 | 18 |
| `next_goal_ft` | 9 | 3 | 27 |
| `home_over_under_ft` | 6 | 2 | 12 |
| `away_over_under_ft` | 6 | 2 | 12 |
| **Total per bookmaker** | | | **78 outcomes** |

Translates to:
- BP/SB cells (odds + probability): **156**
- B9J/BW cells (odds only): **78**
- Watcher tick-log line becomes: `tick <id> status=<X> bp=N/156 sb=N/156 b9j=N/78 bw=N/78`

`build_csv_header()` column total: 14 meta + (3+3+3+18+27+12+12)×2 = **170 columns**.

## Tests

| File | Change |
|---|---|
| `tests/test_models.py` | Update `test_build_csv_header_has_68_columns` → assert 170 columns; rename if desired. Update `test_snapshot_to_csv_row_meta_columns` and `test_snapshot_to_csv_row_blanks_when_failure_status` to assert `len(row) == 170` instead of 68. Extend `test_build_csv_header_price_section_order` to verify new market sections appear in the right order. |
| `tests/test_collector.py` | Add a new test: `_extract_prices_for_manifest` includes prices for the three new markets when present in the parsed-markets list. Add a test for out-of-manifest line skip (e.g., `next_goal_ft` line=10 — dropped). |
| `tests/test_watcher.py` | Update `test_log_tick_summary_format`: new denominators (156 BP/SB, 78 B9J/BW). Update the expected log string. The math in the test comment also needs updating. |
| All other test files | Unchanged. |

### Test rename consideration

`tests/test_models.py::test_build_csv_header_has_68_columns` — the literal `68` in the name will become stale after this change. Two options: rename to `test_build_csv_header_column_count` (data-driven) or just update the assertion and leave the name. The plan picks the rename to avoid future stale-name bumps as the manifest grows.

## Out of scope

- **UX consumers.** Card-expander reorder (next-goal first, then OU, then team-OU), detail-page pills for the new markets, dynamic-line filtering for available lines only. Sub-project 3.
- **Sport scope.** All three markets are soccer-only (the `sport="soccer"` attribute is in bookieskit's mapping). No multi-sport changes.
- **Bookmaker coverage gaps.** Bookieskit notes `sportpesa_id=None` for all three — we don't use SportPesa anyway, no concern.
- **The `_TeamScopedBetwayRegistry` wrapper.** Bookieskit's mapping notes Betway uses literal team names (`"[Home Team] Total"`) substituted at parse-time. That logic is entirely inside bookieskit — our code receives the canonical_id from `parse_markets`, no awareness needed.
- **Detail-page market pills.** Currently `web/app.py` defines `_MARKET_PICKER` with the 1x2 family + `_OU_LINES` for over_under_ft. Adding pills for `next_goal_ft` / `home_over_under_ft` / `away_over_under_ft` lines is sub-project 3.
