# History-row match state column Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `STATE` column between `TIME (UTC)` and the bookmaker columns in the detail-page history table, showing `34' · 1–0` for live rows, `FT · 1–0` for ended, em-dash for upcoming.

**Architecture:** `get_market_history_for_event` JOINs `snapshots` to surface `match_minute`, `score_home`, `score_away`, `status` per row. `HistoryRow` carries them; `_build_event_detail` populates them once per `ts_utc` bucket. Template renders the new column.

**Tech Stack:** Python 3.13, FastAPI + Jinja2, SQLite read-only WAL, pytest.

**Spec reference:** `docs/superpowers/specs/2026-05-22-history-row-match-state-design.md`

**Branch:** `feat/history-row-match-state` (already checked out; spec already committed as `9c4d2b3`).

---

## File map

| Action | File | Responsibility |
|---|---|---|
| Modify | `src/odds_scraper/web/queries.py` | `get_market_history_for_event` SELECT extended; JOIN to `snapshots`. |
| Modify | `src/odds_scraper/web/app.py` | `HistoryRow` gains 4 fields; `_build_event_detail` bucketing populates them once per `ts_utc`. |
| Modify | `src/odds_scraper/web/templates/event_detail.html` | One new `<th>STATE</th>` in main head, one `<th class="state-col"></th>` in sub-head, one `<td>` per data row. |
| Modify | `src/odds_scraper/web/static/app.css` | `.state-col` styling (column width, centered alignment). |
| Modify | `tests/test_web_queries.py` | Query-level test for new columns. |
| Modify | `tests/test_web_app.py` | Three rendering tests: live row, ended row, upcoming em-dash row. |

---

## Task 1: Extend `get_market_history_for_event` query

**Files:**
- Modify: `src/odds_scraper/web/queries.py`
- Modify: `tests/test_web_queries.py`

### Step 1.1 — Write failing test

- [ ] **Add to `tests/test_web_queries.py`** (after the existing `get_market_history_for_event` tests):

```python
def test_get_market_history_for_event_includes_minute_and_score(db: Path):
    conn = sqlite3.connect(str(db), isolation_level=None)
    # The E_LIVE fixture snapshot has match_minute=34, score_home=1, score_away=0
    # already set in the shared db fixture.
    conn.close()
    conn = open_ro_conn(db)
    rows = get_market_history_for_event(conn, "E_LIVE", "1x2_ft", None)
    conn.close()
    assert len(rows) > 0
    r = rows[0]
    assert r["match_minute"] == 34
    assert r["score_home"] == 1
    assert r["score_away"] == 0
    assert r["status"] == "STARTED"
```

### Step 1.2 — Run, confirm FAIL

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_queries.py::test_get_market_history_for_event_includes_minute_and_score -v`
Expected: FAIL with `IndexError` or `KeyError: 'match_minute'`.

### Step 1.3 — Extend the SQL

- [ ] **Edit `src/odds_scraper/web/queries.py`**. Find `get_market_history_for_event` (around line 160). The current body is:

```python
def get_market_history_for_event(
    conn: sqlite3.Connection,
    event_id: str,
    market_id: str,
    line: float | None = None,
) -> list[sqlite3.Row]:
    """..."""
    db_line = 0.0 if line is None else float(line)
    sql = """
        SELECT ts_utc, bookmaker, side, odds, probability
        FROM prices
        WHERE event_id = ?
          AND market_id = ?
          AND line      = ?
          AND odds IS NOT NULL
        ORDER BY ts_utc DESC, bookmaker, side
    """
    return conn.execute(sql, (event_id, market_id, db_line)).fetchall()
```

Replace the SQL block with:

```python
    sql = """
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
    """
```

(The `db_line` computation and the `return conn.execute(...)` line stay the same. `(event_id, market_id, db_line)` parameter order matches the three `?` placeholders.)

### Step 1.4 — Run, confirm PASS

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_queries.py::test_get_market_history_for_event_includes_minute_and_score -v`
Expected: PASS.

### Step 1.5 — Run full web-queries suite

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_queries.py -v`
Expected: every test passes. The existing `test_get_market_history_for_event_*` tests do not assert on the now-added columns and should still pass.

### Step 1.6 — Commit

```bash
git add src/odds_scraper/web/queries.py tests/test_web_queries.py
git commit -m "$(cat <<'EOF'
feat(web/queries): surface match_minute + score per history row

get_market_history_for_event now JOINs the snapshots table to return
match_minute, score_home, score_away, and status alongside each priced
row. The detail-page UI uses these to render a 'STATE' column showing
the match state at each historical tick — essential for live-odds
analysis when reading how odds moved around a goal.

JOIN keys are (event_id, ts_utc, bookmaker), which uniquely identify a
snapshot. No duplication; per-bookmaker rows at the same ts_utc all
carry identical minute/score (they share one BetPawa detail extraction).
EOF
)"
```

---

## Task 2: Render the STATE column

**Files:**
- Modify: `src/odds_scraper/web/app.py`
- Modify: `src/odds_scraper/web/templates/event_detail.html`
- Modify: `src/odds_scraper/web/static/app.css`
- Modify: `tests/test_web_app.py`

### Step 2.1 — Write the three failing rendering tests

- [ ] **Add to `tests/test_web_app.py`**:

```python
def test_event_detail_history_row_renders_live_state(db_path: Path):
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    cur = conn.execute(
        "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, "
        "match_minute, score_home, score_away, fetch_status) "
        "VALUES ('2026-05-22T18:34:12Z', 'E1', 'betpawa', 'STARTED', "
        "34, 1, 0, 'ok')",
    )
    snap_id = cur.lastrowid
    conn.execute(
        "INSERT INTO prices (snapshot_id, event_id, ts_utc, bookmaker, "
        "market_id, line, side, odds, probability) "
        "VALUES (?, 'E1', '2026-05-22T18:34:12Z', 'betpawa', '1x2_2up_ft', "
        "0.0, 'home', 1.85, 0.54)",
        (snap_id,),
    )
    conn.close()
    client = TestClient(create_app(db_path=db_path))
    body = client.get("/events/E1").text
    assert "34' · 1–0" in body


def test_event_detail_history_row_renders_ended_state(db_path: Path):
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    cur = conn.execute(
        "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, "
        "match_minute, score_home, score_away, fetch_status) "
        "VALUES ('2026-05-22T20:00:00Z', 'E1', 'betpawa', 'ENDED', "
        "NULL, 2, 1, 'ok')",
    )
    snap_id = cur.lastrowid
    conn.execute(
        "INSERT INTO prices (snapshot_id, event_id, ts_utc, bookmaker, "
        "market_id, line, side, odds, probability) "
        "VALUES (?, 'E1', '2026-05-22T20:00:00Z', 'betpawa', '1x2_2up_ft', "
        "0.0, 'home', 1.85, 0.54)",
        (snap_id,),
    )
    conn.close()
    client = TestClient(create_app(db_path=db_path))
    body = client.get("/events/E1").text
    assert "FT · 2–1" in body


def test_event_detail_history_row_renders_dash_for_upcoming(db_path: Path):
    """The default fixture seeds an UPCOMING snapshot. The STATE cell
    for that row must contain the em-dash, not a minute or score."""
    client = TestClient(create_app(db_path=db_path))
    body = client.get("/events/E1").text
    # state-col cell present in the row, content is "—"
    assert 'class="state-col">—' in body or 'class="state-col">\n      —' in body


def test_event_detail_history_table_has_state_header(db_path: Path):
    client = TestClient(create_app(db_path=db_path))
    body = client.get("/events/E1").text
    # Main head row has STATE; sub-head has an empty state-col placeholder.
    assert ">STATE<" in body
```

- [ ] **Run — expected FAIL** for all four.

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_app.py -k "history_row_renders or history_table_has_state" -v`

### Step 2.2 — Extend `HistoryRow` and bucketing logic

- [ ] **In `src/odds_scraper/web/app.py`**, find the `HistoryRow` dataclass:

```python
@dataclass
class HistoryRow:
    """One snapshot's prices for a single market, all bookmakers/sides."""
    ts_utc: str
    # cells: {bookmaker: {side: PriceCell}}
    cells: dict[str, dict[str, PriceCell]]
```

Replace with:

```python
@dataclass
class HistoryRow:
    """One snapshot's prices for a single market, all bookmakers/sides,
    plus the match state recorded at that tick (minute + score + status)."""
    ts_utc: str
    match_minute: Optional[int]
    score_home: Optional[int]
    score_away: Optional[int]
    status: str
    # cells: {bookmaker: {side: PriceCell}}
    cells: dict[str, dict[str, PriceCell]]
```

- [ ] **In `_build_event_detail`**, find the bucketing block:

```python
    # Bucket by ts: {ts_utc: {bookmaker: {side: PriceCell}}}
    bucket: dict[str, dict[str, dict[str, PriceCell]]] = {}
    for hr in history_rows:
        ts = hr["ts_utc"]
        bm_cells = bucket.setdefault(ts, {})
        bm_cells.setdefault(hr["bookmaker"], {})[hr["side"]] = PriceCell(
            odds=hr["odds"], probability=hr["probability"],
        )

    # Newest first
    history = [
        HistoryRow(ts_utc=ts, cells=bucket[ts])
        for ts in sorted(bucket.keys(), reverse=True)
    ]
```

Replace with:

```python
    # Bucket by ts: {ts_utc: {"cells": {bm: {side: PriceCell}},
    #                         "minute": int|None, "score_home": int|None,
    #                         "score_away": int|None, "status": str}}
    # Per-bookmaker snapshots at the same ts share identical minute/score
    # (all four extract from the same BetPawa detail), so we set state once
    # on the first row encountered for each ts.
    bucket: dict[str, dict] = {}
    for hr in history_rows:
        ts = hr["ts_utc"]
        entry = bucket.setdefault(ts, {
            "cells": {}, "minute": hr["match_minute"],
            "score_home": hr["score_home"], "score_away": hr["score_away"],
            "status": hr["status"] or "",
        })
        entry["cells"].setdefault(hr["bookmaker"], {})[hr["side"]] = PriceCell(
            odds=hr["odds"], probability=hr["probability"],
        )

    # Newest first
    history = [
        HistoryRow(
            ts_utc=ts,
            match_minute=bucket[ts]["minute"],
            score_home=bucket[ts]["score_home"],
            score_away=bucket[ts]["score_away"],
            status=bucket[ts]["status"],
            cells=bucket[ts]["cells"],
        )
        for ts in sorted(bucket.keys(), reverse=True)
    ]
```

### Step 2.3 — Update the template

- [ ] **Edit `src/odds_scraper/web/templates/event_detail.html`** — find the `<thead>` block:

```jinja
<thead>
  <tr>
    <th class="ts-col">TIME (UTC)</th>
    {% for bm in ("betpawa", "sportybet", "bet9ja", "betway") %}
      <th data-bookmaker="{{ bm }}" colspan="{{ event.sides|length }}">
        {{ {"betpawa":"BetPawa","sportybet":"SportyBet","bet9ja":"Bet9ja","betway":"Betway"}[bm] }}
      </th>
    {% endfor %}
  </tr>
  <tr class="sub-head">
    <th class="ts-col"></th>
    {% for bm in ("betpawa", "sportybet", "bet9ja", "betway") %}
      {% for side in event.sides %}
        <th data-bookmaker="{{ bm }}" class="side-h">
          {{ event.sides_short[loop.index0] }}
        </th>
      {% endfor %}
    {% endfor %}
  </tr>
</thead>
```

Replace with:

```jinja
<thead>
  <tr>
    <th class="ts-col">TIME (UTC)</th>
    <th class="state-col">STATE</th>
    {% for bm in ("betpawa", "sportybet", "bet9ja", "betway") %}
      <th data-bookmaker="{{ bm }}" colspan="{{ event.sides|length }}">
        {{ {"betpawa":"BetPawa","sportybet":"SportyBet","bet9ja":"Bet9ja","betway":"Betway"}[bm] }}
      </th>
    {% endfor %}
  </tr>
  <tr class="sub-head">
    <th class="ts-col"></th>
    <th class="state-col"></th>
    {% for bm in ("betpawa", "sportybet", "bet9ja", "betway") %}
      {% for side in event.sides %}
        <th data-bookmaker="{{ bm }}" class="side-h">
          {{ event.sides_short[loop.index0] }}
        </th>
      {% endfor %}
    {% endfor %}
  </tr>
</thead>
```

- [ ] **In the same file**, find the `<tbody>` row loop:

```jinja
<tbody>
  {% for row in event.history %}
    <tr>
      <td class="ts-col">{{ row.ts_utc }}</td>
      {% for bm in ("betpawa", "sportybet", "bet9ja", "betway") %}
        ...
      {% endfor %}
    </tr>
  {% endfor %}
</tbody>
```

Insert one new `<td>` after the `ts-col` `<td>` and before the bookmaker loop:

```jinja
<tbody>
  {% for row in event.history %}
    <tr>
      <td class="ts-col">{{ row.ts_utc }}</td>
      <td class="state-col">{% if row.match_minute is not none and row.status == 'STARTED' %}{{ row.match_minute }}' · {{ row.score_home }}–{{ row.score_away }}{% elif row.status == 'ENDED' %}FT · {{ row.score_home }}–{{ row.score_away }}{% else %}—{% endif %}</td>
      {% for bm in ("betpawa", "sportybet", "bet9ja", "betway") %}
        ...
      {% endfor %}
    </tr>
  {% endfor %}
</tbody>
```

(The `<td>` is on one line to make `'class="state-col">—'` substring matching reliable in the test.)

### Step 2.4 — Add CSS for the new column

- [ ] **Edit `src/odds_scraper/web/static/app.css`** — append:

```css
.state-col {
  padding: 0 8px;
  text-align: center;
  font-variant-numeric: tabular-nums;
  color: #9ca3af;
  white-space: nowrap;
  font-size: 11px;
}
.history-table th.state-col {
  color: #6b7280;
  font-size: 9px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
```

### Step 2.5 — Run the four failing tests, see them pass

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_app.py -k "history_row_renders or history_table_has_state" -v`
Expected: all four PASS.

### Step 2.6 — Run full web suite

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_app.py tests/test_web_queries.py -v`
Expected: every test passes.

### Step 2.7 — Commit

```bash
git add src/odds_scraper/web/app.py src/odds_scraper/web/templates/event_detail.html src/odds_scraper/web/static/app.css tests/test_web_app.py
git commit -m "$(cat <<'EOF'
feat(web/detail): show match minute + score per history row

New STATE column between TIME (UTC) and bookmaker columns. Renders:
- "34' · 1–0" for live ticks (status == STARTED with match_minute set)
- "FT · 1–0" for ended ticks (status == ENDED)
- "—" otherwise (upcoming or pre-kickoff)

HistoryRow now carries match_minute, score_home, score_away, status —
populated once per ts_utc bucket since all bookmakers at the same tick
share the same BetPawa detail extraction.
EOF
)"
```

---

## Task 3: Full-suite smoke

**Files:** none modified; verification only.

### Step 3.1 — Run all tests

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: every test passes.

### Step 3.2 — Manual visual check (optional)

Start the web app and open a detail page for any event that has had at least one priced tick:

```powershell
.venv\Scripts\python.exe -m odds_scraper.web --db data\odds.db --port 8000
```

Open `http://localhost:8000/events/<some-event-id>`. Verify:
- The history table has a `STATE` column header (small caps, centred).
- Each row's STATE cell shows minute · score for live ticks, `FT · score` for ended, em-dash otherwise.
- Layout is stable: bookmaker column groups stay aligned with their sub-head sides.

### Step 3.3 — Commit any straggler fixes

If anything required a fix during the smoke, commit it with an appropriate `fix(...)` message. Otherwise no commit needed.

---

## Self-review

**Spec coverage:**
- Query gains `match_minute, score_home, score_away, status` columns → Task 1.
- `HistoryRow` carries them → Task 2.2.
- Bucketing picks values once per `ts_utc` → Task 2.2.
- Template adds `STATE` header + `<td>` per row → Task 2.3.
- Three rendering states (live / ended / upcoming) → Task 2.3 + 3 tests in Task 2.1.
- `STATE` column always rendered (per spec) → Task 2.3 (no conditional on event status).

**Placeholder scan:** no "TBD" / "implement later" / "etc." — every step has full code or commands with expected output.

**Type consistency:**
- `HistoryRow` fields introduced in Task 2.2 (`match_minute: Optional[int]`, `score_home: Optional[int]`, `score_away: Optional[int]`, `status: str`) match the template's `row.match_minute`, `row.score_home`, `row.score_away`, `row.status` usage in Task 2.3.
- Query SQL columns (Task 1.3) `match_minute, score_home, score_away, status` match the bucketing reads in Task 2.2 (`hr["match_minute"]`, etc.).
- `status` defaults to `""` (empty string) for safety in the bucketing — the template's `{% elif row.status == 'ENDED' %}` branch correctly handles both `""` and any non-ENDED non-STARTED value via the `else` fallback to em-dash.
