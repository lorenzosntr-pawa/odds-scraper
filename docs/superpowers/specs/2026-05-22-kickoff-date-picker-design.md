# Kickoff filter — date picker replaces custom hours — design

**Status:** approved 2026-05-22
**Touches:** `src/odds_scraper/web/templates/index.html`, `src/odds_scraper/web/static/app.js`, `src/odds_scraper/web/static/app.css`, `tests/test_web_app.py`.
**Untouched:** anything outside `web/`; no Python / queries / models change.

## Motivation

The kickoff filter's "custom hours" number input (`<input type="number" id="kickoff-custom-hours">`) is visually ugly and conceptually awkward — typing `48` to mean "48 hours from now" is opaque. The natural mental model is "show me events on day X." Native `<input type="date">` provides a calendar picker on every modern browser (mobile included), no JS dependency.

The existing relative-window chips (1h / 3h / 6h / 24h / 48h) stay — they're useful "imminent kickoff" filters and complement the date picker. The custom-hour number input is removed.

## Settled inputs

| Decision | Value |
|---|---|
| Date input element | `<input type="date" id="kickoff-date" class="custom-hours">` — native calendar picker. |
| Date semantics | LOCAL date. A kickoff at `2026-05-22T23:30:00Z` is `2026-05-23` in Italy (CEST) — that's how it should be selectable. |
| localStorage state shape | Tagged string in the existing `kickoff_window` key. Values: `"all"`, `"<seconds>"` (numeric as string), or `"date:YYYY-MM-DD"`. |
| Date ↔ relative chip relationship | Mutually exclusive. Picking a date deselects all relative chips. Clicking a relative chip clears the date input. Clicking "All" clears both. |
| Empty date input | No date filter active; relative chip (if any) wins, otherwise "All". |
| Range selection | Out of scope — single-day only. |
| "Today" / "Tomorrow" preset chips | Out of scope. |
| Mobile compatibility | Free with `<input type="date">` — OS-native picker on iOS / Android. |
| Bookmarked sessions with old localStorage values | `"all"` and `"<seconds>"` keep working untouched. No migration needed. |

## Architecture

### Markup change (`index.html`)

Find the existing kickoff filter group:

```jinja
<div class="filter-group">
  <span class="filter-lbl">Kickoff</span>
  <button class="chip kick on" data-window="all">All</button>
  <button class="chip kick"    data-window="3600">1h</button>
  <button class="chip kick"    data-window="10800">3h</button>
  <button class="chip kick"    data-window="21600">6h</button>
  <button class="chip kick"    data-window="86400">24h</button>
  <button class="chip kick"    data-window="172800">48h</button>
  <input type="number" id="kickoff-custom-hours"
         class="custom-hours" placeholder="custom h"
         min="0" step="0.5"
         title="Custom window in hours — overrides the pills">
</div>
```

Replace the `<input>` line with:

```jinja
<input type="date" id="kickoff-date" class="custom-hours"
       title="Show only events kicking off on this day (local time)">
```

The `class="custom-hours"` is reused so existing dark-theme styling for the input applies; only the type and id change. The chip buttons are unchanged.

### Filter state shape

`localStorage["kickoff_window"]` accepts three forms:

| Value | Meaning |
|---|---|
| `"all"` | No time filter. |
| `"3600"` (or any positive integer as string) | Hide events kicking off more than N seconds from `Date.now()`. |
| `"date:2026-05-22"` | Show only events whose **local** date equals `2026-05-22`. |

Branching in `applyKickoffFilter()`:

```javascript
function applyKickoffFilter() {
  const win = LS.load('kickoff_window', 'all');
  document.querySelectorAll('.card[data-kickoff-utc]').forEach(card => {
    const t = Date.parse(card.dataset.kickoffUtc);
    if (isNaN(t)) { card.classList.remove('hidden-by-time'); return; }
    if (win === 'all') { card.classList.remove('hidden-by-time'); return; }
    if (typeof win === 'string' && win.startsWith('date:')) {
      const targetDate = win.slice(5);
      const cardLocalDate = localDateString(new Date(t));
      card.classList.toggle('hidden-by-time', cardLocalDate !== targetDate);
      return;
    }
    // numeric seconds window (current behaviour)
    const cutoffSec = (Date.now() / 1000) + Number(win);
    const ks = t / 1000;
    card.classList.toggle('hidden-by-time', ks > cutoffSec);
  });
}
```

Helper:

```javascript
function localDateString(date) {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}
```

`getFullYear/Month/Date` return the components in the browser's local timezone — that's the whole point. No DST or timezone-conversion code needed.

### Init / event wiring

`initKickoffFilter()` is rewritten to handle the date input alongside the chips:

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

The retired custom-hours parsing branch (the `parseFloat(customInput.value)` block and its NaN/empty fallbacks) is removed.

### CSS

The existing `.custom-hours` rules style the input dark-theme; they apply to `<input type="date">` too. One tweak — date inputs are slightly wider than the old number input by default. We add a `min-width` so the placeholder/value fits without truncating:

```css
.custom-hours { min-width: 120px; }
```

Append this to the existing `.custom-hours` block in `app.css`.

## Tests

| File | Test | Change |
|---|---|---|
| `tests/test_web_app.py` | (new) `test_index_filter_row_has_kickoff_date_picker` | Hit `/`; assert `id="kickoff-date"` and `type="date"` in the response. |
| `tests/test_web_app.py` | (new) `test_index_no_longer_has_custom_hours_input` | Hit `/`; assert `id="kickoff-custom-hours"` is NOT in the response (regression guard). |
| `tests/test_web_app.py` | (existing) `test_index_filter_row_includes_search_and_kickoff` | If this test asserts on the old custom-hours input id, update it to the new id. Otherwise leave alone. |

No JS-level tests — the client-side filter is exercised manually in the smoke check. The existing pytest layer guards the markup contract; the filter behaviour is verified visually.

## Out of scope

- Date range selection (start + end).
- Preset "Today" / "Tomorrow" shortcut chips.
- Server-side filtering by date (events are returned unfiltered by `/events`; the client narrows).
- Switching the relative chips from "next N hours" to "rolling N-hour window starting at X" — relative chips behaviour is unchanged.
- Multi-sport (still single sport: soccer).
- Mobile-specific layout tweaks (sub-project E).
