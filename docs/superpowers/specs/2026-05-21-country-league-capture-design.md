# Country + league capture — design

**Status:** approved 2026-05-21
**Touches:** `db_schema.py`, `models.py`, `collector.py`, `writer.py`, their tests
**Untouched:** `watcher.py`, `event_resolver.py`, `registry.py`, `resolution*.py`, `status.py`, `config.py`, `main.py`, the whole `web/` subpackage

## Motivation

We want events to carry their country and league so the UX can filter by them and the detail page header shows context ("Germany · 2nd Bundesliga"). Both are already present in every BetPawa event-detail response under stable top-level keys `region` (country) and `competition` (league), each with `id` + `name`. We just don't read or store them today.

This sub-project is **pure data plumbing**. UX consumers (filter chips, header text) land in a follow-up sub-project.

## Settled inputs

| Decision | Value |
|---|---|
| Storage | Four new TEXT columns on `events`: `country_id`, `country_name`, `league_id`, `league_name`. All nullable; populated on first sighting per event. |
| Extraction source | BetPawa event-detail dict — `detail["region"]["id"|"name"]` and `detail["competition"]["id"|"name"]`. No bookieskit upgrade required (no `extract_country()` helper to add — direct dict access). |
| Where extraction lives | `collector.collect()`, alongside the existing meta extraction (status, minute, score, participants, kickoff). |
| Snapshot carries the data | Yes — four new fields on the `Snapshot` dataclass, matching the existing pattern for `home`/`away`/`kickoff_utc`/`sr_id`/`genius_id`. Empty string `""` when not extractable. |
| Migration | `db_schema.py` v2 — four `ALTER TABLE events ADD COLUMN ... TEXT` statements. Existing rows get NULL. |
| Backfill | None. Writer upsert (next section) patches NULL → real-value on the next tick per event. |
| Writer upsert | Extended ON CONFLICT clause with the same empty-field-sticky pattern already used for `sr_id`/`genius_id`/`home`/`away`. |
| Sport scope | Out of scope. `category` (sport) is also in the detail dict but we only watch football today; if multi-sport support is ever added it gets its own column then. |
| UX consumers | Out of scope. Filter chips and detail-page header text are sub-project 3. |

## Architecture

### Schema migration (`db_schema.py`)

`SCHEMA_VERSION = 2`. Migration v2 runs four `ALTER TABLE` statements inside the same single transaction the existing `init_schema` migration loop wraps each step in:

```python
_MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {
    1: lambda conn: None,
    2: lambda conn: conn.executescript("""
        ALTER TABLE events ADD COLUMN country_id   TEXT;
        ALTER TABLE events ADD COLUMN country_name TEXT;
        ALTER TABLE events ADD COLUMN league_id    TEXT;
        ALTER TABLE events ADD COLUMN league_name  TEXT;
    """),
}
```

SQLite's `ALTER TABLE ADD COLUMN` is atomic per-statement; the migration loop's outer `BEGIN`/`COMMIT` makes the whole block all-or-nothing against a partial failure.

Note: `executescript` issues its own implicit commit before running (per the warning we already documented in `init_schema`'s docstring). The migration loop already accounts for that — it calls `conn.execute("BEGIN")` first, but `executescript` will commit it. This means the v2 migration runs atomically at the `executescript` level, then the loop's outer `INSERT OR REPLACE INTO schema_version` runs as a separate statement. The race window between the column-adds and the version write is a single SQLite statement; if a crash hits in that microsecond the v2 lambda is idempotent because `ALTER TABLE` doesn't have an `IF NOT EXISTS` form but the column will already exist, so re-running v2 will raise on next boot. To make it crash-safe, the v2 lambda should be:

```python
2: lambda conn: _add_columns_if_missing(conn, "events", [
    ("country_id",   "TEXT"),
    ("country_name", "TEXT"),
    ("league_id",    "TEXT"),
    ("league_name",  "TEXT"),
]),
```

with a small helper that inspects `PRAGMA table_info(events)` and only emits `ALTER TABLE` for columns that don't already exist. One ALTER per column, idempotent under any partial-failure scenario.

### Snapshot model (`models.py`)

```python
@dataclass(frozen=True)
class Snapshot:
    # ... all existing required (no-default) fields exactly as today ...
    ts_utc: datetime
    event_bp_id: str
    sr_id: str
    genius_id: str
    home: str
    away: str
    kickoff_utc: datetime
    status: EventStatus
    match_minute: Optional[int]
    score_home: Optional[int]
    score_away: Optional[int]
    bookmaker: Bookmaker
    fetch_status: FetchStatus
    fetch_error: str
    # prices already has a default_factory; the four new fields slot in
    # AFTER prices because Python forbids non-default fields after defaulted
    # ones. All four default to "" so existing Snapshot(...) call-sites in
    # tests and elsewhere keep working unchanged.
    prices: dict[PriceKey, tuple[Optional[float], Optional[float]]] = field(
        default_factory=dict,
    )
    country_id: str = ""
    country_name: str = ""
    league_id: str = ""
    league_name: str = ""
```

The four new fields default to `""` so existing test fixtures and any other Snapshot constructors don't need to be touched if they don't care about country/league. The collector populates them explicitly; the watcher's `_sentinel_rows` (which constructs Snapshots without prices/meta) leaves them at default `""`.

### Collector (`collector.py`)

In `collect()`, after the existing kickoff/participants extraction:

```python
region = bp_detail.get("region") or {}
competition = bp_detail.get("competition") or {}
country_id = str(region.get("id") or "")
country_name = str(region.get("name") or "")
league_id = str(competition.get("id") or "")
league_name = str(competition.get("name") or "")
```

These four values are passed to every Snapshot constructed in the per-bookmaker loop. All four bookmakers in the same tick get identical country/league values (it's event-level metadata, not bookmaker-level).

If the detail is malformed or missing one of the keys, the `or {}` and `or ""` chains produce `""` — written to the DB as empty string, upsert clause replaces on next good tick.

### Writer (`writer.py`)

Extend the events upsert:

```sql
INSERT INTO events
    (id, sr_id, genius_id, home, away, kickoff_utc,
     country_id, country_name, league_id, league_name)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(id) DO UPDATE SET
    sr_id        = COALESCE(NULLIF(events.sr_id, ''), excluded.sr_id),
    genius_id    = COALESCE(NULLIF(events.genius_id, ''), excluded.genius_id),
    home         = CASE WHEN events.home = '' THEN excluded.home ELSE events.home END,
    away         = CASE WHEN events.away = '' THEN excluded.away ELSE events.away END,
    country_id   = COALESCE(NULLIF(events.country_id, ''), excluded.country_id),
    country_name = CASE WHEN events.country_name IS NULL OR events.country_name = ''
                        THEN excluded.country_name ELSE events.country_name END,
    league_id    = COALESCE(NULLIF(events.league_id, ''), excluded.league_id),
    league_name  = CASE WHEN events.league_name IS NULL OR events.league_name = ''
                        THEN excluded.league_name ELSE events.league_name END
```

The Python-side parameter tuple gains four trailing positional values:

```python
(s.event_bp_id, s.sr_id or None, s.genius_id or None,
 s.home, s.away, _iso(s.kickoff_utc),
 s.country_id or None, s.country_name or None,
 s.league_id or None, s.league_name or None)
```

`or None` so empty strings become NULL in the DB — keeps the COALESCE happy on the next upsert.

### Why the upsert clause for the name fields uses `IS NULL OR = ''`

Existing rows pre-migration have NULL in the new columns. `events.country_name = ''` is false for NULL (SQL three-valued logic), and `COALESCE(NULLIF(NULL, ''), excluded.x)` evaluates to `COALESCE(NULL, excluded.x) = excluded.x` which is fine. But CASE WHEN with bare `= ''` would miss the NULL case. Belt-and-braces: `IS NULL OR = ''` covers both.

For the ID fields, COALESCE+NULLIF handles both NULL and `''` cleanly, so the simpler form is fine.

## Tests

| File | New / changed |
|---|---|
| `tests/test_db_schema.py` | Test v2 migration adds the 4 columns. Test idempotent re-run does not error. Verify schema_version row reflects 2. |
| `tests/test_models.py` | Test Snapshot accepts the 4 new fields. Test defaults to `""`. |
| `tests/test_collector.py` | Test collector pulls country/league from a `bp_detail` containing `region`/`competition` keys. Test missing keys produce `""`. Test all four bookmaker snapshots in one tick get identical country/league values. |
| `tests/test_writer.py` | Test writer round-trips the 4 fields into the `events` table. Test the upsert patches NULL → real-value on a subsequent tick. Test the upsert preserves real-value when a later sentinel tick passes empty strings. |

No web tests for this sub-project — UX is sub-project 3.

## Out of scope

- **UX consumers** (filter chips, detail-page header line). Sub-project 3.
- **Multi-sport support.** Today we hardcode football; the `category` field exists but is not stored. Adding a sport column would be its own change if ever needed.
- **Renaming / re-mapping country & league names.** We store whatever BetPawa returns verbatim. If BetPawa changes a name later, that's a separate de-duping concern.
- **Backfilling historical CSV data** into the new columns. The old CSV stays as-is.
- **Tournament resolution by country/league name.** `tournaments:` in config.yaml still uses competition IDs.
