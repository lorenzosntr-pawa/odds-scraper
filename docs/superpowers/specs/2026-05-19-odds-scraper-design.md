# 1up / 2up odds scraper — design

**Status:** Approved
**Date:** 2026-05-19
**Owner:** lorenzo.santoro@pawatech.com

## Goal

Continuously scrape **1up** and **2up** odds for four BetPawa-anchored Nigerian soccer events across **BetPawa, SportyBet, Bet9ja, Betway**. Capture odds and bookmaker-exposed probability (where exposed: BetPawa, SportyBet). Once a match is live, also capture match minute and score. Persist every snapshot to a single CSV.

## Scope

### In scope
- Country: Nigeria (`ng`) on every bookmaker
- Sport: soccer
- Markets: `1x2_1up_ft`, `1x2_2up_ft`
- Outcomes: home / draw / away
- Bookmakers: BetPawa, SportyBet, Bet9ja, Betway
- Events (BetPawa internal ids): `33660318`, `33660319`, `33605719`, `33575997`
- Cadence: 10 min prematch, 90 s live (configurable)
- Output: single append-only CSV
- Lifecycle: prematch → live → ended (driven by BetPawa status)

### Out of scope (v1)
- Other markets (1x2 full-time, BTTS, O/U, etc.)
- Other sports
- MSport, SportPesa, Betika (bookieskit supports them, but not requested here)
- Country failover
- Implied probability computation
- Metrics / alerting / health endpoints
- CSV rotation

## Decisions

1. **BetPawa exposes 1up/2up but is not in the bookieskit builtin registry.** We extend the registry locally with `MarketMapping`s for `1x2_1up_ft` and `1x2_2up_ft` on BetPawa.
2. **"Probability" means the bookmaker-exposed field**, not implied probability from odds. Only BetPawa and SportyBet expose it; the `probability` column is empty for Bet9ja and Betway.
3. **Runtime model:** one long-running Python process with an internal asyncio scheduler — one task per event.
4. **CSV layout:** single wide CSV `data/odds_snapshots.csv` covering every event.
5. **Status source:** BetPawa event status drives cadence (prematch → live → ended); 3 h post-kickoff watchdog forces stop if status never flips.
6. **ID resolution:** mappings cached in memory and persisted to `data/resolution_cache.json`. Cross-bookmaker matching uses **both SR id and BetGenius id** via union-find (BetPawa publishes both; SR id flips primary when matches go live and BetPawa starts routing through BetGenius).
7. **Missing odds:** always emit the row with empty odds/probability and a `fetch_status` reason — gaps are explicit.

## Architecture

```
                    ┌────────────────────────────────────────┐
                    │             main.py (entrypoint)       │
                    │  - parse args / config                 │
                    │  - construct shared clients & writer   │
                    │  - spawn one EventWatcher per event    │
                    │  - supervise (restart on crash)        │
                    └────────────┬───────────────────────────┘
                                 │
        ┌────────────────────────┼────────────────────────┐
        │                        │                        │
 EventWatcher(33660318)   EventWatcher(33660319)   ... (one per event)
   │ owns lifecycle         │
   │ for that event:        │
   │  - poll BP status      │
   │  - decide cadence      │
   │  - call OddsCollector  │
   │  - write rows          │
   ▼                        ▼
 OddsCollector (shared, stateless)
   │ for one event tick:
   │  - resolve cross-bookmaker ids (cache)
   │  - fan out 4 fetches in parallel
   │  - parse markets → normalized rows
   ▼
 ResolutionCache      Bookmaker clients (shared)
 (JSON-backed)        BetPawa, SportyBet, Bet9ja, Betway
                      (single instance each — bookieskit async clients)
   │
   ▼
 CsvWriter (async-locked)
   └─→ data/odds_snapshots.csv
```

**Concurrency:** single asyncio event loop, one task per event, 4 bookmaker fetches per tick run concurrently with `asyncio.gather`. All bookmaker clients are constructed once inside an `AsyncExitStack` and shared across watchers.

**Cadence per event (driven by BetPawa status):**

| Status                | Cadence          |
| --------------------- | ---------------- |
| `UPCOMING` (prematch) | 600 s            |
| `STARTED` (live)      | 90 s             |
| `ENDED` / `FINISHED`  | final tick, exit |

Watchdog: a watcher force-exits if it is still running 3 h after the BetPawa kickoff time and status has not transitioned to ended (covers stuck status flips).

## Repository layout

```
check_merging/
├── pyproject.toml              # deps: bookieskit @ git+..., python>=3.11
├── README.md                   # how to run, config, file layout
├── config.yaml                 # event ids, country, intervals, output paths
├── src/
│   └── odds_scraper/
│       ├── __init__.py
│       ├── main.py             # entrypoint, supervisor, signal handling
│       ├── config.py           # load + validate config.yaml
│       ├── watcher.py          # EventWatcher — per-event lifecycle
│       ├── collector.py        # OddsCollector — one-tick fan-out fetch
│       ├── resolution.py       # ResolutionCache — id mapping, JSON-backed
│       ├── writer.py           # CsvWriter — async-locked append, headers
│       ├── registry.py         # extend bookieskit registry: BP 1up/2up
│       ├── status.py           # BetPawa status field → enum + helpers
│       └── models.py           # dataclasses: Snapshot, ResolvedIds, etc.
├── tests/
│   ├── test_writer.py
│   ├── test_resolution.py
│   ├── test_registry.py
│   ├── test_watcher_cadence.py
│   ├── test_collector.py
│   └── fixtures/               # captured JSON responses per bookmaker
└── data/
    ├── odds_snapshots.csv      # the output
    └── resolution_cache.json   # persisted id mappings
```

## Components

### `watcher.EventWatcher`
Owns one event. State machine: `unknown → upcoming → live → ended`. Decides cadence, calls `OddsCollector`, writes rows to the shared writer. Exits on `ENDED` or 3 h watchdog. Status-poll failure is retried with backoff (5 s, 15 s, 45 s); after 3 consecutive failures, emits a sentinel `http_error` tick and continues on normal cadence.

### `collector.OddsCollector`
Pure async function `collect(bp_detail) → list[Snapshot]`. Stateless. Resolves cross-bookmaker ids via `ResolutionCache`, fans out 4 fetches via `asyncio.gather`, returns a fixed-shape list of rows (always 4 bookmakers × 2 markets × 3 outcomes = **24 rows per tick**) including failure rows with explicit `fetch_status`. **Never raises** — every error path produces rows.

### `resolution.ResolutionCache`
Get-or-resolve. Loads from `resolution_cache.json` at startup; persists incrementally (one fsync per new entry) so a hard kill never loses cached ids. Cache key is `(betpawa_id, regime)` where `regime ∈ {prematch, live}` — because Bet9ja switches from internal id (prematch) to BetGenius id (live) for the same event.

Resolution uses bookieskit's `extract_event_ids(...)` to pull both SR id and BetGenius id from the BetPawa detail response, then performs union-find against each other bookmaker's known provider ids:

| Bookmaker | Prematch lookup                   | Live lookup                       |
| --------- | --------------------------------- | --------------------------------- |
| SportyBet | SR id (`sr:match:<id>`) direct    | SR id direct, `live=True`         |
| Bet9ja    | `build_prematch_event_map(sport)` | BetGenius id via live endpoint    |
| Betway    | SR id direct                      | SR id direct                      |

If neither SR id nor BetGenius id resolves, the entry is marked stale and next tick will retry.

### `writer.CsvWriter`
Async context manager. One `asyncio.Lock` around appends. Writes the header row once if the file does not exist. UTF-8 encoding, line-buffered. Concurrent appends from different watchers are serialized.

### `registry.py`
Builds a `MarketRegistry` = bookieskit builtins + BetPawa `1x2_1up_ft` / `1x2_2up_ft` mappings. Exposed via `build_registry() -> MarketRegistry`. Used by both `OddsCollector` and tests.

### `status.py`
Single parsing source-of-truth for BetPawa event detail:
- `parse_status(detail) -> EventStatus` (`UPCOMING | STARTED | ENDED | SUSPENDED`)
- `parse_clock(detail) -> int | None` (minutes; `45+2` → `47`, `HT` → 45 sentinel, otherwise `None`)
- `parse_score(detail) -> tuple[int, int] | None`

### `models.py`
Dataclasses: `Snapshot`, `ResolvedIds`, `EventStatus` enum, `FetchStatus` enum.

## Data flow per tick

```
┌─────────────────── EventWatcher.tick(event_id) ───────────────────┐
│ 1. BetPawa.get_event_detail(event_id)                             │
│      → status, kickoff, home, away, score (if live), clock        │
│ 2. If status changed: log + update next-cadence                   │
│ 3. If status == ENDED: write final tick, mark done, return        │
│ 4. snapshots = await OddsCollector.collect(detail)                │
│ 5. for row in snapshots: writer.append(row)                       │
│ 6. sleep(cadence for current status)                              │
└───────────────────────────────────────────────────────────────────┘

OddsCollector.collect(bp_detail):
    ids = ResolutionCache.get_or_resolve(bp_detail)
    results = asyncio.gather(
        fetch_betpawa_markets(bp_detail),         # reuse the detail response
        fetch_sportybet(ids.sportybet, live=ids.is_live),
        fetch_bet9ja(ids.bet9ja, live=ids.is_live),
        fetch_betway(ids.betway, live=ids.is_live),
        return_exceptions=True,
    )
    → flatten to 24 rows, attach (ts_utc, status, score, clock, teams)
```

**One BetPawa call per tick:** the status poll response is reused as the BetPawa odds source — no second round-trip.

## CSV schema

File: `data/odds_snapshots.csv`. One row per `(timestamp, event, bookmaker, market, outcome)`.

```
ts_utc, event_bp_id, sr_id, genius_id,
home, away, kickoff_utc,
status, match_minute, score_home, score_away,
bookmaker, market, outcome,
odds, probability,
fetch_status, fetch_error
```

| Column                       | Type                          | Notes                                                                                  |
| ---------------------------- | ----------------------------- | -------------------------------------------------------------------------------------- |
| `ts_utc`                     | ISO 8601 (`...Z`)             | Snapshot wall-clock at fan-out start                                                   |
| `event_bp_id`                | str                           | BetPawa internal id — the anchor                                                       |
| `sr_id`                      | str \| empty                  | SportRadar id once known                                                               |
| `genius_id`                  | str \| empty                  | BetGenius id once known (live-relevant)                                                |
| `home`, `away`               | str                           | From BetPawa event detail                                                              |
| `kickoff_utc`                | ISO 8601                      | From BetPawa                                                                           |
| `status`                     | enum                          | `UPCOMING` / `STARTED` / `ENDED` / `SUSPENDED`                                         |
| `match_minute`               | int \| empty                  | Populated when `STARTED` (`45+2`→`47`, `HT`→`45`)                                      |
| `score_home`, `score_away`   | int \| empty                  | Populated when `STARTED` or `ENDED`                                                    |
| `bookmaker`                  | enum                          | `betpawa` / `sportybet` / `bet9ja` / `betway`                                          |
| `market`                     | enum                          | `1x2_1up_ft` / `1x2_2up_ft`                                                            |
| `outcome`                    | enum                          | `home` / `draw` / `away`                                                               |
| `odds`                       | float \| empty                | Decimal odds, 2 dp                                                                     |
| `probability`                | float \| empty                | Bookmaker-exposed field — only populated for `betpawa` and `sportybet`, 5 dp           |
| `fetch_status`               | enum                          | `ok` / `suspended` / `not_offered` / `lookup_failed` / `http_error` / `parse_error`    |
| `fetch_error`                | str \| empty                  | Short reason when `fetch_status != ok`                                                 |

**Row count per tick:** always exactly 24 rows. Failure does not reduce the row count — gaps are explicit.

**Volume estimate:** ~5 K rows per event per match-day (prematch 24 h @ 10 min ≈ 3.5 K + live ~95 min @ 90 s ≈ 1.5 K). 4 events ≈ 20 K rows/day. CSV at this scale is trivial.

## Error handling

### Inside `OddsCollector` — per-bookmaker fetch
Each fetch wrapped in `try/except` and mapped to a `fetch_status`:

| Failure                                          | `fetch_status` | Behavior                                            |
| ------------------------------------------------ | -------------- | --------------------------------------------------- |
| Cross-id resolution returned nothing             | `lookup_failed`| Emit 6 empty rows; cache entry marked stale         |
| Bookmaker HTTP error (4xx/5xx, timeout)          | `http_error`   | Emit 6 empty rows                                   |
| Parse succeeded, market not in response          | `not_offered`  | Emit 6 empty rows                                   |
| Market present, outcome has no price             | `suspended`    | Emit row with empty odds                            |
| Unexpected exception inside parse                | `parse_error`  | Log traceback at WARN, emit empty rows              |

The collector itself never raises.

### Inside `EventWatcher`
Only the BetPawa status poll can raise. Strategy:
- Up to 3 consecutive failures: WARN + exponential backoff (5 s, 15 s, 45 s)
- After 3: ERROR + sentinel `http_error` tick + keep going on normal cadence

A watcher only exits cleanly on `status == ENDED` or the 3 h watchdog.

### Supervisor (in `main.py`)
Wraps each watcher task. On uncaught exception: log traceback at ERROR, sleep 30 s (backing off to 5 min max), restart the task.

### Logging
Standard `logging` to stderr, structured `{event_id, bookmaker, action, …}`:
- INFO — tick complete, status change, supervisor restart
- WARN — single-fetch failure, status-poll retry
- ERROR — watcher crash, supervisor restart loop
- DEBUG — raw response shape (gated by `ODDS_SCRAPER_DEBUG=1`, off by default)

### Shutdown
- SIGINT / SIGTERM in `main.py`. Cancel watchers, `gather(..., return_exceptions=True)` with 10 s grace, close `AsyncExitStack` (closes all bookmaker clients), flush + fsync CSV, exit 0.
- Mid-tick writes are protected by the lock — partial rows impossible.
- `resolution_cache.json` is written incrementally — a hard kill loses nothing.

## Testing

Pytest + `pytest-asyncio`. Tests run offline against captured fixtures — no network in CI.

| Module                    | What we test                                                                                                         | How                                                                                                 |
| ------------------------- | -------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| `registry.py`             | Our BetPawa 1up/2up `MarketMapping`s parse a real BetPawa event-detail response correctly                            | Fixture JSON → `parse_markets(..., platform="betpawa")` → assert canonical ids, outcomes, odds, prob |
| `resolution.py`           | Union-find matching: SR-only, Genius-only, both, neither                                                             | Synthetic `EventIds` inputs                                                                          |
| `resolution.py`           | Cache persistence: load, get-or-resolve, mark-stale, save                                                            | Tempdir JSON file; assert on-disk content                                                            |
| `writer.py`               | Header written once, append order stable, all 24 rows always emitted, concurrent appends don't interleave           | Two `asyncio.gather`'d producers; assert row count + parse-back equality                            |
| `status.py`               | Status enum mapping, minute parser (`"45+2"`→47, `"HT"`→45), score parse                                              | Table-driven against captured BetPawa responses                                                      |
| `watcher.py` (cadence)    | `UPCOMING`→600 s, `STARTED`→90 s, `ENDED`→exit, 3 h watchdog                                                          | Fake clock + fake collector; assert call timing in virtual time                                      |
| `collector.py` (contract) | Always emits exactly 24 rows; each failure mode produces correct `fetch_status`                                       | Mock clients, induce each failure, assert row count + statuses                                       |

**Fixtures.** Real captured JSON for one of the four event ids: prematch + live, per bookmaker. Captured via `tests/capture_fixtures.py` (a small script that hits each event id and writes raw JSON). Re-runnable when API shapes drift.

**No integration test in CI.** Provide a `make smoke` target that runs `main.py` for 5 minutes against the real bookmakers and dumps the resulting CSV — used manually before a release.

## Acceptance check (manual, on match day)

1. Start the scraper at least 30 min before kickoff.
2. Confirm prematch rows landing every ~10 min, all 24 rows per tick, no `lookup_failed` after the first cycle.
3. At kickoff, confirm cadence drops to ~90 s, `match_minute`/score columns populate, BetPawa rows start including a non-empty `genius_id`.
4. At full time, confirm a final tick is written and the watcher task exits cleanly.
5. Stop with Ctrl-C. Confirm clean shutdown and that `data/odds_snapshots.csv` is well-formed.

## Configuration (`config.yaml`)

```yaml
country: ng
events:
  - 33660318
  - 33660319
  - 33605719
  - 33575997
cadence:
  prematch_seconds: 600
  live_seconds: 90
  status_retry_backoff_seconds: [5, 15, 45]
  watchdog_after_kickoff_seconds: 10800   # 3 h
output:
  csv_path: data/odds_snapshots.csv
  resolution_cache_path: data/resolution_cache.json
log_level: INFO
```

Values overridable via env vars `ODDS_SCRAPER_*` (e.g. `ODDS_SCRAPER_LOG_LEVEL=DEBUG`).

## Dependencies

- Python ≥ 3.11
- `bookieskit @ git+https://github.com/lorenzosntr-pawa/bookieskit.git`
- `pyyaml` (config)
- `pytest`, `pytest-asyncio` (dev)

## Known risks / open questions

- **bookieskit BetPawa 1up/2up `platform_id` values not yet confirmed.** First implementation step is to capture one live BetPawa event detail response and identify the correct market ids; if the mapping shape is upstreamable, contribute it back to bookieskit instead of carrying it locally.
- **Bet9ja live BetGenius lookup endpoint.** bookieskit docs reference live-via-BetGenius for Bet9ja but exact API surface needs verification during implementation. If the live id isn't directly resolvable, fall back to emitting `lookup_failed` rows for Bet9ja-live until resolved.
- **Bookmaker rate limits.** bookieskit has internal rate limiting per client, but 24 ticks/h prematch + 40 ticks/h live across 4 events × 3 non-BP bookmakers is well within typical envelopes. No mitigation planned for v1.
