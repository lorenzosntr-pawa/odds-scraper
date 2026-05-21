# Live odds UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only web UX (FastAPI + HTMX + Tailwind via CDN) that serves a dark trading-style page comparing odds across bookmakers for live/upcoming/ended events.

**Architecture:** New `src/odds_scraper/web/` subpackage with its own entry point. Two endpoints (`/`, `/events`) — everything else is client-side state. Reads `data/odds.db` over a read-only WAL connection so it never blocks the scraper writer. Runs in a separate process: `python -m odds_scraper.main` (existing) and `python -m odds_scraper.web` (new).

**Tech Stack:** FastAPI, uvicorn, Jinja2, HTMX (CDN), Tailwind (CDN), stdlib sqlite3. pytest with `httpx`-based TestClient (already in fastapi[standard]).

**Spec reference:** `docs/superpowers/specs/2026-05-21-live-odds-ux-design.md`

---

## File map

| Action | File | Responsibility |
|---|---|---|
| Modify | `pyproject.toml` | Add `[project.optional-dependencies] web = [...]` group. |
| Create | `src/odds_scraper/web/__init__.py` | Empty package marker. |
| Create | `src/odds_scraper/web/__main__.py` | CLI: argparse + `uvicorn.run(create_app(...))`. |
| Create | `src/odds_scraper/web/app.py` | `create_app(db_path) -> FastAPI`. Two routes. View-model helpers. |
| Create | `src/odds_scraper/web/queries.py` | `open_ro_conn`, `get_events_by_status`, `get_latest_prices_for_event`. |
| Create | `src/odds_scraper/web/templates/base.html` | Page shell. |
| Create | `src/odds_scraper/web/templates/index.html` | Top bar + tabs + filter bar + events-list container. |
| Create | `src/odds_scraper/web/templates/_events_list.html` | Fragment: outer container + N event cards. |
| Create | `src/odds_scraper/web/templates/_event_card.html` | Fragment: one card, branches collapsed vs opened. |
| Create | `src/odds_scraper/web/static/app.css` | Trading-style theme + chip-hides-column rules. |
| Create | `src/odds_scraper/web/static/app.js` | Chip toggle + tab switch + card open/close. |
| Create | `tests/test_web_queries.py` | Unit tests against an in-memory DB via `db_schema.init_schema`. |
| Create | `tests/test_web_app.py` | Endpoint tests via FastAPI `TestClient`. |
| Unchanged | All other source files | — |

**Dependency ordering:**
- Task 1: deps + skeleton (foundation, no behavior).
- Task 2: queries.py + tests (standalone, no template/app deps).
- Task 3: app.py + templates + endpoint tests (depends on Task 2's queries).
- Task 4: static assets (CSS + JS, depends on templates' class names).
- Task 5: `__main__.py` entry point (depends on app.py).
- Task 6: manual smoke run against live `data/odds.db`.

---

## Task 1: Optional `web` dependency group + package skeleton

**Files:**
- Modify: `pyproject.toml`
- Create: `src/odds_scraper/web/__init__.py`

### Step 1.1 — Add optional dependency group

- [ ] **Read `pyproject.toml`** to locate the existing `[project.optional-dependencies]` block (the `dev` group should already be there).

- [ ] **Edit `pyproject.toml`** to add a `web` group inside `[project.optional-dependencies]`. Find this block:

```toml
[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
]
```

Replace with:

```toml
[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
]
web = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "jinja2>=3.1",
    "httpx>=0.27",
]
```

(`httpx` is needed by FastAPI's `TestClient` and isn't pulled in by `fastapi` alone in modern versions.)

### Step 1.2 — Install the new group

- [ ] **Install web deps**

Run: `.venv/Scripts/python.exe -m pip install -e ".[web,dev]"`
Expected: pip installs fastapi, uvicorn, jinja2, httpx (and their dependencies). Existing dev deps untouched.

### Step 1.3 — Create empty package marker

- [ ] **Create `src/odds_scraper/web/__init__.py`** with one-line content:

```python
"""Web UX subpackage — FastAPI + HTMX read-only consumer of data/odds.db."""
```

### Step 1.4 — Smoke-test imports

- [ ] **Verify imports work**

Run:
```bash
.venv/Scripts/python.exe -c "import fastapi, uvicorn, jinja2, httpx; from odds_scraper import web; print('all imports clean')"
```
Expected: `all imports clean`.

### Step 1.5 — Run existing tests

- [ ] **Confirm nothing existing broke**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: 86/86 still pass (the existing test count from the SQLite branch).

### Step 1.6 — Commit

- [ ] **Commit**

```bash
git add pyproject.toml src/odds_scraper/web/__init__.py
git commit -m "$(cat <<'EOF'
feat(web): add optional web dependency group + package skeleton

FastAPI + uvicorn + Jinja2 + httpx behind [project.optional-dependencies]
group `web`. Install via `pip install -e .[web]`. Scraper runtime
unaffected.
EOF
)"
```

---

## Task 2: `queries.py` — DB read helpers + unit tests

**Files:**
- Create: `src/odds_scraper/web/queries.py`
- Create: `tests/test_web_queries.py`

### Step 2.1 — Write failing tests

- [ ] **Create `tests/test_web_queries.py`** with full content:

```python
import sqlite3
from pathlib import Path

import pytest

from odds_scraper.db_schema import init_schema
from odds_scraper.web.queries import (
    get_events_by_status, get_latest_prices_for_event, open_ro_conn,
)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    """A DB seeded with a few events, snapshots, prices across states."""
    path = tmp_path / "odds.db"
    conn = sqlite3.connect(str(path), isolation_level=None)
    init_schema(conn)

    # Two upcoming, one live, one ended-within-24h, one ended-too-old
    events = [
        ("E_LIVE",     "Live Home",     "Live Away",     "2026-05-21T10:00:00Z"),
        ("E_UPCOMING", "Up Home",       "Up Away",       "2026-05-22T18:30:00Z"),
        ("E_UP2",      "Up2 Home",      "Up2 Away",      "2026-05-22T20:00:00Z"),
        ("E_ENDED",    "Ended Home",    "Ended Away",    "2026-05-20T15:00:00Z"),
        ("E_OLD",      "Old Home",      "Old Away",      "2026-05-18T15:00:00Z"),
    ]
    for eid, h, a, ko in events:
        conn.execute(
            "INSERT INTO events (id, home, away, kickoff_utc) VALUES (?, ?, ?, ?)",
            (eid, h, a, ko),
        )

    # Latest snapshot per event sets its current status.
    snaps = [
        ("E_LIVE",     "2026-05-21T11:00:00Z", "betpawa",   "STARTED",  34, 1, 0),
        ("E_UPCOMING", "2026-05-21T09:00:00Z", "betpawa",   "UPCOMING", None, None, None),
        ("E_UP2",      "2026-05-21T09:00:00Z", "betpawa",   "UPCOMING", None, None, None),
        ("E_ENDED",    "2026-05-20T17:00:00Z", "betpawa",   "ENDED",    90, 2, 1),
        ("E_OLD",      "2026-05-18T17:00:00Z", "betpawa",   "ENDED",    90, 0, 3),
    ]
    snap_ids: dict[str, int] = {}
    for eid, ts, bm, status, minute, sh, sa in snaps:
        cur = conn.execute(
            "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, "
            "match_minute, score_home, score_away, fetch_status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'ok')",
            (ts, eid, bm, status, minute, sh, sa),
        )
        snap_ids[eid] = cur.lastrowid

    # Prices for E_LIVE — both 1x2 family AND over_under_ft, two bookmakers
    bp_snap = snap_ids["E_LIVE"]
    prices_e_live = [
        # (market_id, line, side, odds, prob)
        ("1x2_ft",        0.0, "home", 1.85, 0.54),
        ("1x2_ft",        0.0, "draw", 3.40, 0.29),
        ("1x2_ft",        0.0, "away", 4.20, 0.23),
        ("1x2_1up_ft",    0.0, "home", 1.65, 0.60),
        ("1x2_1up_ft",    0.0, "draw", 3.20, 0.31),
        ("1x2_1up_ft",    0.0, "away", 4.50, 0.22),
        ("1x2_2up_ft",    0.0, "home", 2.50, 0.40),
        ("1x2_2up_ft",    0.0, "draw", 3.80, 0.26),
        ("1x2_2up_ft",    0.0, "away", 6.00, 0.16),
        ("over_under_ft", 2.5, "over",  1.70, 0.58),
        ("over_under_ft", 2.5, "under", 2.10, 0.42),
    ]
    for market_id, line, side, odds, prob in prices_e_live:
        conn.execute(
            "INSERT INTO prices (snapshot_id, event_id, ts_utc, bookmaker, "
            "market_id, line, side, odds, probability) "
            "VALUES (?, ?, ?, 'betpawa', ?, ?, ?, ?, ?)",
            (bp_snap, "E_LIVE", "2026-05-21T11:00:00Z",
             market_id, line, side, odds, prob),
        )
    conn.close()
    return path


def test_open_ro_conn_returns_readonly(db: Path):
    conn = open_ro_conn(db)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO events (id, home, away, kickoff_utc) "
                     "VALUES ('X', 'X', 'X', '2026-01-01T00:00:00Z')")
    conn.close()


def test_get_events_by_status_live(db: Path):
    conn = open_ro_conn(db)
    rows = get_events_by_status(conn, "live")
    conn.close()
    ids = [r["id"] for r in rows]
    assert ids == ["E_LIVE"]


def test_get_events_by_status_upcoming_ordered_by_kickoff(db: Path):
    conn = open_ro_conn(db)
    rows = get_events_by_status(conn, "upcoming")
    conn.close()
    ids = [r["id"] for r in rows]
    # E_UPCOMING kicks off before E_UP2
    assert ids == ["E_UPCOMING", "E_UP2"]


def test_get_events_by_status_ended_excludes_older_than_24h(db: Path, monkeypatch):
    # Pin "now" to 2026-05-21T12:00:00Z so E_ENDED (17:00 day before) is
    # within 24h and E_OLD (17:00 three days before) is outside it.
    import odds_scraper.web.queries as q
    monkeypatch.setattr(q, "_utcnow_iso",
                        lambda: "2026-05-21T12:00:00Z")
    conn = open_ro_conn(db)
    rows = get_events_by_status(conn, "ended")
    conn.close()
    ids = [r["id"] for r in rows]
    assert "E_ENDED" in ids
    assert "E_OLD" not in ids


def test_get_latest_prices_for_event_collapsed_only_1x2(db: Path):
    conn = open_ro_conn(db)
    rows = get_latest_prices_for_event(conn, "E_LIVE", scope="collapsed")
    conn.close()
    market_ids = {r["market_id"] for r in rows}
    assert market_ids == {"1x2_ft", "1x2_1up_ft", "1x2_2up_ft"}


def test_get_latest_prices_for_event_opened_includes_over_under(db: Path):
    conn = open_ro_conn(db)
    rows = get_latest_prices_for_event(conn, "E_LIVE", scope="opened")
    conn.close()
    market_ids = {r["market_id"] for r in rows}
    assert "over_under_ft" in market_ids
    assert {"1x2_ft", "1x2_1up_ft", "1x2_2up_ft"} <= market_ids


def test_invalid_status_raises(db: Path):
    conn = open_ro_conn(db)
    with pytest.raises(ValueError):
        get_events_by_status(conn, "bogus")
    conn.close()
```

- [ ] **Run — confirm fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_web_queries.py -v`
Expected: `ModuleNotFoundError: No module named 'odds_scraper.web.queries'`.

### Step 2.2 — Implement `queries.py`

- [ ] **Create `src/odds_scraper/web/queries.py`** with full content:

```python
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

Status = Literal["live", "upcoming", "ended"]
Scope = Literal["collapsed", "opened"]

_STATUS_DB_VALUES = {
    "live": "STARTED",
    "upcoming": "UPCOMING",
    "ended": "ENDED",
}

_COLLAPSED_MARKETS = ("1x2_ft", "1x2_1up_ft", "1x2_2up_ft")


def open_ro_conn(db_path: Path) -> sqlite3.Connection:
    """Open the odds DB read-only with row factory set to sqlite3.Row."""
    uri = f"file:{Path(db_path).as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _utcnow_iso() -> str:
    """Indirection to allow monkeypatching in tests."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_events_by_status(
    conn: sqlite3.Connection, status: Status,
) -> list[sqlite3.Row]:
    """Return events whose latest snapshot is in the given status.

    Ended events are limited to the last 24 hours.
    """
    if status not in _STATUS_DB_VALUES:
        raise ValueError(
            f"unknown status {status!r}; expected one of "
            f"{sorted(_STATUS_DB_VALUES)}",
        )
    db_status = _STATUS_DB_VALUES[status]
    cutoff: str | None = None
    if status == "ended":
        now = datetime.strptime(_utcnow_iso(), "%Y-%m-%dT%H:%M:%SZ")
        cutoff = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    order_clause = {
        "live":     "ORDER BY s.match_minute DESC",
        "upcoming": "ORDER BY e.kickoff_utc ASC",
        "ended":    "ORDER BY s.ts_utc DESC",
    }[status]
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
          {"AND s.ts_utc >= :cutoff" if cutoff else ""}
        GROUP BY e.id
        {order_clause}
    """
    params: dict[str, str] = {"db_status": db_status}
    if cutoff:
        params["cutoff"] = cutoff
    return conn.execute(sql, params).fetchall()


def get_latest_prices_for_event(
    conn: sqlite3.Connection, event_id: str, scope: Scope = "collapsed",
) -> list[sqlite3.Row]:
    """Latest price per (bookmaker, market_id, line, side) for one event.

    scope='collapsed' restricts to the 1x2 family.
    scope='opened' returns all markets.
    """
    if scope not in ("collapsed", "opened"):
        raise ValueError(f"unknown scope {scope!r}")
    market_filter = ""
    if scope == "collapsed":
        placeholders = ",".join("?" * len(_COLLAPSED_MARKETS))
        market_filter = f"AND market_id IN ({placeholders})"
    sql = f"""
        WITH latest_per_bm AS (
            SELECT event_id, bookmaker, MAX(ts_utc) AS max_ts
            FROM prices
            WHERE event_id = ?
              {market_filter}
            GROUP BY event_id, bookmaker
        )
        SELECT p.bookmaker, p.market_id, p.line, p.side,
               p.odds, p.probability
        FROM prices p
        JOIN latest_per_bm l
          ON l.event_id = p.event_id
         AND l.bookmaker = p.bookmaker
         AND l.max_ts = p.ts_utc
        WHERE p.event_id = ?
          {market_filter}
        ORDER BY p.market_id, p.line, p.side, p.bookmaker
    """
    params: list[str | int | float] = [event_id]
    if scope == "collapsed":
        params.extend(_COLLAPSED_MARKETS)
    params.append(event_id)
    if scope == "collapsed":
        params.extend(_COLLAPSED_MARKETS)
    return conn.execute(sql, params).fetchall()
```

- [ ] **Run tests — confirm pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_web_queries.py -v`
Expected: 7 tests pass.

- [ ] **Run full suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: 86 (previous) + 7 (new) = 93 pass.

### Step 2.3 — Commit

- [ ] **Commit**

```bash
git add src/odds_scraper/web/queries.py tests/test_web_queries.py
git commit -m "$(cat <<'EOF'
feat(web): queries module for live/upcoming/ended events + prices

open_ro_conn opens the DB read-only via file:?mode=ro URI.
get_events_by_status returns the latest snapshot per event filtered
by status; ended is capped to last 24h.
get_latest_prices_for_event returns latest price per (bookmaker,
market_id, line, side) for one event, with collapsed/opened scope.
EOF
)"
```

---

## Task 3: FastAPI app + Jinja templates + endpoint tests

**Files:**
- Create: `src/odds_scraper/web/app.py`
- Create: `src/odds_scraper/web/templates/base.html`
- Create: `src/odds_scraper/web/templates/index.html`
- Create: `src/odds_scraper/web/templates/_events_list.html`
- Create: `src/odds_scraper/web/templates/_event_card.html`
- Create: `tests/test_web_app.py`

### Step 3.1 — Write failing endpoint tests

- [ ] **Create `tests/test_web_app.py`** with full content:

```python
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from odds_scraper.db_schema import init_schema
from odds_scraper.web.app import create_app


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Minimal DB with one upcoming event and 1x2 prices for two bookmakers."""
    path = tmp_path / "odds.db"
    conn = sqlite3.connect(str(path), isolation_level=None)
    init_schema(conn)
    conn.execute(
        "INSERT INTO events (id, home, away, kickoff_utc) "
        "VALUES ('E1', 'Liverpool', 'Arsenal', '2026-05-22T18:30:00Z')",
    )
    cur = conn.execute(
        "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, fetch_status) "
        "VALUES ('2026-05-21T10:00:00Z', 'E1', 'betpawa', 'UPCOMING', 'ok')",
    )
    snap_id = cur.lastrowid
    for market_id in ("1x2_ft", "1x2_1up_ft", "1x2_2up_ft"):
        for side, odds, prob in [
            ("home", 1.85, 0.54), ("draw", 3.40, 0.29), ("away", 4.20, 0.23),
        ]:
            conn.execute(
                "INSERT INTO prices (snapshot_id, event_id, ts_utc, bookmaker, "
                "market_id, line, side, odds, probability) "
                "VALUES (?, 'E1', '2026-05-21T10:00:00Z', 'betpawa', ?, 0.0, ?, ?, ?)",
                (snap_id, market_id, side, odds, prob),
            )
    conn.close()
    return path


@pytest.fixture
def client(db_path: Path) -> TestClient:
    app = create_app(db_path=db_path)
    return TestClient(app)


def test_index_returns_page(client: TestClient):
    r = client.get("/")
    assert r.status_code == 200
    assert "ODDS" in r.text
    assert "events-list" in r.text
    assert "LIVE" in r.text and "UPCOMING" in r.text and "ENDED" in r.text


def test_events_fragment_upcoming_lists_event(client: TestClient):
    r = client.get("/events?status=upcoming")
    assert r.status_code == 200
    assert "Liverpool" in r.text and "Arsenal" in r.text
    assert 'id="events-list"' in r.text


def test_events_fragment_includes_polling_trigger(client: TestClient):
    r = client.get("/events?status=upcoming")
    assert 'hx-trigger="every 30s"' in r.text
    r = client.get("/events?status=live")
    assert 'hx-trigger="every 5s"' in r.text
    r = client.get("/events?status=ended")
    assert 'hx-trigger="every 60s"' in r.text


def test_events_fragment_collapsed_no_prob_column(client: TestClient):
    r = client.get("/events?status=upcoming")
    # No prob marks when collapsed
    assert "+p" not in r.text


def test_events_fragment_opened_shows_prob_for_bp_sb(client: TestClient):
    r = client.get("/events?status=upcoming&open=E1")
    assert "+p" in r.text  # the "+p" indicator in the column header
    # Probability cells rendered for BP only in this fixture
    assert "0.54" in r.text or ".54" in r.text


def test_events_unknown_status_returns_400(client: TestClient):
    r = client.get("/events?status=bogus")
    assert r.status_code == 400


def test_events_empty_status_returns_empty_list(client: TestClient):
    r = client.get("/events?status=live")
    # No live events in this DB → list container present, no event cards
    assert r.status_code == 200
    assert 'id="events-list"' in r.text
    assert "Liverpool" not in r.text
```

- [ ] **Run — confirm fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_web_app.py -v`
Expected: `ModuleNotFoundError: No module named 'odds_scraper.web.app'`.

### Step 3.2 — Create the templates

- [ ] **Create `src/odds_scraper/web/templates/base.html`** with full content:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Odds — Live Comparison</title>
  <script src="https://unpkg.com/htmx.org@2.0.3"></script>
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="stylesheet" href="/static/app.css">
</head>
<body class="bg-[#050505] text-gray-200 font-mono">
  {% block content %}{% endblock %}
  {% block scripts %}{% endblock %}
</body>
</html>
```

- [ ] **Create `src/odds_scraper/web/templates/index.html`** with full content:

```html
{% extends "base.html" %}
{% block content %}
<div class="p-3 max-w-screen-2xl mx-auto">
  <div class="flex justify-between border-b border-gray-900 pb-3 mb-3">
    <div class="text-white font-semibold tracking-wider">ODDS · LIVE COMPARISON</div>
    <div class="flex gap-1" id="tabs">
      <button class="tab" data-status="live">LIVE</button>
      <button class="tab active" data-status="upcoming">UPCOMING</button>
      <button class="tab" data-status="ended">ENDED</button>
    </div>
  </div>

  <div class="flex gap-2 items-center px-2 mb-2 text-xs text-gray-500">
    <span class="text-[9px] uppercase tracking-wider">Bookmakers</span>
    <button class="chip on" data-bookmaker="betpawa">BP</button>
    <button class="chip on" data-bookmaker="sportybet">SB</button>
    <button class="chip on" data-bookmaker="bet9ja">B9J</button>
    <button class="chip on" data-bookmaker="betway">BW</button>
  </div>

  <div id="events-list"
       hx-get="/events?status=upcoming"
       hx-trigger="load"
       hx-swap="outerHTML">
  </div>
</div>
{% endblock %}

{% block scripts %}
<script src="/static/app.js"></script>
{% endblock %}
```

- [ ] **Create `src/odds_scraper/web/templates/_events_list.html`** with full content:

```html
<div id="events-list"
     hx-get="/events?status={{ status }}{% if open_ids %}&open={{ open_ids|join(',') }}{% endif %}"
     hx-trigger="every {{ poll_seconds }}s"
     hx-swap="outerHTML"
     data-status="{{ status }}"
     data-open="{{ open_ids|join(',') }}">
  {% for event in events %}
    {% include "_event_card.html" %}
  {% endfor %}
</div>
```

- [ ] **Create `src/odds_scraper/web/templates/_event_card.html`** with full content:

```html
<div class="card" data-event-id="{{ event.id }}">
  <div class="ev{% if event.id in open_ids %} open{% endif %}"
       onclick="toggleEvent('{{ event.id }}')">
    <div>
      <div class="ev-name">{{ event.home }} — {{ event.away }}
        <span class="caret">{% if event.id in open_ids %}▼{% else %}▶{% endif %}</span>
      </div>
      <div class="ev-meta">
        {% if event.status == 'STARTED' %}
          <span class="status-live">LIVE {{ event.match_minute or '' }}'</span>
          {% if event.score_home is not none %} · {{ event.score_home }} – {{ event.score_away }}{% endif %}
        {% elif event.status == 'UPCOMING' %}
          <span class="status-upcoming">UPCOMING</span> · kickoff {{ event.kickoff_utc }}
        {% elif event.status == 'ENDED' %}
          <span class="status-ended">ENDED</span>
          {% if event.score_home is not none %} · {{ event.score_home }} – {{ event.score_away }}{% endif %}
        {% endif %}
      </div>
    </div>
  </div>

  <div class="col-header">
    <span class="lbl">{% if event.id in open_ids %}OUTCOME{% else %}MARKET · OUTCOME{% endif %}</span>
    <span class="lbl" data-bookmaker="betpawa">BP{% if event.id in open_ids %} <span class="prob-mark">+p</span>{% endif %}</span>
    <span class="lbl" data-bookmaker="sportybet">SB{% if event.id in open_ids %} <span class="prob-mark">+p</span>{% endif %}</span>
    <span class="lbl" data-bookmaker="bet9ja">B9J</span>
    <span class="lbl" data-bookmaker="betway">BW</span>
  </div>

  {% for group in event.market_groups %}
    <div class="group-label">▾ {{ group.label }}</div>
    {% for row in group.rows %}
      <div class="row">
        <span class="outcome">{% if event.id in open_ids %}{{ row.side_label }}{% else %}{{ row.market_label }} · {{ row.side_short }}{% endif %}</span>
        {% for bm in ("betpawa", "sportybet", "bet9ja", "betway") %}
          {% set p = row.prices.get(bm) %}
          <span data-bookmaker="{{ bm }}">
            {% if p %}
              <span class="odds">{{ "%.2f"|format(p.odds) }}</span>
              {% if event.id in open_ids and bm in ("betpawa", "sportybet") and p.probability is not none %}
                <span class="prob">.{{ "%02d"|format((p.probability * 100)|round|int) }}</span>
              {% endif %}
            {% else %}<span class="text-gray-700">—</span>{% endif %}
          </span>
        {% endfor %}
      </div>
    {% endfor %}
  {% endfor %}
</div>
```

### Step 3.3 — Implement `app.py`

- [ ] **Create `src/odds_scraper/web/app.py`** with full content:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import queries

# Markets visible by default in collapsed view, in display order
_COLLAPSED_ORDER = (
    ("1x2_ft",     "1x2 — Full Time", "1x2 ft"),
    ("1x2_1up_ft", "1x2 — 1 Up",      "1x2 1up"),
    ("1x2_2up_ft", "1x2 — 2 Up",      "1x2 2up"),
)

# Order in which OU lines render once an event is opened
_OU_LINES = (1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5, 8.5, 9.5)

_POLL_SECONDS = {"live": 5, "upcoming": 30, "ended": 60}

_SIDE_LABEL = {
    "home": "Home", "draw": "Draw", "away": "Away",
    "over": "Over", "under": "Under",
}

_SIDE_SHORT = {
    "home": "H", "draw": "D", "away": "A", "over": "O", "under": "U",
}


@dataclass
class PriceCell:
    odds: float
    probability: Optional[float]


@dataclass
class OutcomeRow:
    market_label: str       # e.g., "1x2 ft" (used in collapsed view)
    side_label: str         # e.g., "Home" (used in opened view)
    side_short: str         # e.g., "H"
    prices: dict[str, PriceCell]


@dataclass
class MarketGroup:
    label: str              # e.g., "1x2 — Full Time"
    rows: list[OutcomeRow]


@dataclass
class EventView:
    id: str
    home: str
    away: str
    kickoff_utc: str
    status: str
    match_minute: Optional[int]
    score_home: Optional[int]
    score_away: Optional[int]
    market_groups: list[MarketGroup]


def create_app(db_path: Path) -> FastAPI:
    """Build the FastAPI app with a read-only sqlite handle.

    db_path is captured in the closure so handlers reuse one connection per
    process. SQLite connections in WAL mode + check_same_thread=False are
    safe to share across uvicorn worker threads for read-only access.
    """
    pkg_dir = Path(__file__).parent
    templates = Jinja2Templates(directory=str(pkg_dir / "templates"))

    conn = queries.open_ro_conn(db_path)

    app = FastAPI(title="odds-scraper UX")
    app.mount("/static", StaticFiles(directory=str(pkg_dir / "static")), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return templates.TemplateResponse(
            request, "index.html", {},
        )

    @app.get("/events", response_class=HTMLResponse)
    async def events_fragment(
        request: Request,
        status: str = Query("live"),
        open: str = Query(""),
    ):
        if status not in queries._STATUS_DB_VALUES:
            raise HTTPException(status_code=400, detail=f"unknown status {status!r}")
        open_ids = [s for s in open.split(",") if s]
        rows = queries.get_events_by_status(conn, status)  # type: ignore[arg-type]
        events = [_build_event_view(conn, row, open_ids) for row in rows]
        return templates.TemplateResponse(
            request,
            "_events_list.html",
            {
                "status": status,
                "events": events,
                "open_ids": open_ids,
                "poll_seconds": _POLL_SECONDS[status],
            },
        )

    return app


def _build_event_view(
    conn, row, open_ids: list[str],
) -> EventView:
    is_open = row["id"] in open_ids
    scope = "opened" if is_open else "collapsed"
    price_rows = queries.get_latest_prices_for_event(
        conn, row["id"], scope=scope,  # type: ignore[arg-type]
    )
    # Bucket prices: {(market_id, line, side): {bookmaker: PriceCell}}
    bucket: dict[tuple[str, float, str], dict[str, PriceCell]] = {}
    for pr in price_rows:
        key = (pr["market_id"], pr["line"], pr["side"])
        bucket.setdefault(key, {})[pr["bookmaker"]] = PriceCell(
            odds=pr["odds"], probability=pr["probability"],
        )

    groups: list[MarketGroup] = []
    # 1x2 family: always rendered (collapsed AND opened)
    for market_id, group_label, market_short in _COLLAPSED_ORDER:
        rows_for_group = []
        for side in ("home", "draw", "away"):
            prices = bucket.get((market_id, 0.0, side), {})
            rows_for_group.append(OutcomeRow(
                market_label=market_short,
                side_label=_SIDE_LABEL[side],
                side_short=_SIDE_SHORT[side],
                prices=prices,
            ))
        groups.append(MarketGroup(label=group_label, rows=rows_for_group))

    # OU lines: only when opened
    if is_open:
        for line in _OU_LINES:
            rows_for_group = []
            for side in ("over", "under"):
                prices = bucket.get(("over_under_ft", line, side), {})
                rows_for_group.append(OutcomeRow(
                    market_label=f"OU {line}",
                    side_label=_SIDE_LABEL[side],
                    side_short=_SIDE_SHORT[side],
                    prices=prices,
                ))
            # Skip line groups with no data at all so opened cards don't
            # show 9 empty OU groups for prematch events that haven't
            # been quoted at high lines yet.
            if any(r.prices for r in rows_for_group):
                groups.append(MarketGroup(
                    label=f"Over/Under {line}", rows=rows_for_group,
                ))

    return EventView(
        id=row["id"], home=row["home"], away=row["away"],
        kickoff_utc=row["kickoff_utc"], status=row["status"],
        match_minute=row["match_minute"],
        score_home=row["score_home"], score_away=row["score_away"],
        market_groups=groups,
    )
```

### Step 3.4 — Create empty static directory

- [ ] **Create the `static/` directory** so `StaticFiles(...)` doesn't error at app start. Task 4 fills its contents.

```bash
mkdir -p src/odds_scraper/web/static
# Touch a placeholder so git tracks the directory
echo "/* placeholder, real content lands in Task 4 */" > src/odds_scraper/web/static/app.css
echo "/* placeholder, real content lands in Task 4 */" > src/odds_scraper/web/static/app.js
```

### Step 3.5 — Run endpoint tests

- [ ] **Run app tests — confirm pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_web_app.py -v`
Expected: 7 tests pass.

- [ ] **Run full suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: 93 (previous) + 7 (new) = 100 pass.

### Step 3.6 — Commit

- [ ] **Commit**

```bash
git add src/odds_scraper/web/app.py src/odds_scraper/web/templates/ \
        src/odds_scraper/web/static/ tests/test_web_app.py
git commit -m "$(cat <<'EOF'
feat(web): FastAPI app + Jinja templates + endpoint tests

Two routes: GET / (full page) and GET /events (HTMX fragment).
View-model layer (_build_event_view) buckets latest-prices rows into
market groups + outcome rows shaped for the template loops. Polling
cadence baked into the fragment per status (5s/30s/60s).

OU groups skip lines with no quoted prices to avoid 9 empty groups
on prematch events.
EOF
)"
```

---

## Task 4: Static assets — CSS theme + JS for chips, tabs, open state

**Files:**
- Modify: `src/odds_scraper/web/static/app.css`
- Modify: `src/odds_scraper/web/static/app.js`

No unit tests for static assets — covered by Task 6 manual smoke. Visual confirmation only.

### Step 4.1 — Write the CSS

- [ ] **Replace `src/odds_scraper/web/static/app.css`** with full content:

```css
/* Top bar tabs */
.tab {
  padding: 4px 12px;
  font-size: 11px;
  color: #888;
  background: #0f0f0f;
  border: 1px solid #1a1a1a;
  border-radius: 3px;
  cursor: pointer;
  text-transform: uppercase;
  letter-spacing: 0.06em;
}
.tab.active {
  background: #1a2a1a;
  color: #4ade80;
  border-color: #2a4a2a;
}

/* Bookmaker chips */
.chip {
  padding: 2px 10px;
  border-radius: 3px;
  background: #1a1a1a;
  color: #d1d5db;
  font-size: 10px;
  cursor: pointer;
}
.chip.on {
  background: #4ade80;
  color: #000;
  font-weight: 600;
}

/* Event card */
.card {
  background: #0a0a0a;
  border: 1px solid #1a1a1a;
  border-radius: 4px;
  margin-bottom: 8px;
  overflow: hidden;
  font-size: 11px;
}
.ev {
  padding: 8px 12px;
  background: #141414;
  border-bottom: 1px solid #1f1f1f;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.ev:hover { background: #181818; }
.ev.open {
  background: #1a1f2e;
  border-bottom-color: #2a3a55;
}
.ev-name { color: #fff; font-weight: 600; }
.ev-meta { color: #888; font-size: 10px; margin-top: 2px; }
.caret { color: #60a5fa; font-size: 9px; }

.status-live     { color: #fbbf24; font-weight: 600; }
.status-upcoming { color: #60a5fa; }
.status-ended    { color: #888; }

/* Tables */
.lbl {
  color: #888;
  font-size: 9px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.prob-mark { color: #4ade80; font-size: 9px; }
.col-header {
  background: #0f0f0f;
  padding: 6px 12px;
  display: grid;
  grid-template-columns: 110px repeat(4, 1fr);
  gap: 4px;
}
.group-label {
  padding: 5px 12px;
  background: #1a1f2e;
  color: #93c5fd;
  font-size: 9px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  border-top: 1px solid #2a3a55;
  border-bottom: 1px solid #1a1a1a;
  cursor: pointer;
}
.group-label:first-of-type { border-top: 0; }
.row {
  display: grid;
  grid-template-columns: 110px repeat(4, 1fr);
  gap: 4px;
  padding: 5px 12px;
  align-items: center;
  border-bottom: 1px solid #111;
}
.row:last-child { border-bottom: 0; }
.outcome { color: #d1d5db; }
.odds    { color: #4ade80; font-weight: 600; }
.prob    { color: #94a3b8; font-size: 9px; margin-left: 4px; }

/* Bookmaker chip toggle — body class hides matching columns */
body.hide-betpawa   [data-bookmaker="betpawa"]   { display: none; }
body.hide-sportybet [data-bookmaker="sportybet"] { display: none; }
body.hide-bet9ja    [data-bookmaker="bet9ja"]    { display: none; }
body.hide-betway    [data-bookmaker="betway"]    { display: none; }
```

### Step 4.2 — Write the JS

- [ ] **Replace `src/odds_scraper/web/static/app.js`** with full content:

```javascript
// Bookmaker chip toggle + localStorage persistence.
// On startup: read stored selections, apply CSS classes, wire click handlers.
function initChips() {
  const stored = JSON.parse(localStorage.getItem('bookmakers') || '{}');
  document.querySelectorAll('.chip[data-bookmaker]').forEach(c => {
    const bm = c.dataset.bookmaker;
    const on = stored[bm] !== false;  // default on
    c.classList.toggle('on', on);
    document.body.classList.toggle(`hide-${bm}`, !on);
    c.addEventListener('click', () => {
      const nowOn = !c.classList.contains('on');
      c.classList.toggle('on', nowOn);
      document.body.classList.toggle(`hide-${bm}`, !nowOn);
      stored[bm] = nowOn;
      localStorage.setItem('bookmakers', JSON.stringify(stored));
    });
  });
}

// Tab switching via HTMX programmatic request.
function initTabs() {
  document.querySelectorAll('.tab[data-status]').forEach(t => {
    t.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
      t.classList.add('active');
      window.htmx.ajax('GET', `/events?status=${t.dataset.status}`, '#events-list');
    });
  });
}

// Card open/close — update the list's ?open= URL param then re-request.
window.toggleEvent = function(eventId) {
  const list = document.getElementById('events-list');
  if (!list) return;
  const current = list.dataset.open || '';
  const ids = new Set(current.split(',').filter(Boolean));
  if (ids.has(eventId)) ids.delete(eventId);
  else ids.add(eventId);
  const status = list.dataset.status || 'live';
  const openParam = Array.from(ids).join(',');
  const url = `/events?status=${status}${openParam ? '&open=' + openParam : ''}`;
  window.htmx.ajax('GET', url, '#events-list');
};

// Group-label expand/collapse: toggle a class on the group's sibling .row nodes.
// Implementation deferred — by default all groups render expanded, so the
// click handler only matters once a user wants to collapse. Trivial follow-up.

document.addEventListener('DOMContentLoaded', () => {
  initChips();
  initTabs();
});
```

### Step 4.3 — Smoke-import the app

- [ ] **Verify the app still imports cleanly**

Run:
```bash
.venv/Scripts/python.exe -c "from odds_scraper.web.app import create_app; from pathlib import Path; app = create_app(Path('data/odds.db')); print('app built:', app.title)"
```
Expected: `app built: odds-scraper UX`.

### Step 4.4 — Run tests

- [ ] **Run full suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: 100/100 still pass.

### Step 4.5 — Commit

- [ ] **Commit**

```bash
git add src/odds_scraper/web/static/app.css src/odds_scraper/web/static/app.js
git commit -m "$(cat <<'EOF'
feat(web): trading-style dark theme + chip/tab/open-state JS

CSS: dark monospace, green odds, muted prob, blue accent for open
event headers. Bookmaker chip toggle hides columns via body class.

JS: chip persistence via localStorage. Tab switch + card open/close
both go through htmx.ajax against the events-list element.
EOF
)"
```

---

## Task 5: `__main__.py` entry point

**Files:**
- Create: `src/odds_scraper/web/__main__.py`

### Step 5.1 — Implement the entry

- [ ] **Create `src/odds_scraper/web/__main__.py`** with full content:

```python
from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from ..config import load_config
from .app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(
        description="odds-scraper web UX — read-only consumer of data/odds.db",
    )
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    cfg = load_config(args.config)
    db_path = Path(cfg.output.db_path)
    if not db_path.exists():
        raise SystemExit(
            f"db not found at {db_path}; run the scraper first to create it",
        )
    app = create_app(db_path=db_path)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
```

### Step 5.2 — Smoke-test `--help`

- [ ] **Verify the CLI parses**

Run:
```bash
.venv/Scripts/python.exe -m odds_scraper.web --help
```
Expected: help text listing `--config`, `--host`, `--port`.

### Step 5.3 — Commit

- [ ] **Commit**

```bash
git add src/odds_scraper/web/__main__.py
git commit -m "$(cat <<'EOF'
feat(web): __main__ entry point launching uvicorn

`python -m odds_scraper.web --config config.yaml` reads db_path from
the existing config and serves at 127.0.0.1:8080 by default.
--host / --port override.
EOF
)"
```

---

## Task 6: Manual smoke run against live data/odds.db

**Files:** none modified; verification only.

### Step 6.1 — Run all tests one more time

- [ ] **Run all tests**

Run: `.venv/Scripts/python.exe -m pytest -v`
Expected: 100 tests pass. (Numbers may vary by 1-2 from baseline; key is no regressions.)

### Step 6.2 — Boot the scraper (Terminal 1)

- [ ] **Start the scraper**

Run: `python -m odds_scraper.main --config config.yaml`

Let it bootstrap until you see the first `tick ... bp=N/54 ...` log line. Leave it running.

### Step 6.3 — Boot the web UX (Terminal 2)

- [ ] **Start the web server**

In a new PowerShell:
```powershell
.\.venv\Scripts\Activate.ps1
python -m odds_scraper.web --config config.yaml
```
Expected: uvicorn log lines including `Uvicorn running on http://127.0.0.1:8080`.

### Step 6.4 — Open the browser and verify

- [ ] **Browse to http://127.0.0.1:8080**

Verify:
1. Top bar shows `ODDS · LIVE COMPARISON` and three tabs (LIVE / UPCOMING / ENDED). UPCOMING is active by default.
2. Filter bar shows four chips (BP, SB, B9J, BW), all green.
3. Below: a list of event cards for the UPCOMING events from your `data/odds.db` (MLS + any standalone).
4. Each card shows 9 outcome rows grouped by three labeled headers: `▾ 1x2 — Full Time`, `▾ 1x2 — 1 Up`, `▾ 1x2 — 2 Up`.
5. Numbers are green (odds), no probability column visible.

### Step 6.5 — Interaction smoke

- [ ] **Click a bookmaker chip** — that column should disappear. Click again — it returns. Reload the page — your selection persists (localStorage).

- [ ] **Click an event header** — the card "opens": the header gets a blue tint, the `+p` markers appear next to BP/SB column headers, and the prob values appear next to BP/SB odds. OU line groups appear underneath the 1x2 family (only for lines that have data).

- [ ] **Click the same header again** — card collapses back to default state.

- [ ] **Click the LIVE tab** — switches to live events (likely empty if no matches are in-play right now). The events-list fragment polls every 5s; you can confirm by opening browser dev tools → Network and watching `/events?status=live` fetches.

- [ ] **Click ENDED** — shows events whose latest snapshot was ENDED within the last 24h.

### Step 6.6 — Stop both processes

- [ ] **Ctrl+C in Terminal 2** to stop the web server.
- [ ] **Ctrl+C in Terminal 1** to stop the scraper.

### Step 6.7 — Commit any fixes

- [ ] If the smoke surfaced bugs, commit fixes with `fix(web): ...` messages.

If everything worked first try, no commit is needed.

---

## Self-review

**Spec coverage:**
- Stack (FastAPI + HTMX + Tailwind via CDN) → Task 3 (templates), Task 4 (CSS), Task 5 (entry)
- Separate process / read-only WAL → Task 2 (open_ro_conn), Task 5 (entry)
- Two endpoints (`/`, `/events`) → Task 3 step 3.3 (app.py)
- Tabs (Live/Upcoming/Ended, 24h cap on ended) → Task 2 (cutoff in get_events_by_status), Task 3 (template)
- Card states (collapsed / opened) → Task 3 (template + _build_event_view scope branching)
- Collapsed default markets (1x2 family) → Task 3 (`_COLLAPSED_ORDER` constant + queries' scope='collapsed')
- Opened view: all markets + prob for BP/SB → Task 3 (template branching on `event.id in open_ids`)
- Market group separation (labeled header rows) → Task 3 (template `group-label` divs), Task 4 (CSS)
- 1x2 family split into 3 groups; OU one group per line → Task 3 (`_build_event_view` build order)
- Bookmaker filter (chips, client-side CSS, localStorage) → Task 4 (CSS + JS)
- Polling cadence per tab (5s / 30s / 60s) → Task 3 (`_POLL_SECONDS`)
- Open-state via URL `?open=` query → Task 3 (handler reads `open` param) + Task 4 (`toggleEvent` JS updates URL)
- Localhost binding → Task 5 (`--host 127.0.0.1` default)
- Theme (dark, green odds, muted prob, blue open state) → Task 4 (CSS)
- Tests: queries unit + endpoint tests → Tasks 2 + 3
- Manual smoke → Task 6

**Placeholder scan:** no "TBD" or "implement later" in the plan body. The group-label expand/collapse JS handler is acknowledged as a follow-up enhancement (groups render expanded by default, so the missing handler doesn't break v1).

**Type consistency:**
- `EventView`, `MarketGroup`, `OutcomeRow`, `PriceCell` defined in Task 3, consumed by templates in Task 3 — names match exactly.
- `queries.open_ro_conn`, `queries.get_events_by_status`, `queries.get_latest_prices_for_event` — signatures consistent across Tasks 2 (definition) and 3 (call sites).
- `_POLL_SECONDS` keys (`live`/`upcoming`/`ended`) match the `status` query-param values and the `_STATUS_DB_VALUES` mapping in queries.py.
- `_COLLAPSED_ORDER` tuple shape `(market_id, group_label, market_short)` consistent between definition (app.py top) and use (`_build_event_view`).
- Bookmaker tuple `("betpawa", "sportybet", "bet9ja", "betway")` consistent across `_event_card.html`, app.css, app.js.
