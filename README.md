# odds-scraper

1up / 2up odds scraper for four BetPawa-anchored NG soccer events across
BetPawa, SportyBet, Bet9ja and Betway. Captures bookmaker-exposed probability
where available (BetPawa, SportyBet) and match score / clock once live.

## Quick start (Windows PowerShell)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python -m odds_scraper.main --config config.yaml
```

The scraper runs continuously, one asyncio task per event. Each task polls
BetPawa for status and decides cadence: ~10 min while `UPCOMING`, ~90 s while
`STARTED`, exits on `ENDED`. Per tick it fans out fetches to all four
bookmakers in parallel and appends 24 rows (4 bookmakers × 2 markets ×
3 outcomes) to `data/odds_snapshots.csv`.

Stop with **Ctrl-C** — the writer flushes and bookmaker clients close cleanly.

## Configuration

Edit `config.yaml` to change events, cadence, or output paths.

| Field | Meaning |
| ----- | ------- |
| `country` | Country code passed to each bookmaker client. Currently `ng`. |
| `events` | BetPawa internal event ids — the anchor for cross-bookmaker resolution. |
| `cadence.prematch_seconds` | Tick interval while event is `UPCOMING`. |
| `cadence.live_seconds` | Tick interval while event is `STARTED`. |
| `cadence.status_retry_backoff_seconds` | BetPawa status-poll backoffs after failure. |
| `cadence.watchdog_after_kickoff_seconds` | Force-exit a watcher this many seconds after kickoff if status never flips to ENDED. |
| `output.csv_path` | Single append-only output CSV. |
| `output.resolution_cache_path` | JSON file persisting cross-bookmaker id mappings. |
| `log_level` | Python log level. Override with `ODDS_SCRAPER_LOG_LEVEL`. |

## CSV columns

```
ts_utc, event_bp_id, sr_id, genius_id,
home, away, kickoff_utc,
status, match_minute, score_home, score_away,
bookmaker, market, outcome,
odds, probability,
fetch_status, fetch_error
```

`fetch_status` is one of `ok | suspended | not_offered | lookup_failed | http_error | parse_error`.
Failures still emit rows with empty odds — gaps are explicit, never silent.

Full schema in `docs/superpowers/specs/2026-05-19-odds-scraper-design.md`.

## Tests

```powershell
pytest -v
```

Tests run offline against fixtures in `tests/fixtures/`. No network in CI.

## Refreshing fixtures from real APIs

```powershell
python scripts/capture_fixtures.py 33660318 --bookmaker betpawa --out tests/fixtures/betpawa_event_real.json
python scripts/capture_fixtures.py sr:match:68995156 --bookmaker sportybet --out tests/fixtures/sportybet_event.json
```

Use this when BetPawa changes their JSON shape (e.g. new fields, renamed
keys) or to add a new event for testing.

## Known limitations

- The BetPawa 1up canonical mapping is already in `bookieskit` builtins
  (`market_id=28000810`). The 2up mapping (`28000850`) is added locally in
  `src/odds_scraper/registry.py` — if BetPawa renames the market type or
  changes the id, update there.
- Bet9ja live resolution depends on BetGenius id parity with BetPawa.
  Events that route via BetGenius on Bet9ja-live but lack a BetGenius id on
  BetPawa will emit `lookup_failed` rows for Bet9ja-live.
- Single output CSV, no rotation. At ~20 K rows per match-day it stays small
  for months; rotate manually if needed.
- Windows: signals are delivered as `KeyboardInterrupt`, not via
  `add_signal_handler`. `Ctrl-C` works; `SIGTERM` via tooling does not.
