# Wide CSV + new markets — design

**Status:** approved 2026-05-21
**Touches:** `models.py`, `collector.py`, `writer.py`, `watcher.py`, `tests/`
**Untouched:** `registry.py`, `resolution*.py`, `status.py`, `config.py`, `main.py`

## Motivation

The CSV is currently long-format: one row per (tick × event × bookmaker × market × outcome). Each tick produces 24 rows per event (4 bookmakers × 2 markets × 3 outcomes), and adding markets multiplies row count linearly. We want to:

1. Capture more markets per snapshot (classic 1x2 full-time and over/under lines, in addition to the existing 1up / 2up variants).
2. Reduce row count to keep the file analysable: one row per (tick × event × bookmaker), with markets/outcomes as columns.
3. Set up the schema so further markets (BTTS, double chance, half-time, handicaps…) are a one-line append, not a five-file refactor.

## Settled inputs

| Decision | Value |
|---|---|
| Markets (full set) | `1x2_ft`, `1x2_1up_ft`, `1x2_2up_ft`, `over_under_ft` |
| O/U lines | 1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5, 8.5, 9.5 (`.0` lines and 0.5 excluded) |
| Probability columns | populated for BetPawa & SportyBet on every market; B9J/BW columns exist but are systematically blank |
| Row granularity | one row per (tick × event × bookmaker) |
| Rows per tick | 16 (4 events × 4 bookmakers) |
| Column count per row | 68 (14 meta + 54 price cells) |
| Row-level `fetch_status` | `ok` \| `lookup_failed` \| `http_error` \| `parse_error` |
| Per-cell suspension info | not encoded — empty cell + row-status `ok` = "bookmaker didn't return a price for this outcome" |
| Tick log format | `tick <id> status=<X> bp=N/54 sb=N/54 b9j=N/27 bw=N/27` |
| CSV file path | `data/odds_snapshots.csv` (unchanged) |
| Migration | on first open, if the file's header is the old long-format header, rename it to `data/odds_snapshots_v1_YYYY-MM-DD.csv` and start a new file |

## Architecture

### Source of truth: the market manifest

A single module-level constant in `models.py` is the source of truth for which markets are written and in what column order. CSV header, collector loop, sentinel rows, and tests all derive from it.

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class MarketSpec:
    canonical_id: str               # bookieskit canonical, e.g. "over_under_ft"
    column_prefix: str              # CSV column prefix, e.g. "ou"
    sides: tuple[str, ...]          # ("home","draw","away") or ("over","under")
    lines: tuple[float, ...] | None # None = simple market; otherwise O/U-style

MARKET_MANIFEST: tuple[MarketSpec, ...] = (
    MarketSpec("1x2_ft",        "1x2_ft",      ("home","draw","away"), None),
    MarketSpec("1x2_1up_ft",    "1x2_1up_ft",  ("home","draw","away"), None),
    MarketSpec("1x2_2up_ft",    "1x2_2up_ft",  ("home","draw","away"), None),
    MarketSpec("over_under_ft", "ou",          ("over","under"),
               (1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5, 8.5, 9.5)),
)
```

### Header generator

```python
def build_csv_header() -> tuple[str, ...]:
    meta = (
        "ts_utc", "event_bp_id", "sr_id", "genius_id",
        "home", "away", "kickoff_utc",
        "status", "match_minute", "score_home", "score_away",
        "bookmaker", "fetch_status", "fetch_error",
    )
    price_cols: list[str] = []
    for spec in MARKET_MANIFEST:
        if spec.lines is None:
            for side in spec.sides:
                price_cols.append(f"{spec.column_prefix}_{side}_odds")
                price_cols.append(f"{spec.column_prefix}_{side}_prob")
        else:
            for line in spec.lines:
                for side in spec.sides:
                    price_cols.append(f"{spec.column_prefix}_{line}_{side}_odds")
                    price_cols.append(f"{spec.column_prefix}_{line}_{side}_prob")
    return meta + tuple(price_cols)
```

Resulting header begins:
```
ts_utc, event_bp_id, sr_id, genius_id, home, away, kickoff_utc,
status, match_minute, score_home, score_away,
bookmaker, fetch_status, fetch_error,
1x2_ft_home_odds, 1x2_ft_home_prob, 1x2_ft_draw_odds, 1x2_ft_draw_prob, 1x2_ft_away_odds, 1x2_ft_away_prob,
1x2_1up_ft_home_odds, 1x2_1up_ft_home_prob, ...
1x2_2up_ft_home_odds, ...
ou_1.5_over_odds, ou_1.5_over_prob, ou_1.5_under_odds, ou_1.5_under_prob,
ou_2.5_over_odds, ...
...
ou_9.5_under_prob
```

Total: 14 + 6 + 6 + 6 + (9 × 4) = **68 columns**.

### Snapshot model

```python
@dataclass(frozen=True)
class PriceKey:
    market_id: str
    line: float | None      # None for simple markets
    side: str

@dataclass(frozen=True)
class Snapshot:
    ts_utc: datetime
    event_bp_id: str
    sr_id: str
    genius_id: str
    home: str
    away: str
    kickoff_utc: datetime
    status: EventStatus
    match_minute: int | None
    score_home: int | None
    score_away: int | None
    bookmaker: Bookmaker
    fetch_status: FetchStatus
    fetch_error: str
    prices: dict[PriceKey, tuple[float | None, float | None]]
    # value = (odds, probability); probability is None for B9J/BW
    # and for any outcome where the bookmaker didn't return one.

    def to_csv_row(self) -> tuple[str, ...]:
        # iterates MARKET_MANIFEST in fixed order, looks up each cell
        # in self.prices; emits "" for missing keys.
```

### FetchStatus shrinkage

```python
class FetchStatus(str, Enum):
    OK             = "ok"
    LOOKUP_FAILED  = "lookup_failed"   # no target_id resolved
    HTTP_ERROR     = "http_error"      # fetcher raised
    PARSE_ERROR    = "parse_error"     # parse_markets raised
```

`SUSPENDED` and `NOT_OFFERED` are removed — they were per-outcome facts that don't roll up cleanly to a row. The new contract: under `fetch_status=ok`, an empty cell means "the bookmaker didn't return a usable price for this outcome" (either market absent or outcome suspended — we can no longer distinguish, and accept that loss).

### Collector refactor

`collector.collect()` returns exactly 4 rows (one per bookmaker) instead of 24:

```python
async def collect(self, bp_detail, resolved, sr_id, genius_id) -> list[Snapshot]:
    # meta extraction unchanged (ts, status, minute, score, names, kickoff)

    async def run(b: Bookmaker, target_id: str | None):
        if b != Bookmaker.BETPAWA and not target_id:
            return b, (FetchStatus.LOOKUP_FAILED, "no id resolved", [])
        try:
            if b == Bookmaker.BETPAWA:
                markets = await self._fetchers[b](bp_detail)
            else:
                markets = await self._fetchers[b](target_id)
            return b, (FetchStatus.OK, "", markets)
        except Exception as e:
            short = " ".join(str(e).split())[:120]
            log.warning("fetch failed for %s: %s", b.value, short)
            return b, (FetchStatus.HTTP_ERROR, f"{type(e).__name__}: {short}", [])

    results = dict(await asyncio.gather(*[run(b, ...) for b in Bookmaker]))

    rows: list[Snapshot] = []
    for b in Bookmaker:
        status_fetch, error, markets = results[b]
        want_prob = b in _PROB_BOOKMAKERS
        prices = (_extract_prices_for_manifest(markets, want_prob)
                  if status_fetch == FetchStatus.OK else {})
        rows.append(Snapshot(meta..., bookmaker=b,
                             fetch_status=status_fetch, fetch_error=error,
                             prices=prices))
    return rows  # exactly 4
```

The price-extraction helper iterates the manifest:

```python
def _extract_prices_for_manifest(
    markets: list, want_prob: bool,
) -> dict[PriceKey, tuple[float | None, float | None]]:
    by_canon = {m.canonical_id: m for m in markets}
    out: dict[PriceKey, tuple] = {}
    for spec in MARKET_MANIFEST:
        m = by_canon.get(spec.canonical_id)
        if m is None:
            continue
        if spec.lines is None:
            # simple market: read outcomes by canonical_name
            by_side = {o.canonical_name: o for o in m.outcomes}
            for side in spec.sides:
                o = by_side.get(side)
                if o is None or o.odds is None:
                    continue
                prob = getattr(o, "true_probability", None) if want_prob else None
                out[PriceKey(spec.canonical_id, None, side)] = (
                    float(o.odds), float(prob) if prob is not None else None,
                )
        else:
            # parameterized market: read m.lines[line] by canonical_name
            lines_map = m.lines or {}
            for line in spec.lines:
                outcomes = lines_map.get(line)
                if not outcomes:
                    continue
                by_side = {o.canonical_name: o for o in outcomes}
                for side in spec.sides:
                    o = by_side.get(side)
                    if o is None or o.odds is None:
                        continue
                    prob = getattr(o, "true_probability", None) if want_prob else None
                    out[PriceKey(spec.canonical_id, line, side)] = (
                        float(o.odds), float(prob) if prob is not None else None,
                    )
    return out
```

Out-of-manifest lines (`.0` lines, 10.5, etc.) are silently ignored — they're never looked up.

### Writer change

`CsvWriter` becomes header-aware on open:

```python
class CsvWriter:
    async def __aenter__(self):
        path = self._path
        header = build_csv_header()
        if path.exists():
            existing = read_first_line(path)
            if existing and existing != ",".join(header):
                # old-format file — preserve and start fresh
                ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                path.rename(path.with_name(f"{path.stem}_v1_{ts}{path.suffix}"))
        is_new = not path.exists()
        self._file = await aiofiles.open(path, "a", newline="")
        if is_new:
            await self._file.write(",".join(header) + "\n")
        return self
```

The rename is one-shot (gated by header mismatch) so re-running the app never trashes the new file.

### Watcher changes

```python
def _sentinel_rows(self, reason: str) -> list[Snapshot]:
    # one row per bookmaker, empty prices, fetch_status=http_error
    ts = datetime.now(timezone.utc)
    return [
        Snapshot(ts_utc=ts, event_bp_id=self.event_bp_id,
                 sr_id="", genius_id="", home="", away="",
                 kickoff_utc=ts, status=self._last_status,
                 match_minute=None, score_home=None, score_away=None,
                 bookmaker=b,
                 fetch_status=FetchStatus.HTTP_ERROR, fetch_error=reason,
                 prices={})
        for b in Bookmaker
    ]  # exactly 4 rows

def _log_tick_summary(self, rows: list[Snapshot]) -> None:
    # Denominators:
    #   BP/SB: 27 outcomes × 2 (odds+prob) = 54
    #   B9J/BW: 27 outcomes × 1 (odds only) = 27
    denom = {
        Bookmaker.BETPAWA: 54, Bookmaker.SPORTYBET: 54,
        Bookmaker.BET9JA: 27, Bookmaker.BETWAY: 27,
    }
    counts: dict[Bookmaker, int] = {b: 0 for b in Bookmaker}
    for r in rows:
        for (_k, (odds, prob)) in r.prices.items():
            if odds is not None:
                counts[r.bookmaker] += 1
            if prob is not None:
                counts[r.bookmaker] += 1
    log.info(
        "tick %s status=%s bp=%d/%d sb=%d/%d b9j=%d/%d bw=%d/%d",
        self.event_bp_id, self._last_status.value,
        counts[Bookmaker.BETPAWA],   denom[Bookmaker.BETPAWA],
        counts[Bookmaker.SPORTYBET], denom[Bookmaker.SPORTYBET],
        counts[Bookmaker.BET9JA],    denom[Bookmaker.BET9JA],
        counts[Bookmaker.BETWAY],    denom[Bookmaker.BETWAY],
    )
```

### Edge cases

| Situation | Row outcome |
|---|---|
| Bookmaker fetch raises (HTTP/timeout) | `fetch_status=http_error`, all price cells blank |
| `target_id` couldn't be resolved | `fetch_status=lookup_failed`, all blank |
| `parse_markets` raises | `fetch_status=parse_error`, all blank |
| Manifest market absent from response | `fetch_status=ok`, just those cells blank |
| Outcome present but no odds | cell blank |
| BP/SB outcome present, no `true_probability` | odds populated, prob blank |
| Bookmaker returns out-of-manifest lines | silently ignored |
| Bookmaker returns `.0` lines | silently ignored |

## Tests

| File | Change |
|---|---|
| `test_models.py` | Rewrite for new Snapshot; cover `to_csv_row()` flattening incl. missing cells; cover BP/SB prob-populated vs B9J/BW prob-blank; cover `build_csv_header()` |
| `test_collector.py` | Rewrite expectations: 4 rows per call; verify simple + parameterized price extraction; verify out-of-manifest lines and `.0` lines are ignored; verify `HTTP_ERROR` / `LOOKUP_FAILED` / `PARSE_ERROR` paths produce row with empty prices |
| `test_writer.py` | Update expected header to manifest-generated tuple; new test: old-header file is renamed to `*_v1_YYYY-MM-DD.csv` on open |
| `test_watcher.py` | Update sentinel count (24 → 4); update tick-summary log assertions |
| `test_registry.py`, `test_status.py`, `test_resolution.py`, `test_config.py`, `test_main_supervisor.py` | Unchanged |

**New test:** manifest round-trip — given a synthetic `parsed markets` payload exercising every market in the manifest, assert `Snapshot.to_csv_row()` puts values in the exact positions advertised by `build_csv_header()`.

## Out of scope

- New markets beyond the 4 settled families (BTTS, double-chance, half-time, handicaps, corners…). Adding any of these is a one-line append to `MARKET_MANIFEST`.
- Sport-aware registry (basketball/tennis builtins exist in bookieskit but are unused).
- Backfilling old long-format data into the new schema. The old file is preserved as-is post-rename.
- Per-market status columns (rejected: empty-cell signal is enough).
- Probability columns for Bet9ja / Betway (they don't expose `true_probability`).
