# UX consumers (country/league filter + new-market expander + two-stage pills) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the home-page and detail-page web UX to the country/league data (sub-project 1) and the three new markets (sub-project 2): cascading Country→League dropdowns, card-expander gains next_goal + per-team OU groups, detail-page pills become a two-stage family + line selector showing only lines with data for the event.

**Architecture:** Pure web-layer change inside `src/odds_scraper/web/`. A single ordered `_EXPANDER_MARKETS` table in `app.py` declares which parameterized markets appear in the card expander and detail-page family-chip row, in display order. Outcome ordering (`sides`) is read from `MARKET_MANIFEST` via a `_spec_by_id` lookup so the UX stays manifest-driven. The country/league index is fetched once and embedded as JSON in the `index.html` response; the cascading dropdown is a client-side reactive widget.

**Tech Stack:** Python 3.13, FastAPI + Jinja2 + HTMX (CDN) + vanilla JS, SQLite read-only WAL, pytest.

**Spec reference:** `docs/superpowers/specs/2026-05-22-ux-consumers-country-league-new-markets-design.md`

**Branch:** create `feat/ux-consumers-country-league-new-markets` off `main` before starting Task 1.

---

## File map

| Action | File | Responsibility |
|---|---|---|
| Modify | `src/odds_scraper/web/app.py` | `_SIDE_LABEL`/`_SIDE_SHORT` gain `"none"`; `_sides_for(market_id)` helper replaces hardcoded `_SIDES_*`; `_EXPANDER_MARKETS` ordered table; `_build_event_view` loops over it; `_build_event_detail` produces family + line pill rows; `/events` route accepts `country` + `league`; `/` route embeds country/league index JSON. |
| Modify | `src/odds_scraper/web/queries.py` | New `get_country_league_index`; new `get_available_lines`; `get_events_by_status` gains `country_id` + `league_id` kwargs; `get_event_meta` SELECT extended with `e.country_name, e.league_name`. |
| Modify | `src/odds_scraper/web/templates/index.html` | Filter row gains two `<select>` elements; `<script id="country-league-index">` JSON tag. |
| Modify | `src/odds_scraper/web/templates/_event_card.html` | Expand-toggle button copy switches from "Show N Over/Under lines" → "Show N more odds". (Card iterates `event.market_groups` already — no per-market-id changes needed.) |
| Modify | `src/odds_scraper/web/templates/event_detail.html` | Header gains subtitle `<div>`; pill block restructured into family row + conditional line row; inline `{"home":"H",…}` dict replaced by `side_short` already on backend payload. |
| Modify | `src/odds_scraper/web/static/app.js` | New `initCountryLeagueFilter()` reads embedded JSON, populates selects, persists choice; existing functions unchanged. |
| Modify | `tests/test_web_queries.py` | New tests for the three new query operations; reuse existing fixture pattern. |
| Modify | `tests/test_web_app.py` | New tests for filter route, subtitle, card-expander groups, two-stage pills; update existing `test_event_detail_pills_include_ou_lines`. |

---

## Task 1: `none` side label + sides lookup from manifest

**Files:**
- Modify: `src/odds_scraper/web/app.py`
- Modify: `src/odds_scraper/web/templates/event_detail.html`
- Test: `tests/test_web_app.py`

Foundational change. Without this, `next_goal_ft` will raise `KeyError` at template render time. Also replaces two hardcoded side tuples with a manifest-driven lookup, so future markets with new outcome strings don't need backend code edits.

### Step 1.1 — Write failing test for `none` rendering

- [ ] **Add to `tests/test_web_app.py`**, after `test_event_detail_renders_default_market`:

```python
def test_event_detail_renders_next_goal_market_with_none_side(db_path: Path):
    """next_goal_ft has a 'none' side. Detail page must render its short label
    without KeyError. Seeds one priced next_goal_ft row at line=1.0."""
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    cur = conn.execute(
        "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, fetch_status) "
        "VALUES ('2026-05-21T10:02:00Z', 'E1', 'betpawa', 'UPCOMING', 'ok')",
    )
    snap_id = cur.lastrowid
    for side, odds, prob in [
        ("home", 1.85, 0.54), ("none", 8.50, 0.12), ("away", 3.50, 0.29),
    ]:
        conn.execute(
            "INSERT INTO prices (snapshot_id, event_id, ts_utc, bookmaker, "
            "market_id, line, side, odds, probability) "
            "VALUES (?, 'E1', '2026-05-21T10:02:00Z', 'betpawa', 'next_goal_ft', 1.0, ?, ?, ?)",
            (snap_id, side, odds, prob),
        )
    conn.close()
    app = create_app(db_path=db_path)
    client = TestClient(app)
    r = client.get("/events/E1?market=ng_1.0")
    assert r.status_code == 200
    # The "N" short label for the "none" outcome must appear in the table head.
    assert ">N<" in r.text
```

- [ ] **Run the test** — expected: FAIL with one of: `KeyError: 'none'` (template's hardcoded dict), or `HTTPException 400: unknown market 'ng_1.0'` (slug not registered yet).

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_app.py::test_event_detail_renders_next_goal_market_with_none_side -v`

### Step 1.2 — Update `_SIDE_LABEL` / `_SIDE_SHORT`

- [ ] **Edit `src/odds_scraper/web/app.py`** — find the existing `_SIDE_LABEL` and `_SIDE_SHORT` dicts and add the `"none"` entry. Final state:

```python
_SIDE_LABEL = {
    "home": "Home", "draw": "Draw", "away": "Away",
    "over": "Over", "under": "Under",
    "none": "None",
}

_SIDE_SHORT = {
    "home": "H", "draw": "D", "away": "A",
    "over": "O", "under": "U",
    "none": "N",
}
```

### Step 1.3 — Replace `_SIDES_1X2` / `_SIDES_OU` constants with manifest lookup

- [ ] **In `src/odds_scraper/web/app.py`**, find this block at module level:

```python
# Outcome ordering per market shape
_SIDES_1X2 = ("home", "draw", "away")
_SIDES_OU = ("over", "under")
```

Replace it with:

```python
# Outcome ordering comes from MARKET_MANIFEST — sub-project 3 sourced this
# from the manifest so adding markets with new sides only requires updating
# _SIDE_LABEL / _SIDE_SHORT, not the web layer's wiring.
from odds_scraper.models import MARKET_MANIFEST, MarketSpec

_spec_by_id: dict[str, MarketSpec] = {s.canonical_id: s for s in MARKET_MANIFEST}


def _sides_for(market_id: str) -> tuple[str, ...]:
    return _spec_by_id[market_id].sides
```

(The `from odds_scraper.models import …` lands at the top of the file with the other imports; move it there during the edit. The module-level `_spec_by_id` and `_sides_for` definitions live near where `_SIDES_1X2` used to be.)

- [ ] **In `_build_event_view`**, find the loop:

```python
for market_id, group_label, market_short in _COLLAPSED_ORDER:
    rows_for_group = []
    for side in _SIDES_1X2:
```

Change `for side in _SIDES_1X2:` to `for side in _sides_for(market_id):`.

- [ ] **In the same function**, find the OU section:

```python
for line in _OU_LINES:
    rows_for_group = []
    for side in _SIDES_OU:
```

Change `for side in _SIDES_OU:` to `for side in _sides_for("over_under_ft"):` (Task 3 rewrites this whole block; the minimal change for Task 1 is just to remove the `_SIDES_OU` constant reference).

- [ ] **In `_build_event_detail`**, find:

```python
sides = _SIDES_OU if market_id == "over_under_ft" else _SIDES_1X2
```

Replace with:

```python
sides = _sides_for(market_id)
```

### Step 1.4 — Update detail template to use `event.sides` short labels

- [ ] **Edit `src/odds_scraper/web/templates/event_detail.html`** — find the inline side abbreviation dict in the `<th class="side-h">` block:

```jinja
<th data-bookmaker="{{ bm }}" class="side-h">
  {{ {"home":"H","draw":"D","away":"A","over":"O","under":"U"}[side] }}
</th>
```

This hardcoded dict will `KeyError` on `"none"`. We need a side-short lookup that the backend already knows about. Two options — the simpler is to pass a side→short mapping in the template context. Actual fix: add a Jinja filter via the template's context (passed from `_build_event_detail`). Concretely, in `_build_event_detail` add to the return:

`EventDetail(…, side_short_map={s: _SIDE_SHORT[s] for s in sides}, …)` — but that changes the dataclass. Simpler: build the short labels in Python and ship them as a tuple aligned with `event.sides`. Concrete plan:

In `EventDetail` (dataclass at the top of `app.py`), add a new field after `sides`:

```python
sides_short: tuple[str, ...]
```

In `_build_event_detail`, after `sides = _sides_for(market_id)`, build:

```python
sides_short = tuple(_SIDE_SHORT[s] for s in sides)
```

and pass `sides_short=sides_short` in the `EventDetail(...)` constructor call.

Then in `event_detail.html`, replace the inline dict with index-aligned access:

```jinja
<th data-bookmaker="{{ bm }}" class="side-h">
  {{ event.sides_short[loop.index0] }}
</th>
```

(The surrounding `{% for side in event.sides %}` provides `loop.index0`.)

### Step 1.5 — Register the `ng_1.0` slug so the test can reach the template

The failing test hits `/events/E1?market=ng_1.0`. The slug-to-market mapping currently doesn't include `next_goal_ft`. Task 7 builds out the full picker for all new markets; Task 1's smallest viable change is to also register the new slugs alongside the existing ones — but that drags pill-rendering changes in early.

Cleaner path: have Task 1 register all parameterized-market slugs in `_PICKER_BY_SLUG` (without yet making pills render them) so the test can navigate, then Task 7 reorganizes the pill UI.

- [ ] **In `src/odds_scraper/web/app.py`**, find `_build_market_picker()`. Today it only knows about the 1x2 family and `over_under_ft`. Extend it to iterate `_EXPANDER_MARKETS` (which Task 3 defines as a constant — for now, inline the list here):

```python
# Display order for parameterized markets in the detail-page pill bar AND
# the home-page card expander. Sub-project 3's single source of truth.
_EXPANDER_MARKETS: tuple[tuple[str, str], ...] = (
    ("next_goal_ft",       "Next Goal"),
    ("over_under_ft",      "Match O/U"),
    ("home_over_under_ft", "Home O/U"),
    ("away_over_under_ft", "Away O/U"),
)

def _build_market_picker() -> list[tuple[str, Optional[float], str, str]]:
    picker: list[tuple[str, Optional[float], str, str]] = []
    for mid in queries.COLLAPSED_MARKETS:
        label = _MARKET_LABELS[mid][0]
        picker.append((mid, None, label, mid))
    for market_id, label_prefix in _EXPANDER_MARKETS:
        spec = _spec_by_id[market_id]
        prefix = spec.column_prefix
        for line in spec.lines or ():
            slug = f"{prefix}_{line}"
            picker.append((market_id, line, f"{label_prefix} {line}", slug))
    return picker
```

The old `_OU_LINES` constant is no longer used by `_build_market_picker` but is still used by the `_build_event_view` OU loop. Task 3 retires it entirely. **Leave `_OU_LINES` in the file for now.**

### Step 1.6 — Run the test, see it pass

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_app.py::test_event_detail_renders_next_goal_market_with_none_side -v`

Expected: PASS.

### Step 1.7 — Run full web tests

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_app.py tests/test_web_queries.py -v`

Expected: all pass. (The existing `test_event_detail_pills_include_ou_lines` should still pass — the pills list still contains the OU slugs; the new markets are now also registered but the test only asserts presence of OU slugs.)

### Step 1.8 — Commit

```bash
git add src/odds_scraper/web/app.py src/odds_scraper/web/templates/event_detail.html tests/test_web_app.py
git commit -m "$(cat <<'EOF'
feat(web): add 'none' side support + manifest-driven sides lookup

next_goal_ft has a 'none' outcome (no-more-goals) that the web UX
couldn't render — _SIDE_LABEL / _SIDE_SHORT were missing it and the
event_detail template had a hardcoded {home:H, draw:D, ...} dict that
would KeyError. Both fixed; the template now reads side_short from the
backend payload (one place to add new sides).

Also replaces the hardcoded _SIDES_1X2 / _SIDES_OU constants with a
_sides_for(market_id) helper that reads MarketSpec.sides — the same
single-source-of-truth pattern the rest of the codebase uses.

_PICKER_BY_SLUG is extended to register all parameterized-market slugs
(ng_*, ou_home_*, ou_away_*) so URLs resolve. The full pill UI lands
in Task 7.
EOF
)"
```

---

## Task 2: `get_available_lines` query

**Files:**
- Modify: `src/odds_scraper/web/queries.py`
- Test: `tests/test_web_queries.py`

A new query that returns the distinct `(market_id, line)` tuples that have priced rows for one event. Task 7's two-stage detail pills will use it to render only the lines that have data.

### Step 2.1 — Write failing test for happy path

- [ ] **Add to `tests/test_web_queries.py`**:

```python
def test_get_available_lines_returns_only_lines_with_data(db: Path):
    from odds_scraper.web.queries import get_available_lines
    conn = open_ro_conn(db)
    avail = get_available_lines(conn, "E_LIVE")
    conn.close()
    # The shared fixture writes over_under_ft at line=2.5 only, plus
    # 1x2_ft / 1x2_1up_ft / 1x2_2up_ft at the sentinel line=0.0.
    # Sentinel-zero rows must be filtered out.
    assert avail == {"over_under_ft": [2.5]}
```

- [ ] **Run test — expected FAIL** with `ImportError: cannot import name 'get_available_lines'`.

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_queries.py::test_get_available_lines_returns_only_lines_with_data -v`

### Step 2.2 — Write failing test for the multi-line, multi-market case

- [ ] **Add to `tests/test_web_queries.py`** (new fixture-extending test that inserts more rows ad-hoc):

```python
def test_get_available_lines_multi_market_multi_line(db: Path):
    """Verify ordering and grouping when several lines and markets coexist."""
    from odds_scraper.web.queries import get_available_lines
    conn = sqlite3.connect(str(db), isolation_level=None)
    # Reuse the E_LIVE snapshot_id from the fixture. We need its id.
    snap_id = conn.execute(
        "SELECT id FROM snapshots WHERE event_id='E_LIVE' LIMIT 1"
    ).fetchone()[0]
    extra_prices = [
        ("over_under_ft",      3.5, "over",  2.50, None),
        ("next_goal_ft",       1.0, "home",  1.85, None),
        ("next_goal_ft",       2.0, "away",  3.90, None),
        ("home_over_under_ft", 0.5, "over",  1.30, None),
        ("away_over_under_ft", 1.5, "under", 1.55, None),
    ]
    for market_id, line, side, odds, prob in extra_prices:
        conn.execute(
            "INSERT INTO prices (snapshot_id, event_id, ts_utc, bookmaker, "
            "market_id, line, side, odds, probability) "
            "VALUES (?, 'E_LIVE', '2026-05-21T11:00:00Z', 'betpawa', "
            "?, ?, ?, ?, ?)",
            (snap_id, market_id, line, side, odds, prob),
        )
    conn.close()
    conn = open_ro_conn(db)
    avail = get_available_lines(conn, "E_LIVE")
    conn.close()
    assert avail == {
        "over_under_ft":      [2.5, 3.5],
        "next_goal_ft":       [1.0, 2.0],
        "home_over_under_ft": [0.5],
        "away_over_under_ft": [1.5],
    }
```

- [ ] **Run both tests — expected both FAIL** with import error.

### Step 2.3 — Implement `get_available_lines`

- [ ] **Edit `src/odds_scraper/web/queries.py`**, append at end of file:

```python
def get_available_lines(
    conn: sqlite3.Connection, event_id: str,
) -> dict[str, list[float]]:
    """Distinct (market_id, line) pairs that have priced rows for one event.

    Skips the 0.0 sentinel line that SqliteWriter stores for non-parameterized
    markets (1x2 family) so only true parameterized lines come back. Real
    parameterized lines like 0.5 pass through because 0.5 > 0.

    Used by the detail page's two-stage market picker to render only the
    lines that this event actually has data for.
    """
    sql = """
        SELECT DISTINCT market_id, line
        FROM prices
        WHERE event_id = ?
          AND line > 0
        ORDER BY market_id, line
    """
    rows = conn.execute(sql, (event_id,)).fetchall()
    out: dict[str, list[float]] = {}
    for r in rows:
        out.setdefault(r["market_id"], []).append(r["line"])
    return out
```

### Step 2.4 — Run the two tests

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_queries.py::test_get_available_lines_returns_only_lines_with_data tests/test_web_queries.py::test_get_available_lines_multi_market_multi_line -v`

Expected: both PASS.

### Step 2.5 — Run full web-queries suite

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_queries.py -v`
Expected: all pass.

### Step 2.6 — Commit

```bash
git add src/odds_scraper/web/queries.py tests/test_web_queries.py
git commit -m "$(cat <<'EOF'
feat(web/queries): add get_available_lines for two-stage detail pills

Returns a {market_id: [lines]} dict containing only the (market_id, line)
tuples that have priced rows for one event. The 0.0 sentinel that the
writer stores for non-parameterized markets (1x2 family) is filtered
via line > 0 — valid 0.5+ lines pass through.

The detail-page market picker (Task 7) consumes this to render line
pills only for lines with data; families with no available lines get
their family pill greyed out.
EOF
)"
```

---

## Task 3: Card expander — new market groups in fixed order

**Files:**
- Modify: `src/odds_scraper/web/app.py`
- Modify: `src/odds_scraper/web/templates/_event_card.html`
- Test: `tests/test_web_app.py`

Replace the OU-only card-expander loop with one that iterates `_EXPANDER_MARKETS` for all four parameterized markets in display order. Only emit a group when at least one outcome is priced.

### Step 3.1 — Write failing tests for new market groups

- [ ] **Add to `tests/test_web_app.py`**:

```python
def test_events_card_shows_next_goal_group_when_priced(db_path: Path):
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    snap_id = conn.execute(
        "SELECT id FROM snapshots WHERE event_id='E1' LIMIT 1"
    ).fetchone()[0]
    for side, odds, prob in [
        ("home", 1.85, 0.54), ("none", 8.5, 0.12), ("away", 3.5, 0.29),
    ]:
        conn.execute(
            "INSERT INTO prices (snapshot_id, event_id, ts_utc, bookmaker, "
            "market_id, line, side, odds, probability) "
            "VALUES (?, 'E1', '2026-05-21T10:00:00Z', 'betpawa', 'next_goal_ft', 1.0, ?, ?, ?)",
            (snap_id, side, odds, prob),
        )
    conn.close()
    client = TestClient(create_app(db_path=db_path))
    r = client.get("/events?status=upcoming")
    assert "Next Goal 1" in r.text
    # Sentinel cells for the three sides are present in the row labels.
    assert "NG 1 · H" in r.text or "NG 1.0 · H" in r.text
    assert "NG 1 · N" in r.text or "NG 1.0 · N" in r.text


def test_events_card_shows_per_team_ou_groups_when_priced(db_path: Path):
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    snap_id = conn.execute(
        "SELECT id FROM snapshots WHERE event_id='E1' LIMIT 1"
    ).fetchone()[0]
    rows = [
        ("home_over_under_ft", 0.5, "over",  1.30, 0.74),
        ("home_over_under_ft", 0.5, "under", 3.50, 0.26),
        ("away_over_under_ft", 1.5, "over",  2.50, 0.40),
        ("away_over_under_ft", 1.5, "under", 1.55, 0.60),
    ]
    for market_id, line, side, odds, prob in rows:
        conn.execute(
            "INSERT INTO prices (snapshot_id, event_id, ts_utc, bookmaker, "
            "market_id, line, side, odds, probability) "
            "VALUES (?, 'E1', '2026-05-21T10:00:00Z', 'betpawa', ?, ?, ?, ?, ?)",
            (snap_id, market_id, line, side, odds, prob),
        )
    conn.close()
    client = TestClient(create_app(db_path=db_path))
    r = client.get("/events?status=upcoming")
    assert "Home O/U 0.5" in r.text
    assert "Away O/U 1.5" in r.text


def test_events_card_omits_market_group_with_no_data(db_path: Path):
    """No next_goal_ft / home_over_under_ft / away_over_under_ft data → those
    group labels are absent. (over_under_ft is also absent for this minimal
    fixture; only 1x2 family appears.)"""
    client = TestClient(create_app(db_path=db_path))
    r = client.get("/events?status=upcoming")
    for label in ("Next Goal", "Match O/U", "Home O/U", "Away O/U"):
        assert label not in r.text, f"expected {label!r} absent in plain fixture"


def test_events_card_expander_groups_in_fixed_order(db_path: Path):
    """When all four parameterized markets have at least one priced line, the
    expander groups must appear in order: next_goal → over_under → home_OU → away_OU."""
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    snap_id = conn.execute(
        "SELECT id FROM snapshots WHERE event_id='E1' LIMIT 1"
    ).fetchone()[0]
    rows = [
        ("over_under_ft",      2.5, "over",  1.70, 0.58),
        ("next_goal_ft",       1.0, "home",  1.85, 0.54),
        ("home_over_under_ft", 0.5, "over",  1.30, 0.74),
        ("away_over_under_ft", 0.5, "over",  1.40, 0.69),
    ]
    for market_id, line, side, odds, prob in rows:
        conn.execute(
            "INSERT INTO prices (snapshot_id, event_id, ts_utc, bookmaker, "
            "market_id, line, side, odds, probability) "
            "VALUES (?, 'E1', '2026-05-21T10:00:00Z', 'betpawa', ?, ?, ?, ?, ?)",
            (snap_id, market_id, line, side, odds, prob),
        )
    conn.close()
    client = TestClient(create_app(db_path=db_path))
    body = client.get("/events?status=upcoming").text
    i_ng  = body.find("Next Goal")
    i_ou  = body.find("Match O/U")
    i_hou = body.find("Home O/U")
    i_aou = body.find("Away O/U")
    assert -1 < i_ng < i_ou < i_hou < i_aou


def test_events_card_expander_button_label_updates(db_path: Path):
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    snap_id = conn.execute(
        "SELECT id FROM snapshots WHERE event_id='E1' LIMIT 1"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO prices (snapshot_id, event_id, ts_utc, bookmaker, "
        "market_id, line, side, odds, probability) "
        "VALUES (?, 'E1', '2026-05-21T10:00:00Z', 'betpawa', 'next_goal_ft', 1.0, 'home', 1.85, 0.54)",
        (snap_id,),
    )
    conn.close()
    client = TestClient(create_app(db_path=db_path))
    body = client.get("/events?status=upcoming").text
    assert "more odds" in body  # new label
    assert "Show 1 Over/Under" not in body  # old label retired
```

- [ ] **Run tests — expected FAIL** for the first four (groups missing); the fifth fails on the old "Show N Over/Under" label still being present.

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_app.py -k "card_shows_next_goal or card_shows_per_team or card_omits_market or expander_groups_in_fixed_order or expander_button_label" -v`

### Step 3.2 — Replace OU-only loop with `_EXPANDER_MARKETS` loop

- [ ] **Edit `src/odds_scraper/web/app.py`** — find the `_OU_LINES` constant and the OU section in `_build_event_view`:

```python
_OU_LINES = (1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5, 8.5, 9.5)
```

`_EXPANDER_MARKETS` was already added in Task 1. The `_OU_LINES` constant is no longer needed — **delete it**.

In `_build_market_picker`, the loop already uses `_EXPANDER_MARKETS` via Task 1. Nothing more there.

In `_build_event_view`, find the OU section:

```python
# OU lines as extra (hidden-by-default) groups. Only emit groups
# that actually have at least one priced outcome.
for line in _OU_LINES:
    rows_for_group = []
    for side in _sides_for("over_under_ft"):
        prices = bucket.get(("over_under_ft", line, side), {})
        rows_for_group.append(OutcomeRow(
            market_label=f"OU {line}",
            side_label=_SIDE_LABEL[side],
            side_short=_SIDE_SHORT[side],
            prices=prices,
        ))
    if any(r.prices for r in rows_for_group):
        groups.append(MarketGroup(
            label=f"Over/Under {line}",
            rows=rows_for_group,
            is_extra=True,
        ))
```

Replace the whole block with:

```python
# Parameterized markets as extra (hidden-by-default) groups, in the
# display order set by _EXPANDER_MARKETS. Only emit a group when at
# least one outcome is priced for that (market, line) pair.
_SHORT_PREFIX = {
    "next_goal_ft":       "NG",
    "over_under_ft":      "OU",
    "home_over_under_ft": "H-OU",
    "away_over_under_ft": "A-OU",
}
for market_id, label_prefix in _EXPANDER_MARKETS:
    spec = _spec_by_id[market_id]
    for line in spec.lines or ():
        rows_for_group = []
        for side in _sides_for(market_id):
            prices = bucket.get((market_id, line, side), {})
            rows_for_group.append(OutcomeRow(
                market_label=f"{_SHORT_PREFIX[market_id]} {line}",
                side_label=_SIDE_LABEL[side],
                side_short=_SIDE_SHORT[side],
                prices=prices,
            ))
        if any(r.prices for r in rows_for_group):
            groups.append(MarketGroup(
                label=f"{label_prefix} {line}",
                rows=rows_for_group,
                is_extra=True,
            ))
```

Move `_SHORT_PREFIX` to module level (right after `_EXPANDER_MARKETS`); inlining was for readability only.

### Step 3.3 — Update expand-toggle copy in card template

- [ ] **Edit `src/odds_scraper/web/templates/_event_card.html`** — find the `<button class="expand-toggle">` block:

```jinja
<button class="expand-toggle" type="button"
        data-collapsed-label="▼ Show {{ ns.extra_count }} Over/Under {% if ns.extra_count == 1 %}line{% else %}lines{% endif %}"
        data-expanded-label="▲ Hide Over/Under">
  ▼ Show {{ ns.extra_count }} Over/Under {% if ns.extra_count == 1 %}line{% else %}lines{% endif %}
</button>
```

Replace with:

```jinja
<button class="expand-toggle" type="button"
        data-collapsed-label="▼ Show {{ ns.extra_count }} more {% if ns.extra_count == 1 %}market{% else %}markets{% endif %}"
        data-expanded-label="▲ Hide extra markets">
  ▼ Show {{ ns.extra_count }} more {% if ns.extra_count == 1 %}market{% else %}markets{% endif %}
</button>
```

### Step 3.4 — Run the Task 3 tests, see them pass

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_app.py -k "card_shows_next_goal or card_shows_per_team or card_omits_market or expander_groups_in_fixed_order or expander_button_label" -v`

Expected: all pass.

### Step 3.5 — Run full web suite

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_app.py tests/test_web_queries.py -v`
Expected: all pass.

If `test_events_card_skips_ou_groups_when_no_data` (a pre-existing test) is in the suite and references `"Over/Under"` literal in a way that's affected by the rename, update its assertion to use `"more market"` or whichever the new label is. (Inspect that test's body before changing it.)

### Step 3.6 — Commit

```bash
git add src/odds_scraper/web/app.py src/odds_scraper/web/templates/_event_card.html tests/test_web_app.py
git commit -m "$(cat <<'EOF'
feat(web/card): show next_goal + per-team Over/Under in expander

Card-expander loop is now driven by _EXPANDER_MARKETS (next_goal_ft →
over_under_ft → home_over_under_ft → away_over_under_ft). Each
parameterized market emits one group per line that has any priced
outcome; empty groups are skipped (existing "data-driven emission"
pattern, generalized).

Expand-toggle copy updated from "Show N Over/Under lines" to
"Show N more markets" since the expander now covers four market families.
EOF
)"
```

---

## Task 4: `get_event_meta` extension + detail-page subtitle

**Files:**
- Modify: `src/odds_scraper/web/queries.py`
- Modify: `src/odds_scraper/web/app.py`
- Modify: `src/odds_scraper/web/templates/event_detail.html`
- Test: `tests/test_web_app.py`, `tests/test_web_queries.py`

The `events` table already has `country_name` / `league_name` columns (sub-project 1). `get_event_meta` doesn't surface them today.

### Step 4.1 — Write failing test for query

- [ ] **Add to `tests/test_web_queries.py`**:

```python
def test_get_event_meta_returns_country_and_league(db: Path):
    conn = sqlite3.connect(str(db), isolation_level=None)
    conn.execute(
        "UPDATE events SET country_id='242', country_name='Germany', "
        "league_id='12091', league_name='2nd Bundesliga' WHERE id='E_LIVE'"
    )
    conn.close()
    conn = open_ro_conn(db)
    row = get_event_meta(conn, "E_LIVE")
    conn.close()
    assert row is not None
    assert row["country_name"] == "Germany"
    assert row["league_name"] == "2nd Bundesliga"
```

- [ ] **Run — expected FAIL** with `KeyError` on `'country_name'`.

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_queries.py::test_get_event_meta_returns_country_and_league -v`

### Step 4.2 — Extend `get_event_meta` SELECT

- [ ] **Edit `src/odds_scraper/web/queries.py`** — in `get_event_meta`, find the SELECT clause:

```python
        SELECT
            e.id, e.home, e.away, e.kickoff_utc,
            s.status, s.match_minute, s.score_home, s.score_away,
            s.ts_utc AS latest_ts
```

Extend it to:

```python
        SELECT
            e.id, e.home, e.away, e.kickoff_utc,
            e.country_name, e.league_name,
            s.status, s.match_minute, s.score_home, s.score_away,
            s.ts_utc AS latest_ts
```

### Step 4.3 — Surface country/league in `EventDetail` and template

- [ ] **Edit `src/odds_scraper/web/app.py`** — extend the `EventDetail` dataclass:

```python
@dataclass
class EventDetail:
    id: str
    home: str
    away: str
    kickoff_utc: str
    status: str
    match_minute: Optional[int]
    score_home: Optional[int]
    score_away: Optional[int]
    country_name: str
    league_name: str
    # Currently selected market info
    market_label: str
    market_slug: str
    sides: tuple[str, ...]
    sides_short: tuple[str, ...]
    pills: list[tuple[str, str, bool]]
    history: list[HistoryRow]
```

In `_build_event_detail`, pass the two new fields:

```python
return EventDetail(
    id=ev_row["id"],
    home=ev_row["home"],
    away=ev_row["away"],
    kickoff_utc=ev_row["kickoff_utc"],
    status=ev_row["status"],
    match_minute=ev_row["match_minute"],
    score_home=ev_row["score_home"],
    score_away=ev_row["score_away"],
    country_name=ev_row["country_name"] or "",
    league_name=ev_row["league_name"] or "",
    market_label=market_label,
    market_slug=market_slug,
    sides=sides,
    sides_short=sides_short,
    pills=pills,
    history=history,
)
```

(`ev_row["country_name"]` may be `NULL` for legacy rows; the `or ""` collapse keeps the template logic simple.)

### Step 4.4 — Write failing test for detail-page subtitle

- [ ] **Add to `tests/test_web_app.py`**:

```python
def test_event_detail_subtitle_renders_country_and_league(db_path: Path):
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.execute(
        "UPDATE events SET country_id='242', country_name='Germany', "
        "league_id='12091', league_name='2nd Bundesliga' WHERE id='E1'"
    )
    conn.close()
    client = TestClient(create_app(db_path=db_path))
    body = client.get("/events/E1").text
    assert "Germany" in body
    assert "2nd Bundesliga" in body
    assert "Germany · 2nd Bundesliga" in body


def test_event_detail_subtitle_omits_when_both_empty(db_path: Path):
    """An event without country/league info should not render a stray separator."""
    client = TestClient(create_app(db_path=db_path))
    body = client.get("/events/E1").text
    # The fixture leaves country/league NULL. The middle-dot separator must not
    # appear in the subtitle position. (It may appear in score lines like
    # "score 1 · 0" — we anchor the search on the dedicated subtitle class.)
    assert 'class="event-subtitle"' not in body
```

- [ ] **Run — expected FAIL** because subtitle markup doesn't exist yet.

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_app.py::test_event_detail_subtitle_renders_country_and_league tests/test_web_app.py::test_event_detail_subtitle_omits_when_both_empty -v`

### Step 4.5 — Add subtitle to template

- [ ] **Edit `src/odds_scraper/web/templates/event_detail.html`** — find the team-name header:

```jinja
<div class="text-white font-semibold tracking-wider">
  <a href="/" class="text-gray-400 hover:text-gray-200">← EVENTS</a>
  <span class="ml-3">{{ event.home }} — {{ event.away }}</span>
</div>
```

After the closing `</div>` of the outer `<div class="flex justify-between border-b border-gray-900 pb-3 mb-3">` (line ~14), add immediately before `<div class="flex gap-2 items-center px-2 mb-3 text-xs text-gray-500">`:

```jinja
{% if event.country_name or event.league_name %}
  <div class="event-subtitle px-2 mb-2 text-xs text-gray-500">
    {% if event.country_name and event.league_name %}
      {{ event.country_name }} · {{ event.league_name }}
    {% elif event.country_name %}
      {{ event.country_name }}
    {% else %}
      {{ event.league_name }}
    {% endif %}
  </div>
{% endif %}
```

### Step 4.6 — Run the subtitle tests

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_app.py -k "subtitle" -v`
Expected: both PASS.

### Step 4.7 — Run full web suite

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_app.py tests/test_web_queries.py -v`
Expected: all pass.

### Step 4.8 — Commit

```bash
git add src/odds_scraper/web/queries.py src/odds_scraper/web/app.py src/odds_scraper/web/templates/event_detail.html tests/test_web_app.py tests/test_web_queries.py
git commit -m "$(cat <<'EOF'
feat(web/detail): show country · league subtitle under team names

get_event_meta now surfaces country_name and league_name (the columns
have been there since sub-project 1 but the query didn't SELECT them).
EventDetail carries both as strings (empty for NULL legacy rows) and
the template renders 'Country · League' beneath the back-link header.

Edge cases:
- Both empty → subtitle <div> is omitted entirely (no stray separator).
- One side populated → renders just that field without the dot.
EOF
)"
```

---

## Task 5: Country/league index query + filter kwargs on `get_events_by_status`

**Files:**
- Modify: `src/odds_scraper/web/queries.py`
- Test: `tests/test_web_queries.py`

Server-side data for the cascading dropdown and the filtered events list. Task 6 wires it into the route + UI.

### Step 5.1 — Write failing test for `get_country_league_index`

- [ ] **Add to `tests/test_web_queries.py`**:

```python
def test_get_country_league_index_groups_by_country(tmp_path: Path):
    from odds_scraper.web.queries import get_country_league_index
    db = tmp_path / "odds.db"
    conn = sqlite3.connect(str(db), isolation_level=None)
    init_schema(conn)
    rows = [
        ("E1", "Germany", "242", "Bundesliga",      "BL1"),
        ("E2", "Germany", "242", "2nd Bundesliga",  "BL2"),
        ("E3", "USA",     "USA1", "MLS",            "MLS1"),
    ]
    for eid, country_name, country_id, league_name, league_id in rows:
        conn.execute(
            "INSERT INTO events (id, home, away, kickoff_utc, country_id, "
            "country_name, league_id, league_name) "
            "VALUES (?, 'H', 'A', '2026-05-22T00:00:00Z', ?, ?, ?, ?)",
            (eid, country_id, country_name, league_id, league_name),
        )
    conn.close()
    conn = open_ro_conn(db)
    index = get_country_league_index(conn)
    conn.close()
    assert index == [
        {
            "country_id": "242", "country_name": "Germany",
            "leagues": [
                {"league_id": "BL2", "league_name": "2nd Bundesliga"},
                {"league_id": "BL1", "league_name": "Bundesliga"},
            ],
        },
        {
            "country_id": "USA1", "country_name": "USA",
            "leagues": [
                {"league_id": "MLS1", "league_name": "MLS"},
            ],
        },
    ]


def test_get_country_league_index_skips_empty_country_name(tmp_path: Path):
    from odds_scraper.web.queries import get_country_league_index
    db = tmp_path / "odds.db"
    conn = sqlite3.connect(str(db), isolation_level=None)
    init_schema(conn)
    conn.execute(
        "INSERT INTO events (id, home, away, kickoff_utc, country_name, league_name) "
        "VALUES ('E_OK',    'H', 'A', '2026-05-22T00:00:00Z', 'Spain', 'La Liga'),"
        "       ('E_NULL',  'H', 'A', '2026-05-22T00:00:00Z', NULL,    NULL),"
        "       ('E_EMPTY', 'H', 'A', '2026-05-22T00:00:00Z', '',      '')",
    )
    conn.close()
    conn = open_ro_conn(db)
    index = get_country_league_index(conn)
    conn.close()
    country_names = [c["country_name"] for c in index]
    assert country_names == ["Spain"]
```

- [ ] **Run — expected FAIL** with `ImportError`.

### Step 5.2 — Implement `get_country_league_index`

- [ ] **Edit `src/odds_scraper/web/queries.py`** — append at end of file:

```python
def get_country_league_index(
    conn: sqlite3.Connection,
) -> list[dict]:
    """Distinct country+league pairs across all events, grouped by country.

    Skips rows where country_name is NULL or empty. Country list is sorted
    by country_name; leagues within each country sorted by league_name.

    Used to populate the cascading Country → League filter on the home page.
    """
    sql = """
        SELECT DISTINCT country_id, country_name, league_id, league_name
        FROM events
        WHERE country_name IS NOT NULL AND country_name != ''
        ORDER BY country_name, league_name
    """
    out: list[dict] = []
    last_country: tuple[str, str] | None = None
    for r in conn.execute(sql).fetchall():
        key = (r["country_id"] or "", r["country_name"] or "")
        if last_country != key:
            out.append({
                "country_id": key[0],
                "country_name": key[1],
                "leagues": [],
            })
            last_country = key
        out[-1]["leagues"].append({
            "league_id":   r["league_id"]   or "",
            "league_name": r["league_name"] or "",
        })
    return out
```

### Step 5.3 — Run the two index tests

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_queries.py -k "country_league_index" -v`
Expected: both pass.

### Step 5.4 — Write failing tests for filter kwargs on `get_events_by_status`

- [ ] **Add to `tests/test_web_queries.py`** (after the existing `get_events_by_status` tests):

```python
def test_get_events_by_status_filters_by_country_id(tmp_path: Path):
    db = tmp_path / "odds.db"
    conn = sqlite3.connect(str(db), isolation_level=None)
    init_schema(conn)
    conn.executemany(
        "INSERT INTO events (id, home, away, kickoff_utc, country_id, country_name) "
        "VALUES (?, 'H', 'A', '2026-05-22T18:30:00Z', ?, ?)",
        [
            ("E_DE", "242", "Germany"),
            ("E_US", "USA1", "USA"),
        ],
    )
    for eid in ("E_DE", "E_US"):
        conn.execute(
            "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, fetch_status) "
            "VALUES ('2026-05-21T10:00:00Z', ?, 'betpawa', 'UPCOMING', 'ok')",
            (eid,),
        )
    conn.close()
    conn = open_ro_conn(db)
    rows = get_events_by_status(conn, "upcoming", country_id="242")
    conn.close()
    ids = [r["id"] for r in rows]
    assert ids == ["E_DE"]


def test_get_events_by_status_filters_by_league_id(tmp_path: Path):
    db = tmp_path / "odds.db"
    conn = sqlite3.connect(str(db), isolation_level=None)
    init_schema(conn)
    conn.executemany(
        "INSERT INTO events (id, home, away, kickoff_utc, league_id, league_name) "
        "VALUES (?, 'H', 'A', '2026-05-22T18:30:00Z', ?, ?)",
        [
            ("E_BL1", "BL1", "Bundesliga"),
            ("E_BL2", "BL2", "2nd Bundesliga"),
        ],
    )
    for eid in ("E_BL1", "E_BL2"):
        conn.execute(
            "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, fetch_status) "
            "VALUES ('2026-05-21T10:00:00Z', ?, 'betpawa', 'UPCOMING', 'ok')",
            (eid,),
        )
    conn.close()
    conn = open_ro_conn(db)
    rows = get_events_by_status(conn, "upcoming", league_id="BL2")
    conn.close()
    ids = [r["id"] for r in rows]
    assert ids == ["E_BL2"]


def test_get_events_by_status_no_filter_returns_all(tmp_path: Path):
    """Empty country_id / league_id are no-ops."""
    db = tmp_path / "odds.db"
    conn = sqlite3.connect(str(db), isolation_level=None)
    init_schema(conn)
    conn.executemany(
        "INSERT INTO events (id, home, away, kickoff_utc) "
        "VALUES (?, 'H', 'A', '2026-05-22T18:30:00Z')",
        [("E1",), ("E2",)],
    )
    for eid in ("E1", "E2"):
        conn.execute(
            "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, fetch_status) "
            "VALUES ('2026-05-21T10:00:00Z', ?, 'betpawa', 'UPCOMING', 'ok')",
            (eid,),
        )
    conn.close()
    conn = open_ro_conn(db)
    rows = get_events_by_status(conn, "upcoming", country_id="", league_id="")
    conn.close()
    ids = {r["id"] for r in rows}
    assert ids == {"E1", "E2"}
```

- [ ] **Run — expected FAIL** with `TypeError: unexpected keyword 'country_id'`.

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_queries.py -k "filters_by_country_id or filters_by_league_id or no_filter_returns_all" -v`

### Step 5.5 — Extend `get_events_by_status` signature

- [ ] **Edit `src/odds_scraper/web/queries.py`** — change the signature and WHERE-clause assembly. Find:

```python
def get_events_by_status(
    conn: sqlite3.Connection, status: Status,
) -> list[sqlite3.Row]:
```

Replace with:

```python
def get_events_by_status(
    conn: sqlite3.Connection, status: Status,
    *, country_id: str = "", league_id: str = "",
) -> list[sqlite3.Row]:
```

Find the SQL block:

```python
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
```

Replace with:

```python
    country_clause = "AND e.country_id = :country_id" if country_id else ""
    league_clause  = "AND e.league_id  = :league_id"  if league_id  else ""
    cutoff_clause  = "AND s.ts_utc >= :cutoff"        if cutoff     else ""
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
          {cutoff_clause}
          {country_clause}
          {league_clause}
        GROUP BY e.id
        {order_clause}
    """
    params: dict[str, str] = {"db_status": db_status}
    if cutoff:
        params["cutoff"] = cutoff
    if country_id:
        params["country_id"] = country_id
    if league_id:
        params["league_id"] = league_id
    return conn.execute(sql, params).fetchall()
```

### Step 5.6 — Run the filter tests

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_queries.py -k "filters_by_country_id or filters_by_league_id or no_filter_returns_all" -v`
Expected: all pass.

### Step 5.7 — Run full web-queries suite

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_queries.py -v`
Expected: all pass.

### Step 5.8 — Commit

```bash
git add src/odds_scraper/web/queries.py tests/test_web_queries.py
git commit -m "$(cat <<'EOF'
feat(web/queries): country/league index + filter kwargs

Two query additions for the home-page cascading filter:

- get_country_league_index(conn) returns countries with nested leagues
  for the dropdown payload. Skips rows with NULL/empty country_name.
- get_events_by_status gains country_id / league_id kwargs that splice
  AND clauses into the existing WHERE. Empty string = no filter.

Both use bound parameters; no string interpolation of user input.
EOF
)"
```

---

## Task 6: Filter row HTML + JS for cascading dropdowns

**Files:**
- Modify: `src/odds_scraper/web/app.py`
- Modify: `src/odds_scraper/web/templates/index.html`
- Modify: `src/odds_scraper/web/static/app.js`
- Test: `tests/test_web_app.py`

The `/` route fetches the country/league index and embeds it as JSON in the response. The `/events` route accepts `country` + `league` query params and passes them to the query. The HTML adds two `<select>` elements, and `app.js` wires up the cascading behavior.

### Step 6.1 — Write failing tests

- [ ] **Add to `tests/test_web_app.py`**:

```python
def test_index_embeds_country_league_index_json(db_path: Path):
    """The home page must include the index payload as JSON so the client
    can render the dropdowns without a second HTTP round-trip."""
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.execute(
        "UPDATE events SET country_id='242', country_name='Germany', "
        "league_id='BL2', league_name='2nd Bundesliga' WHERE id='E1'"
    )
    conn.close()
    client = TestClient(create_app(db_path=db_path))
    body = client.get("/").text
    assert 'id="country-league-index"' in body
    assert 'type="application/json"' in body
    assert "Germany" in body
    assert "2nd Bundesliga" in body


def test_events_fragment_filters_by_country(db_path: Path):
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.executemany(
        "INSERT INTO events (id, home, away, kickoff_utc, country_id, country_name) "
        "VALUES (?, 'H', 'A', '2026-05-22T18:30:00Z', ?, ?)",
        [("E_DE", "242", "Germany"), ("E_US", "USA1", "USA")],
    )
    for eid in ("E_DE", "E_US"):
        conn.execute(
            "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, fetch_status) "
            "VALUES ('2026-05-21T10:00:00Z', ?, 'betpawa', 'UPCOMING', 'ok')",
            (eid,),
        )
    conn.close()
    client = TestClient(create_app(db_path=db_path))
    r = client.get("/events?status=upcoming&country=242")
    assert r.status_code == 200
    assert 'href="/events/E_DE"' in r.text
    assert 'href="/events/E_US"' not in r.text


def test_events_fragment_filters_by_league(db_path: Path):
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.executemany(
        "INSERT INTO events (id, home, away, kickoff_utc, league_id, league_name) "
        "VALUES (?, 'H', 'A', '2026-05-22T18:30:00Z', ?, ?)",
        [("E_BL1", "BL1", "Bundesliga"), ("E_BL2", "BL2", "2nd Bundesliga")],
    )
    for eid in ("E_BL1", "E_BL2"):
        conn.execute(
            "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, fetch_status) "
            "VALUES ('2026-05-21T10:00:00Z', ?, 'betpawa', 'UPCOMING', 'ok')",
            (eid,),
        )
    conn.close()
    client = TestClient(create_app(db_path=db_path))
    r = client.get("/events?status=upcoming&league=BL2")
    assert 'href="/events/E_BL2"' in r.text
    assert 'href="/events/E_BL1"' not in r.text


def test_index_filter_row_has_country_and_league_selects(db_path: Path):
    client = TestClient(create_app(db_path=db_path))
    body = client.get("/").text
    assert 'id="country-select"' in body
    assert 'id="league-select"' in body
```

- [ ] **Run — expected FAIL** (no JSON tag, no country query param handling, no selects in template).

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_app.py -k "country_league_index or filters_by_country or filters_by_league or filter_row_has_country" -v`

### Step 6.2 — Update `/events` route signature

- [ ] **Edit `src/odds_scraper/web/app.py`** — find the events route:

```python
@app.get("/events", response_class=HTMLResponse)
async def events_fragment(
    request: Request,
    status: str = Query("live"),
):
    if status not in queries.VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"unknown status {status!r}")
    rows = queries.get_events_by_status(conn, status)  # type: ignore[arg-type]
```

Replace with:

```python
@app.get("/events", response_class=HTMLResponse)
async def events_fragment(
    request: Request,
    status: str = Query("live"),
    country: str = Query(""),
    league: str = Query(""),
):
    if status not in queries.VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"unknown status {status!r}")
    rows = queries.get_events_by_status(  # type: ignore[arg-type]
        conn, status, country_id=country, league_id=league,
    )
```

### Step 6.3 — Update `/` route to embed the country/league JSON

- [ ] **Edit `src/odds_scraper/web/app.py`** — find the index route:

```python
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {})
```

Replace with:

```python
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    country_league_index = queries.get_country_league_index(conn)
    return templates.TemplateResponse(
        request, "index.html",
        {"country_league_index": country_league_index},
    )
```

### Step 6.4 — Update `index.html` template

- [ ] **Edit `src/odds_scraper/web/templates/index.html`** — find the `<div class="filter-row">` block. Insert a new `filter-group` for Country/League **before** the existing Bookmakers group (so the filter row reads left to right: Country, League, Bookmakers, Kickoff, Search). Final block shape:

```jinja
<div class="filter-row">
  <div class="filter-group">
    <span class="filter-lbl">Country</span>
    <select id="country-select" class="filter-select">
      <option value="">All</option>
      {% for c in country_league_index %}
        <option value="{{ c.country_id }}">{{ c.country_name }}</option>
      {% endfor %}
    </select>
    <span class="filter-lbl">League</span>
    <select id="league-select" class="filter-select" disabled>
      <option value="">All</option>
    </select>
  </div>

  <div class="filter-group">
    <span class="filter-lbl">Bookmakers</span>
    {# ... existing chips ... #}
  </div>
  {# ... rest of the existing filter row ... #}
</div>
```

- [ ] **Inside the same `index.html`**, immediately before `<div id="events-list" …>`, add the JSON tag:

```jinja
<script type="application/json" id="country-league-index">
{
  "items": [
    {% for c in country_league_index %}
      {
        "country_id":   {{ c.country_id|tojson }},
        "country_name": {{ c.country_name|tojson }},
        "leagues": [
          {% for l in c.leagues %}
            {"league_id": {{ l.league_id|tojson }}, "league_name": {{ l.league_name|tojson }}}{% if not loop.last %},{% endif %}
          {% endfor %}
        ]
      }{% if not loop.last %},{% endif %}
    {% endfor %}
  ]
}
</script>
```

### Step 6.5 — Add cascading-filter logic to `app.js`

- [ ] **Edit `src/odds_scraper/web/static/app.js`** — append a new function after `initSearch`:

```javascript
// -----------------------------------------------------------------------------
// Country/League cascading dropdowns
// -----------------------------------------------------------------------------
function initCountryLeagueFilter() {
  const countrySel = document.getElementById('country-select');
  const leagueSel  = document.getElementById('league-select');
  if (!countrySel || !leagueSel) return;

  const dataTag = document.getElementById('country-league-index');
  let index = [];
  if (dataTag && dataTag.textContent) {
    try { index = JSON.parse(dataTag.textContent).items || []; }
    catch { index = []; }
  }

  const stored = LS.load('country_league_filter', {country_id: '', league_id: ''});

  function populateLeagues(country_id) {
    leagueSel.innerHTML = '<option value="">All</option>';
    if (!country_id) {
      leagueSel.disabled = true;
      return;
    }
    const country = index.find(c => c.country_id === country_id);
    if (!country) {
      leagueSel.disabled = true;
      return;
    }
    for (const league of country.leagues) {
      const opt = document.createElement('option');
      opt.value = league.league_id;
      opt.textContent = league.league_name;
      leagueSel.appendChild(opt);
    }
    leagueSel.disabled = country.leagues.length === 0;
  }

  function currentStatus() {
    const activeTab = document.querySelector('.tab[data-status].active');
    return (activeTab && activeTab.dataset.status) || 'upcoming';
  }

  function refresh() {
    const country_id = countrySel.value;
    const league_id  = leagueSel.value;
    LS.save('country_league_filter', {country_id, league_id});
    const params = new URLSearchParams({
      status: currentStatus(),
      country: country_id,
      league:  league_id,
    });
    window.htmx.ajax('GET', `/events?${params.toString()}`,
                     {target: '#events-list', swap: 'outerHTML'});
  }

  countrySel.value = stored.country_id || '';
  populateLeagues(stored.country_id || '');
  if (stored.league_id && leagueSel.querySelector(`option[value="${stored.league_id}"]`)) {
    leagueSel.value = stored.league_id;
  }

  countrySel.addEventListener('change', () => {
    populateLeagues(countrySel.value);
    refresh();
  });
  leagueSel.addEventListener('change', refresh);

  // On initial page load, the events-list fragment fires its hx-get on its
  // own (hx-trigger="load"). We only need to fire when the user changes the
  // filter — but on first load we want the stored filter to be honoured
  // immediately, so if either is non-empty, kick off a refresh after the
  // initial fragment loads.
  if (stored.country_id || stored.league_id) {
    // Defer until after htmx wires its load trigger so we don't race.
    setTimeout(refresh, 0);
  }
}
```

- [ ] **In the `DOMContentLoaded` handler at the bottom**, add `initCountryLeagueFilter()` to the call list — after `initSearch()`:

```javascript
document.addEventListener('DOMContentLoaded', () => {
  initBookmakerChips();
  initTabs();
  initKickoffFilter();
  initSearch();
  initCountryLeagueFilter();
  initExpandToggles();
  initEventDelegates();
  applyAllCardState();
});
```

- [ ] **Also update `initTabs`** so tab switches carry the active country/league filter forward. Find:

```javascript
window.htmx.ajax('GET', `/events?status=${t.dataset.status}`,
                 {target: '#events-list', swap: 'outerHTML'});
```

Replace with:

```javascript
const stored = LS.load('country_league_filter', {country_id: '', league_id: ''});
const params = new URLSearchParams({
  status:  t.dataset.status,
  country: stored.country_id || '',
  league:  stored.league_id  || '',
});
window.htmx.ajax('GET', `/events?${params.toString()}`,
                 {target: '#events-list', swap: 'outerHTML'});
```

### Step 6.6 — Add minimal CSS for the new selects

- [ ] **Edit `src/odds_scraper/web/static/app.css`** — append:

```css
.filter-select {
  background: #0f1115;
  color: #d1d5db;
  border: 1px solid #1f2330;
  border-radius: 4px;
  padding: 2px 6px;
  font-size: 11px;
  font-family: inherit;
}
.filter-select:disabled {
  opacity: 0.4;
}
```

(If `app.css` has a different style for `.chip` / inputs already, follow that convention instead — but the variables above match the existing dark-mode palette.)

### Step 6.7 — Run all Task 6 tests

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_app.py -k "country_league_index or filters_by_country or filters_by_league or filter_row_has_country" -v`
Expected: all PASS.

### Step 6.8 — Run full web suite

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_app.py tests/test_web_queries.py -v`
Expected: all pass.

### Step 6.9 — Commit

```bash
git add src/odds_scraper/web/app.py src/odds_scraper/web/templates/index.html src/odds_scraper/web/static/app.js src/odds_scraper/web/static/app.css tests/test_web_app.py
git commit -m "$(cat <<'EOF'
feat(web): cascading Country → League filter in home-page filter row

Two new <select> elements at the start of the filter row. The index
payload is embedded as JSON in the index.html response (no extra HTTP
round-trip) and the client populates the country select on boot. When
the user changes Country, League re-populates to that country's leagues
and resets to "All". Both selections persist in localStorage and ride
along with Tab switches.

Server-side: /events route accepts ?country=... &league=... and
forwards them to get_events_by_status as country_id / league_id kwargs.
Empty string = no filter. Existing bookmarks and HTMX polling URLs
without these params keep working.
EOF
)"
```

---

## Task 7: Two-stage detail-page pills (family → line)

**Files:**
- Modify: `src/odds_scraper/web/app.py`
- Modify: `src/odds_scraper/web/templates/event_detail.html`
- Modify: `src/odds_scraper/web/static/app.css` (optional — for disabled-state styling)
- Test: `tests/test_web_app.py`

The detail page splits its market pill bar into two rows: family chips (always visible) and line chips (visible only for the active parameterized family, and only for lines that have data for the event).

### Step 7.1 — Add new `EventDetail` fields for the family-stage UI

- [ ] **Edit `src/odds_scraper/web/app.py`** — extend the `EventDetail` dataclass with two new fields after `pills`:

```python
@dataclass
class EventDetail:
    id: str
    home: str
    away: str
    kickoff_utc: str
    status: str
    match_minute: Optional[int]
    score_home: Optional[int]
    score_away: Optional[int]
    country_name: str
    league_name: str
    market_label: str
    market_slug: str
    sides: tuple[str, ...]
    sides_short: tuple[str, ...]
    pills: list[tuple[str, str, bool]]                       # legacy flat pills — kept for back-compat
    family_pills: list[tuple[str, str, bool, bool]]          # (family_id, label, active, disabled)
    line_pills: list[tuple[str, str, bool]]                  # (slug, label, active)
    history: list[HistoryRow]
```

(`family_pills` and `line_pills` are the new structured output. `pills` is left in place for one task cycle so any external consumer doesn't break; Task 8 retires it after smoke.)

### Step 7.2 — Write failing tests

- [ ] **Add to `tests/test_web_app.py`**:

```python
def test_event_detail_family_pills_includes_new_markets(db_path: Path):
    """Family row exists with chips for all families (1x2 trio + 4 parameterized)."""
    client = TestClient(create_app(db_path=db_path))
    body = client.get("/events/E1").text
    for label in (
        "1x2 — Full Time", "1x2 — 1 Up", "1x2 — 2 Up",
        "Next Goal", "Match O/U", "Home O/U", "Away O/U",
    ):
        assert label in body


def test_event_detail_disables_family_pill_when_no_lines_available(db_path: Path):
    """Fixture has no next_goal / over_under / home_OU / away_OU prices.
    Their family chips must render with a disabled marker."""
    client = TestClient(create_app(db_path=db_path))
    body = client.get("/events/E1").text
    # The four parameterized families with no data must be marked disabled.
    # Implementation uses class="family-pill disabled" or similar.
    assert "family-pill disabled" in body or "disabled\">Next Goal" in body


def test_event_detail_line_pills_filtered_to_available_lines(db_path: Path):
    """Insert over_under_ft prices for lines 2.5 and 3.5 only.
    Click on Match O/U family → only those two lines appear as pills."""
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    snap_id = conn.execute(
        "SELECT id FROM snapshots WHERE event_id='E1' LIMIT 1"
    ).fetchone()[0]
    for line in (2.5, 3.5):
        for side, odds in [("over", 1.7), ("under", 2.1)]:
            conn.execute(
                "INSERT INTO prices (snapshot_id, event_id, ts_utc, bookmaker, "
                "market_id, line, side, odds, probability) "
                "VALUES (?, 'E1', '2026-05-21T10:00:00Z', 'betpawa', "
                "'over_under_ft', ?, ?, ?, NULL)",
                (snap_id, line, side, odds),
            )
    conn.close()
    client = TestClient(create_app(db_path=db_path))
    body = client.get("/events/E1?market=ou_2.5").text
    # Active family = Match O/U → line chips visible:
    assert ">2.5<" in body
    assert ">3.5<" in body
    # Lines without data must NOT appear as pills:
    for missing in ("4.5", "5.5", "6.5", "7.5", "8.5", "9.5"):
        assert f"line=ou_{missing}" not in body
        # Also assert the pill label itself isn't in line-pill markup. The
        # safest anchor is the slug substring in the href:
        assert f"?market=ou_{missing}" not in body


def test_event_detail_active_line_pill_is_marked(db_path: Path):
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    snap_id = conn.execute(
        "SELECT id FROM snapshots WHERE event_id='E1' LIMIT 1"
    ).fetchone()[0]
    for line in (2.5, 3.5):
        for side, odds in [("over", 1.7), ("under", 2.1)]:
            conn.execute(
                "INSERT INTO prices (snapshot_id, event_id, ts_utc, bookmaker, "
                "market_id, line, side, odds, probability) "
                "VALUES (?, 'E1', '2026-05-21T10:00:00Z', 'betpawa', "
                "'over_under_ft', ?, ?, ?, NULL)",
                (snap_id, line, side, odds),
            )
    conn.close()
    client = TestClient(create_app(db_path=db_path))
    body = client.get("/events/E1?market=ou_3.5").text
    # Line-pill for 3.5 has the active class.
    assert 'class="line-pill active"' in body
    # Line-pill for 2.5 does NOT.
    import re
    m = re.search(r'href="/events/E1\?market=ou_2\.5"[^>]*class="line-pill([^"]*)"', body)
    assert m is not None
    assert "active" not in m.group(1)
```

- [ ] Also **update the existing test `test_event_detail_pills_include_ou_lines`** to assert against the new structure. Find it; replace its body with:

```python
def test_event_detail_pills_include_ou_lines(db_path: Path):
    """Selecting Match O/U with lines 2.5 + 3.5 in the DB exposes both as
    line pills (two-stage family + line UI)."""
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    snap_id = conn.execute(
        "SELECT id FROM snapshots WHERE event_id='E1' LIMIT 1"
    ).fetchone()[0]
    for line in (2.5, 3.5):
        for side, odds in [("over", 1.7), ("under", 2.1)]:
            conn.execute(
                "INSERT INTO prices (snapshot_id, event_id, ts_utc, bookmaker, "
                "market_id, line, side, odds, probability) "
                "VALUES (?, 'E1', '2026-05-21T10:00:00Z', 'betpawa', "
                "'over_under_ft', ?, ?, ?, NULL)",
                (snap_id, line, side, odds),
            )
    conn.close()
    client = TestClient(create_app(db_path=db_path))
    body = client.get("/events/E1?market=ou_2.5").text
    assert "ou_2.5" in body
    assert "ou_3.5" in body
```

- [ ] **Run — expected FAIL** for the new tests (markup doesn't exist) and possibly the updated old test.

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_app.py -k "family_pills_includes_new or disables_family_pill_when_no_lines or line_pills_filtered_to_available or active_line_pill_is_marked or pills_include_ou_lines" -v`

### Step 7.3 — Build family + line pill data in `_build_event_detail`

- [ ] **Edit `src/odds_scraper/web/app.py`**. Add a new module-level constant for the 1x2 family pill entries (canonical_id list of single-pill families):

```python
# Single-pill families on the detail page family-chip row. Parameterized
# families come from _EXPANDER_MARKETS.
_FAMILY_PILLS_1X2 = (
    ("1x2_ft",     "1x2 — Full Time"),
    ("1x2_1up_ft", "1x2 — 1 Up"),
    ("1x2_2up_ft", "1x2 — 2 Up"),
)
```

In `_build_event_detail`, replace the existing flat pills list construction:

```python
pills = [
    (slug, label, slug == market_slug)
    for mid, _ln, label, slug in _MARKET_PICKER
]
```

with:

```python
available_lines = queries.get_available_lines(conn, ev_row["id"])

# Active family — derive from the active slug.
active_market_id, _active_line, _active_label = _PICKER_BY_SLUG[market_slug]

# Family pills: 1x2 trio (each is its own family with no line row), then
# the four parameterized families.
family_pills: list[tuple[str, str, bool, bool]] = []
for fid, label in _FAMILY_PILLS_1X2:
    family_pills.append((
        fid, label,
        fid == active_market_id,  # active
        False,                    # never disabled
    ))
for fid, label in _EXPANDER_MARKETS:
    has_lines = bool(available_lines.get(fid))
    family_pills.append((
        fid, label,
        fid == active_market_id,
        not has_lines,
    ))

# Line pills: only if the active family is parameterized AND has data.
line_pills: list[tuple[str, str, bool]] = []
if active_market_id in {fid for fid, _ in _EXPANDER_MARKETS}:
    spec = _spec_by_id[active_market_id]
    prefix = spec.column_prefix
    for line in available_lines.get(active_market_id, []):
        slug = f"{prefix}_{line}"
        line_pills.append((slug, str(line), slug == market_slug))

# Keep the legacy flat pills list for now — Task 8 retires it.
pills = [
    (slug, label, slug == market_slug)
    for mid, _ln, label, slug in _MARKET_PICKER
]
```

In the `return EventDetail(...)` call, add the two new keyword args:

```python
return EventDetail(
    ...
    pills=pills,
    family_pills=family_pills,
    line_pills=line_pills,
    history=history,
)
```

### Step 7.4 — Family-chip click default-slug logic

When the user clicks a parameterized family pill, the URL should land on the lowest available line for that family. Since clicks are plain `<a href>`, the template needs to compute that target URL at render time.

- [ ] **In `_build_event_detail`**, after `available_lines = …` and before building `family_pills`, build an auxiliary map:

```python
family_default_slug: dict[str, str] = {}
for canonical_id, _label in _FAMILY_PILLS_1X2:
    # 1x2 family default slug equals the canonical_id itself
    family_default_slug[canonical_id] = canonical_id
for canonical_id, _label in _EXPANDER_MARKETS:
    lines = available_lines.get(canonical_id, [])
    if lines:
        prefix = _spec_by_id[canonical_id].column_prefix
        family_default_slug[canonical_id] = f"{prefix}_{lines[0]}"
```

Extend the `family_pills` tuple from 4-tuple `(family_id, label, active, disabled)` to **5-tuple** `(family_id, label, default_slug, active, disabled)`:

```python
@dataclass
class EventDetail:
    ...
    family_pills: list[tuple[str, str, str, bool, bool]]
    ...
```

Update the family_pills builder accordingly:

```python
for fid, label in _FAMILY_PILLS_1X2:
    family_pills.append((
        fid, label, family_default_slug[fid],
        fid == active_market_id,
        False,
    ))
for fid, label in _EXPANDER_MARKETS:
    has_lines = bool(available_lines.get(fid))
    family_pills.append((
        fid, label,
        family_default_slug.get(fid, ""),  # empty if no lines (chip is disabled)
        fid == active_market_id,
        not has_lines,
    ))
```

### Step 7.5 — Update `event_detail.html` template

- [ ] **Edit `src/odds_scraper/web/templates/event_detail.html`** — find the pill block:

```jinja
<div class="pills">
  {% for slug, label, active in event.pills %}
    <a href="/events/{{ event.id }}?market={{ slug }}"
       class="pill{% if active %} active{% endif %}">{{ label }}</a>
  {% endfor %}
</div>
```

Replace with:

```jinja
<div class="pills family-pills">
  {% for fid, label, default_slug, active, disabled in event.family_pills %}
    {% if disabled %}
      <span class="family-pill disabled" title="No data">{{ label }}</span>
    {% else %}
      <a href="/events/{{ event.id }}?market={{ default_slug }}"
         class="family-pill{% if active %} active{% endif %}">{{ label }}</a>
    {% endif %}
  {% endfor %}
</div>
{% if event.line_pills %}
  <div class="pills line-pills">
    {% for slug, label, active in event.line_pills %}
      <a href="/events/{{ event.id }}?market={{ slug }}"
         class="line-pill{% if active %} active{% endif %}">{{ label }}</a>
    {% endfor %}
  </div>
{% endif %}
```

### Step 7.6 — Add CSS for the new pill classes (optional)

- [ ] **Edit `src/odds_scraper/web/static/app.css`** — if `.pill` styling exists, mirror it for `.family-pill` and `.line-pill`. Append:

```css
.family-pill {
  /* mirrors the existing .pill rule */
  display: inline-block;
  padding: 3px 8px;
  margin-right: 3px;
  background: #161821;
  color: #d1d5db;
  border-radius: 4px;
  font-size: 11px;
  text-decoration: none;
  border: 1px solid #1f2330;
}
.family-pill.active {
  background: #233a55;
  border-color: #2f5378;
  color: #ffffff;
}
.family-pill.disabled {
  opacity: 0.35;
  cursor: not-allowed;
  pointer-events: none;
}
.line-pill {
  display: inline-block;
  padding: 2px 7px;
  margin-right: 3px;
  background: #0f1115;
  color: #9ca3af;
  border-radius: 4px;
  font-size: 11px;
  text-decoration: none;
  border: 1px solid #1f2330;
}
.line-pill.active {
  background: #233a55;
  color: #ffffff;
  border-color: #2f5378;
}
.line-pills {
  margin-top: 6px;
}
```

(If existing `.pill` rules differ, follow their colour palette instead — these are placeholders mirroring the existing dark theme.)

### Step 7.7 — Run Task 7 tests, see them pass

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_app.py -k "family_pills_includes_new or disables_family_pill_when_no_lines or line_pills_filtered_to_available or active_line_pill_is_marked or pills_include_ou_lines" -v`

Expected: all PASS.

### Step 7.8 — Run full web suite

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_app.py tests/test_web_queries.py -v`
Expected: all pass.

### Step 7.9 — Retire the legacy flat `pills` field

- [ ] **In `src/odds_scraper/web/app.py`**, remove the `pills` field from `EventDetail` and remove the `pills = [...]` block in `_build_event_detail`. Remove the `pills=pills` kwarg from the `EventDetail(...)` call.

- [ ] **Verify no template references `event.pills`** — grep the templates directory; only `event_detail.html` should reference it, and the Task 7.5 edit already removed that.

- [ ] **Re-run full web suite** to confirm.

### Step 7.10 — Commit

```bash
git add src/odds_scraper/web/app.py src/odds_scraper/web/templates/event_detail.html src/odds_scraper/web/static/app.css tests/test_web_app.py
git commit -m "$(cat <<'EOF'
feat(web/detail): two-stage market pills - family + line

Detail page now shows a family-chip row (1x2 ft / 1x2 1up / 1x2 2up /
Next Goal / Match O/U / Home O/U / Away O/U) and a line-chip row below
it that's only rendered when a parameterized family is active. The
line chips show only the lines that have priced data for this event
(from get_available_lines).

Clicking a parameterized family chip with available lines lands on the
lowest line for that family. Family chips with no available lines are
greyed out and non-clickable (.family-pill.disabled).

The legacy flat 'pills' list field on EventDetail is removed in this
commit since no template still references it.
EOF
)"
```

---

## Task 8: Full-suite smoke + manual UI walkthrough

**Files:** none modified; verification only.

### Step 8.1 — Full pytest suite

- [ ] **Run all tests**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: every test passes. Count grows compared to pre-change by the tests added across Tasks 1, 3, 4, 5, 6, 7. No regressions.

If anything fails, fix it before continuing.

### Step 8.2 — Start the web app and walk through the UI

- [ ] **Start the web app:**

```powershell
.venv\Scripts\python.exe -m odds_scraper.web.__main__ --db data\odds.db --port 8000
```

(Adjust the command to match the existing `__main__` entry — check `src/odds_scraper/web/__main__.py` if unsure.)

- [ ] **Open** `http://localhost:8000/` in a browser.

- [ ] **Verify the filter row:** two new dropdowns at the left (Country / League). Country starts at "All", League is disabled and shows "All". Pick a country → League dropdown populates with that country's leagues and becomes enabled. Pick a league → the events list narrows. Both choices persist after a page reload.

- [ ] **Verify card expander:** for any event with priced over_under_ft data, click "Show N more markets". Expand should reveal Next Goal, Match O/U, Home O/U, Away O/U groups (in that order) — only those with data appear. Each group shows lines that have data, with side labels (H/D/A or O/U or H/N/A).

- [ ] **Click into a detail page.** Verify the country · league subtitle renders beneath the team-name header. Verify the family pill row shows all 7 families. Parameterized families with no data are visibly greyed. Click "Match O/U" → line pills appear below showing only the lines with data. Click a line pill → history table updates to that line's data.

- [ ] **Verify next_goal pill:** if the event has next_goal data, the "Next Goal" family pill is enabled. Click it. Line pills appear. Click a line → history table shows columns labelled `H | N | A` (no KeyError).

### Step 8.3 — Verify the live DB serves the new markets

If the scraper hasn't been restarted on sub-project 2's branch yet (per the carry-forward), restart it now so sub-project 3's UI has new-market data to display:

- [ ] **In the scraper terminal:** Ctrl+C, then `python -m odds_scraper.main --config config.yaml`. Wait ~60 seconds for a tick.

- [ ] **Verify** the home page card-expander now shows Next Goal / Home O/U / Away O/U for at least some events.

### Step 8.4 — Commit any straggler fixes

- [ ] If anything required a fix during the smoke walkthrough, commit it with an appropriate `fix(...)` message. Otherwise no commit is needed in this task.

---

## Self-review

**Spec coverage:**
- (a) `_SIDE_LABEL["none"]` / `_SIDE_SHORT["none"]` + `_sides_for(market_id)` lookup → Task 1
- (b) Card expander reorder + new market groups → Task 3
- (c) Detail-page country · league subtitle → Task 4
- (d) Cascading country/league dropdowns → Tasks 5 + 6
- (e) Two-stage detail-page pills with dynamic-line filtering → Task 7 (depends on Task 2's `get_available_lines`)
- Spec says no schema changes → confirmed (no `db_schema.py` task)
- Spec says no new collected data → confirmed (no `collector.py` task)
- `get_event_meta` extension noted in self-review fix → Task 4
- Toggle button copy change from "Show OU lines" to broader phrasing → Task 3
- localStorage persistence for filter selections → Task 6
- "Family chip disabled when no available lines" → Task 7

**Placeholder scan:** no "TBD", no "implement later". Each step provides full code or commands with expected output.

**Type consistency:**
- `EventDetail` field set is consistent: introduced once in Task 1.4 (`sides_short`), extended in Task 4.3 (`country_name`, `league_name`), restructured in Task 7.1 (`family_pills`, `line_pills`, with `pills` retired in 7.9).
- `family_pills` tuple shape settled in Task 7.4 as `(family_id, label, default_slug, active, disabled)` — the template in Task 7.5 unpacks the same five fields.
- Column-prefix string values (`"ou"`, `"ng"`, `"ou_home"`, `"ou_away"`) match what `MARKET_MANIFEST` actually carries (verified by reading `src/odds_scraper/models.py` during planning).
- `_EXPANDER_MARKETS` is introduced in Task 1.5 and reused as-is in Tasks 3.2 and 7.3.
- `get_available_lines` signature `(conn, event_id) -> dict[str, list[float]]` matches consumer in Task 7.3.
- `get_events_by_status` signature `(conn, status, *, country_id="", league_id="")` matches consumer in Task 6.2.
- `get_country_league_index` return shape `list[dict]` with `{country_id, country_name, leagues: [{league_id, league_name}]}` matches the template's `{% for c in country_league_index %}` loop in Task 6.4 and the JSON tag.
