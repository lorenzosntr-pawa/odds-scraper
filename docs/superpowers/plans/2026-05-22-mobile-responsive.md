# Mobile responsive layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the home page and detail page usable on phone portraits (320–430px) by locking primary columns and horizontally scrolling the rest, stacking the filter row vertically, and enforcing 32px touch targets. Desktop layout unchanged.

**Architecture:** One small HTML wrapper (`<div class="card-grid">`) lets the event card become a horizontal scroll container on phone. All phone-specific rules live in a single `@media (max-width: 640px) { … }` block at the end of `app.css`. `position: sticky` plus `left:` offsets keep designated columns anchored as the rest scrolls beneath.

**Tech Stack:** HTML/CSS only — no Python, no JS, no template logic. Jinja2 wrapper div, native CSS sticky positioning.

**Spec reference:** `docs/superpowers/specs/2026-05-22-mobile-responsive-design.md`

**Branch:** `feat/mobile-responsive` (already checked out; spec committed as `b733093` + `64cf20a`).

---

## File map

| Action | File | Responsibility |
|---|---|---|
| Modify | `src/odds_scraper/web/templates/_event_card.html` | Wrap `.col-header` + market-blocks in a `<div class="card-grid">` (event title `<a class="ev">` stays outside). |
| Modify | `src/odds_scraper/web/static/app.css` | Append one `@media (max-width: 640px) { … }` block with card scroll, history scroll, filter row stack, and touch-target rules. |
| Modify | `tests/test_web_app.py` | One new markup test confirming the `.card-grid` wrapper exists in the right spot. |

---

## Task 1: HTML wrapper + mobile media query

**Files:**
- Modify: `src/odds_scraper/web/templates/_event_card.html`
- Modify: `src/odds_scraper/web/static/app.css`
- Modify: `tests/test_web_app.py`

### Step 1.1 — Write the failing test

Append to `tests/test_web_app.py`:

```python
def test_events_card_wraps_grid_in_card_grid_div(client: TestClient):
    """The event card wraps its column header + market blocks in a single
    .card-grid div so the phone media query can make ONE scroll container
    per card. The event-title link (.ev) stays OUTSIDE the wrapper so it
    never scrolls. This test guards the HTML contract the mobile CSS
    depends on."""
    r = client.get("/events?status=upcoming")
    body = r.text
    assert '<div class="card-grid">' in body
    # The event-title anchor must appear BEFORE the card-grid wrapper so it
    # stays put above the horizontal scroll area on phone.
    ev_pos = body.find('<a class="ev"')
    grid_pos = body.find('<div class="card-grid"')
    assert ev_pos != -1 and grid_pos != -1, "both elements must exist"
    assert ev_pos < grid_pos, "<a class='ev'> must precede <div class='card-grid'>"
```

Run it to confirm failure:
`.venv\Scripts\python.exe -m pytest tests/test_web_app.py::test_events_card_wraps_grid_in_card_grid_div -v`
Expected: FAIL (the wrapper doesn't exist yet).

### Step 1.2 — Add the `<div class="card-grid">` wrapper

Edit `src/odds_scraper/web/templates/_event_card.html`. The current top-level structure is:

```jinja
<div class="card"
     data-event-id="{{ event.id }}"
     data-event-name="{{ (event.home + ' ' + event.away)|lower }}"
     data-kickoff-utc="{{ event.kickoff_utc }}">
  <a class="ev" href="/events/{{ event.id }}">
    <div>
      <div class="ev-name">…</div>
      <div class="ev-meta">…</div>
    </div>
  </a>

  <div class="col-header">
    …
  </div>

  {% for group in event.market_groups %}
    <div class="market-block" data-group-key="{{ group.group_key }}">
      …
    </div>
  {% endfor %}
</div>
```

Wrap the `<div class="col-header">` AND the `{% for group … %}` loop in a single new `<div class="card-grid">`. The closing `</div>` of `card-grid` must go BEFORE the closing `</div>` of `.card`. Final structure:

```jinja
<div class="card"
     data-event-id="{{ event.id }}"
     data-event-name="{{ (event.home + ' ' + event.away)|lower }}"
     data-kickoff-utc="{{ event.kickoff_utc }}">
  <a class="ev" href="/events/{{ event.id }}">
    <div>
      <div class="ev-name">…</div>
      <div class="ev-meta">…</div>
    </div>
  </a>

  <div class="card-grid">
    <div class="col-header">
      …
    </div>

    {% for group in event.market_groups %}
      <div class="market-block" data-group-key="{{ group.group_key }}">
        …
      </div>
    {% endfor %}
  </div>
</div>
```

Do not change the inner contents of `.col-header` or any `.market-block`. Indentation of those blocks shifts by 2 spaces — preserve trailing-whitespace consistency.

### Step 1.3 — Append the mobile media query to `app.css`

Edit `src/odds_scraper/web/static/app.css`. Append this block at the END of the file (after every existing rule):

```css
/* ============================================================= */
/* Phone portrait — 320–430px wide, with regression band to 640px */
/* ============================================================= */
@media (max-width: 640px) {

  /* ----- Event card: lock outcome + BetPawa, scroll SB/B9J/BW ----- */
  .card-grid {
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
  }
  .col-header,
  .market-block .row {
    min-width: 480px;
  }
  .col-header > .lbl:first-child,
  .market-block .row > .outcome {
    position: sticky;
    left: 0;
    background: #0a0a0a;
    z-index: 2;
  }
  .col-header > .lbl[data-bookmaker="betpawa"],
  .market-block .row > span[data-bookmaker="betpawa"] {
    position: sticky;
    left: 180px;
    background: #0a0a0a;
    z-index: 2;
  }

  /* ----- Detail-page history table: lock TIME + STATE + BP ----- */
  .history-wrap {
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
  }
  .history-table {
    min-width: 720px;
  }
  .history-table th.ts-col,
  .history-table td.ts-col {
    width: 150px;
    min-width: 150px;
    position: sticky;
    left: 0;
    background: #0a0a0a;
    z-index: 2;
  }
  .history-table th.state-col,
  .history-table td.state-col {
    width: 80px;
    min-width: 80px;
    position: sticky;
    left: 150px;
    background: #0a0a0a;
    z-index: 2;
  }
  .history-table [data-bookmaker="betpawa"] {
    position: sticky;
    left: 230px;
    background: #0a0a0a;
    z-index: 2;
  }

  /* ----- Filter row: stack groups vertically ----- */
  .filter-row {
    flex-direction: column;
    align-items: stretch;
    gap: 10px;
  }
  .filter-group {
    flex-wrap: wrap;
  }
  .filter-group.filter-search {
    max-width: none;
  }
  .filter-select {
    flex: 1;
  }

  /* ----- Touch targets: 32px minimum for chips, pills, inputs ----- */
  .chip {
    min-height: 32px;
    padding: 6px 10px;
    font-size: 12px;
  }
  .family-pill,
  .line-pill,
  .pill {
    min-height: 32px;
    padding: 6px 10px;
    font-size: 12px;
  }
  .filter-select,
  #kickoff-date {
    min-height: 32px;
    padding: 6px 8px;
    font-size: 12px;
  }
  .group-label {
    padding: 8px 12px;
    font-size: 10px;
  }
}
```

(Insert the block verbatim. No other rule in `app.css` is touched.)

### Step 1.4 — Run the test, see it pass

`.venv\Scripts\python.exe -m pytest tests/test_web_app.py::test_events_card_wraps_grid_in_card_grid_div -v`
Expected: PASS.

### Step 1.5 — Run full web suite

`.venv\Scripts\python.exe -m pytest tests/test_web_app.py tests/test_web_queries.py -v`
Expected: every test passes. Any pre-existing test that asserted on `<a class="ev"…</a><div class="col-header"` adjacency would fail — none should, but if one does, update its assertion to allow the new `<div class="card-grid">` between them.

### Step 1.6 — Run full top-level suite

`.venv\Scripts\python.exe -m pytest -q`
Expected: every test passes.

### Step 1.7 — Commit

```bash
git add src/odds_scraper/web/templates/_event_card.html src/odds_scraper/web/static/app.css tests/test_web_app.py
git commit -m "$(cat <<'EOF'
feat(web/mobile): phone portrait layout - lock-and-scroll columns

One @media (max-width: 640px) block holds every phone-specific rule.
Event cards get a new <div class="card-grid"> wrapper so the column
header + market blocks share one horizontal scroll container; the
outcome label and BetPawa column stay sticky at the left edge while
SB/B9J/BW scroll under the user's swipe.

Detail-page history table follows the same pattern: TIME, STATE and
the BetPawa column-group are sticky-pinned (with explicit widths so
offsets are deterministic); the other three bookmakers scroll.

Filter row stacks each filter group vertically on phone. Touch targets
on chips, pills, dropdowns and the date input land at 32px minimum.
Desktop layout (>= 640px) is untouched.
EOF
)"
```

## Before You Begin

Ask if anything is unclear; otherwise proceed.

## Self-Review

- `<div class="card-grid">` wraps `.col-header` AND every `.market-block`?
- `<a class="ev">` stays OUTSIDE `.card-grid` (event title doesn't scroll)?
- The `@media (max-width: 640px)` block is appended AT THE END of `app.css` (not interleaved with existing rules)?
- Card sticky offsets: outcome `left: 0`, BetPawa `left: 180px`?
- History sticky offsets: TIME `left: 0`, STATE `left: 150px`, BetPawa `left: 230px`?
- TIME column gets `width: 150px; min-width: 150px;`?
- STATE column gets `width: 80px; min-width: 80px;`?
- Sticky cells get `background: #0a0a0a` AND `z-index: 2`?
- All touch-target rules use `min-height: 32px` (not exact height)?
- Single commit covering 3 files?
- Full pytest suite green?

## Report Format

- **Status:** DONE | DONE_WITH_CONCERNS | BLOCKED | NEEDS_CONTEXT
- What you implemented
- Test results
- File changed + commit SHA
- Self-review findings

---

## Task 2: Full-suite smoke + manual phone visual check

**Files:** none modified; verification only.

### Step 2.1 — Run all tests

`.venv\Scripts\python.exe -m pytest -q`
Expected: every test passes.

### Step 2.2 — Manual phone visual check

Start the web app:

```powershell
.venv\Scripts\python.exe -m odds_scraper.web --db data\odds.db --port 8000
```

Open `http://localhost:8000/`. In Chrome/Firefox DevTools, switch to a phone viewport (e.g., iPhone SE = 375×667 or Pixel 7 = 412×915):

- The filter row stacks vertically — Country/League, Bookmakers, Kickoff, Search each on their own row.
- The kickoff date input is 32px tall, full-width inside its filter-group.
- Event cards show the event title at top (not scrolling). Below, you see `OUTCOME · BetPawa` columns pinned on the left; horizontally swipe to reveal SB/B9J/BW.
- Group labels are full-width, clickable, and at least 32px tall.
- Click into a detail page. The history table shows `TIME · STATE · BetPawa(H/D/A)` pinned, horizontal scroll for SB/B9J/BW.
- Touch targets (family pills, line pills, chips) all feel finger-friendly.

Optional real-device check on a phone if available — paste the local IP URL into the phone's browser.

### Step 2.3 — Desktop regression check

Switch DevTools back to desktop viewport (≥ 1024px wide). Verify:
- Filter row is back to a single horizontal row.
- Card layout is the unchanged flex grid (no horizontal scroll, no sticky cells).
- History table renders with no horizontal scroll (all columns fit).
- Chips and pills are the original tighter size.

### Step 2.4 — Commit any straggler fixes

If anything needed adjustment during smoke, commit it with an appropriate `fix(...)` message. Otherwise no commit needed.

---

## Self-review

**Spec coverage:**
- `<div class="card-grid">` wrapper added → Task 1.2.
- Single `@media (max-width: 640px)` block in `app.css` → Task 1.3.
- Card sticky-and-scroll (outcome + BP locked) → Task 1.3 rules.
- History sticky-and-scroll (TIME + STATE + BP locked) → Task 1.3 rules + explicit width pins.
- Filter row stacks vertically → Task 1.3 rules.
- Touch targets 32px minimum → Task 1.3 rules.
- HTML test for wrapper presence + ordering → Task 1.1.
- Manual phone visual check + desktop regression → Task 2.

**Placeholder scan:** no "TBD" / "implement later" / "etc." — every step has full code or commands with expected output.

**Type consistency:**
- CSS class names (`.card-grid`, `.col-header`, `.market-block`, `.row`, `.history-wrap`, `.history-table`, `.ts-col`, `.state-col`, `[data-bookmaker="betpawa"]`, `.chip`, `.family-pill`, `.line-pill`, `.pill`, `.filter-select`, `#kickoff-date`, `.group-label`) match the existing template emissions.
- Sticky `left:` offsets are consistent across the spec, plan, and CSS block: card outcome=0/BP=180; history TIME=0/STATE=150/BP=230.
- Background colour `#0a0a0a` matches the existing card and table backgrounds in `app.css`.
