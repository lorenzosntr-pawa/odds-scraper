# Country + league capture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture and persist `country_id`, `country_name`, `league_id`, `league_name` for every event the scraper writes, derived from BetPawa's event-detail `region` and `competition` keys.

**Architecture:** Pure data plumbing. `db_schema.py` bumps to v2 and idempotently adds four nullable TEXT columns to the `events` table. `Snapshot` gains four fields (default `""`). `collector.collect()` pulls the values from `bp_detail` alongside its existing meta extraction. `writer._write_batch` extends the events upsert with the same empty-field-sticky patching pattern. No UX consumption in this sub-project.

**Tech Stack:** Python stdlib `sqlite3`, dataclasses, pytest with pytest-asyncio (auto mode).

**Spec reference:** `docs/superpowers/specs/2026-05-21-country-league-capture-design.md`

---

## File map

| Action | File | Responsibility |
|---|---|---|
| Modify | `src/odds_scraper/db_schema.py` | Add `_add_columns_if_missing` helper; v2 migration adds 4 columns idempotently; update `_BASE_DDL` so fresh DBs get them at creation time; bump `SCHEMA_VERSION` to 2. |
| Modify | `src/odds_scraper/models.py` | Add 4 trailing fields to `Snapshot` (default `""`). |
| Modify | `src/odds_scraper/collector.py` | Pull `region` + `competition` from `bp_detail`; pass to Snapshot construction. |
| Modify | `src/odds_scraper/writer.py` | Extend events upsert SQL + Python parameter tuple. |
| Modify | `tests/test_db_schema.py` | Test v2 columns exist on fresh + on upgraded v1 DB. Test idempotency. |
| Modify | `tests/test_collector.py` | Test extraction with `region`/`competition` present and absent. |
| Modify | `tests/test_writer.py` | Test round-trip and the NULL→real-value patching. |

Test ordering is TDD: write/extend tests first per task, see them fail, implement, see them pass, commit. Each task green at end.

---

## Task 1: db_schema v2 — idempotent column-add migration

**Files:**
- Modify: `src/odds_scraper/db_schema.py`
- Modify: `tests/test_db_schema.py`

### Step 1.1 — Add failing tests

- [ ] **Append to `tests/test_db_schema.py`** (existing tests stay):

```python
def test_v2_adds_country_and_league_columns(conn):
    init_schema(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(events)").fetchall()}
    assert {"country_id", "country_name", "league_id", "league_name"} <= cols


def test_v2_schema_version_recorded(conn):
    init_schema(conn)
    row = conn.execute(
        "SELECT version FROM schema_version WHERE rowid = 1"
    ).fetchone()
    assert row[0] == 2


def test_v2_upgrades_a_v1_database(conn):
    # Simulate a v1 database: run only the v1 base DDL (no country/league
    # columns) and pin schema_version to 1.
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS schema_version (
            rowid    INTEGER PRIMARY KEY CHECK (rowid = 1),
            version  INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS events (
            id           TEXT PRIMARY KEY,
            sr_id        TEXT,
            genius_id    TEXT,
            home         TEXT NOT NULL,
            away         TEXT NOT NULL,
            kickoff_utc  TEXT NOT NULL
        );
        INSERT OR REPLACE INTO schema_version (rowid, version) VALUES (1, 1);
    """)
    # Insert a pre-existing event so we can verify the migration didn't drop data
    conn.execute(
        "INSERT INTO events (id, home, away, kickoff_utc) "
        "VALUES ('E_OLD', 'Old Home', 'Old Away', '2026-05-01T00:00:00Z')",
    )

    # Run the migration
    init_schema(conn)

    # New columns exist
    cols = {r[1] for r in conn.execute("PRAGMA table_info(events)").fetchall()}
    assert {"country_id", "country_name", "league_id", "league_name"} <= cols
    # Existing data preserved, new columns NULL
    row = conn.execute(
        "SELECT id, home, country_id, country_name, league_id, league_name "
        "FROM events WHERE id = 'E_OLD'"
    ).fetchone()
    assert row[0] == "E_OLD"
    assert row[1] == "Old Home"
    assert row[2] is None and row[3] is None and row[4] is None and row[5] is None
    # Version bumped to 2
    v = conn.execute(
        "SELECT version FROM schema_version WHERE rowid = 1"
    ).fetchone()
    assert v[0] == 2


def test_v2_migration_is_idempotent_after_partial_failure(conn):
    # Simulate the scenario where the v2 ALTER TABLE statements have already
    # been applied to some columns but the schema_version was never bumped
    # (a crash between the ALTERs and the version write). Re-running
    # init_schema must NOT raise "duplicate column name".
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS schema_version (
            rowid    INTEGER PRIMARY KEY CHECK (rowid = 1),
            version  INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS events (
            id           TEXT PRIMARY KEY,
            sr_id        TEXT,
            genius_id    TEXT,
            home         TEXT NOT NULL,
            away         TEXT NOT NULL,
            kickoff_utc  TEXT NOT NULL,
            country_id   TEXT,
            country_name TEXT
        );
        INSERT OR REPLACE INTO schema_version (rowid, version) VALUES (1, 1);
    """)
    # Some country cols already added; v2 should add only the missing two
    init_schema(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(events)").fetchall()}
    assert {"country_id", "country_name", "league_id", "league_name"} <= cols
    v = conn.execute(
        "SELECT version FROM schema_version WHERE rowid = 1"
    ).fetchone()
    assert v[0] == 2
```

- [ ] **Run — confirm 4 new tests fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_db_schema.py -v`
Expected: The 4 existing schema tests pass; the 4 new tests fail (SCHEMA_VERSION still 1, columns don't exist, etc.).

### Step 1.2 — Implement v2 migration

- [ ] **Edit `src/odds_scraper/db_schema.py`** to:
  1. Add the 4 new columns to `_BASE_DDL` so fresh DBs get them at creation time.
  2. Add a private `_add_columns_if_missing` helper.
  3. Wire v2 migration via that helper.
  4. Bump `SCHEMA_VERSION` to 2.

The relevant parts of the file after edits should look like:

```python
SCHEMA_VERSION = 2

_BASE_DDL = """
CREATE TABLE IF NOT EXISTS schema_version (
    rowid    INTEGER PRIMARY KEY CHECK (rowid = 1),
    version  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id           TEXT PRIMARY KEY,
    sr_id        TEXT,
    genius_id    TEXT,
    home         TEXT NOT NULL,
    away         TEXT NOT NULL,
    kickoff_utc  TEXT NOT NULL,
    country_id   TEXT,
    country_name TEXT,
    league_id    TEXT,
    league_name  TEXT
);

CREATE TABLE IF NOT EXISTS snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc        TEXT NOT NULL,
    event_id      TEXT NOT NULL REFERENCES events(id),
    bookmaker     TEXT NOT NULL,
    status        TEXT NOT NULL,
    match_minute  INTEGER,
    score_home    INTEGER,
    score_away    INTEGER,
    fetch_status  TEXT NOT NULL,
    fetch_error   TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS prices (
    snapshot_id  INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    event_id     TEXT NOT NULL,
    ts_utc       TEXT NOT NULL,
    bookmaker    TEXT NOT NULL,
    market_id    TEXT NOT NULL,
    line         REAL NOT NULL DEFAULT 0.0,
    side         TEXT NOT NULL,
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
"""


def _add_columns_if_missing(
    conn: sqlite3.Connection, table: str, columns: list[tuple[str, str]],
) -> None:
    """Idempotently ALTER TABLE to add columns that don't already exist.

    columns is a list of (col_name, col_type) pairs, e.g.,
    [("country_id", "TEXT"), ("country_name", "TEXT")].

    SQLite's `ALTER TABLE ADD COLUMN` has no IF NOT EXISTS form, so we
    inspect PRAGMA table_info and only emit the ALTER for missing names.
    Makes migrations safe against partial-completion crashes where the
    ALTER succeeded but the schema_version bump didn't.
    """
    existing = {
        row[1]  # PRAGMA table_info columns: cid, name, type, notnull, dflt, pk
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    for col_name, col_type in columns:
        if col_name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}")


_MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {
    1: lambda conn: None,
    2: lambda conn: _add_columns_if_missing(conn, "events", [
        ("country_id",   "TEXT"),
        ("country_name", "TEXT"),
        ("league_id",    "TEXT"),
        ("league_name",  "TEXT"),
    ]),
}
```

The rest of `db_schema.py` (the `init_schema` function and `_current_version`) is unchanged.

### Step 1.3 — Run tests

- [ ] **Run db_schema tests — verify all 8 pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_db_schema.py -v`
Expected: 8 tests pass (4 existing + 4 new).

- [ ] **Run full suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: All tests still pass. No regression.

### Step 1.4 — Commit

- [ ] **Commit**

```bash
git add src/odds_scraper/db_schema.py tests/test_db_schema.py
git commit -m "$(cat <<'EOF'
feat(db_schema): v2 adds country_id/name + league_id/name to events

_BASE_DDL now includes the four new TEXT columns so fresh DBs get
them at creation. v2 migration uses _add_columns_if_missing which
inspects PRAGMA table_info and only emits ALTER TABLE for columns
that don't already exist — safe against partial-failure scenarios
where the ALTERs succeeded but the schema_version bump did not.

SCHEMA_VERSION = 2.
EOF
)"
```

---

## Task 2: Snapshot model — 4 new fields

**Files:**
- Modify: `src/odds_scraper/models.py`
- Modify: `tests/test_models.py`

### Step 2.1 — Add failing test

- [ ] **Append to `tests/test_models.py`**:

```python
def test_snapshot_default_country_league_fields_are_empty():
    snap = Snapshot(**_meta_kwargs())
    assert snap.country_id == ""
    assert snap.country_name == ""
    assert snap.league_id == ""
    assert snap.league_name == ""


def test_snapshot_accepts_country_league_kwargs():
    snap = Snapshot(**_meta_kwargs(
        country_id="242", country_name="Germany",
        league_id="12091", league_name="2nd Bundesliga",
    ))
    assert snap.country_id == "242"
    assert snap.country_name == "Germany"
    assert snap.league_id == "12091"
    assert snap.league_name == "2nd Bundesliga"
```

(The `_meta_kwargs()` helper already exists in test_models.py and produces a base Snapshot kwarg dict; the two new tests reuse it with and without the new fields.)

- [ ] **Run — confirm 2 new tests fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_models.py -v`
Expected: existing tests pass; the 2 new tests fail with `TypeError: ... got an unexpected keyword argument 'country_id'` or similar.

### Step 2.2 — Add fields to Snapshot

- [ ] **Edit `src/odds_scraper/models.py`** — find the `Snapshot` dataclass and append the four new fields AFTER the `prices` field (which has `default_factory=dict`). The complete dataclass should look like:

```python
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
    match_minute: Optional[int]
    score_home: Optional[int]
    score_away: Optional[int]
    bookmaker: Bookmaker
    fetch_status: FetchStatus
    fetch_error: str
    # NB: frozen=True does not freeze the contents of `prices` — the dict
    # itself remains mutable. By convention, populate it at construction in
    # the collector and never mutate afterwards.
    prices: dict[PriceKey, tuple[Optional[float], Optional[float]]] = field(
        default_factory=dict,
    )
    # Event-level metadata captured from BetPawa's region/competition keys.
    # Default "" so existing constructors (sentinel rows, older tests) keep
    # working unchanged.
    country_id: str = ""
    country_name: str = ""
    league_id: str = ""
    league_name: str = ""

    def to_csv_row(self) -> tuple[str, ...]:
        ...  # body unchanged
```

The `to_csv_row()` method body remains exactly as it is — country/league columns are not part of the CSV format (which is gone) and they're not used by `to_csv_row` anyway.

### Step 2.3 — Run tests

- [ ] **Run model tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_models.py -v`
Expected: All model tests pass (existing + 2 new).

- [ ] **Run full suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: All tests still pass. Note: the new fields have defaults, so existing Snapshot constructors in test fixtures (e.g., `_make_snap` in test_writer.py, `_one_snap` in test_watcher.py) don't need any changes.

### Step 2.4 — Commit

- [ ] **Commit**

```bash
git add src/odds_scraper/models.py tests/test_models.py
git commit -m "$(cat <<'EOF'
feat(models): 4 new country/league fields on Snapshot

country_id, country_name, league_id, league_name — all default to ""
so existing Snapshot constructors in tests and the sentinel-rows
path keep working unchanged. The collector populates them in the
next task.
EOF
)"
```

---

## Task 3: Collector — extract country/league from `bp_detail`

**Files:**
- Modify: `src/odds_scraper/collector.py`
- Modify: `tests/test_collector.py`

### Step 3.1 — Add failing tests

- [ ] **Append to `tests/test_collector.py`**:

```python
def _bp_detail_with_country_league() -> dict:
    """Same shape as _bp_detail() but with region/competition populated."""
    return {
        "id": "33660318",
        "participants": [
            {"id": "1", "name": "Team A", "position": 1},
            {"id": "2", "name": "Team B", "position": 2},
        ],
        "startTime": "2026-05-19T15:00:00Z",
        "additionalInfo": {"live": False},
        "results": None,
        "region":      {"id": "242",   "name": "Germany"},
        "competition": {"id": "12091", "name": "2nd Bundesliga"},
    }


async def test_collector_extracts_country_and_league(collector):
    rows = await collector.collect(
        _bp_detail_with_country_league(),
        resolved={Bookmaker.SPORTYBET: "sr:match:1",
                  Bookmaker.BET9JA: "b9j-7",
                  Bookmaker.BETWAY: "sr:match:1"},
        sr_id="sr:match:1", genius_id="",
    )
    # All four bookmaker rows get identical event-level metadata
    for r in rows:
        assert r.country_id == "242"
        assert r.country_name == "Germany"
        assert r.league_id == "12091"
        assert r.league_name == "2nd Bundesliga"


async def test_collector_handles_missing_region_competition(collector):
    # When the detail has no region/competition keys, all four fields
    # collapse to "" rather than raising.
    rows = await collector.collect(
        _bp_detail(),  # existing helper, no region/competition keys
        resolved={Bookmaker.SPORTYBET: "sr:match:1",
                  Bookmaker.BET9JA: "b9j-7",
                  Bookmaker.BETWAY: "sr:match:1"},
        sr_id="sr:match:1", genius_id="",
    )
    for r in rows:
        assert r.country_id == ""
        assert r.country_name == ""
        assert r.league_id == ""
        assert r.league_name == ""
```

- [ ] **Run — confirm 2 new tests fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_collector.py -v`
Expected: existing 11 tests pass; the 2 new tests fail because the collector doesn't populate the new Snapshot fields yet.

### Step 3.2 — Extract in collector

- [ ] **Edit `src/odds_scraper/collector.py`** — in the `collect` method, after the existing kickoff line, add the country/league extraction. Then update the `Snapshot(...)` constructor call inside the loop to pass them.

Find this block (approximately lines 39-48 + the Snapshot construction at ~lines 86-101):

```python
        ts = datetime.now(timezone.utc)
        status = parse_status(bp_detail)
        minute = parse_clock(bp_detail)
        score = parse_score(bp_detail)

        participants = extract_participants(bp_detail, "betpawa")
        home = participants.home or ""
        away = participants.away or ""

        kickoff = extract_kickoff(bp_detail, "betpawa") or ts
```

Add immediately after `kickoff`:

```python
        # Country and league come straight from BetPawa's structured
        # top-level keys. or {} defends against missing keys; or "" makes
        # individual missing names empty strings so the writer's upsert
        # treats them the same as a sentinel-row update (no-op patching).
        region = bp_detail.get("region") or {}
        competition = bp_detail.get("competition") or {}
        country_id = str(region.get("id") or "")
        country_name = str(region.get("name") or "")
        league_id = str(competition.get("id") or "")
        league_name = str(competition.get("name") or "")
```

Then find the `Snapshot(...)` construction in the per-bookmaker loop and add the four new kwargs. The whole `rows.append(Snapshot(...))` block becomes:

```python
            rows.append(Snapshot(
                ts_utc=ts,
                event_bp_id=str(bp_detail.get("id", "")),
                sr_id=sr_id or "",
                genius_id=genius_id or "",
                home=home, away=away,
                kickoff_utc=kickoff,
                status=status,
                match_minute=minute,
                score_home=score[0] if score else None,
                score_away=score[1] if score else None,
                bookmaker=b,
                fetch_status=status_fetch,
                fetch_error=error,
                prices=prices,
                country_id=country_id,
                country_name=country_name,
                league_id=league_id,
                league_name=league_name,
            ))
```

### Step 3.3 — Run tests

- [ ] **Run collector tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_collector.py -v`
Expected: 13 pass (11 existing + 2 new).

- [ ] **Run full suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: all green.

### Step 3.4 — Commit

- [ ] **Commit**

```bash
git add src/odds_scraper/collector.py tests/test_collector.py
git commit -m "$(cat <<'EOF'
feat(collector): extract country + league from BetPawa detail

bp_detail's top-level region/competition keys carry id+name pairs.
Read them with chained `or {}` / `or ""` guards so missing keys
become empty strings (writer's upsert treats those as no-op patches).
All four bookmaker snapshots from one tick share identical
event-level metadata.
EOF
)"
```

---

## Task 4: Writer — upsert events with country/league

**Files:**
- Modify: `src/odds_scraper/writer.py`
- Modify: `tests/test_writer.py`

### Step 4.1 — Add failing tests

- [ ] **Append to `tests/test_writer.py`**:

```python
async def test_writer_stores_country_and_league(tmp_path: Path):
    path = tmp_path / "out.db"
    snap = _make_snap(0)
    # _make_snap defaults country/league to "" via the dataclass default;
    # override here so we can check the round-trip.
    snap = Snapshot(
        ts_utc=snap.ts_utc,
        event_bp_id=snap.event_bp_id,
        sr_id=snap.sr_id, genius_id=snap.genius_id,
        home=snap.home, away=snap.away, kickoff_utc=snap.kickoff_utc,
        status=snap.status, match_minute=snap.match_minute,
        score_home=snap.score_home, score_away=snap.score_away,
        bookmaker=snap.bookmaker, fetch_status=snap.fetch_status,
        fetch_error=snap.fetch_error, prices=snap.prices,
        country_id="242", country_name="Germany",
        league_id="12091", league_name="2nd Bundesliga",
    )
    async with SqliteWriter(path) as w:
        await w.append([snap])
    rows = _query(
        path,
        "SELECT country_id, country_name, league_id, league_name "
        "FROM events WHERE id = ?",
        (snap.event_bp_id,),
    )
    assert rows == [("242", "Germany", "12091", "2nd Bundesliga")]


async def test_writer_patches_null_country_league_on_next_tick(tmp_path: Path):
    # First tick lacks country/league (e.g., a sentinel snapshot when the
    # detail poll failed); writer stores NULLs. Second tick has real values;
    # the upsert patches them in.
    path = tmp_path / "out.db"
    first = _make_snap(
        0,
        # country/league default to ""
    )
    second = Snapshot(
        ts_utc=first.ts_utc,
        event_bp_id=first.event_bp_id,
        sr_id=first.sr_id, genius_id=first.genius_id,
        home=first.home, away=first.away, kickoff_utc=first.kickoff_utc,
        status=first.status, match_minute=first.match_minute,
        score_home=first.score_home, score_away=first.score_away,
        bookmaker=first.bookmaker, fetch_status=first.fetch_status,
        fetch_error=first.fetch_error, prices=first.prices,
        country_id="242", country_name="Germany",
        league_id="12091", league_name="2nd Bundesliga",
    )
    async with SqliteWriter(path) as w:
        await w.append([first])
        # After the first tick the row exists with NULL country/league
        before = _query(
            path,
            "SELECT country_id, country_name, league_id, league_name "
            "FROM events WHERE id = ?",
            (first.event_bp_id,),
        )
        assert before == [(None, None, None, None)]
        await w.append([second])
    after = _query(
        path,
        "SELECT country_id, country_name, league_id, league_name "
        "FROM events WHERE id = ?",
        (first.event_bp_id,),
    )
    assert after == [("242", "Germany", "12091", "2nd Bundesliga")]


async def test_writer_keeps_country_league_when_later_tick_is_empty(tmp_path: Path):
    # Real values written first; a later sentinel tick (empty country/league)
    # must NOT overwrite the good values.
    path = tmp_path / "out.db"
    first = _make_snap(0)
    first_good = Snapshot(
        ts_utc=first.ts_utc,
        event_bp_id=first.event_bp_id,
        sr_id=first.sr_id, genius_id=first.genius_id,
        home=first.home, away=first.away, kickoff_utc=first.kickoff_utc,
        status=first.status, match_minute=first.match_minute,
        score_home=first.score_home, score_away=first.score_away,
        bookmaker=first.bookmaker, fetch_status=first.fetch_status,
        fetch_error=first.fetch_error, prices=first.prices,
        country_id="242", country_name="Germany",
        league_id="12091", league_name="2nd Bundesliga",
    )
    sentinel = _make_snap(1, fetch_status=FetchStatus.HTTP_ERROR,
                          fetch_error="timeout", prices={})
    async with SqliteWriter(path) as w:
        await w.append([first_good])
        await w.append([sentinel])  # empty country/league
    rows = _query(
        path,
        "SELECT country_id, country_name, league_id, league_name "
        "FROM events WHERE id = ?",
        (first.event_bp_id,),
    )
    assert rows == [("242", "Germany", "12091", "2nd Bundesliga")]
```

- [ ] **Run — confirm 3 new tests fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_writer.py -v`
Expected: existing tests pass; the 3 new tests fail (writer doesn't write country/league columns yet).

### Step 4.2 — Extend writer's events upsert

- [ ] **Edit `src/odds_scraper/writer.py`** — in `_write_batch`, find the events INSERT and extend both the SQL and the parameter tuple. The complete `conn.execute("INSERT INTO events ...")` block should become:

```python
                conn.execute(
                    "INSERT INTO events "
                    "(id, sr_id, genius_id, home, away, kickoff_utc, "
                    " country_id, country_name, league_id, league_name) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET "
                    "  sr_id = COALESCE(NULLIF(events.sr_id, ''), excluded.sr_id), "
                    "  genius_id = COALESCE(NULLIF(events.genius_id, ''), excluded.genius_id), "
                    "  home = CASE WHEN events.home = '' THEN excluded.home ELSE events.home END, "
                    "  away = CASE WHEN events.away = '' THEN excluded.away ELSE events.away END, "
                    "  country_id = COALESCE(NULLIF(events.country_id, ''), excluded.country_id), "
                    "  country_name = CASE "
                    "      WHEN events.country_name IS NULL OR events.country_name = '' "
                    "      THEN excluded.country_name ELSE events.country_name END, "
                    "  league_id = COALESCE(NULLIF(events.league_id, ''), excluded.league_id), "
                    "  league_name = CASE "
                    "      WHEN events.league_name IS NULL OR events.league_name = '' "
                    "      THEN excluded.league_name ELSE events.league_name END",
                    (s.event_bp_id, s.sr_id or None, s.genius_id or None,
                     s.home, s.away, _iso(s.kickoff_utc),
                     s.country_id or None, s.country_name or None,
                     s.league_id or None, s.league_name or None),
                )
```

The rest of `_write_batch` (snapshot insert, prices insert, BEGIN/COMMIT/ROLLBACK) is unchanged.

### Step 4.3 — Run tests

- [ ] **Run writer tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_writer.py -v`
Expected: all writer tests pass (existing + 3 new).

- [ ] **Run full suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: all green.

### Step 4.4 — Commit

- [ ] **Commit**

```bash
git add src/odds_scraper/writer.py tests/test_writer.py
git commit -m "$(cat <<'EOF'
feat(writer): upsert events.country_* and league_* with sticky patching

SQL gains four columns; ON CONFLICT clause uses the same pattern
already in place for sr_id/home/away — COALESCE(NULLIF(...)) for the
ID fields and CASE WHEN ... IS NULL OR = '' THEN excluded ELSE current
for the name fields (CASE handles the pre-migration NULL case that
COALESCE doesn't cover for fields stored with the empty-string-as-
empty convention).

Snapshot fields default to "" so sentinel rows just don't move the
needle on existing values.
EOF
)"
```

---

## Task 5: Full-suite smoke + live DB sanity check

**Files:** none modified; verification only.

### Step 5.1 — Run the full test suite

- [ ] **Run all tests**

Run: `.venv/Scripts/python.exe -m pytest -v`
Expected: every test passes. Specifically:
- `test_db_schema.py`: 8 (4 existing + 4 new)
- `test_models.py`: 14 (12 existing + 2 new)
- `test_collector.py`: 13 (11 existing + 2 new)
- `test_writer.py`: 14 (11 existing + 3 new)
- All other test files unchanged.

### Step 5.2 — Verify migration runs against the live odds.db

- [ ] **Migrate the existing live DB**

The user already has `data/odds.db` from previous scraper runs at SCHEMA_VERSION=1. Opening it with the new code should idempotently bump it to v2.

Run:
```bash
.venv/Scripts/python.exe -c "
import pathlib, sqlite3
from odds_scraper.db_schema import init_schema
db = pathlib.Path('data/odds.db').resolve()
conn = sqlite3.connect(str(db), isolation_level=None)
conn.execute('PRAGMA foreign_keys = ON')
init_schema(conn)
v = conn.execute('SELECT version FROM schema_version').fetchone()
print('schema_version:', v)
cols = [r[1] for r in conn.execute('PRAGMA table_info(events)').fetchall()]
print('events columns:', cols)
conn.close()
"
```
Expected: `schema_version: (2,)`. `events columns` includes `country_id`, `country_name`, `league_id`, `league_name` at the end.

Existing rows in the events table will have NULL in the new columns — they'll be patched on the next scraper tick per event.

### Step 5.3 — Restart the scraper and verify population

- [ ] **Restart the scraper**

If the user has the scraper running, Ctrl+C and re-run:

```powershell
python -m odds_scraper.main --config config.yaml
```

Let it run for at least one tick (~60-90s for the first batch).

### Step 5.4 — Confirm country/league are now populated for active events

- [ ] **Check the DB**

Run:
```bash
.venv/Scripts/python.exe -c "
import pathlib, sqlite3
db = pathlib.Path('data/odds.db').resolve()
conn = sqlite3.connect(f'file:{db.as_posix()}?mode=ro', uri=True)
rows = conn.execute(
    'SELECT id, home, away, country_name, league_name '
    'FROM events ORDER BY id LIMIT 5'
).fetchall()
for r in rows:
    print(r)
"
```
Expected: 5 events, all populated with non-empty country_name and league_name (e.g., `('33681190', 'Colorado Rapids', 'FC Dallas', 'USA', 'MLS')`).

### Step 5.5 — Commit any straggler fixes

- [ ] If anything required a fix during smoke, commit it. Otherwise skip.

If everything worked, no commit is needed in this task.

---

## Self-review

**Spec coverage:**
- 4 new TEXT columns on `events` → Task 1 (`_BASE_DDL` + v2 migration)
- Migration is idempotent against partial-failure → Task 1 (`_add_columns_if_missing` helper + test_v2_migration_is_idempotent_after_partial_failure)
- 4 new Snapshot fields with default `""` → Task 2
- Collector extraction from `bp_detail.region` / `bp_detail.competition` → Task 3
- Writer upsert with sticky empty-field patching → Task 4
- Tests for each layer → Tasks 1-4
- No UX consumption (deferred to sub-project 3) → not in this plan

**Placeholder scan:** no "TBD", no "implement later". Every step has complete code or a complete command with expected output. The to_csv_row() body is referenced as "unchanged" — that's correct, it's already in models.py and stays as-is; the implementer doesn't need to retype it.

**Type consistency:**
- Column names `country_id`, `country_name`, `league_id`, `league_name` — consistent across DDL, Snapshot fields, collector extraction, writer SQL, all tests
- Snapshot field order — new fields placed AFTER `prices` (which has `default_factory`) so default-ordering rules are satisfied
- The `_add_columns_if_missing(conn, table, columns)` signature — used in Task 1; called in `_MIGRATIONS[2]`
- `region` and `competition` dict keys in `bp_detail` — confirmed via live BetPawa inspection earlier in the session
