# Card grid alignment + per-market collapse — design

**Status:** approved 2026-05-22
**Touches:** `src/odds_scraper/web/app.py`, `_event_card.html`, `event_detail.html`, `app.css`, `app.js`, `tests/test_web_app.py`.
**Untouched:** anything outside `web/`; `queries.py` unchanged.

## Motivation

Two coupled UX problems on the detail and home pages:

1. **Column headers don't line up with their data.** Both the home-page card markets-grid (`MARKET · OUTCOME · BP · SB · B9J · BW`) and the detail-page history table have headers that visually drift from the columns underneath. This is "the worst" UI pain in the current build.

2. **No way to collapse individual market families inside a card.** Today every card unconditionally shows all three 1x2 family rows (ft, 1up, 2up) plus one big collapsible chunk for the parameterized markets. The user can't say "I only care about 1x2 — Full Time and 1x2 — 1 Up, hide everything else by default."

This sub-project fixes both in one cohesive pass since they touch the same files (card template + CSS).

## Settled inputs

| Decision | Value |
|---|---|
| Card layout primitive | CSS grid (replaces today's flex). `.col-header` and every `.row` share the same `grid-template-columns`. |
| Grid template | `minmax(180px, 1fr) repeat(4, 80px)` — outcome column flexes; four bookmaker columns are fixed-width and identical. |
| Per-market toggle UX | Click the existing `.group-label` to toggle that group's rows. No new icons; just make the label itself clickable with a chevron rotation. |
| Group key for localStorage | Non-parameterized: `canonical_id` (e.g., `1x2_ft`). Parameterized: `{canonical_id}_{line}` (e.g., `next_goal_ft_1.0`, `over_under_ft_2.5`). Stable across cards and reloads. |
| localStorage key | `card_market_collapse`. Shape `{group_key: true}` — true = collapsed; absent = use default. |
| Default expanded groups (empty localStorage) | `1x2_ft` AND `1x2_1up_ft`. Everything else collapsed by default. |
| Detail-page default market | `1x2_1up_ft` (was `1x2_2up_ft`). |
| Bottom "Show N more markets" button | **Retired.** Per-group toggles replace it. `is_extra` flag on `MarketGroup` also retired. |
| History table alignment fix | `text-align: center` on `thead th`; matched `min-width` on side-cell `<th>` and `<td>`; centered cell content. |
| Empty-bookmaker columns | Always reserved (header + cells render even when no priced data — empty cells show em-dash today). The bookmaker-chip filter continues to `display: none` the column when off. |
| Animation | None. Instant show/hide via `display: none`. |

## Architecture

### Card layout (`_event_card.html` + `app.css`)

Replace the existing flex spans with grid rows. Each `.row` and the `.col-header` element gets `display: grid` and the same `grid-template-columns` value. The CSS rule attaches to `.row` and `.col-header` together so they stay in lockstep — adding a new column in the future means one CSS edit.

When a bookmaker chip is toggled off, the body class `body.hide-betpawa` already exists; its CSS rule `[data-bookmaker="betpawa"] { display: none }` causes that grid cell to drop out of the layout, and `grid-template-columns: minmax(180px, 1fr) repeat(4, 80px)` keeps producing 4 explicit tracks. Some tracks render empty when their cells are hidden — that's the explicit "always reserve the column" decision, and it keeps the remaining headers locked over their data.

### Per-market collapse mechanic

Each `.market-block` in the card gets `data-group-key="…"` carrying the stable key described above. The `.group-label` `<div>` becomes clickable. A new JS function `initMarketCollapse()` reads `localStorage["card_market_collapse"]` (shape `{group_key: bool}`), applies a `.collapsed` class to matching `.market-block` elements, and re-applies after every htmx swap (same pattern as `applyExpandedState` today).

CSS:

```css
.market-block.collapsed .row { display: none; }
.market-block .group-label { cursor: pointer; user-select: none; }
.market-block.collapsed .group-label::before { content: "▸ "; }
.market-block:not(.collapsed) .group-label::before { content: "▾ "; }
```

(The existing `▾` prefix in the Jinja `<div class="group-label">` text is removed; the CSS pseudo-element produces it now, rotating on collapse.)

**Default-state allowlist** is a tiny JS constant:

```javascript
const EXPANDED_BY_DEFAULT = new Set(["1x2_ft", "1x2_1up_ft"]);
```

When applying state, if `card_market_collapse[group_key]` is `undefined`, the group is collapsed unless its key is in the allowlist.

### MarketGroup data model

`MarketGroup` loses its `is_extra` field. `_build_event_view` no longer marks any group as extra (no `is_extra=True` and no `is_extra=False` — the field is gone). All groups are uniform and individually toggleable.

`MarketGroup` gains `group_key: str` instead — built from `(canonical_id, line)`:

```python
@dataclass
class MarketGroup:
    label: str          # e.g., "1x2 — Full Time"
    group_key: str      # e.g., "1x2_ft" or "next_goal_ft_1.0"
    rows: list[OutcomeRow]
```

For the 1x2 family, `group_key = canonical_id`. For parameterized markets, `group_key = f"{canonical_id}_{line}"`. Stable, unique within a card, globally meaningful across all cards.

### Detail-page default market

One constant edit in `app.py`:

```python
_DEFAULT_MARKET_SLUG = "1x2_1up_ft"
```

### History table alignment fix

The history `<table>` keeps its structure. CSS additions:

```css
.history-table thead th { text-align: center; }
.history-table th.side-h,
.history-table td.num { min-width: 56px; }
.history-table td.num { text-align: center; }
```

The bookmaker `<th colspan="2|3">` text centers within its colspan area; the sub-head `<th>` and `<td>` share a fixed min-width so the colspan visually aligns over its columns.

## Tests

| File | Test | Change |
|---|---|---|
| `tests/test_web_app.py` | `test_events_card_skips_ou_groups_when_no_data` | Drop any `is_extra` assertion; keep the "label absent" check. |
| `tests/test_web_app.py` | `test_events_card_has_expand_toggle_when_ou_present` | Replace: assert each rendered market block has `data-group-key="…"` instead of asserting a "Show N more markets" button exists. |
| `tests/test_web_app.py` | `test_events_card_expander_button_label_updates` | **Delete.** Bottom button gone. |
| `tests/test_web_app.py` | (new) `test_events_card_emits_group_key_per_market` | Assert `data-group-key="1x2_ft"`, `data-group-key="1x2_1up_ft"`, `data-group-key="1x2_2up_ft"` present. With OU data: assert `data-group-key="over_under_ft_2.5"`. |
| `tests/test_web_app.py` | (new) `test_events_card_no_is_extra_marker` | Assert `class="market-extra"` no longer appears in any card markup (regression guard for the retired flag). |
| `tests/test_web_app.py` | (new) `test_event_detail_default_market_is_1up` | GET `/events/E1` (no `?market=`); assert `1x2 — 1 Up` chip carries `active` class, `1x2 — 2 Up` does not. |
| `tests/test_web_app.py` | Existing `test_event_detail_renders_default_market` | Update `"1x2 — 2 Up" in r.text` assertion → `"1x2 — 1 Up" in r.text`. |
| `tests/test_web_app.py` | (new) `test_history_table_has_centered_headers_css_hook` | Assert the rendered HTML contains a `<table class="history-table">` element (CSS is verified separately by visual smoke). |

No new collector / writer / models tests. No `queries.py` changes.

## Out of scope

- Animated collapse transition.
- Goal-event row highlighting in history.
- Master "Expand all / collapse all" toggle.
- Hiding empty bookmaker columns globally (decision: always reserve).
- Mobile responsiveness (sub-project E).
- Date-picker kickoff filter (sub-project B).
