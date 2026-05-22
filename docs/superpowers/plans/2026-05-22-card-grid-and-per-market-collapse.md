# Card grid alignment + per-market collapse Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix card and history-table column alignment (flex with explicit widths replaces grid), make every market group in an event card individually collapsible with global per-market persistence, change the detail-page default market from 2up → 1up.

**Architecture:** Backend stops marking `is_extra` and instead emits a stable `group_key` per `MarketGroup`. Template renders `data-group-key` per `.market-block`; CSS keys the collapsed state off a `.collapsed` class. JS reads `localStorage["card_market_collapse"]`, applies the class, falls back to a small `EXPANDED_BY_DEFAULT` allowlist for fresh visits. The old whole-card "Show N more markets" button retires. History table gets `text-align: center` on `thead th` plus matched `min-width` on side cells.

**Tech Stack:** Python 3.13, FastAPI + Jinja2, vanilla JS + localStorage, pytest.

**Spec reference:** `docs/superpowers/specs/2026-05-22-card-grid-and-per-market-collapse-design.md`

**Branch:** `feat/card-grid-and-per-market-collapse` (already checked out; spec committed as `2c595c8` + `c582838`).

---

## File map

| Action | File | Responsibility |
|---|---|---|
| Modify | `src/odds_scraper/web/app.py` | `MarketGroup` loses `is_extra`, gains `group_key: str`; `_build_event_view` populates it; `_DEFAULT_MARKET_SLUG = "1x2_1up_ft"`. |
| Modify | `src/odds_scraper/web/templates/_event_card.html` | Remove the bottom `.expand-toggle` button + `ns.extra_count` counter; remove the `▾ ` prefix in `.group-label`; remove the `market-extra` class branch; emit `data-group-key`. |
| Modify | `src/odds_scraper/web/static/app.css` | Replace grid card-row CSS with flex + explicit widths; retire `.market-extra` / `.expand-toggle` / `.card.expanded` rules; add `.market-block.collapsed`, chevron `::before`, history-table centering. |
| Modify | `src/odds_scraper/web/static/app.js` | Remove `initExpandToggles`, `applyExpandedState`, `expanded_events` references. Add `initMarketCollapse` + `applyMarketCollapseState`. Wire into `DOMContentLoaded` and `initEventDelegates` (htmx swap re-applies state). |
| Modify | `tests/test_web_app.py` | Update existing tests; add three new tests. |

---

## Task 1: Per-market collapse end-to-end (backend + template + CSS + JS + tests)

**Files:**
- Modify: `src/odds_scraper/web/app.py`
- Modify: `src/odds_scraper/web/templates/_event_card.html`
- Modify: `src/odds_scraper/web/static/app.css`
- Modify: `src/odds_scraper/web/static/app.js`
- Modify: `tests/test_web_app.py`

### Step 1.1 — Update existing tests + add new ones (tests-first)

The existing test suite asserts on `is_extra`-driven markup (`market-extra` class, `expand-toggle` button, "more market" label) and the 2up default. Those assertions become stale; new ones replace them.

- [ ] **Edit `tests/test_web_app.py`**:

**A. Replace `test_events_card_skips_ou_groups_when_no_data`** (around line 258). Current body:

```python
def test_events_card_skips_ou_groups_when_no_data(client: TestClient):
    # Fixture only has 1x2 family data — no OU rows in DB → no expand
    # toggle should be rendered.
    r = client.get("/events?status=upcoming")
    assert "expand-toggle" not in r.text
```

Replace with:

```python
def test_events_card_skips_ou_groups_when_no_data(client: TestClient):
    """Fixture only has 1x2 family data — no OU rows in DB → no OU group
    is rendered (we check by group_key absence, not by old market-extra)."""
    r = client.get("/events?status=upcoming")
    assert 'data-group-key="over_under_ft_' not in r.text
    assert 'data-group-key="next_goal_ft_' not in r.text
```

**B. Replace `test_events_card_has_expand_toggle_when_ou_present`** (around line 301). Current body asserts `"expand-toggle" in r.text` and `"market-extra" in r.text`. Replace with:

```python
def test_events_card_has_expand_toggle_when_ou_present(db_with_ou_path: Path):
    """With OU 2.5 priced, the card emits a market-block with the
    matching group_key — the per-market collapse hook for the JS layer."""
    app = create_app(db_path=db_with_ou_path)
    client = TestClient(app)
    r = client.get("/events?status=upcoming")
    assert 'data-group-key="over_under_ft_2.5"' in r.text
    assert "Match O/U 2.5" in r.text
    # The retired bottom button must NOT appear.
    assert "expand-toggle" not in r.text
    assert "market-extra" not in r.text
```

**C. Delete `test_events_card_expander_button_label_updates`** (around line 397). The whole function (def + body) goes away.

**D. Update `test_event_detail_renders_default_market`** (around line 107). Current body asserts `"1x2 — 2 Up" in r.text` and `"1.90" in r.text`. The default changes to 1up, and the fixture's `1.90` is only attached to `1x2_2up_ft` so it would disappear from the history when the default switches. Replace the function body with:

```python
def test_event_detail_renders_default_market(client: TestClient):
    r = client.get("/events/E1")
    assert r.status_code == 200
    # Header includes team names + back link
    assert "Liverpool" in r.text and "Arsenal" in r.text
    assert 'href="/"' in r.text
    # Default market is now 1x2 — 1 Up; its family-pill is active
    assert "1x2 — 1 Up" in r.text
    assert 'class="family-pill active"' in r.text
    # History table shows 1x2_1up_ft prices from the fixture's only snapshot
    assert "1.85" in r.text
    # Bookmaker headers present
    assert "BetPawa" in r.text
```

**E. Append three new tests at the end of `tests/test_web_app.py`**:

```python
def test_events_card_emits_group_key_per_market(client: TestClient):
    """Every market block carries data-group-key for the JS collapse layer.
    1x2 family group_key = canonical_id."""
    r = client.get("/events?status=upcoming")
    assert 'data-group-key="1x2_ft"' in r.text
    assert 'data-group-key="1x2_1up_ft"' in r.text
    assert 'data-group-key="1x2_2up_ft"' in r.text


def test_events_card_no_is_extra_marker(client: TestClient):
    """The retired is_extra flag must not leak into rendered markup —
    regression guard for the data-group-key migration."""
    r = client.get("/events?status=upcoming")
    assert "market-extra" not in r.text
    assert "expand-toggle" not in r.text


def test_event_detail_default_market_is_1up(client: TestClient):
    """Without ?market= query, the active family chip is 1x2 — 1 Up."""
    r = client.get("/events/E1")
    body = r.text
    # The active class lands on the 1up chip, not the 2up chip.
    import re
    m_1up = re.search(r'class="family-pill[^"]*"[^>]*>1x2 — 1 Up<', body)
    m_2up = re.search(r'class="family-pill[^"]*"[^>]*>1x2 — 2 Up<', body)
    assert m_1up is not None and "active" in m_1up.group(0)
    assert m_2up is not None and "active" not in m_2up.group(0)
```

### Step 1.2 — Run, confirm failures

`.venv\Scripts\python.exe -m pytest tests/test_web_app.py -v`
Expected: failures across the four replaced/new tests plus `test_event_detail_renders_default_market`. Other tests still pass.

### Step 1.3 — Update `MarketGroup` and `_build_event_view`

- [ ] **Edit `src/odds_scraper/web/app.py`**.

Find the `MarketGroup` dataclass (around line 103):

```python
@dataclass
class MarketGroup:
    label: str              # e.g., "1x2 — Full Time"
    rows: list[OutcomeRow]
    # is_extra=True groups are hidden by default in the card view; revealed
    # via the expand toggle. The detail page uses the market-picker pills
    # instead and ignores this flag.
    is_extra: bool = False
```

Replace with:

```python
@dataclass
class MarketGroup:
    label: str              # e.g., "1x2 — Full Time"
    group_key: str          # stable key for per-market collapse persistence:
                            #   "1x2_ft" / "1x2_1up_ft" / "1x2_2up_ft" for the
                            #   1x2 family; f"{canonical_id}_{line}" for
                            #   parameterized markets (e.g., "over_under_ft_2.5").
    rows: list[OutcomeRow]
```

(`is_extra` is removed entirely. No call-site keeps it.)

Find `_build_event_view` (around line 250). The 1x2 family loop currently looks like:

```python
for market_id, group_label, market_short in _COLLAPSED_ORDER:
    rows_for_group = []
    for side in _sides_for(market_id):
        prices = bucket.get((market_id, 0.0, side), {})
        rows_for_group.append(OutcomeRow(
            market_label=market_short,
            side_label=_SIDE_LABEL[side],
            side_short=_SIDE_SHORT[side],
            prices=prices,
        ))
    groups.append(MarketGroup(
        label=group_label, rows=rows_for_group, is_extra=False,
    ))
```

Replace the `groups.append(...)` call with:

```python
    groups.append(MarketGroup(
        label=group_label, group_key=market_id, rows=rows_for_group,
    ))
```

(If the original used `is_extra=False` explicitly, just remove that arg and add `group_key=market_id`. The `market_id` IS the canonical_id for the 1x2 family.)

Find the parameterized-markets loop (around line 264) which iterates `_EXPANDER_MARKETS`:

```python
for market_id, label_prefix in _EXPANDER_MARKETS:
    spec = _spec_by_id[market_id]
    for line in spec.lines or ():
        rows_for_group = []
        for side in _sides_for(market_id):
            prices = bucket.get((market_id, line, side), {})
            rows_for_group.append(OutcomeRow(...))
        if any(r.prices for r in rows_for_group):
            groups.append(MarketGroup(
                label=f"{label_prefix} {line}",
                rows=rows_for_group,
                is_extra=True,
            ))
```

Replace the `groups.append(...)` block inside this loop with:

```python
        if any(r.prices for r in rows_for_group):
            groups.append(MarketGroup(
                label=f"{label_prefix} {line}",
                group_key=f"{market_id}_{line}",
                rows=rows_for_group,
            ))
```

(Drop `is_extra=True`.)

### Step 1.4 — Change `_DEFAULT_MARKET_SLUG`

- [ ] **In `src/odds_scraper/web/app.py`**, find:

```python
# Default market for the detail page when none specified — focus is 2up
_DEFAULT_MARKET_SLUG = "1x2_2up_ft"
```

Replace with:

```python
# Default market for the detail page when none specified — focus is 1up
_DEFAULT_MARKET_SLUG = "1x2_1up_ft"
```

### Step 1.5 — Update card template

- [ ] **Edit `src/odds_scraper/web/templates/_event_card.html`**. The current `<div class="market-block">` block:

```jinja
{% set ns = namespace(extra_count=0) %}
{% for group in event.market_groups %}
  {% if group.is_extra %}{% set ns.extra_count = ns.extra_count + 1 %}{% endif %}
  <div class="market-block{% if group.is_extra %} market-extra{% endif %}">
    <div class="group-label">▾ {{ group.label }}</div>
    {% for row in group.rows %}
      <div class="row">
        ...
      </div>
    {% endfor %}
  </div>
{% endfor %}

{% if ns.extra_count > 0 %}
  <button class="expand-toggle" type="button"
          data-collapsed-label="▼ Show {{ ns.extra_count }} more {% if ns.extra_count == 1 %}market{% else %}markets{% endif %}"
          data-expanded-label="▲ Hide extra markets">
    ▼ Show {{ ns.extra_count }} more {% if ns.extra_count == 1 %}market{% else %}markets{% endif %}
  </button>
{% endif %}
```

Replace with:

```jinja
{% for group in event.market_groups %}
  <div class="market-block" data-group-key="{{ group.group_key }}">
    <div class="group-label">{{ group.label }}</div>
    {% for row in group.rows %}
      <div class="row">
        ...
      </div>
    {% endfor %}
  </div>
{% endfor %}
```

(The `{% set ns = … %}` counter, the `market-extra` conditional class, the `▾ ` prefix on the label, and the entire bottom `<button>` are all gone. The inner `<div class="row">` body — `<span class="outcome">…</span>` and the bookmaker spans — stays exactly as-is.)

### Step 1.6 — Replace card grid CSS with flex + explicit widths

- [ ] **Edit `src/odds_scraper/web/static/app.css`**. Find these existing rules:

```css
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
```

Replace with:

```css
.col-header {
  background: #0f0f0f;
  padding: 6px 12px;
  display: flex;
  align-items: center;
  gap: 4px;
}
.col-header > .lbl:first-child {
  flex: 1;
  min-width: 180px;
}
.col-header > .lbl[data-bookmaker] {
  width: 80px;
  text-align: center;
  white-space: nowrap;
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
  user-select: none;
}
.group-label:first-of-type { border-top: 0; }
.group-label::before {
  content: "▾ ";
  display: inline-block;
  font-size: 11px;
}
.market-block.collapsed .group-label::before {
  content: "▸ ";
}
.market-block.collapsed .row { display: none; }
.row {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 5px 12px;
  border-bottom: 1px solid #111;
}
.row:last-child { border-bottom: 0; }
.row > .outcome {
  flex: 1;
  min-width: 180px;
}
.row > span[data-bookmaker] {
  width: 80px;
  text-align: center;
  white-space: nowrap;
}
```

Then find and DELETE the retired `.market-extra` / `.expand-toggle` block (around lines 155–173):

```css
/* Per-card OU expand toggle + hidden extras */
.market-extra { display: none; }
.card.expanded .market-extra { display: block; }
.expand-toggle {
  display: block;
  width: 100%;
  padding: 6px 12px;
  background: #0f0f0f;
  color: #888;
  border: 0;
  border-top: 1px solid #1a1a1a;
  font-family: inherit;
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  cursor: pointer;
}
.expand-toggle:hover { background: #181818; color: #d1d5db; }
.card.expanded .expand-toggle { color: #4ade80; }
```

Remove the entire block (including the `/* Per-card OU expand toggle + hidden extras */` comment line).

### Step 1.7 — Replace JS expand-toggle code with per-market collapse

- [ ] **Edit `src/odds_scraper/web/static/app.js`**. Find the `applyExpandedState` and `initExpandToggles` functions (in the comment-block "Per-card 'Show OU lines' expand toggle"). Replace the entire block (both functions and the comment header) with:

```javascript
// -----------------------------------------------------------------------------
// Per-market collapse (each market block in a card)
// -----------------------------------------------------------------------------
// localStorage shape: {group_key: true} where group_key is the value of
// data-group-key on the .market-block (e.g., "1x2_ft", "next_goal_ft_1.0").
// true = collapsed. Absent = use the EXPANDED_BY_DEFAULT allowlist.
const EXPANDED_BY_DEFAULT = new Set(["1x2_ft", "1x2_1up_ft"]);

function applyMarketCollapseState() {
  const stored = LS.load('card_market_collapse', {});
  document.querySelectorAll('.market-block[data-group-key]').forEach(block => {
    const key = block.dataset.groupKey;
    let collapsed;
    if (key in stored) {
      collapsed = !!stored[key];
    } else {
      collapsed = !EXPANDED_BY_DEFAULT.has(key);
    }
    block.classList.toggle('collapsed', collapsed);
  });
}

function initMarketCollapse() {
  document.body.addEventListener('click', evt => {
    const label = evt.target.closest('.market-block .group-label');
    if (!label) return;
    const block = label.closest('.market-block');
    const key = block && block.dataset.groupKey;
    if (!key) return;
    const stored = LS.load('card_market_collapse', {});
    const currentlyCollapsed = block.classList.contains('collapsed');
    stored[key] = !currentlyCollapsed;
    LS.save('card_market_collapse', stored);
    applyMarketCollapseState();
  });
}
```

Then find the `applyAllCardState` function (further down). Replace `applyExpandedState();` with `applyMarketCollapseState();`. The function should read:

```javascript
function applyAllCardState() {
  applyMarketCollapseState();
  applyKickoffFilter();
  applySearchFilter();
}
```

Find `initEventDelegates`. Replace the `initExpandToggles();` call inside the `htmx:afterSwap` handler with `applyMarketCollapseState();`:

```javascript
function initEventDelegates() {
  document.body.addEventListener('htmx:afterSwap', evt => {
    if (evt.target && evt.target.id === 'events-list') {
      // After a fragment swap, re-apply per-market collapse + chip/time/search state
      applyAllCardState();
    }
  });
}
```

Find the `DOMContentLoaded` handler at the bottom. Replace the `initExpandToggles();` call with `initMarketCollapse();`. The final handler should read:

```javascript
document.addEventListener('DOMContentLoaded', () => {
  initBookmakerChips();
  initTabs();
  initKickoffFilter();
  initSearch();
  initCountryLeagueFilter();
  initMarketCollapse();
  initEventDelegates();
  applyAllCardState();
});
```

The `expanded_events` localStorage key is now orphaned but causes no errors (the new code never reads it). No migration needed.

### Step 1.8 — Run the full test suite

`.venv\Scripts\python.exe -m pytest tests/test_web_app.py tests/test_web_queries.py -v`
Expected: every test passes. Pay attention to the previously-failing ones from Step 1.2 — they should be green now.

If `test_event_detail_renders_default_market` is still red because the fixture's `1.90` line was important, double-check that the updated assertion body (Step 1.1.D) no longer references `1.90`.

### Step 1.9 — Run the full top-level pytest suite

`.venv\Scripts\python.exe -m pytest -q`
Expected: every test passes (159 → roughly same count given net changes are: 1 deleted, 3 added, others updated).

### Step 1.10 — Commit

```bash
git add src/odds_scraper/web/app.py src/odds_scraper/web/templates/_event_card.html src/odds_scraper/web/static/app.css src/odds_scraper/web/static/app.js tests/test_web_app.py
git commit -m "$(cat <<'EOF'
feat(web/card): per-market collapse + flex layout + 1up default

Every market block in an event card now has its own collapse toggle.
Click the group label to flip; state persists in localStorage under
card_market_collapse keyed by group_key (canonical_id for 1x2 family,
"{canonical_id}_{line}" for parameterized). On fresh visit, only
1x2_ft and 1x2_1up_ft are expanded by default — everything else
collapsed.

Card row layout switches from grid to flex with explicit widths, so
the column headers ("MARKET · OUTCOME · BP · SB · B9J · BW") line up
with their data cells. Bookmaker chip toggles continue to collapse
hidden columns out of the layout (natural flex behaviour).

The old bottom "Show N more markets" button retires along with the
is_extra flag on MarketGroup — uniform per-group collapse replaces it.

Detail-page default market changes from 1x2_2up_ft to 1x2_1up_ft.
EOF
)"
```

---

## Task 2: History table alignment

**Files:**
- Modify: `src/odds_scraper/web/static/app.css`
- Modify: `tests/test_web_app.py`

CSS-only fix for the history table on the detail page. Bookmaker `<th colspan="2|3">` cells need centred text; sub-head side-cell `<th>`s and the matching `<td class="num">` cells need a shared `min-width` so the colspan parent visually sits over its data columns.

### Step 2.1 — Write the regression test

- [ ] **Append to `tests/test_web_app.py`**:

```python
def test_history_table_has_centered_headers_css_hook(client: TestClient):
    """Sanity check that the table class hook is rendered so CSS can attach.
    Visual centring is verified manually; this test guards the markup contract."""
    r = client.get("/events/E1")
    assert '<table class="history-table">' in r.text
```

(This test must already pass because the current template emits `<table class="history-table">`. We add it as a regression guard before the CSS change in case anyone refactors the template later.)

### Step 2.2 — Confirm it passes pre-change

`.venv\Scripts\python.exe -m pytest tests/test_web_app.py::test_history_table_has_centered_headers_css_hook -v`
Expected: PASS already (markup is unchanged for this assertion).

### Step 2.3 — Add history-table centring CSS

- [ ] **Edit `src/odds_scraper/web/static/app.css`**. The existing history-table rules live around line 232. Find the `.history-table` block and ADD these rules at the end of the history-table section:

```css
.history-table thead th {
  text-align: center;
}
.history-table th.ts-col,
.history-table td.ts-col {
  text-align: left;
}
.history-table th.side-h,
.history-table td.num {
  min-width: 56px;
}
.history-table td.num {
  text-align: center;
}
```

(The `.ts-col` override keeps the TIME (UTC) column left-aligned even though it's inside the centered `thead th`. The `.state-col` rule already has `text-align: center` from the previous sub-project, so no conflict — its CSS sits AFTER these rules in the file.)

### Step 2.4 — Run the regression test + full suite

`.venv\Scripts\python.exe -m pytest tests/test_web_app.py -v`
Expected: all pass.

`.venv\Scripts\python.exe -m pytest -q`
Expected: full suite green.

### Step 2.5 — Commit

```bash
git add src/odds_scraper/web/static/app.css tests/test_web_app.py
git commit -m "$(cat <<'EOF'
fix(web/detail): center history-table headers + matched side widths

thead th gets text-align: center so bookmaker <th colspan=...> labels
sit over their colspan area. Side-cell <th class=side-h> and the
matching <td class=num> share a 56px min-width so the visual columns
line up with the bookmaker header above. .ts-col stays left-aligned.

Adds a regression-guard test that the <table class="history-table">
markup hook is present.
EOF
)"
```

---

## Task 3: Full-suite smoke + manual UI check

**Files:** none modified; verification only.

### Step 3.1 — Run all tests

`.venv\Scripts\python.exe -m pytest -q`
Expected: every test passes.

### Step 3.2 — Visual walkthrough (manual)

Start the web app:

```powershell
.venv\Scripts\python.exe -m odds_scraper.web --db data\odds.db --port 8000
```

Open `http://localhost:8000/`. With localStorage cleared, verify:

- Each card opens with only `1x2 ft` and `1x2 1up` expanded; `1x2 2up`, OU lines, Next Goal, Home OU, Away OU all collapsed (chevron `▸`).
- Click a `.group-label` to expand a market — chevron flips to `▾`, rows render with column headers exactly above their data.
- Refresh the page — collapse state persists.
- Toggle a bookmaker chip off — that column's cells and header disappear from every card (flex layout collapses the column).
- Click into an event — detail page lands on `1x2 — 1 Up` (active family chip).
- History table column headers (`BetPawa | SportyBet | Bet9ja | Betway`) are centred over their data columns. STATE column from the previous sub-project still renders correctly.

### Step 3.3 — Commit any straggler fixes

If anything required a fix during the smoke, commit it with an appropriate `fix(...)` message. Otherwise no commit needed.

---

## Self-review

**Spec coverage:**
- Card layout primitive switches to flex with explicit widths → Task 1.6.
- Per-market group collapse with persistence → Task 1.7 (JS) + Task 1.6 (CSS).
- Group key shape: canonical_id for 1x2, `{canonical_id}_{line}` for parameterized → Task 1.3.
- Default expanded allowlist (`1x2_ft`, `1x2_1up_ft`) → Task 1.7 (JS const).
- Detail-page default market 2up → 1up → Task 1.4.
- Bottom "Show N more markets" button retires → Task 1.5 (template) + Task 1.6 (CSS).
- `is_extra` field retires → Task 1.3.
- History table centring → Task 2.3.
- Empty bookmaker column behavior (data missing → "—" rendered; chip-off → column collapses) → Task 1.6's flex CSS handles both.

**Placeholder scan:** no "TBD" / "implement later" — every step has full code or commands with expected output.

**Type consistency:**
- `MarketGroup.group_key: str` is introduced in Task 1.3 and consumed by `_event_card.html` (`{{ group.group_key }}`) in Task 1.5 → matches.
- localStorage key `card_market_collapse` is used identically in `applyMarketCollapseState` and `initMarketCollapse` → matches.
- `data-group-key` attribute name matches `block.dataset.groupKey` JS access (the camelCase `groupKey` is the standard `dataset` mapping for `data-group-key`).
- `EXPANDED_BY_DEFAULT` allowlist members (`1x2_ft`, `1x2_1up_ft`) match the canonical_ids in `MARKET_MANIFEST` and `_COLLAPSED_ORDER`.
