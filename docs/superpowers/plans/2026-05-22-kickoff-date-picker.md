# Kickoff filter date picker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the custom-hours number input in the home-page kickoff filter with a native `<input type="date">` picker; keep relative-window chips; extend `localStorage["kickoff_window"]` with a `"date:YYYY-MM-DD"` tagged-string form so picked dates persist.

**Architecture:** Pure client-side change. `index.html` swaps one input element; `app.js::initKickoffFilter` is rewritten to handle the date input alongside the chips (mutex behaviour); `app.js::applyKickoffFilter` gains a new branch for the `date:` prefix that compares the card's local-time date against the picked date.

**Tech Stack:** Vanilla JS + localStorage + native HTML `<input type="date">`, Jinja2, FastAPI test client.

**Spec reference:** `docs/superpowers/specs/2026-05-22-kickoff-date-picker-design.md`

**Branch:** `feat/kickoff-date-picker` (already checked out; spec committed as `fc628b8`).

---

## File map

| Action | File | Responsibility |
|---|---|---|
| Modify | `src/odds_scraper/web/templates/index.html` | Replace `<input type="number" id="kickoff-custom-hours">` with `<input type="date" id="kickoff-date">`. |
| Modify | `src/odds_scraper/web/static/app.js` | Rewrite `initKickoffFilter` for chip ↔ date mutex; add `date:` branch in `applyKickoffFilter`; add `localDateString` helper. |
| Modify | `src/odds_scraper/web/static/app.css` | One-line `min-width: 120px` on `.custom-hours` so the date input fits its native rendering. |
| Modify | `tests/test_web_app.py` | Update `test_index_filter_row_includes_search_and_kickoff` (asserts on the new id); add two new markup tests. |

---

## Task 1: Swap the input + wire the new logic

**Files:**
- Modify: `src/odds_scraper/web/templates/index.html`
- Modify: `src/odds_scraper/web/static/app.js`
- Modify: `src/odds_scraper/web/static/app.css`
- Modify: `tests/test_web_app.py`

### Step 1.1 — Update + add failing markup tests

Edit `tests/test_web_app.py`. Make these three changes:

**A. Update `test_index_filter_row_includes_search_and_kickoff`** (around line 237). Current body:

```python
def test_index_filter_row_includes_search_and_kickoff(client: TestClient):
    r = client.get("/")
    # Filter row labels
    assert "Bookmakers" in r.text and "Kickoff" in r.text and "Search" in r.text
    # Kickoff window pills (1h / 3h / 6h / 24h / 48h)
    for win in ("3600", "10800", "21600", "86400", "172800"):
        assert f'data-window="{win}"' in r.text
    assert 'data-window="all"' in r.text
    # Custom hours input and search input
    assert 'id="kickoff-custom-hours"' in r.text
    assert 'id="search-input"' in r.text
```

Replace the `assert 'id="kickoff-custom-hours"' in r.text` line with:

```python
    assert 'id="kickoff-date"' in r.text
```

Leave the rest of the function alone. The final body should read:

```python
def test_index_filter_row_includes_search_and_kickoff(client: TestClient):
    r = client.get("/")
    # Filter row labels
    assert "Bookmakers" in r.text and "Kickoff" in r.text and "Search" in r.text
    # Kickoff window pills (1h / 3h / 6h / 24h / 48h)
    for win in ("3600", "10800", "21600", "86400", "172800"):
        assert f'data-window="{win}"' in r.text
    assert 'data-window="all"' in r.text
    # Date picker (replaces the old custom-hours input) and search input
    assert 'id="kickoff-date"' in r.text
    assert 'id="search-input"' in r.text
```

**B. Append a new test** at the end of `tests/test_web_app.py`:

```python
def test_index_kickoff_date_input_is_type_date(client: TestClient):
    """Native <input type="date"> opens the OS calendar picker on mobile
    and desktop alike — verify the type attribute survives the template."""
    r = client.get("/")
    # The input tag should be type="date" — not type="number".
    # We search for the substring within the input opening tag.
    assert 'id="kickoff-date"' in r.text
    # Sanity: somewhere in the markup, type="date" appears (specifically on our
    # new input). The substring check is good enough — there are no other
    # type="date" inputs in the home page today.
    assert 'type="date"' in r.text
```

**C. Append a regression-guard test** at the end of `tests/test_web_app.py`:

```python
def test_index_no_longer_has_custom_hours_input(client: TestClient):
    """The old kickoff-custom-hours number input has been retired."""
    r = client.get("/")
    assert 'id="kickoff-custom-hours"' not in r.text
```

### Step 1.2 — Run, confirm two failures + one continued pass

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_app.py -k "filter_row_includes_search_and_kickoff or kickoff_date_input or custom_hours_input" -v`

Expected:
- `test_index_filter_row_includes_search_and_kickoff`: FAIL (asserts on `id="kickoff-date"` which doesn't exist yet).
- `test_index_kickoff_date_input_is_type_date`: FAIL.
- `test_index_no_longer_has_custom_hours_input`: FAIL (the old input is still there).

### Step 1.3 — Swap the input in the template

Edit `src/odds_scraper/web/templates/index.html`. Find this block in the kickoff `<div class="filter-group">`:

```jinja
<input type="number" id="kickoff-custom-hours"
       class="custom-hours" placeholder="custom h"
       min="0" step="0.5"
       title="Custom window in hours — overrides the pills">
```

Replace it with:

```jinja
<input type="date" id="kickoff-date" class="custom-hours"
       title="Show only events kicking off on this day (local time)">
```

The surrounding `<button class="chip kick …">` elements stay exactly as they are.

### Step 1.4 — Add `localDateString` helper + `date:` branch in `applyKickoffFilter`

Edit `src/odds_scraper/web/static/app.js`. Find `applyKickoffFilter` (it currently starts with the comment `// Kickoff timing filter` around line 50–75).

Insert a new helper `localDateString` immediately above `applyKickoffFilter`:

```javascript
// -----------------------------------------------------------------------------
// Kickoff timing filter
// -----------------------------------------------------------------------------
function localDateString(date) {
  // Return YYYY-MM-DD in the browser's local timezone. The date picker emits
  // local-time dates, so we compare against the card's kickoff_utc parsed
  // to local components — not its UTC components.
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}
```

Then replace the existing `applyKickoffFilter` body. Current code:

```javascript
function applyKickoffFilter() {
  const win = LS.load('kickoff_window', 'all');
  const cutoffSec = (win === 'all') ? null
                                    : (Date.now() / 1000) + Number(win);
  document.querySelectorAll('.card[data-kickoff-utc]').forEach(card => {
    if (cutoffSec === null) {
      card.classList.remove('hidden-by-time');
      return;
    }
    const t = Date.parse(card.dataset.kickoffUtc);
    if (isNaN(t)) {
      // No parseable kickoff (shouldn't happen) — always show
      card.classList.remove('hidden-by-time');
      return;
    }
    const ks = t / 1000;
    // Hide only events that kick off AFTER the window. Live/ended events
    // (kickoff in the past) always pass.
    card.classList.toggle('hidden-by-time', ks > cutoffSec);
  });
}
```

Replace with:

```javascript
function applyKickoffFilter() {
  const win = LS.load('kickoff_window', 'all');
  document.querySelectorAll('.card[data-kickoff-utc]').forEach(card => {
    const t = Date.parse(card.dataset.kickoffUtc);
    if (isNaN(t)) {
      // No parseable kickoff (shouldn't happen) — always show
      card.classList.remove('hidden-by-time');
      return;
    }
    if (win === 'all') {
      card.classList.remove('hidden-by-time');
      return;
    }
    if (typeof win === 'string' && win.startsWith('date:')) {
      const targetDate = win.slice(5);
      const cardLocalDate = localDateString(new Date(t));
      card.classList.toggle('hidden-by-time', cardLocalDate !== targetDate);
      return;
    }
    // Numeric seconds window: hide events that kick off AFTER now + win.
    // Live/ended events (kickoff in the past) always pass.
    const cutoffSec = (Date.now() / 1000) + Number(win);
    const ks = t / 1000;
    card.classList.toggle('hidden-by-time', ks > cutoffSec);
  });
}
```

### Step 1.5 — Rewrite `initKickoffFilter` for chip ↔ date mutex

In the same file, find `initKickoffFilter` (immediately below the function you just edited). The current body uses `customInput` (a number input) with `parseFloat` and isCustom-window logic. Replace the entire function body with:

```javascript
function initKickoffFilter() {
  const current = LS.load('kickoff_window', 'all');
  const dateInput = document.getElementById('kickoff-date');
  const chips = Array.from(document.querySelectorAll('.chip.kick[data-window]'));

  // Restore stored state on boot.
  const isDate = typeof current === 'string' && current.startsWith('date:');
  if (isDate && dateInput) {
    dateInput.value = current.slice(5);
    chips.forEach(c => c.classList.remove('on'));
  } else {
    chips.forEach(c => c.classList.toggle('on', c.dataset.window === String(current)));
    if (dateInput) dateInput.value = '';
  }

  // Chip click: clear date input, set chip state.
  chips.forEach(c => {
    c.addEventListener('click', () => {
      chips.forEach(x => x.classList.remove('on'));
      c.classList.add('on');
      if (dateInput) dateInput.value = '';
      LS.save('kickoff_window', c.dataset.window);
      applyKickoffFilter();
    });
  });

  // Date change: clear chips, save tagged string.
  if (dateInput) {
    dateInput.addEventListener('input', () => {
      const v = dateInput.value;
      if (!v) {
        // User cleared the date — revert to "All" so something is selected.
        chips.forEach(x => x.classList.remove('on'));
        document.querySelector('.chip.kick[data-window="all"]')?.classList.add('on');
        LS.save('kickoff_window', 'all');
      } else {
        chips.forEach(x => x.classList.remove('on'));
        LS.save('kickoff_window', `date:${v}`);
      }
      applyKickoffFilter();
    });
  }
}
```

The retired `customInput` / `parseFloat` / `isCustom` branches are all gone with the rewrite.

### Step 1.6 — Add CSS tweak

Edit `src/odds_scraper/web/static/app.css`. Find the existing `.search-input, .custom-hours` block (around line 135):

```css
.search-input,
.custom-hours {
  background: #0f0f0f;
  border: 1px solid #1a1a1a;
  border-radius: 3px;
  color: #d1d5db;
  font-family: inherit;
  font-size: 11px;
  padding: 3px 8px;
  outline: none;
}
.search-input { flex: 1; }
.custom-hours {
  width: 80px;
  text-align: right;
  margin-left: 4px;
}
```

Update the `.custom-hours` rule. Replace:

```css
.custom-hours {
  width: 80px;
  text-align: right;
  margin-left: 4px;
}
```

With:

```css
.custom-hours {
  min-width: 120px;
  margin-left: 4px;
}
```

(The `width: 80px` and `text-align: right` are dropped — `<input type="date">` needs room for its native calendar icon and rendered date string. `min-width` gives it that space without locking the field to a single fixed width.)

### Step 1.7 — Run the three failing tests, see them pass

`.venv\Scripts\python.exe -m pytest tests/test_web_app.py -k "filter_row_includes_search_and_kickoff or kickoff_date_input or custom_hours_input" -v`
Expected: all three PASS.

### Step 1.8 — Run full web suite

`.venv\Scripts\python.exe -m pytest tests/test_web_app.py tests/test_web_queries.py -v`
Expected: every test passes.

### Step 1.9 — Run full top-level suite

`.venv\Scripts\python.exe -m pytest -q`
Expected: every test passes.

### Step 1.10 — Commit

```bash
git add src/odds_scraper/web/templates/index.html src/odds_scraper/web/static/app.js src/odds_scraper/web/static/app.css tests/test_web_app.py
git commit -m "$(cat <<'EOF'
feat(web/filter): replace custom-hours input with native date picker

The Kickoff filter row's "custom h" number input was ugly and opaque
(typing 48 to mean 48 hours). Replaced with <input type="date">, which
opens the OS calendar on every modern browser including mobile.

localStorage kickoff_window gains a third tagged-string form
"date:YYYY-MM-DD" alongside the existing "all" and "<seconds>" values.
Existing bookmarked sessions with old values keep working unchanged.

Date semantics are LOCAL: a card's kickoff_utc is parsed and compared
against the picked date via the browser's local-time components, so a
23:30 UTC kickoff appears under the right calendar day in the user's
region.

Date input and relative chips are mutually exclusive — picking a date
clears the chips and vice versa; clearing the date input reverts to
"All".
EOF
)"
```

---

## Task 2: Full-suite smoke + visual check

**Files:** none modified; verification only.

### Step 2.1 — Run all tests

`.venv\Scripts\python.exe -m pytest -q`
Expected: every test passes.

### Step 2.2 — Manual visual check

Start the web app:

```powershell
.venv\Scripts\python.exe -m odds_scraper.web --db data\odds.db --port 8000
```

Open `http://localhost:8000/`. With localStorage cleared:

- The Kickoff row reads `[All] [1h] [3h] [6h] [24h] [48h] [📅 date picker]` — the date picker is the rightmost element.
- Clicking a chip filters events as today.
- Picking a date deselects all chips and narrows the visible cards to events kicking off on that local-date.
- Clicking a chip after picking a date clears the date input.
- Clicking "All" clears the date input and shows all events.
- Refresh the page — the active filter (chip OR date) persists.

Bonus check on a phone or DevTools mobile emulation: tapping the date input opens the OS calendar picker (iOS or Android native UI).

### Step 2.3 — Commit any straggler fixes

If anything required a fix during smoke, commit it with an appropriate `fix(...)` message. Otherwise no commit needed.

---

## Self-review

**Spec coverage:**
- Markup swap (`type="number" id="kickoff-custom-hours"` → `type="date" id="kickoff-date"`) → Task 1.3.
- localStorage `kickoff_window` accepts `"date:YYYY-MM-DD"` → Task 1.5 (`LS.save('kickoff_window', \`date:${v}\`)`).
- `localDateString` helper → Task 1.4.
- `applyKickoffFilter` gains the `date:` branch → Task 1.4.
- `initKickoffFilter` rewrites mutex behaviour → Task 1.5.
- CSS `.custom-hours` widened → Task 1.6.
- Retire the old custom-hours-specific logic (`parseFloat`, `isCustom`) → Task 1.5 (whole-function rewrite drops it).
- Markup tests (new id present, type=date, old id absent) → Task 1.1.
- Existing test asserting on the old id is updated minimally → Task 1.1.A.

**Placeholder scan:** no "TBD" / "implement later" / "etc." — every step has full code or commands with expected output.

**Type consistency:**
- `id="kickoff-date"` appears identically in the template, the JS (`document.getElementById('kickoff-date')`), and the two new tests.
- `kickoff_window` stored values follow the documented shape: `"all"` | numeric-string | `"date:YYYY-MM-DD"`. The branch order in `applyKickoffFilter` matches: short-circuit `'all'`, then check `date:` prefix, then fallback to numeric.
- Tagged-string prefix `date:` is used identically in `LS.save` (writer in `initKickoffFilter`) and `win.startsWith('date:')` + `win.slice(5)` (reader in `applyKickoffFilter`).
