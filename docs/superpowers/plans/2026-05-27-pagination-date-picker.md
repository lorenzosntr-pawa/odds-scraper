# Pagination + Date Picker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add scroll-based pagination (20 events per batch) across all tabs, remove the 24h ended cutoff, and add a date picker to the ended tab.

**Architecture:** Backend adds `offset`/`limit`/`date_from`/`date_to` params to the existing `get_events_by_status` query. The `/events` endpoint detects whether it's a full-page or HTMX-append request. `_events_list.html` renders a scroll sentinel div at the bottom that triggers loading the next batch via HTMX `revealed` trigger. The existing auto-poll refresh replaces only the visible cards without resetting scroll.

**Tech Stack:** Python, SQLite, FastAPI, HTMX, Jinja2

---

### Task 1: Add pagination + date params to `get_events_by_status`

**Files:**
- Modify: `src/odds_scraper/web/queries.py:34-96`
- Test: `tests/test_web_queries.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_web_queries.py`:

```python
def test_get_events_pagination_limit_offset(db_path: Path):
    """Pagination returns correct slice of events."""
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    # Seed 5 upcoming events
    for i in range(5):
        eid = f"EV{i}"
        conn.execute(
            "INSERT OR IGNORE INTO events (id, home, away, kickoff_utc) "
            "VALUES (?, ?, ?, ?)",
            (eid, f"Home{i}", f"Away{i}", f"2026-06-01T{10+i}:00:00Z"),
        )
        conn.execute(
            "INSERT INTO snapshots (event_id, ts_utc, bookmaker, status, fetch_status) "
            "VALUES (?, '2026-06-01T09:00:00Z', 'betpawa', 'UPCOMING', 'ok')",
            (eid,),
        )
    conn.close()
    conn = queries.open_ro_conn(db_path)
    all_rows = queries.get_events_by_status(conn, "upcoming", limit=100)
    page1 = queries.get_events_by_status(conn, "upcoming", limit=2, offset=0)
    page2 = queries.get_events_by_status(conn, "upcoming", limit=2, offset=2)
    conn.close()
    assert len(page1) == 2
    assert len(page2) == 2
    assert page1[0]["id"] != page2[0]["id"]


def test_get_events_ended_no_24h_cutoff(db_path: Path):
    """Ended events are no longer limited to 24h — old events appear."""
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.execute(
        "INSERT OR IGNORE INTO events (id, home, away, kickoff_utc) "
        "VALUES ('OLD1', 'TeamA', 'TeamB', '2026-05-01T10:00:00Z')",
    )
    conn.execute(
        "INSERT INTO snapshots (event_id, ts_utc, bookmaker, status, fetch_status) "
        "VALUES ('OLD1', '2026-05-01T12:00:00Z', 'betpawa', 'ENDED', 'ok')",
    )
    conn.close()
    conn = queries.open_ro_conn(db_path)
    rows = queries.get_events_by_status(conn, "ended", limit=100)
    conn.close()
    ids = [r["id"] for r in rows]
    assert "OLD1" in ids


def test_get_events_ended_date_filter(db_path: Path):
    """date_from / date_to filter ended events by snapshot timestamp."""
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    for day in ("01", "10", "20"):
        eid = f"MAY{day}"
        conn.execute(
            "INSERT OR IGNORE INTO events (id, home, away, kickoff_utc) "
            "VALUES (?, 'A', 'B', ?)",
            (eid, f"2026-05-{day}T10:00:00Z"),
        )
        conn.execute(
            "INSERT INTO snapshots (event_id, ts_utc, bookmaker, status, fetch_status) "
            "VALUES (?, ?, 'betpawa', 'ENDED', 'ok')",
            (eid, f"2026-05-{day}T12:00:00Z"),
        )
    conn.close()
    conn = queries.open_ro_conn(db_path)
    rows = queries.get_events_by_status(
        conn, "ended", date_from="2026-05-05", date_to="2026-05-15", limit=100,
    )
    conn.close()
    ids = [r["id"] for r in rows]
    assert "MAY10" in ids
    assert "MAY01" not in ids
    assert "MAY20" not in ids
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_web_queries.py -k "pagination or cutoff or date_filter" -v`
Expected: FAIL — `get_events_by_status` doesn't accept `limit`/`offset`/`date_from`/`date_to` params.

- [ ] **Step 3: Update `get_events_by_status`**

In `src/odds_scraper/web/queries.py`, replace the function signature and body:

```python
def get_events_by_status(
    conn: sqlite3.Connection, status: Status,
    *, country_id: str = "", league_id: str = "",
    offset: int = 0, limit: int = 20,
    date_from: str = "", date_to: str = "",
) -> list[sqlite3.Row]:
    """Return events whose latest snapshot is in the given status.

    Results are paginated via offset/limit. For the ended tab,
    date_from/date_to filter by snapshot timestamp (YYYY-MM-DD).
    """
    if status not in _STATUS_DB_VALUES:
        raise ValueError(
            f"unknown status {status!r}; expected one of "
            f"{sorted(_STATUS_DB_VALUES)}",
        )
    db_status = _STATUS_DB_VALUES[status]
    order_clause = {
        "live":     "ORDER BY s.match_minute DESC",
        "upcoming": "ORDER BY e.kickoff_utc ASC",
        "ended":    "ORDER BY s.ts_utc DESC",
    }[status]
    country_clause = "AND e.country_id = :country_id" if country_id else ""
    league_clause  = "AND e.league_id  = :league_id"  if league_id  else ""
    date_from_clause = "AND s.ts_utc >= :date_from" if date_from else ""
    date_to_clause   = "AND s.ts_utc < :date_to_next" if date_to else ""
    sql = f"""
        WITH latest AS (
            SELECT event_id, MAX(ts_utc) AS max_ts
            FROM snapshots
            GROUP BY event_id
        )
        SELECT
            e.id, e.home, e.away, e.kickoff_utc,
            s.status, s.match_minute, s.score_home, s.score_away,
            s.ts_utc AS latest_ts
        FROM events e
        JOIN latest l ON l.event_id = e.id
        JOIN snapshots s
          ON s.event_id = l.event_id
         AND s.ts_utc  = l.max_ts
        WHERE s.status = :db_status
          AND e.home != '' AND e.away != ''
          {date_from_clause}
          {date_to_clause}
          {country_clause}
          {league_clause}
        GROUP BY e.id
        {order_clause}
        LIMIT :limit OFFSET :offset
    """
    params: dict[str, str | int] = {
        "db_status": db_status,
        "limit": limit,
        "offset": offset,
    }
    if date_from:
        params["date_from"] = f"{date_from}T00:00:00Z"
    if date_to:
        params["date_to_next"] = (
            datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
        ).strftime("%Y-%m-%dT00:00:00Z")
    if country_id:
        params["country_id"] = country_id
    if league_id:
        params["league_id"] = league_id
    return conn.execute(sql, params).fetchall()
```

Key changes: removed 24h cutoff entirely, added `LIMIT :limit OFFSET :offset`, added `date_from`/`date_to` clauses.

- [ ] **Step 4: Add `get_date_range` helper**

Add below `get_events_by_status` in `queries.py`:

```python
def get_date_range(conn: sqlite3.Connection) -> dict[str, str]:
    """Min/max kickoff dates across all events, for date picker bounds."""
    row = conn.execute(
        "SELECT MIN(DATE(kickoff_utc)) AS min_date, "
        "       MAX(DATE(kickoff_utc)) AS max_date "
        "FROM events WHERE home != '' AND away != ''"
    ).fetchone()
    return {
        "min": row["min_date"] or "",
        "max": row["max_date"] or "",
    }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_web_queries.py -v`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add src/odds_scraper/web/queries.py tests/test_web_queries.py
git commit -m "feat(queries): add pagination, date filter, remove 24h ended cutoff"
```

---

### Task 2: Update `/events` endpoint for pagination + HTMX partials

**Files:**
- Modify: `src/odds_scraper/web/app.py:252-291`

- [ ] **Step 1: Update the `/events` route**

Replace the `events_fragment` function in `app.py`:

```python
    @app.get("/events", response_class=HTMLResponse)
    async def events_fragment(
        request: Request,
        status: str = Query("live"),
        country: str = Query(""),
        league: str = Query(""),
        offset: int = Query(0),
        limit: int = Query(20),
        date_from: str = Query(""),
        date_to: str = Query(""),
    ):
        if status not in queries.VALID_STATUSES:
            raise HTTPException(status_code=400, detail=f"unknown status {status!r}")
        rows = queries.get_events_by_status(
            conn, status, country_id=country, league_id=league,
            offset=offset, limit=limit,
            date_from=date_from, date_to=date_to,
        )
        prices_by_event = queries.get_latest_prices_for_events(
            conn, [row["id"] for row in rows], scope="opened",
        )
        latest_leads = pricer_score_state.max_leads_latest_for_events(
            conn, {row["id"] for row in rows},
        )
        events = [
            _build_event_view(
                row, prices_by_event.get(row["id"], []),
                max_leads=latest_leads.get(row["id"], (0, 0)),
            )
            for row in rows
        ]
        return templates.TemplateResponse(
            request,
            "_events_list.html",
            {
                "status": status,
                "events": events,
                "poll_seconds": _POLL_SECONDS[status],
                "offset": offset,
                "limit": limit,
                "country": country,
                "league": league,
                "date_from": date_from,
                "date_to": date_to,
                "has_more": len(events) == limit,
            },
        )
```

- [ ] **Step 2: Pass date range to index page**

In the `index` route, add date range to the template context:

```python
    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request, status: str = Query("upcoming")):
        if status not in queries.VALID_STATUSES:
            status = "upcoming"
        country_league_index = queries.get_country_league_index(conn)
        date_range = queries.get_date_range(conn)
        return templates.TemplateResponse(
            request, "index.html",
            {
                "country_league_index": country_league_index,
                "initial_status": status,
                "date_range": date_range,
            },
        )
```

- [ ] **Step 3: Commit**

```bash
git add src/odds_scraper/web/app.py
git commit -m "feat(app): wire pagination params and date range to endpoints"
```

---

### Task 3: Update `_events_list.html` with scroll sentinel

**Files:**
- Modify: `src/odds_scraper/web/templates/_events_list.html`

- [ ] **Step 1: Rewrite the template**

Replace the entire `_events_list.html` content:

```html
<div id="events-list"
     hx-get="/events?status={{ status }}&country={{ country }}&league={{ league }}&date_from={{ date_from }}&date_to={{ date_to }}&limit={{ limit }}"
     hx-trigger="every {{ poll_seconds }}s"
     hx-swap="outerHTML"
     data-status="{{ status }}">
  {% for event in events %}
    {% include "_event_card.html" %}
  {% endfor %}
  {% if has_more %}
  <div class="scroll-sentinel"
       hx-get="/events?status={{ status }}&offset={{ offset + limit }}&limit={{ limit }}&country={{ country }}&league={{ league }}&date_from={{ date_from }}&date_to={{ date_to }}"
       hx-trigger="revealed"
       hx-swap="afterend">
  </div>
  {% endif %}
</div>
```

The sentinel div appears only when there are more events. When it scrolls into view, HTMX fetches the next batch and inserts the cards after the sentinel (which then gets replaced by the new cards + a new sentinel if there are yet more).

**Important:** The `hx-swap="afterend"` on the sentinel means the response should be just the cards + next sentinel, NOT wrapped in a `#events-list` div. We need a separate partial for appended batches.

- [ ] **Step 2: Create `_events_batch.html` for appended pages**

Create `src/odds_scraper/web/templates/_events_batch.html`:

```html
{% for event in events %}
  {% include "_event_card.html" %}
{% endfor %}
{% if has_more %}
<div class="scroll-sentinel"
     hx-get="/events?status={{ status }}&offset={{ offset + limit }}&limit={{ limit }}&country={{ country }}&league={{ league }}&date_from={{ date_from }}&date_to={{ date_to }}"
     hx-trigger="revealed"
     hx-swap="outerHTML">
</div>
{% endif %}
```

- [ ] **Step 3: Update app.py to return batch template for scroll requests**

In the `events_fragment` endpoint, detect whether this is an append (offset > 0) or initial load, and return the appropriate template:

Add after the `has_more` line in the template context:

```python
        template_name = "_events_batch.html" if offset > 0 else "_events_list.html"
        return templates.TemplateResponse(
            request,
            template_name,
            { ... same context ... },
        )
```

- [ ] **Step 4: Add minimal CSS for the sentinel**

Add to `src/odds_scraper/web/static/app.css`:

```css
.scroll-sentinel { height: 1px; }
```

- [ ] **Step 5: Commit**

```bash
git add src/odds_scraper/web/templates/_events_list.html src/odds_scraper/web/templates/_events_batch.html src/odds_scraper/web/app.py src/odds_scraper/web/static/app.css
git commit -m "feat(ui): infinite scroll pagination with HTMX sentinel"
```

---

### Task 4: Add date picker to ended tab

**Files:**
- Modify: `src/odds_scraper/web/templates/index.html`
- Modify: `src/odds_scraper/web/static/app.js`
- Modify: `src/odds_scraper/web/static/app.css`

- [ ] **Step 1: Add date picker HTML to index.html**

In `index.html`, after the search filter-group (line 65), add:

```html
    <div class="filter-group filter-date-range">
      <span class="filter-lbl">Date range</span>
      <input type="date" id="date-from" class="date-input"
             min="{{ date_range.min }}" max="{{ date_range.max }}"
             title="Show events from this date">
      <span class="filter-lbl">to</span>
      <input type="date" id="date-to" class="date-input"
             min="{{ date_range.min }}" max="{{ date_range.max }}"
             title="Show events up to this date">
    </div>
```

- [ ] **Step 2: Add CSS to show date picker only on ended tab**

Add to `app.css`:

```css
.filter-date-range { display: none; }
body.tab-ended .filter-date-range { display: flex; }
```

- [ ] **Step 3: Wire date picker in app.js**

Add a new section in `app.js` after the search section:

```javascript
// -----------------------------------------------------------------------------
// Date range filter (ended tab only)
// -----------------------------------------------------------------------------
function applyDateRangeFromStorage() {
  const stored = LS.load(filterKey('date_range'), {from: '', to: ''});
  const fromEl = document.getElementById('date-from');
  const toEl = document.getElementById('date-to');
  if (fromEl) fromEl.value = stored.from || '';
  if (toEl) toEl.value = stored.to || '';
}

function getDateRange() {
  return LS.load(filterKey('date_range'), {from: '', to: ''});
}

function initDateRange() {
  const fromEl = document.getElementById('date-from');
  const toEl = document.getElementById('date-to');
  if (!fromEl || !toEl) return;

  function onChange() {
    LS.save(filterKey('date_range'), {
      from: fromEl.value || '',
      to: toEl.value || '',
    });
    reloadEvents();
  }
  fromEl.addEventListener('change', onChange);
  toEl.addEventListener('change', onChange);
  applyDateRangeFromStorage();
}
```

- [ ] **Step 4: Update `_activateTab` to include date params and restore state**

In the `_activateTab` function, add `applyDateRangeFromStorage()` alongside the other apply calls, and include date params in the HTMX request:

```javascript
function _activateTab(status) {
  document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
  const target = document.querySelector(`.tab[data-status="${status}"]`);
  if (target) target.classList.add('active');
  applyBodyTabClass();
  applyCountryLeagueFromStorage();
  applySearchInputFromStorage();
  applyKickoffControlsFromStorage();
  applyDateRangeFromStorage();
  const stored = LS.load(filterKey('country_league_filter'),
                         {country_id: '', league_id: ''});
  const dateRange = getDateRange();
  const params = new URLSearchParams({
    status,
    country: stored.country_id || '',
    league:  stored.league_id  || '',
  });
  if (dateRange.from) params.set('date_from', dateRange.from);
  if (dateRange.to) params.set('date_to', dateRange.to);
  window.htmx.ajax('GET', `/events?${params.toString()}`,
                   {target: '#events-list', swap: 'outerHTML'});
}
```

- [ ] **Step 5: Add `reloadEvents` helper and update country/league/search to use it**

Add a shared helper that rebuilds the URL from all current filter state and fetches page 1:

```javascript
function reloadEvents() {
  const status = currentStatus();
  const stored = LS.load(filterKey('country_league_filter'),
                         {country_id: '', league_id: ''});
  const dateRange = getDateRange();
  const params = new URLSearchParams({
    status,
    country: stored.country_id || '',
    league:  stored.league_id  || '',
  });
  if (dateRange.from) params.set('date_from', dateRange.from);
  if (dateRange.to) params.set('date_to', dateRange.to);
  window.htmx.ajax('GET', `/events?${params.toString()}`,
                   {target: '#events-list', swap: 'outerHTML'});
}
```

Update the existing country/league `onchange` handler to call `reloadEvents()` instead of manually building params. This ensures all filter changes include the date range.

- [ ] **Step 6: Call `initDateRange` in the init block**

In the `document.addEventListener('DOMContentLoaded', ...)` block at the bottom of `app.js`, add `initDateRange();` after `initSearch();`.

- [ ] **Step 7: Commit**

```bash
git add src/odds_scraper/web/templates/index.html src/odds_scraper/web/static/app.js src/odds_scraper/web/static/app.css
git commit -m "feat(ui): date picker for ended tab + wire into filter chain"
```

---

### Task 5: Full test suite + manual verification

- [ ] **Step 1: Run full test suite**

Run: `pytest tests/ -v --ignore=tests/test_simulator_routes.py --ignore=tests/test_web_app.py --ignore=tests/test_web_queries.py`

Then (if fastapi available): `pytest tests/test_web_app.py tests/test_web_queries.py tests/test_simulator_routes.py -v`

Expected: All pass.

- [ ] **Step 2: Manual browser test**

Start the dev server: `python -m odds_scraper.web --port 8080`

Verify:
- **Upcoming tab:** Shows 20 events, scrolling loads more
- **Live tab:** Shows 20 events, scrolling loads more, auto-refresh works
- **Ended tab:** Shows ALL ended events (not just 24h), scrolling loads more
- **Ended tab date picker:** Setting from/to filters events, scrolling loads more within filter
- **Country/league filter:** Works with pagination, resets to page 1 on change
- **Tab switching:** Restores filter state including date range

- [ ] **Step 3: Commit any fixes**

```bash
git add -A
git commit -m "fix: test and polish pagination + date picker"
```

- [ ] **Step 4: Push to main**

```bash
git push origin main
```
