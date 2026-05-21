# SQLite storage — design

**Status:** approved 2026-05-21
**Touches:** `writer.py` (rewrite), `config.py`, `main.py`, new `db_schema.py`, tests
**Untouched:** `models.py`, `collector.py`, `watcher.py`, `event_resolver.py`, `registry.py`, `resolution*.py`, `status.py`

## Motivation

The scraper currently writes a wide CSV (68 columns per row, one row per (tick × event × bookmaker)). For a server deployment that runs continuously, CSV has three real problems:

1. **No queryability while writing.** Analysts and a future live UX can't ask "what's the current odds for event X?" without reading the whole file.
2. **No retention story.** The file grows forever. Pruning means tedious manual housekeeping.
3. **Wide rows become brittle.** Each new market = new columns = downstream tooling has to track header evolution.

A normalized SQLite database fixes all three. Single file. WAL mode for concurrent readers. Indexed time-series queries. Schema unchanged when new markets land (the bookieskit team will be adding them upstream).

This spec covers **storage + schema only**. The live UX and the periodic ClickHouse export are deferred to their own sub-projects, both of which will be straightforward read-only / batch-export consumers of this DB.

## Settled inputs

| Decision | Value |
|---|---|
| Engine | SQLite + WAL + `synchronous=NORMAL` + `foreign_keys=ON` + `busy_timeout=5000` |
| Schema shape | Normalized: `events`, `snapshots`, `prices`. `prices` carries denormalized `event_id`, `ts_utc`, `bookmaker` for direct time-series queries. |
| `events.id` type | `TEXT` (matches `Snapshot.event_bp_id` end-to-end; no casting) |
| `prices.line` for non-parameterized markets | Sentinel `0.0` (PRIMARY KEY can't include NULL columns cleanly; the `.0` lines are excluded from manifests anyway) |
| Transactions | One per `append()` call (atomic per-tick batch) |
| Concurrency | `asyncio.Lock` + `loop.run_in_executor` for sync sqlite3 calls |
| Schema versioning | `schema_version` table + migration dict in `db_schema.py` |
| Config knob | `output.db_path` (default `data/odds.db`); `output.csv_path` removed |
| CSV cutover | Clean replace. Old CSV files stay on disk as archives — no in-place migration. |
| Module structure | `writer.py` rewritten (interface preserved); new `db_schema.py` |
| Dependencies | stdlib `sqlite3` only. No new third-party deps. |

## Architecture

### Schema (DDL lives in `db_schema.py`)

```sql
CREATE TABLE IF NOT EXISTS schema_version (
    rowid    INTEGER PRIMARY KEY CHECK (rowid = 1),
    version  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id           TEXT PRIMARY KEY,     -- BetPawa event id (string)
    sr_id        TEXT,
    genius_id    TEXT,
    home         TEXT NOT NULL,
    away         TEXT NOT NULL,
    kickoff_utc  TEXT NOT NULL          -- ISO 8601
);

CREATE TABLE IF NOT EXISTS snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc        TEXT NOT NULL,
    event_id      TEXT NOT NULL REFERENCES events(id),
    bookmaker     TEXT NOT NULL,
    status        TEXT NOT NULL,        -- UPCOMING/STARTED/SUSPENDED/ENDED/UNKNOWN
    match_minute  INTEGER,
    score_home    INTEGER,
    score_away    INTEGER,
    fetch_status  TEXT NOT NULL,        -- ok/lookup_failed/http_error/parse_error
    fetch_error   TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS prices (
    snapshot_id  INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    event_id     TEXT NOT NULL,         -- denormalized for direct queries
    ts_utc       TEXT NOT NULL,         -- denormalized for direct queries
    bookmaker    TEXT NOT NULL,         -- denormalized for direct queries
    market_id    TEXT NOT NULL,         -- "1x2_ft", "over_under_ft", ...
    line         REAL NOT NULL DEFAULT 0.0,  -- 0.0 = non-parameterized
    side         TEXT NOT NULL,         -- "home"/"away"/"over"/...
    odds         REAL,
    probability  REAL,
    PRIMARY KEY (snapshot_id, market_id, line, side)
);

CREATE INDEX IF NOT EXISTS idx_snapshots_event_ts
    ON snapshots(event_id, ts_utc);
CREATE INDEX IF NOT EXISTS idx_snapshots_ts
    ON snapshots(ts_utc);
CREATE INDEX IF NOT EXISTS idx_prices_event_market_outcome
    ON prices(event_id, market_id, line, side, ts_utc);
CREATE INDEX IF NOT EXISTS idx_prices_ts
    ON prices(ts_utc);
```

**Why this shape:**

- `events` is small (one row per match-ever-seen), keyed on the natural BetPawa id. `INSERT OR IGNORE` semantics — first sighting wins; later snapshots for the same event don't overwrite.
- `snapshots` is medium-sized (one row per tick × event × bookmaker). The synthetic `id` lets `prices` refer to it with a short integer FK.
- `prices` is the hot table — one row per (tick × event × bookmaker × outcome). The dominant analytical query is time-series for a single outcome:
  ```sql
  SELECT ts_utc, odds, probability
  FROM prices
  WHERE event_id = '33638734'
    AND bookmaker = 'betpawa'
    AND market_id = 'over_under_ft'
    AND line = 2.5
    AND side = 'over'
  ORDER BY ts_utc;
  ```
  The `idx_prices_event_market_outcome` index serves this exact pattern — zero joins, full index range scan.
- `idx_prices_ts` exists for the future export job's window query (`WHERE ts_utc BETWEEN ? AND ?`).
- `ON DELETE CASCADE` on `prices.snapshot_id` — when the export job eventually deletes old snapshots, their prices follow automatically.

### Schema versioning (`db_schema.py`)

```python
from typing import Callable
import sqlite3

SCHEMA_VERSION = 1

_BASE_DDL = """..."""   # the CREATE TABLE / CREATE INDEX block above

_MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {
    1: lambda conn: None,   # base DDL already applied
}


def init_schema(conn: sqlite3.Connection) -> None:
    """Idempotent: apply base DDL, then any pending migrations."""
    conn.executescript(_BASE_DDL)
    current = _current_version(conn)
    for v in range(current + 1, SCHEMA_VERSION + 1):
        _MIGRATIONS[v](conn)
        conn.execute(
            "INSERT OR REPLACE INTO schema_version (rowid, version) "
            "VALUES (1, ?)",
            (v,),
        )


def _current_version(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT version FROM schema_version WHERE rowid = 1"
    ).fetchone()
    return row[0] if row else 0
```

When the ClickHouse export sub-project lands (and needs `exported_at`):

```python
SCHEMA_VERSION = 2
_MIGRATIONS = {
    1: lambda conn: None,
    2: lambda conn: conn.execute(
        "ALTER TABLE snapshots ADD COLUMN exported_at TEXT"
    ),
}
```

Existing DBs migrate on next boot; fresh DBs jump straight to v2.

### Writer (`writer.py`)

```python
from __future__ import annotations

import asyncio
import logging
import sqlite3
from pathlib import Path
from typing import Iterable

from .db_schema import init_schema
from .models import Snapshot, _iso  # _iso reused from existing models

log = logging.getLogger(__name__)


class SqliteWriter:
    """Replaces CsvWriter. Same async context-manager + append() interface."""

    def __init__(self, path: Path | str):
        self._path = Path(path)
        self._lock = asyncio.Lock()
        self._conn: sqlite3.Connection | None = None

    async def __aenter__(self) -> "SqliteWriter":
        self._path.parent.mkdir(parents=True, exist_ok=True)
        loop = asyncio.get_running_loop()
        self._conn = await loop.run_in_executor(None, self._open)
        return self

    def _open(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path), isolation_level=None)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        init_schema(conn)
        return conn

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._conn is not None:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._conn.close)
            self._conn = None

    async def append(self, snapshots: Iterable[Snapshot]) -> None:
        snaps = list(snapshots)
        if not snaps:
            return
        async with self._lock:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._write_batch, snaps)

    def _write_batch(self, snaps: list[Snapshot]) -> None:
        conn = self._conn
        assert conn is not None
        try:
            conn.execute("BEGIN")
            for s in snaps:
                # Upsert events. ON CONFLICT DO UPDATE patches placeholder
                # rows (empty home/away from a sentinel snapshot that
                # happened to land first) on the next tick with real data.
                # The UPDATE only fires when the existing values are empty,
                # so good data is never overwritten by later sentinels.
                conn.execute(
                    "INSERT INTO events "
                    "(id, sr_id, genius_id, home, away, kickoff_utc) "
                    "VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET "
                    "  sr_id = COALESCE(NULLIF(events.sr_id, ''), excluded.sr_id), "
                    "  genius_id = COALESCE(NULLIF(events.genius_id, ''), excluded.genius_id), "
                    "  home = CASE WHEN events.home = '' THEN excluded.home ELSE events.home END, "
                    "  away = CASE WHEN events.away = '' THEN excluded.away ELSE events.away END",
                    (s.event_bp_id, s.sr_id or None, s.genius_id or None,
                     s.home, s.away, _iso(s.kickoff_utc)),
                )
                cur = conn.execute(
                    "INSERT INTO snapshots "
                    "(ts_utc, event_id, bookmaker, status, match_minute, "
                    "score_home, score_away, fetch_status, fetch_error) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (_iso(s.ts_utc), s.event_bp_id, s.bookmaker.value,
                     s.status.value, s.match_minute, s.score_home, s.score_away,
                     s.fetch_status.value, s.fetch_error),
                )
                snap_id = cur.lastrowid
                rows = [
                    (snap_id, s.event_bp_id, _iso(s.ts_utc),
                     s.bookmaker.value,
                     key.market_id,
                     key.line if key.line is not None else 0.0,
                     key.side, odds, prob)
                    for key, (odds, prob) in s.prices.items()
                ]
                if rows:
                    conn.executemany(
                        "INSERT INTO prices "
                        "(snapshot_id, event_id, ts_utc, bookmaker, "
                        "market_id, line, side, odds, probability) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        rows,
                    )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
```

Notes:
- `isolation_level=None` puts the connection in autocommit, so explicit `BEGIN`/`COMMIT`/`ROLLBACK` works as expected.
- `loop.run_in_executor(None, …)` runs the sync sqlite3 calls on the default thread pool, freeing the event loop. The `asyncio.Lock` serializes concurrent `append()` calls so only one executor thread touches the connection at a time.
- Transaction scope = one `append()` call = one tick × 4 bookmakers = ~100 prices. Atomic per-tick.
- The `events` row uses `INSERT … ON CONFLICT(id) DO UPDATE` with empty-field guards. Real data stays sticky; placeholder rows (created when the very first tick for an event is a sentinel/failed snapshot with empty home/away) get patched on the next good tick. Good data never gets overwritten by later sentinels.

### Config (`config.py`)

```python
@dataclass(frozen=True)
class OutputConfig:
    db_path: str                       # NEW (default "data/odds.db")
    resolution_cache_path: str
    # csv_path removed
```

`load_config` reads `output.db_path` via `raw["output"].get("db_path", "data/odds.db")`. Existing configs that still carry `csv_path: data/odds_snapshots.csv` aren't read by the new code; the key is harmlessly ignored. Users will want to update their `config.yaml` to use `db_path` instead, but old configs don't crash.

### `main.py` wiring

```python
# Replace this line:
writer = await stack.enter_async_context(CsvWriter(Path(cfg.output.csv_path)))
# With:
writer = await stack.enter_async_context(SqliteWriter(Path(cfg.output.db_path)))
```

And `from .writer import CsvWriter` → `from .writer import SqliteWriter`. Two lines.

### Design property: markets cost zero DDL

Not a feature of this spec — a consequence of the normalized layout. When bookieskit ships new markets and `MARKET_MANIFEST` gets updated, the writer iterates the manifest as it already does, hits the new market in the parsed response, and inserts `prices` rows with the new `market_id`. The schema doesn't change; no migration; no test rewrite.

## Tests

| File | Change |
|---|---|
| `tests/test_db_schema.py` | **NEW.** init_schema idempotency, schema_version row, all tables + indexes exist. |
| `tests/test_writer.py` | Rewrite. ~10 tests for SqliteWriter (see list below). |
| `tests/test_config.py` | Touch up — `OutputConfig.db_path` field + default. |
| Other test files | Unchanged. |

### `tests/test_writer.py` cases

1. **Fresh DB creates schema** — open writer on fresh path, query `sqlite_master`, verify `events`, `snapshots`, `prices`, `schema_version` all exist.
2. **Open existing DB doesn't re-init** — write, close, re-open, write again; no error; both writes durable.
3. **Single tick: events + snapshots + prices atomically** — `append([snap])` with non-empty prices → 1 event row, 1 snapshot row, len(snap.prices) price rows.
4. **Event row is idempotent across ticks** — two snapshots for same `event_bp_id` → one events row, two snapshots rows.
5. **fetch_status=http_error → snapshot row, zero prices** — failure mode preserved.
6. **Probability NULL for B9J/BW** — snapshot from those bookmakers stores `prices.probability IS NULL`.
7. **Sentinel `line=0.0` for non-parameterized markets** — `PriceKey("1x2_ft", None, "home")` writes `prices.line = 0.0`; PK accepts it.
8. **Parameterized markets store line as REAL** — `PriceKey("over_under_ft", 2.5, "over")` writes `prices.line = 2.5`.
9. **Concurrent appends serialize** — `asyncio.gather(w.append(50_snaps), w.append(50_snaps))` → 100 snapshot rows, no torn writes.
10. **Transaction rollback on partial failure** — inject a constraint violation mid-batch (e.g., duplicate PK); verify zero rows from that batch survive.
11. **Placeholder event row gets patched on next good tick** — append a sentinel snapshot first (empty home/away), then append a successful snapshot for the same `event_bp_id` with real names. Verify `events.home` and `events.away` now hold the real values, not the empty placeholders.

### `tests/test_db_schema.py` cases

1. **`init_schema` creates all tables on fresh connection.**
2. **`init_schema` is idempotent** — called twice → no error, no duplicate rows.
3. **`schema_version` row contains current `SCHEMA_VERSION`.**
4. **All four indexes exist** — `sqlite_master` query for indexes.

### `tests/test_config.py` additions

1. `test_load_with_db_path` — `output.db_path: data/x.db` loads to `cfg.output.db_path == "data/x.db"`.
2. `test_db_path_default` — `output:` block without `db_path` → defaults to `"data/odds.db"`.
3. `test_old_csv_path_key_ignored` — config with `output.csv_path: data/old.csv` (and no `db_path`) loads cleanly with default db_path; csv_path not present on OutputConfig.

## Module map

```
src/odds_scraper/
├── db_schema.py        NEW
├── writer.py           REWRITTEN: SqliteWriter (replaces CsvWriter).
├── config.py           OutputConfig.db_path replaces csv_path.
├── main.py             Imports SqliteWriter; passes db_path.
├── models.py           Unchanged.
├── collector.py        Unchanged.
├── watcher.py          Unchanged.
├── event_resolver.py   Unchanged.
├── registry.py         Unchanged.
├── resolution*.py      Unchanged.
└── status.py           Unchanged.
```

## Out of scope

- **ClickHouse export.** Separate sub-project. Will add `exported_at` via a v2 migration; owns batch upload and retention.
- **Live UX.** Separate sub-project. Read-only consumer of the same SQLite DB via WAL.
- **Historical CSV import.** Old CSVs stay on disk as archives. One-off script if ever needed; not part of this work.
- **New markets.** Bookieskit team owns mappings; `MARKET_MANIFEST` updates flow through automatically.
- **`aiosqlite` dependency.** Sticking with stdlib + `run_in_executor`. Optimize later if profiling demands.
- **Multi-writer / multi-process.** One scraper writes; future UX is read-only. WAL handles that; no shared-write design needed.
- **Updating `config.yaml`** automatically to remove `csv_path`. Old configs work silently; users can clean up at their leisure.
