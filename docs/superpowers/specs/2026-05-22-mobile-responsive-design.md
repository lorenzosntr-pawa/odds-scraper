# Mobile responsive layout — design

**Status:** approved 2026-05-22
**Touches:** `src/odds_scraper/web/templates/_event_card.html`, `src/odds_scraper/web/static/app.css`, `tests/test_web_app.py`.
**Untouched:** Python (no backend), JS (no behaviour change), other templates.

## Motivation

The web UX renders fine on desktop but breaks on phone portraits (320–430px). The event cards' flex row is ~500px wide (180px outcome + 4×80px bookmaker cells) so it overflows the viewport; the history table is even wider (10–14 columns). Filter chips and pills are sized for mouse, not finger tips. The user does live-odds analysis on the move and needs the UI to work on a phone.

This sub-project lands the mobile-specific layout in a single `@media (max-width: 640px)` block. Desktop layout is unchanged.

## Settled inputs

| Decision | Value |
|---|---|
| Device targets in scope | Phone portrait (320–430px wide). Desktop (≥ 1024px) gets a regression check only. |
| Out-of-scope sizes | Phone landscape / small tablet (640–768px), tablet portrait (768–1024px) — they may benefit but are not explicit targets. |
| Breakpoint | `@media (max-width: 640px)` is the single boundary. One media-query block in `app.css` holds every phone-specific rule. |
| Card layout on phone | Lock outcome label + BetPawa column at the left; SportyBet/Bet9ja/Betway scroll horizontally. |
| History table on phone | Lock TIME + STATE + BetPawa (all three sticky columns); SB/B9J/BW scroll. |
| Filter row on phone | Stack each `.filter-group` vertically (column flex). Chip rows wrap inside their group. |
| Touch targets | Minimum 32px height on phone for chips, pills, dropdowns, the date input, and the per-market-collapse `.group-label`. Desktop sizes unchanged. |
| BetPawa hidden via chip filter | Sticky position falls off (only BP gets `left: 180px`). User has no left anchor; they swipe freely among the remaining 3 columns. Acceptable for v1. |

## Architecture

### Single new HTML wrapper

`_event_card.html` currently structures each card as:

```jinja
<div class="card">
  <a class="ev">…</a>                          {# event title row #}
  <div class="col-header">…</div>              {# column headers #}
  {% for group in event.market_groups %}
    <div class="market-block" …>…</div>
  {% endfor %}
</div>
```

Add a wrapper `<div class="card-grid">` around `.col-header` + the market-blocks (event title row stays outside so it never scrolls):

```jinja
<div class="card">
  <a class="ev">…</a>
  <div class="card-grid">
    <div class="col-header">…</div>
    {% for group in event.market_groups %}
      <div class="market-block" …>…</div>
    {% endfor %}
  </div>
</div>
```

`.card-grid` has no styling at desktop widths (zero CSS rules without the media query) — pure transparent wrapper that lights up only on phone.

### CSS — one media-query block

All phone-specific rules live in a single `@media (max-width: 640px) { … }` block at the end of `app.css`. The block:

```css
@media (max-width: 640px) {

  /* ------------------------------------------------------------- */
  /* Event card — lock outcome + BP, horizontal scroll for SB/B9J/BW */
  /* ------------------------------------------------------------- */
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

  /* ------------------------------------------------------------- */
  /* Detail-page history table — lock TIME + STATE + BP */
  /* ------------------------------------------------------------- */
  .history-wrap {
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
  }
  .history-table {
    min-width: 720px;
  }
  /* Pin explicit widths on the locked columns so sticky left offsets are
     predictable. (Desktop layout uses these widths as min-only; phone
     forces them exact via the media query block.) */
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
    left: 230px;  /* TIME (150) + STATE (80) */
    background: #0a0a0a;
    z-index: 2;
  }

  /* ------------------------------------------------------------- */
  /* Filter row — stack groups vertically */
  /* ------------------------------------------------------------- */
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

  /* ------------------------------------------------------------- */
  /* Touch targets — tighten chips, pills, inputs, group labels */
  /* ------------------------------------------------------------- */
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

### Why sticky + horizontal scroll works

The key insight: `.card-grid` is a flex/block container with `overflow-x: auto` on phone. Inside, each row (`.col-header`, `.market-block .row`) has `min-width: 480px`, which is wider than the viewport on phone. The browser produces horizontal scroll for the container. Cells with `position: sticky; left: 0;` (and `left: 180px;` for BP) stay anchored to the left edge while the rest scrolls beneath them.

The header row and data rows scroll together because they're all children of the same `.card-grid` container. Each row independently has the same sticky-cells configuration, so the visual lock is consistent across rows.

The same pattern applies to `.history-wrap` containing `<table class="history-table">`. `<th>` and `<td>` cells with `position: sticky` are supported in modern table rendering (Chrome/Safari/Firefox/Edge — iOS 13+ and Android 8+).

### Backgrounds on sticky cells

Each sticky cell gets `background: #0a0a0a` matching the existing card and table background. Without this, the cells underneath (which are scrolling) would show through the sticky cell. `z-index: 2` keeps them on top of any default stacking.

## Tests

| File | Test | Change |
|---|---|---|
| `tests/test_web_app.py` | (new) `test_events_card_wraps_grid_in_card_grid_div` | Hit `/events?status=upcoming`; assert `<div class="card-grid">` exists inside each card; assert `<a class="ev"` is BEFORE `<div class="card-grid"` (header stays outside the scroll container). |

CSS-only behavior (sticky positioning, media queries, layout reflow) is not unit-testable through the FastAPI client. The single markup test guards the HTML wrapper contract. Visual verification at narrow widths is the smoke step.

## Out of scope

- Phone landscape / tablet-specific overrides.
- JS to dynamically re-anchor the leftmost-visible bookmaker when BP is chip-toggled off.
- Loading states, skeletons, spinners.
- Accessibility audit beyond the 32px touch-target minimum.
- Dark-mode toggle / theming.
- PWA / installable / offline.
- New colour palette or icon set.
- Server-side responsive rendering (always sends the same HTML; CSS adapts).
