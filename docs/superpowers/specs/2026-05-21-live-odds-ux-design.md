# Live odds UX — design

**Status:** approved 2026-05-21
**New:** `src/odds_scraper/web/` subpackage + tests
**Untouched runtime:** `models.py`, `collector.py`, `watcher.py`, `event_resolver.py`, `writer.py`, `db_schema.py`, `main.py`, `config.py` (no changes needed for v1)

## Motivation

The scraper now writes a normalized SQLite DB (`data/odds.db`). To inspect what it's collecting in real time — and to compare odds across bookmakers at a glance — we need a read-only web UX. The design priorities, in order, are:

1. **Live focus.** Live matches are the most time-sensitive view; the UX must keep them fresh.
2. **Cross-bookmaker odds comparison.** Side-by-side bookmaker prices for each outcome of the markets we track.
3. **Trading-style information density.** Dark monospace, compact rows, no chrome.
4. **Minimum viable footprint.** Single-user, localhost, no auth, no build pipeline.

This spec covers **storage + schema's read-side consumer**. The ClickHouse export is a separate sub-project.

## Settled inputs

| Decision | Value |
|---|---|
| Stack | FastAPI + Jinja2 + HTMX (CDN) + Tailwind (CDN) |
| Server | uvicorn (stdlib install via `uvicorn[standard]`) |
| Process model | Separate from scraper; both share `data/odds.db` via WAL |
| DB connection mode | Read-only (`file:...?mode=ro&uri=true`) |
| Module home | `src/odds_scraper/web/` subpackage |
| Dependency group | `[project.optional-dependencies] web = ["fastapi", "uvicorn[standard]", "jinja2"]` |
| Entry | `python -m odds_scraper.web --config config.yaml` |
| Network binding | `127.0.0.1:8080` by default (configurable via flags) |
| Tabs | Live · Upcoming · Ended (Ended limited to last 24h) |
| Polling | Live 5s · Upcoming 30s · Ended 60s (the entire tab fragment polls itself via HTMX) |
| Card states | Collapsed (default) and Opened (click event header) |
| Collapsed visible markets | `1x2_ft`, `1x2_1up_ft`, `1x2_2up_ft` × {home, draw, away} (9 outcome rows) |
| Opened visible markets | All markets in `MARKET_MANIFEST` (currently 1x2 family + over_under_ft 1.5–9.5) |
| Probability column | Shown only for BetPawa & SportyBet, only when event is opened |
| Market group separation | Labeled header row per market group |
| Group layout: 1x2 family | Three separate groups: `▾ 1x2 — Full Time`, `▾ 1x2 — 1 Up`, `▾ 1x2 — 2 Up`. Each has 3 outcome rows (Home/Draw/Away). |
| Group layout: over_under_ft | One group per line: `▾ Over/Under 1.5`, `▾ Over/Under 2.5`, …, `▾ Over/Under 9.5`. Each has 2 rows (Over/Under). 9 groups, 18 rows total when all expanded. |
| Default expansion (opened event) | All market groups rendered expanded (every row visible). User can click a group's ▾ caret to collapse it — client-side display toggle, no extra request. |
| Bookmaker filter | Chips at top, all 4 enabled by default, client-side CSS toggle, persisted to localStorage |
| Open-state persistence | URL query string `?open=<id1>,<id2>` — client-side JS updates this; polling preserves state |
| Number formatting | Odds 2 decimals (`1.85`), probability 2 decimals short form (`.54`) |
| Theme | Dark monospace; green for odds (`#4ade80`); muted gray for probability (`#94a3b8`); blue accent for open state (`#1a1f2e` bg / `#93c5fd` text) |

## Architecture

### Process model

```
┌──────────────────────────┐         ┌──────────────────────────┐
│ Terminal 1               │         │ Terminal 2               │
│ python -m odds_scraper   │         │ python -m odds_scraper   │
│   .main --config ...     │         │   .web --config ...      │
│                          │         │                          │
│ SqliteWriter             │  WAL    │ FastAPI + uvicorn        │
│ (read-write)             │ ◄─────► │ (read-only)              │
└────────────┬─────────────┘         └────────────┬─────────────┘
             │                                    │
             ▼                                    ▼
             ┌────────────────────────────────────┐
             │  data/odds.db (WAL mode)           │
             └────────────────────────────────────┘
```

The web process opens the DB read-only. SQLite WAL mode means concurrent reads don't block the scraper's writes, and vice versa. Two processes, one file. No shared Python objects, no thread-pool coupling, no signal-handling tangles.

### Module layout

```
src/odds_scraper/web/
├── __init__.py
├── __main__.py             # python -m odds_scraper.web — uvicorn entry + CLI args
├── app.py                  # FastAPI app, route definitions, template rendering
├── queries.py              # SQL helpers: get_events_by_status, get_latest_prices_for_event
├── templates/
│   ├── base.html           # Page shell, dark theme, Tailwind+HTMX CDN tags
│   ├── index.html          # Top bar + tabs + filter chips + events-list container
│   ├── _events_list.html   # Fragment: card list for one tab
│   └── _event_card.html    # Fragment: one card (collapsed OR opened, branch in template)
└── static/
    └── app.css             # Hand-rolled overrides on top of Tailwind utilities

tests/
├── test_web_queries.py     # SQL helpers against a fixture DB
└── test_web_app.py         # FastAPI endpoints via TestClient
```

### Dependency group (`pyproject.toml`)

```toml
[project.optional-dependencies]
web = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "jinja2>=3.1",
]
```

Install: `pip install -e .[web]`. The scraper's runtime never imports any of these.

### Entry point (`src/odds_scraper/web/__main__.py`)

```python
import argparse
import uvicorn
from pathlib import Path

from .app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="odds-scraper web UX")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    app = create_app(config_path=args.config)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
```

## Endpoints

Two endpoints. Everything else is client-side state (chip filtering via CSS class, market-group expansion via display toggle) + HTMX swaps.

### `GET /`

Returns the full page (`index.html`):
- Top bar with title + tabs (Live · Upcoming · Ended)
- Bookmaker filter chips
- Empty `#events-list` container that immediately fires its first `hx-get` to load the initial tab (Live)

### `GET /events`

Returns the events-list fragment (`_events_list.html`).

Query params:
- `status=live|upcoming|ended` (default `live`)
- `open=<id1>,<id2>,...` — comma-separated event IDs to render in opened state (default empty)

Response: HTML fragment swapped into `#events-list` via HTMX `hx-swap="outerHTML"`. The fragment carries its own polling trigger:

```html
<div id="events-list"
     hx-get="/events?status=live&open=33681190"
     hx-trigger="every 5s"
     hx-swap="outerHTML">
  <!-- N <div class="card">...</div> nodes, each event_card.html rendered -->
</div>
```

The polling cadence varies by status:
- `live` → `hx-trigger="every 5s"`
- `upcoming` → `hx-trigger="every 30s"`
- `ended` → `hx-trigger="every 60s"`

### Client-side interactions (no extra endpoints)

| User action | What happens |
|---|---|
| Click tab | JS calls `htmx.ajax('GET', '/events?status=' + new_status, '#events-list')`; updates active-tab CSS |
| Click bookmaker chip | JS toggles a CSS class on `<body>` like `.hide-b9j`; CSS rules `[data-bookmaker="b9j"] { display: none }` hide those columns. Selection persisted to `localStorage.bookmakers` |
| Click event header (collapsed → opened) | JS reads current `open=` from the `#events-list` `hx-get`, adds this event_id, writes back, triggers HTMX request immediately |
| Click event header (opened → collapsed) | JS removes the event_id from `open=`, writes back, triggers HTMX request |
| Click market group label (▾ caret) | JS toggles `display` on `.market-variant-rows` within that group — pure client-side. Markets always rendered in the fragment; expansion is visual only. |

## Data queries (`queries.py`)

Read-only connection opened once at app startup:

```python
def open_ro_conn(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn
```

### `get_events_by_status(conn, status: str) -> list[Event]`

Returns one row per event whose latest snapshot has `status = <status>`.

For `status='ended'`, also filter by latest ts within the last 24h.

```sql
WITH latest_per_event AS (
    SELECT event_id, MAX(ts_utc) AS max_ts
    FROM snapshots
    GROUP BY event_id
)
SELECT
    e.id, e.home, e.away, e.kickoff_utc,
    s.status, s.match_minute, s.score_home, s.score_away,
    s.ts_utc AS latest_ts
FROM events e
JOIN latest_per_event l ON l.event_id = e.id
JOIN snapshots s ON s.event_id = l.event_id AND s.ts_utc = l.max_ts
WHERE s.status = :status
  AND (:cutoff IS NULL OR s.ts_utc >= :cutoff)
GROUP BY e.id                     -- collapse if multiple bookmakers at same ts
ORDER BY
    CASE :status
        WHEN 'live'     THEN -COALESCE(s.match_minute, 0)
        WHEN 'upcoming' THEN strftime('%s', e.kickoff_utc)
        WHEN 'ended'    THEN -strftime('%s', s.ts_utc)
    END;
```

### `get_latest_prices_for_event(conn, event_id: str, scope: str) -> list[Price]`

Latest price per `(bookmaker, market_id, line, side)` for the event.

`scope='collapsed'` → only `1x2_ft`, `1x2_1up_ft`, `1x2_2up_ft` markets.
`scope='opened'` → all markets.

```sql
WITH latest_per_bm AS (
    SELECT event_id, bookmaker, MAX(ts_utc) AS max_ts
    FROM prices
    WHERE event_id = :event_id
      AND (:collapsed_only = 0 OR market_id IN ('1x2_ft','1x2_1up_ft','1x2_2up_ft'))
    GROUP BY event_id, bookmaker
)
SELECT p.bookmaker, p.market_id, p.line, p.side, p.odds, p.probability
FROM prices p
JOIN latest_per_bm l
  ON l.event_id = p.event_id
 AND l.bookmaker = p.bookmaker
 AND l.max_ts = p.ts_utc
WHERE p.event_id = :event_id
  AND (:collapsed_only = 0 OR p.market_id IN ('1x2_ft','1x2_1up_ft','1x2_2up_ft'))
ORDER BY p.market_id, p.side, p.bookmaker;
```

### N+1 is acceptable here

For each event in the list (typically 4–30), we make one prices query. With 30 events at the upper end, that's 31 queries per poll. On SQLite locally with WAL + the indexes already in `db_schema.py`, this is sub-50ms total. We pay the simplicity cost over a single big JOIN.

## Templates

### `base.html`

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Odds — Live Comparison</title>
  <script src="https://unpkg.com/htmx.org@2.0.3"></script>
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="stylesheet" href="{{ url_for('static', path='app.css') }}">
</head>
<body class="bg-[#050505] text-gray-200 font-mono">
  {% block content %}{% endblock %}
  {% block scripts %}{% endblock %}
</body>
</html>
```

### `index.html`

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
       hx-trigger="load, every 30s"
       hx-swap="outerHTML">
  </div>
</div>
{% endblock %}

{% block scripts %}<script src="{{ url_for('static', path='app.js') }}"></script>{% endblock %}
```

### `_events_list.html`

```html
<div id="events-list"
     hx-get="/events?status={{ status }}{% if open_ids %}&open={{ open_ids|join(',') }}{% endif %}"
     hx-trigger="every {{ poll_seconds }}s"
     hx-swap="outerHTML"
     data-status="{{ status }}"
     data-open="{{ open_ids|join(',') }}">
  {% for event in events %}
    {% include "_event_card.html" with context %}
  {% endfor %}
</div>
```

### `_event_card.html`

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

  {# Column header — different if open (prob columns shown) vs closed #}
  <div class="col-header">
    <span class="lbl">{% if event.id in open_ids %}OUTCOME{% else %}MARKET · OUTCOME{% endif %}</span>
    <span class="lbl" data-bookmaker="betpawa">BP{% if event.id in open_ids %} <span class="prob-mark">+p</span>{% endif %}</span>
    <span class="lbl" data-bookmaker="sportybet">SB{% if event.id in open_ids %} <span class="prob-mark">+p</span>{% endif %}</span>
    <span class="lbl" data-bookmaker="bet9ja">B9J</span>
    <span class="lbl" data-bookmaker="betway">BW</span>
  </div>

  {% for market_id, market_name in markets_in_order(event) %}
    <div class="group-label">▾ {{ market_name }}</div>
    {% for side in ("home", "draw", "away") %}
      <div class="row">
        <span class="outcome">{% if event.id in open_ids %}{{ side|capitalize }}{% else %}{{ market_short(market_id) }} · {{ side[0]|upper }}{% endif %}</span>
        {% for bm in ("betpawa", "sportybet", "bet9ja", "betway") %}
          {% set p = event.prices.get((market_id, None, side), {}).get(bm) %}
          <span data-bookmaker="{{ bm }}">
            {% if p %}
              <span class="odds">{{ "%.2f"|format(p.odds) }}</span>
              {% if event.id in open_ids and bm in ("betpawa", "sportybet") and p.probability is not none %}
                <span class="prob">.{{ "%02d"|format(p.probability * 100) }}</span>
              {% endif %}
            {% else %}—{% endif %}
          </span>
        {% endfor %}
      </div>
    {% endfor %}
  {% endfor %}
</div>
```

(Pseudocode — actual implementation will use the `prices` data shape returned by `get_latest_prices_for_event`. The key invariant is: collapsed card omits the prob column even for BP/SB; opened card shows it.)

### `static/app.css`

Hand-rolled overrides for the dense monospace look. Tailwind utilities cover layout/spacing; CSS file covers the trading-style theme.

```css
.tab { padding: 4px 12px; font-size: 11px; color: #888; background: #0f0f0f;
       border: 1px solid #1a1a1a; border-radius: 3px; cursor: pointer;
       text-transform: uppercase; letter-spacing: 0.06em; }
.tab.active { background: #1a2a1a; color: #4ade80; border-color: #2a4a2a; }

.chip { padding: 2px 10px; border-radius: 3px; background: #1a1a1a;
        color: #d1d5db; font-size: 10px; cursor: pointer; }
.chip.on { background: #4ade80; color: #000; font-weight: 600; }

.card { background: #0a0a0a; border: 1px solid #1a1a1a; border-radius: 4px;
        margin-bottom: 8px; overflow: hidden; font-size: 11px; }
.ev { padding: 8px 12px; background: #141414; border-bottom: 1px solid #1f1f1f;
      cursor: pointer; display: flex; align-items: center; justify-content: space-between; }
.ev.open { background: #1a1f2e; border-bottom-color: #2a3a55; }
.ev-name { color: #fff; font-weight: 600; }
.ev-meta { color: #888; font-size: 10px; margin-top: 2px; }

.status-live    { color: #fbbf24; font-weight: 600; }
.status-upcoming{ color: #60a5fa; }
.status-ended   { color: #888; }

.lbl { color: #888; font-size: 9px; text-transform: uppercase; letter-spacing: 0.05em; }
.col-header { background: #0f0f0f; padding: 6px 12px;
              display: grid; grid-template-columns: 80px repeat(4, 1fr); gap: 4px; }
.group-label { padding: 5px 12px; background: #1a1f2e; color: #93c5fd;
               font-size: 9px; text-transform: uppercase; letter-spacing: 0.08em;
               border-top: 1px solid #2a3a55; border-bottom: 1px solid #1a1a1a; }
.group-label:first-of-type { border-top: 0; }

.row { display: grid; grid-template-columns: 80px repeat(4, 1fr); gap: 4px;
       padding: 5px 12px; align-items: center; border-bottom: 1px solid #111; }
.row:last-child { border-bottom: 0; }
.outcome { color: #d1d5db; }
.odds { color: #4ade80; font-weight: 600; }
.prob { color: #94a3b8; font-size: 9px; }

/* Bookmaker chip toggle — body class hides matching columns */
body.hide-betpawa   [data-bookmaker="betpawa"]   { display: none; }
body.hide-sportybet [data-bookmaker="sportybet"] { display: none; }
body.hide-bet9ja    [data-bookmaker="bet9ja"]    { display: none; }
body.hide-betway    [data-bookmaker="betway"]    { display: none; }
```

### Client-side JS (`static/app.js`)

```javascript
// Bookmaker chip toggling + persistence
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

// Tabs switch via HTMX programmatic request
function initTabs() {
  document.querySelectorAll('.tab[data-status]').forEach(t => {
    t.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
      t.classList.add('active');
      htmx.ajax('GET', `/events?status=${t.dataset.status}`, '#events-list');
    });
  });
}

// Card open/close — updates the polling URL's ?open= param then refetches
function toggleEvent(eventId) {
  const list = document.getElementById('events-list');
  const ids = new Set((list.dataset.open || '').split(',').filter(Boolean));
  if (ids.has(eventId)) ids.delete(eventId);
  else ids.add(eventId);
  const status = list.dataset.status;
  const openParam = Array.from(ids).join(',');
  const url = `/events?status=${status}${openParam ? '&open=' + openParam : ''}`;
  htmx.ajax('GET', url, '#events-list');
}

document.addEventListener('DOMContentLoaded', () => {
  initChips();
  initTabs();
});
```

(Pseudocode level — implementation has more guards around malformed states and the markets-expand click handler.)

## Testing

| File | Coverage |
|---|---|
| `tests/test_web_queries.py` | Build a fixture DB via `db_schema.init_schema`, insert known events / snapshots / prices, then call `get_events_by_status` and `get_latest_prices_for_event` to assert the returned rows match the input. ~6 tests. |
| `tests/test_web_app.py` | FastAPI `TestClient`. Cover: `GET /` returns 200 + contains "ODDS · LIVE COMPARISON" header; `GET /events?status=live` returns a fragment; `GET /events?status=upcoming&open=33` returns a fragment with the opened-event markup for event 33; unknown `status` returns 400. ~5 tests. |

### Not unit-tested (covered by manual smoke)

- HTMX swap behavior (browser-level)
- localStorage chip persistence (browser-level)
- The actual rendering quality / spacing / colors (visual; that's why we did the mockup)
- The auto-polling loop (timing-dependent; covered by visual verification)

### `tests/test_web_queries.py` cases

1. `get_events_by_status('live')` returns only events whose latest snapshot is `STARTED`
2. `get_events_by_status('upcoming')` returns only `UPCOMING`, ordered by kickoff_utc ascending
3. `get_events_by_status('ended')` returns only `ENDED` AND latest ts within 24h
4. `get_latest_prices_for_event(event_id, scope='collapsed')` returns only 1x2 family rows
5. `get_latest_prices_for_event(event_id, scope='opened')` returns all markets
6. For events with multiple snapshots, only the latest per bookmaker is returned

### `tests/test_web_app.py` cases

1. `GET /` returns 200, body contains tabs HTML
2. `GET /events?status=upcoming` returns 200, content type `text/html`, contains `<div id="events-list"`
3. `GET /events?status=upcoming&open=33681190` (with that event in DB) returns the opened-card markup (contains `class="ev open"` and `class="prob"`)
4. `GET /events?status=live` with no live events returns an empty-but-valid fragment (no error)
5. `GET /events?status=bogus` returns 400

## Out of scope

- **Authentication / authorization.** Localhost-only; trust the box.
- **Live odds-drift charts.** Just the latest snapshot per (bookmaker, outcome). Time-series charts are a follow-up.
- **Historical ENDED beyond 24h.** A future "Archive" tab with date filter / search.
- **Mobile-responsive layout.** Desktop-first; the trading table needs the horizontal space.
- **Sort/filter beyond bookmaker chips.** No "sort by kickoff", no per-market hide. Add on demand.
- **Manual refresh button.** Auto-poll only.
- **CSV / JSON export from the UI.** Future feature.
- **Search / jump-to-event by name.** YAGNI for now.
- **Light theme / theme switching.** Dark only.
- **Multi-language.** English only.
- **A user setting to change the default-collapsed markets.** Hardcoded to the 1x2 family for v1.
- **Streaming updates via SSE/WebSocket.** Polling is enough at this scale.
