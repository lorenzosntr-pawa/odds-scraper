# New markets (next_goal + per-team Over/Under) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `MARKET_MANIFEST` with three new markets that bookieskit 0.14.0 ships out of the box — `next_goal_ft`, `home_over_under_ft`, `away_over_under_ft` — so the scraper starts collecting their prices.

**Architecture:** Single source-of-truth edit. `MARKET_MANIFEST` in `models.py` gains three new `MarketSpec` entries. Everything else (collector extraction loop, writer SQL, watcher tick-log denominator, SQLite schema) iterates the manifest and adapts automatically. Tests that assert on derived counts get their numbers updated.

**Tech Stack:** Python 3.11+, bookieskit 0.14.0 (already installed from commit 360f24d), pytest with pytest-asyncio (auto mode).

**Spec reference:** `docs/superpowers/specs/2026-05-21-new-markets-next-goal-team-ou-design.md`

---

## File map

| Action | File | Responsibility |
|---|---|---|
| Modify | `src/odds_scraper/models.py` | `MARKET_MANIFEST` gains 3 `MarketSpec` entries. No other code change in the file. |
| Modify | `tests/test_models.py` | Update existing header-count assertions (68 → 170). Extend `test_build_csv_header_price_section_order` to verify new sections. Rename `test_build_csv_header_has_68_columns` so the literal column count isn't baked into the test name. |
| Modify | `tests/test_watcher.py` | Update `test_log_tick_summary_format`: denominators go from `54/54/27/27` to `156/156/78/78`. |
| Modify | `tests/test_collector.py` | Add coverage for the three new markets — extraction succeeds, out-of-manifest lines are silently dropped. |
| Unchanged | All other source files (`db_schema.py`, `collector.py`, `writer.py`, `watcher.py`, etc.) | The manifest-as-source-of-truth pattern means no code changes here. |

Task ordering puts the manifest edit and its dependent test updates in one cohesive commit per cluster: Task 1 covers models + test_models together, Task 2 covers test_watcher, Task 3 covers test_collector, Task 4 is the smoke run.

---

## Task 1: Extend `MARKET_MANIFEST` + update test_models.py

**Files:**
- Modify: `src/odds_scraper/models.py`
- Modify: `tests/test_models.py`

The manifest edit and the column-count tests are tightly coupled — they go in one commit so HEAD never has a build that's red on these tests.

### Step 1.1 — Update test_models.py assertions in-place

- [ ] **Find and rename `test_build_csv_header_has_68_columns`** in `tests/test_models.py` to `test_build_csv_header_column_count`. Update its body to assert `len(header) == 170`. The complete replacement:

```python
def test_build_csv_header_column_count():
    # 14 meta columns + price columns from MARKET_MANIFEST:
    #   1x2_ft        3 sides × 2 (odds+prob) = 6
    #   1x2_1up_ft    3 × 2 = 6
    #   1x2_2up_ft    3 × 2 = 6
    #   over_under_ft 9 lines × 2 sides × 2 = 36
    #   next_goal_ft  9 lines × 3 sides × 2 = 54
    #   home_over_under_ft 6 lines × 2 sides × 2 = 24
    #   away_over_under_ft 6 lines × 2 sides × 2 = 24
    # 14 + 6+6+6+36+54+24+24 = 170
    header = build_csv_header()
    assert len(header) == 170
```

- [ ] **Update `test_snapshot_to_csv_row_meta_columns`** — find `assert len(row) == 68` and change to `assert len(row) == 170`.

- [ ] **Update `test_snapshot_to_csv_row_blanks_when_failure_status`** — same change, `assert len(row) == 68` → `assert len(row) == 170`.

- [ ] **Extend `test_build_csv_header_price_section_order`** — the existing assertions for header[14:20], [20:26], [26:32], [32:36] stay (they cover the 1x2 family and the first OU line). Update the final `header[-4:]` assertion and add three new spot-checks. The full new body of the test:

```python
def test_build_csv_header_price_section_order():
    header = build_csv_header()
    # 1x2_ft section
    assert header[14:20] == (
        "1x2_ft_home_odds", "1x2_ft_home_prob",
        "1x2_ft_draw_odds", "1x2_ft_draw_prob",
        "1x2_ft_away_odds", "1x2_ft_away_prob",
    )
    # 1x2_1up_ft section
    assert header[20:26] == (
        "1x2_1up_ft_home_odds", "1x2_1up_ft_home_prob",
        "1x2_1up_ft_draw_odds", "1x2_1up_ft_draw_prob",
        "1x2_1up_ft_away_odds", "1x2_1up_ft_away_prob",
    )
    # 1x2_2up_ft section
    assert header[26:32] == (
        "1x2_2up_ft_home_odds", "1x2_2up_ft_home_prob",
        "1x2_2up_ft_draw_odds", "1x2_2up_ft_draw_prob",
        "1x2_2up_ft_away_odds", "1x2_2up_ft_away_prob",
    )
    # over_under_ft starts at index 32 (first OU line 1.5)
    assert header[32:36] == (
        "ou_1.5_over_odds", "ou_1.5_over_prob",
        "ou_1.5_under_odds", "ou_1.5_under_prob",
    )
    # over_under_ft ends at index 68 (last OU line 9.5 has 4 cells)
    assert header[64:68] == (
        "ou_9.5_over_odds", "ou_9.5_over_prob",
        "ou_9.5_under_odds", "ou_9.5_under_prob",
    )
    # next_goal_ft starts at index 68 (first goal-number line 1.0)
    assert header[68:74] == (
        "ng_1.0_home_odds", "ng_1.0_home_prob",
        "ng_1.0_none_odds", "ng_1.0_none_prob",
        "ng_1.0_away_odds", "ng_1.0_away_prob",
    )
    # home_over_under_ft starts at index 122 (first line 0.5)
    assert header[122:126] == (
        "ou_home_0.5_over_odds", "ou_home_0.5_over_prob",
        "ou_home_0.5_under_odds", "ou_home_0.5_under_prob",
    )
    # away_over_under_ft starts at index 146 (first line 0.5)
    assert header[146:150] == (
        "ou_away_0.5_over_odds", "ou_away_0.5_over_prob",
        "ou_away_0.5_under_odds", "ou_away_0.5_under_prob",
    )
    # Last four columns: away_over_under_ft line 5.5 over/under
    assert header[-4:] == (
        "ou_away_5.5_under_odds", "ou_away_5.5_under_prob",
        "ou_away_5.5_under_odds", "ou_away_5.5_under_prob",
    )
```

Wait — the last assertion above repeats "under" twice. The over_under_ft pattern goes `over, under` for each line. So for `ou_away_5.5`, the cells are `over_odds, over_prob, under_odds, under_prob` in that order. The last 4 columns of the header are the `under_odds, under_prob` of the `5.5` line... but that's only 2 cells. Hmm. Let me recompute.

Each parameterized line produces `len(sides) * 2` cells (each side has odds+prob columns). For `ou_away`, sides = `("over", "under")` → 4 cells per line:
1. `ou_away_5.5_over_odds`
2. `ou_away_5.5_over_prob`
3. `ou_away_5.5_under_odds`
4. `ou_away_5.5_under_prob`

So `header[-4:]` IS the 5.5 line's full 4 cells. The correct assertion:

```python
    # Last four columns: away_over_under_ft line 5.5 (the manifest's final line)
    assert header[-4:] == (
        "ou_away_5.5_over_odds", "ou_away_5.5_over_prob",
        "ou_away_5.5_under_odds", "ou_away_5.5_under_prob",
    )
```

Use that (correct) version in the final test, not the duplicate version above.

- [ ] **Run the model tests — confirm they now FAIL**

Run: `.venv/Scripts/python.exe -m pytest tests/test_models.py -v; echo "exit=$?"`
Expected: the updated assertions fail because MARKET_MANIFEST hasn't been extended yet (column count is still 68, sections beyond OU don't exist). `exit=1`.

### Step 1.2 — Extend MARKET_MANIFEST

- [ ] **Edit `src/odds_scraper/models.py`** — find the existing `MARKET_MANIFEST` constant. Append three new `MarketSpec` entries before the closing parenthesis. The full new constant:

```python
MARKET_MANIFEST: tuple[MarketSpec, ...] = (
    MarketSpec("1x2_ft",        "1x2_ft",      ("home", "draw", "away"), None),
    MarketSpec("1x2_1up_ft",    "1x2_1up_ft",  ("home", "draw", "away"), None),
    MarketSpec("1x2_2up_ft",    "1x2_2up_ft",  ("home", "draw", "away"), None),
    MarketSpec(
        # column_prefix shortened to "ou"; must remain unique across manifest
        "over_under_ft", "ou", ("over", "under"),
        (1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5, 8.5, 9.5),
    ),
    # Next-goal — 3-way (home / none / away). `line` is the goal number
    # (prematch = 1; live shifts up as goals score). Bookieskit's outcome
    # mapping uses "none" (not "draw") for the no-more-goals outcome.
    MarketSpec(
        "next_goal_ft", "ng", ("home", "none", "away"),
        (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0),
    ),
    # Per-team Over/Under — separate markets per team in bookieskit.
    MarketSpec(
        "home_over_under_ft", "ou_home", ("over", "under"),
        (0.5, 1.5, 2.5, 3.5, 4.5, 5.5),
    ),
    MarketSpec(
        "away_over_under_ft", "ou_away", ("over", "under"),
        (0.5, 1.5, 2.5, 3.5, 4.5, 5.5),
    ),
)
```

No other change in `models.py`.

### Step 1.3 — Run model tests + full suite

- [ ] **Run model tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_models.py -v; echo "exit=$?"`
Expected: every test in `test_models.py` passes. `exit=0`.

- [ ] **Run full suite — expect SOME failures in test_watcher.py and test_collector.py**

Run: `.venv/Scripts/python.exe -m pytest -q; echo "exit=$?"`
Expected: a few tests in `test_watcher.py` and `test_collector.py` may fail because the manifest grew but their assertions still expect the old denominators / coverage. These will be fixed in Tasks 2 and 3. Note the failures; they're expected at this checkpoint.

- [ ] **Commit**

```bash
git add src/odds_scraper/models.py tests/test_models.py
git commit -m "$(cat <<'EOF'
feat(manifest): add next_goal_ft + per-team Over/Under markets

MARKET_MANIFEST gains three entries that bookieskit 0.14.0 ships
mappings for across all 4 bookmakers:

  next_goal_ft         9 goal-number lines × (home/none/away)
  home_over_under_ft   6 lines × (over/under)
  away_over_under_ft   6 lines × (over/under)

Everything that iterates MARKET_MANIFEST (collector extraction,
SQLite write, watcher tick-log denominator) picks up the new
markets automatically — no other source change needed.

CSV-header column total grows from 68 to 170. Existing tests in
test_models.py updated to match; test_build_csv_header_has_68_columns
renamed to test_build_csv_header_column_count so the literal count
isn't baked into the test name.

test_watcher.py and test_collector.py still need their numbers
updated; that lands in the next commits.
EOF
)"
```

---

## Task 2: Update watcher tick-log test for new denominators

**Files:**
- Modify: `tests/test_watcher.py`

### Step 2.1 — Update the expected log string

- [ ] **Open `tests/test_watcher.py`** and find `test_log_tick_summary_format`. The current expected line is:

```python
expected = "tick 33660318 status=STARTED bp=4/54 sb=2/54 b9j=1/27 bw=3/27"
```

The denominators are derived from `_price_cell_count` in `watcher.py`, which iterates `MARKET_MANIFEST`. After the manifest extension:
- BP/SB (probability bookmakers): 78 outcomes × 2 cells = **156**
- B9J/BW (odds-only): 78 outcomes × 1 cell = **78**

The numerator part (`4`, `2`, `1`, `3`) stays the same because the fixture's `rows` only populate prices for the `1x2_ft` `home` outcome — neither the numerator counts nor the underlying row data change with the manifest growth.

- [ ] **Update the expected line and its preceding math comment.** The block in `test_log_tick_summary_format` currently reads:

```python
    # Denominators come from MARKET_MANIFEST via _price_cell_count:
    #   27 outcomes (3 simple × 3 sides + 9 O/U lines × 2 sides);
    #   ×2 cells for BP/SB (odds+prob), ×1 for B9J/BW (odds only).
    # If MARKET_MANIFEST changes, update both this test and the watcher's
    # _price_cell_count consumers together.
    expected = "tick 33660318 status=STARTED bp=4/54 sb=2/54 b9j=1/27 bw=3/27"
```

Replace with:

```python
    # Denominators come from MARKET_MANIFEST via _price_cell_count:
    #   78 outcomes:
    #     3 simple × 3 sides            = 9
    #     9 over_under_ft × 2 sides     = 18
    #     9 next_goal_ft × 3 sides      = 27
    #     6 home_over_under_ft × 2      = 12
    #     6 away_over_under_ft × 2      = 12
    #   ×2 cells for BP/SB (odds+prob), ×1 for B9J/BW (odds only).
    # If MARKET_MANIFEST changes, update both this test and the watcher's
    # _price_cell_count consumers together.
    expected = "tick 33660318 status=STARTED bp=4/156 sb=2/156 b9j=1/78 bw=3/78"
```

### Step 2.2 — Run watcher tests

- [ ] **Run watcher tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_watcher.py -v; echo "exit=$?"`
Expected: all 5 watcher tests pass. `exit=0`.

### Step 2.3 — Commit

- [ ] **Commit**

```bash
git add tests/test_watcher.py
git commit -m "$(cat <<'EOF'
test(watcher): update tick-log denominators for new markets

_price_cell_count iterates MARKET_MANIFEST so the watcher's denominator
follows the manifest. After three new markets land (78 outcomes total
per bookmaker; was 27), BP/SB = 156 cells, B9J/BW = 78 cells. The
numerator part of the fixture is unchanged because the test only
populates 1x2_ft prices on its synthetic snapshots.
EOF
)"
```

---

## Task 3: Add collector tests for the new markets

**Files:**
- Modify: `tests/test_collector.py`

### Step 3.1 — Add tests covering the new markets

Append these tests to `tests/test_collector.py`. They reuse the existing `_O`, `_M`, `_PM`, `_bp_detail` stand-ins. (The `_PM` stand-in models a parameterized market with a `lines: {line_value: [outcomes]}` shape, mirroring bookieskit's `NormalizedMarket` for parameterized markets.)

```python
async def test_collector_extracts_next_goal_prices():
    next_goal_markets = [
        _PM("next_goal_ft", {
            1.0: {"home": (1.85, 0.54), "none": (8.5, 0.12), "away": (3.50, 0.29)},
            2.0: {"home": (2.20, 0.45), "none": (3.0, 0.33), "away": (3.90, 0.26)},
        }),
    ]
    coll = OddsCollector(
        fetchers={
            Bookmaker.BETPAWA: AsyncMock(return_value=next_goal_markets),
            Bookmaker.SPORTYBET: AsyncMock(return_value=next_goal_markets),
            Bookmaker.BET9JA: AsyncMock(return_value=next_goal_markets),
            Bookmaker.BETWAY: AsyncMock(return_value=next_goal_markets),
        },
    )
    rows = await coll.collect(
        _bp_detail(),
        resolved={Bookmaker.SPORTYBET: "sr:match:1",
                  Bookmaker.BET9JA: "b9j-7",
                  Bookmaker.BETWAY: "sr:match:1"},
        sr_id="sr:match:1", genius_id="",
    )
    bp = next(r for r in rows if r.bookmaker == Bookmaker.BETPAWA)
    assert bp.prices[PriceKey("next_goal_ft", 1.0, "home")] == (1.85, 0.54)
    assert bp.prices[PriceKey("next_goal_ft", 1.0, "none")] == (8.5, 0.12)
    assert bp.prices[PriceKey("next_goal_ft", 2.0, "away")] == (3.90, 0.26)


async def test_collector_extracts_per_team_over_under_prices():
    team_ou_markets = [
        _PM("home_over_under_ft", {
            0.5: {"over": (1.30, 0.74), "under": (3.50, 0.26)},
            1.5: {"over": (2.10, 0.46), "under": (1.70, 0.54)},
        }),
        _PM("away_over_under_ft", {
            0.5: {"over": (1.40, 0.69), "under": (3.00, 0.31)},
            1.5: {"over": (2.50, 0.40), "under": (1.55, 0.60)},
        }),
    ]
    coll = OddsCollector(
        fetchers={
            Bookmaker.BETPAWA: AsyncMock(return_value=team_ou_markets),
            Bookmaker.SPORTYBET: AsyncMock(return_value=team_ou_markets),
            Bookmaker.BET9JA: AsyncMock(return_value=team_ou_markets),
            Bookmaker.BETWAY: AsyncMock(return_value=team_ou_markets),
        },
    )
    rows = await coll.collect(
        _bp_detail(),
        resolved={Bookmaker.SPORTYBET: "sr:match:1",
                  Bookmaker.BET9JA: "b9j-7",
                  Bookmaker.BETWAY: "sr:match:1"},
        sr_id="sr:match:1", genius_id="",
    )
    bp = next(r for r in rows if r.bookmaker == Bookmaker.BETPAWA)
    assert bp.prices[PriceKey("home_over_under_ft", 0.5, "over")] == (1.30, 0.74)
    assert bp.prices[PriceKey("home_over_under_ft", 1.5, "under")] == (1.70, 0.54)
    assert bp.prices[PriceKey("away_over_under_ft", 0.5, "over")] == (1.40, 0.69)
    assert bp.prices[PriceKey("away_over_under_ft", 1.5, "under")] == (1.55, 0.60)


async def test_collector_ignores_next_goal_lines_above_manifest_cap():
    # next_goal_ft manifest covers (1..9). Lines beyond that should be
    # silently dropped, same behaviour as over_under_ft.
    out_of_range_markets = [
        _PM("next_goal_ft", {
            5.0: {"home": (1.85, 0.54), "none": (8.5, 0.12), "away": (3.50, 0.29)},
            10.0: {"home": (50.0, 0.02), "none": (100.0, 0.01), "away": (50.0, 0.02)},
        }),
    ]
    coll = OddsCollector(
        fetchers={
            Bookmaker.BETPAWA: AsyncMock(return_value=out_of_range_markets),
            Bookmaker.SPORTYBET: AsyncMock(return_value=out_of_range_markets),
            Bookmaker.BET9JA: AsyncMock(return_value=out_of_range_markets),
            Bookmaker.BETWAY: AsyncMock(return_value=out_of_range_markets),
        },
    )
    rows = await coll.collect(
        _bp_detail(),
        resolved={Bookmaker.SPORTYBET: "sr:match:1",
                  Bookmaker.BET9JA: "b9j-7",
                  Bookmaker.BETWAY: "sr:match:1"},
        sr_id="sr:match:1", genius_id="",
    )
    bp = next(r for r in rows if r.bookmaker == Bookmaker.BETPAWA)
    lines_seen = {k.line for k in bp.prices if k.market_id == "next_goal_ft"}
    assert lines_seen == {5.0}  # 10.0 dropped
```

### Step 3.2 — Run collector tests

- [ ] **Run collector tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_collector.py -v; echo "exit=$?"`
Expected: all existing collector tests still pass plus the 3 new tests pass. `exit=0`.

- [ ] **Run full suite — confirm everything green**

Run: `.venv/Scripts/python.exe -m pytest -q; echo "exit=$?"`
Expected: every test passes (manifest extension fully landed across models, watcher, collector). `exit=0`.

### Step 3.3 — Commit

- [ ] **Commit**

```bash
git add tests/test_collector.py
git commit -m "$(cat <<'EOF'
test(collector): cover next_goal_ft + per-team Over/Under extraction

Three new tests verify _extract_prices_for_manifest handles the new
markets:
- next_goal_ft prices land at the right PriceKey (line, side) tuples
- home_over_under_ft and away_over_under_ft both extract cleanly,
  separate markets per the bookieskit mapping
- next_goal_ft lines outside the manifest cap (e.g., line=10.0) are
  silently dropped — same behaviour as over_under_ft
EOF
)"
```

---

## Task 4: Full-suite smoke + live verification

**Files:** none modified; verification only.

### Step 4.1 — Run the full suite one more time

- [ ] **Run all tests**

Run: `.venv/Scripts/python.exe -m pytest -v; echo "exit=$?"`
Expected: every test passes. Count grows compared to pre-change by the 3 new collector tests; the 2 existing tests that previously failed at the intermediate Task 1 / Task 2 checkpoints now pass. `exit=0`.

### Step 4.2 — Verify the live DB ingests new market prices

This step requires the scraper to be running. If it is, restart it to pick up the new manifest:

```powershell
# In the scraper terminal: Ctrl+C, then
python -m odds_scraper.main --config config.yaml
```

Wait at least one tick for each event (~60–90 seconds for the first batch).

- [ ] **Check the DB for new market prices**

Run:
```bash
.venv/Scripts/python.exe -c "
import pathlib, sqlite3
db = pathlib.Path('data/odds.db').resolve()
conn = sqlite3.connect(f'file:{db.as_posix()}?mode=ro', uri=True)
markets = conn.execute(
    'SELECT market_id, COUNT(*) FROM prices GROUP BY market_id ORDER BY market_id'
).fetchall()
for m, n in markets:
    print(f'  {m}: {n} rows')
"
```
Expected output:
- `1x2_ft`, `1x2_1up_ft`, `1x2_2up_ft` — pre-existing markets with their existing row counts plus a few new rows from post-restart ticks.
- `over_under_ft` — pre-existing market.
- **`next_goal_ft` — NEW row count (>0) after at least one post-restart tick.**
- **`home_over_under_ft` — NEW row count (>0).**
- **`away_over_under_ft` — NEW row count (>0).**

The exact row counts depend on how long the scraper has been running with the new manifest; presence of any non-zero count for the three new market_ids is the success criterion.

### Step 4.3 — Commit any straggler fixes

- [ ] If anything required a fix during the smoke run, commit it with an appropriate `fix(...)` message. Otherwise no commit is needed in this task.

---

## Self-review

**Spec coverage:**
- Three new MarketSpec entries appended → Task 1 step 1.2
- `next_goal_ft` outcomes `(home, none, away)` and lines `(1..9)` → Task 1 step 1.2
- `home_over_under_ft` / `away_over_under_ft` lines `(0.5..5.5)` → Task 1 step 1.2
- Unique column prefixes (`ng`, `ou_home`, `ou_away`) → Task 1 step 1.2
- CSV header count update (68 → 170) → Task 1 step 1.1 + 1.2
- Watcher tick-log denominator update (54 → 156, 27 → 78) → Task 2
- Collector coverage for the new markets including out-of-manifest line drop → Task 3
- Live verification that prices flow → Task 4
- Spec assertion that no DDL change needed → confirmed by no migration task in plan
- Spec assertion that no registry.py patches needed → confirmed by no registry task in plan

**Placeholder scan:** no "TBD", no "implement later", every step has full code or commands with expected output.

**Type consistency:**
- Outcome strings match bookieskit's exact mapping: `("home", "none", "away")` for next_goal (not "draw"), `("over", "under")` for per-team OU
- Column prefixes consistent across spec, manifest, and test assertions: `ng_*`, `ou_home_*`, `ou_away_*`
- Line values are floats (`1.0`, `2.0`, etc.) matching `MarketSpec.lines: tuple[float, ...] | None` typing and the SQLite `prices.line REAL` column
- Total outcome counts cross-check: 3+3+3 + 9×2 + 9×3 + 6×2 + 6×2 = 78. BP/SB cells = 156. B9J/BW cells = 78. CSV header = 14 + 78×2 = 170. All consistent across spec, plan, and tests.
