# V2 as Primary Engine — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make V2 the only engine running in the live scraper pipeline and the only one shown in the web UI. V1 remains available only in the simulator.

**Architecture:** Remove V1 engine calls from `live_writer.compute_and_write` and the home-page card builder in `app.py`. Collapse the dual SIM v1/v2 columns into a single "SIM" column sourced from V2. `queries.get_our_history_for_event` returns V2 as primary with V1 fallback for pre-V2 historical rows.

**Tech Stack:** Python, SQLite, Jinja2, FastAPI

---

### Task 1: Update `queries.get_our_history_for_event` — V2 primary with V1 fallback

**Files:**
- Modify: `src/odds_scraper/web/queries.py:253-296`
- Test: `tests/test_web_queries.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_web_queries.py`:

```python
def test_our_history_returns_v2_as_primary(db_path: Path):
    """get_our_history_for_event returns V2 fields as the primary
    home_odds/away_odds keys, falling back to V1 for pre-V2 rows."""
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    # Row with both V1 and V2 — V2 should win
    conn.execute(
        "INSERT INTO pricer_live_results "
        "(event_id, ts_utc, basis_used, "
        " our_1up_home_capped, our_1up_away_capped, our_p_home_1, our_p_away_1, "
        " v2_1up_home_capped, v2_1up_away_capped, v2_p_home_1, v2_p_away_1) "
        "VALUES ('E1', '2026-05-21T10:00:00Z', 'bp', "
        "        1.48, 4.10, 0.66, 0.12, "
        "        1.55, 3.90, 0.63, 0.14)",
    )
    # Row with only V1 (pre-V2 historical) — V1 fallback
    conn.execute(
        "INSERT INTO pricer_live_results "
        "(event_id, ts_utc, basis_used, "
        " our_1up_home_capped, our_1up_away_capped, our_p_home_1, our_p_away_1) "
        "VALUES ('E1', '2026-05-21T10:05:00Z', 'bp', "
        "        1.48, 4.10, 0.66, 0.12)",
    )
    conn.close()
    conn = queries.open_ro_conn(db_path)
    result = queries.get_our_history_for_event(conn, "E1", "1x2_1up_ft")
    conn.close()
    # Tick with V2 → V2 values used
    t1 = result["2026-05-21T10:00:00Z"]
    assert t1["home_odds"] == 1.55
    assert t1["away_odds"] == 3.90
    assert t1["home_prob"] == 0.63
    # Tick without V2 → V1 fallback
    t2 = result["2026-05-21T10:05:00Z"]
    assert t2["home_odds"] == 1.48
    assert t2["away_odds"] == 4.10
    assert t2["home_prob"] == 0.66
    # No v2_* keys in the output — unified interface
    assert "v2_home_odds" not in t1
    assert "v2_home_odds" not in t2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_web_queries.py::test_our_history_returns_v2_as_primary -v`
Expected: FAIL — current code returns V1 as `home_odds` and V2 under `v2_home_odds`.

- [ ] **Step 3: Update `get_our_history_for_event`**

In `src/odds_scraper/web/queries.py`, replace the return dict comprehension (lines 287-295) with V2-primary logic that falls back to V1:

```python
    return {
        r["ts_utc"]: {
            "home_odds": r[v2_odds_h] if r[v2_odds_h] is not None else r[odds_h],
            "away_odds": r[v2_odds_a] if r[v2_odds_a] is not None else r[odds_a],
            "home_prob": r[v2_prob_h] if r[v2_prob_h] is not None else r[prob_h],
            "away_prob": r[v2_prob_a] if r[v2_prob_a] is not None else r[prob_a],
        }
        for r in rows
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_web_queries.py::test_our_history_returns_v2_as_primary -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/odds_scraper/web/queries.py tests/test_web_queries.py
git commit -m "feat(queries): return V2 as primary engine output with V1 fallback"
```

---

### Task 2: Collapse detail history to single SIM column

**Files:**
- Modify: `src/odds_scraper/web/app.py:513-548`
- Modify: `src/odds_scraper/web/templates/event_detail.html:71-72`
- Test: `tests/test_web_app.py`

- [ ] **Step 1: Update `_build_event_detail` in `app.py`**

Replace lines 513-548 (the sim + sim_v2 cell building and book tuple logic):

```python
    for ts, our in our_by_ts.items():
        sim_cells: dict[str, PriceCell] = {}
        if our["home_odds"] is not None:
            sim_cells["home"] = PriceCell(
                odds=our["home_odds"], probability=our["home_prob"],
            )
        if our["away_odds"] is not None:
            sim_cells["away"] = PriceCell(
                odds=our["away_odds"], probability=our["away_prob"],
            )
        if sim_cells and ts in bucket:
            bucket[ts]["cells"]["sim"] = sim_cells

    show_sim_col = bool(our_by_ts) and market_id in ("1x2_1up_ft", "1x2_2up_ft")
    if show_sim_col:
        history_books = ("betpawa", "sportybet", "sim", "bet9ja", "betway")
    else:
        history_books = ("betpawa", "sportybet", "bet9ja", "betway")
```

This removes the `sim_v2_cells` block, the `show_sim_v2_col` flag, and the triple book-tuple conditional.

- [ ] **Step 2: Update `event_detail.html` template**

On line 71, change the book_label map:
```jinja2
{% set book_label = {"betpawa":"BetPawa","sportybet":"SportyBet","bet9ja":"Bet9ja","betway":"Betway","sim":"SIM"} %}
{% set sim_books = ("sim",) %}
```

Remove `"sim_v2"` from both the label map and the `sim_books` tuple.

- [ ] **Step 3: Update existing test**

In `tests/test_web_app.py`, find the test that checks for `SIM v1` (around line 923) and update:

```python
    assert re.search(r"<th[^>]*data-bookmaker=\"sim\"[^>]*>\s*SIM\s*</th>", body)
```

Change `SIM v1` to just `SIM`.

- [ ] **Step 4: Run detail page tests**

Run: `pytest tests/test_web_app.py -k "event_detail" -v`
Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add src/odds_scraper/web/app.py src/odds_scraper/web/templates/event_detail.html tests/test_web_app.py
git commit -m "feat(detail): collapse SIM v1/v2 into single SIM column using V2"
```

---

### Task 3: Remove V1 from home-page card builder and simplify EventView

**Files:**
- Modify: `src/odds_scraper/web/app.py:130-160, 400-458`
- Modify: `src/odds_scraper/web/templates/_event_card.html:50-127`
- Test: `tests/test_web_app.py`

- [ ] **Step 1: Simplify `EventView` dataclass**

In `app.py`, remove the `v2_*` fields from `EventView` (lines 152-159). The `our_*` fields stay but will now be populated from V2 output:

```python
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
    our_1up_home: Optional[float]
    our_1up_away: Optional[float]
    our_2up_home: Optional[float]
    our_2up_away: Optional[float]
    our_p_1up_home: Optional[float]
    our_p_1up_away: Optional[float]
    our_p_2up_home: Optional[float]
    our_p_2up_away: Optional[float]
    bp_has_1up: bool
    bp_has_2up: bool
```

- [ ] **Step 2: Replace V1 engine call with V2 in card builder**

In the `_build_event_card` function (around lines 400-458), replace the two engine try/except blocks with a single V2 call. Populate `our_*` locals from V2 output:

```python
    if engine_inputs is not None:
        score = (row["score_home"] or 0, row["score_away"] or 0)
        engine_inputs["score"] = (int(score[0]), int(score[1]))
        engine_inputs["max_home_lead"] = max_leads[0]
        engine_inputs["max_away_lead"] = max_leads[1]
        engine_kwargs = {k: v for k, v in engine_inputs.items() if not k.startswith("_")}
        try:
            result = engine_v2.price_early_payout_markets(**engine_kwargs)
            our_1up_home = result["market_1up"]["home_margin"]
            our_1up_away = result["market_1up"]["away_margin"]
            our_2up_home = result["market_2up"]["home_margin"]
            our_2up_away = result["market_2up"]["away_margin"]
            our_p_1up_home = result["p_home_1"]
            our_p_1up_away = result["p_away_1"]
            our_p_2up_home = result["p_home_2"]
            our_p_2up_away = result["p_away_2"]
        except Exception:  # noqa: BLE001
            pass
```

Update the `EventView(...)` constructor call to remove `v2_*` kwargs:

```python
    return EventView(
        id=row["id"], home=row["home"], away=row["away"],
        kickoff_utc=row["kickoff_utc"], status=row["status"],
        match_minute=row["match_minute"],
        score_home=row["score_home"], score_away=row["score_away"],
        market_groups=groups,
        our_1up_home=our_1up_home, our_1up_away=our_1up_away,
        our_2up_home=our_2up_home, our_2up_away=our_2up_away,
        our_p_1up_home=our_p_1up_home, our_p_1up_away=our_p_1up_away,
        our_p_2up_home=our_p_2up_home, our_p_2up_away=our_p_2up_away,
        bp_has_1up=bp_has_1up, bp_has_2up=bp_has_2up,
    )
```

- [ ] **Step 3: Simplify `_event_card.html` template**

Remove `v2_value`/`v2_prob` variables and the stacked v1/v2 SIM cell. The template macro (lines 50-127) becomes:

Variable extraction (remove v2 lines, keep only `our_*`):
```jinja2
          {% set our_value = none %}
          {% set our_prob = none %}
          {% set bp_has_quote = false %}
          {% if is_up_row %}
            {% if group.group_key == '1x2_1up_ft' %}
              {% set bp_has_quote = event.bp_has_1up %}
              {% if row.side_short == 'H' %}
                {% set our_value = event.our_1up_home %}
                {% set our_prob = event.our_p_1up_home %}
              {% endif %}
              {% if row.side_short == 'A' %}
                {% set our_value = event.our_1up_away %}
                {% set our_prob = event.our_p_1up_away %}
              {% endif %}
            {% else %}
              {% set bp_has_quote = event.bp_has_2up %}
              {% if row.side_short == 'H' %}
                {% set our_value = event.our_2up_home %}
                {% set our_prob = event.our_p_2up_home %}
              {% endif %}
              {% if row.side_short == 'A' %}
                {% set our_value = event.our_2up_away %}
                {% set our_prob = event.our_p_2up_away %}
              {% endif %}
            {% endif %}
          {% endif %}
```

SIM column (replace the stacked v1/v2 block with a single value):
```jinja2
            <span data-bookmaker="sim">
              {% if is_up_row and bp_has_quote and our_value is not none %}
                <span class="odds sim">{{ "%.2f"|format(our_value) }}</span>
                {% if our_prob is not none %}<span class="prob">.{{ "%02d"|format([(our_prob * 100)|round|int, 99]|min) }}</span>{% endif %}
              {% else %}<span class="text-gray-700">—</span>{% endif %}
            </span>
```

Remove the `sim-stack` class and `sim-tag`/`sim-line` spans entirely.

- [ ] **Step 4: Run event card tests**

Run: `pytest tests/test_web_app.py -k "sim" -v`
Expected: All pass. The `sim-tag` / `sim-stack` assertions may need removal if any tests assert their presence.

- [ ] **Step 5: Clean up unused V1 engine import in `app.py`**

Remove `engine` from the pricer imports at the top of `app.py` if it's no longer used. Keep `engine_v2`.

- [ ] **Step 6: Commit**

```bash
git add src/odds_scraper/web/app.py src/odds_scraper/web/templates/_event_card.html tests/test_web_app.py
git commit -m "feat(cards): V2-only SIM column, remove stacked v1/v2 layout"
```

---

### Task 4: Remove V1 from `live_writer.compute_and_write`

**Files:**
- Modify: `src/odds_scraper/pricer/live_writer.py:40-134`
- Test: `tests/test_pricer_live_writer.py`

- [ ] **Step 1: Update existing tests**

In `tests/test_pricer_live_writer.py`:

Update `test_live_writer_persists_v2_columns_alongside_v1` (rename to `test_live_writer_persists_v2_columns`). Assert V2 cells are populated and V1 cells are NULL:

```python
def test_live_writer_persists_v2_columns(tmp_path: Path):
    """live_writer runs V2 only — our_* (V1) columns are NULL,
    v2_* columns populated."""
    conn = sqlite3.connect(str(tmp_path / "v2.db"), isolation_level=None)
    init_schema(conn)
    conn.row_factory = sqlite3.Row
    rows = [
        _tick_snapshot(Bookmaker.BETPAWA, with_prob=True),
        _tick_snapshot(Bookmaker.SPORTYBET, with_prob=False),
    ]
    ok = live_writer.compute_and_write_from_snapshots(
        conn, "E1", "2026-05-21T10:00:00Z", rows, (0, 0),
    )
    assert ok
    row = conn.execute(
        "SELECT our_p_home_1, v2_p_home_1, "
        "       our_1up_home_capped, v2_1up_home_capped "
        "FROM pricer_live_results WHERE event_id='E1'"
    ).fetchone()
    assert row is not None
    # V1 cells NULL — no longer computed in live pipeline
    assert row["our_p_home_1"] is None
    assert row["our_1up_home_capped"] is None
    # V2 cells populated
    assert row["v2_p_home_1"] is not None
    assert row["v2_1up_home_capped"] is not None
    conn.close()
```

Update `test_live_writer_v2_diverges_from_v1_on_live_trailing` — rename to `test_live_writer_v2_trailing_produces_output` and just assert V2 is populated (no V1 comparison):

```python
def test_live_writer_v2_trailing_produces_output(tmp_path: Path):
    """At a live trailing score (1-0), V2 trailing 1UP is populated."""
    conn = sqlite3.connect(str(tmp_path / "v2_live.db"), isolation_level=None)
    init_schema(conn)
    conn.row_factory = sqlite3.Row
    rows = [
        _live_trailing_snapshot(Bookmaker.BETPAWA),
        _live_trailing_snapshot(Bookmaker.SPORTYBET),
    ]
    ok = live_writer.compute_and_write_from_snapshots(
        conn, "E1", "2026-05-22T10:00:00Z", rows, (1, 0),
    )
    assert ok
    row = conn.execute(
        "SELECT our_1up_away_capped, v2_1up_away_capped "
        "FROM pricer_live_results WHERE event_id='E1'"
    ).fetchone()
    assert row is not None
    assert row["our_1up_away_capped"] is None  # V1 no longer runs
    assert row["v2_1up_away_capped"] is not None  # V2 trailing away populated
    conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_pricer_live_writer.py -v`
Expected: FAIL — tests expect V1 NULL but current code still populates V1.

- [ ] **Step 3: Update `compute_and_write`**

In `src/odds_scraper/pricer/live_writer.py`, replace the V1 engine call + V2 engine call with V2 only:

```python
    engine_kwargs = {k: v for k, v in engine_inputs.items() if not k.startswith("_")}
    try:
        res_v2 = engine_v2.price_early_payout_markets(**engine_kwargs)
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "v2 engine crashed on event=%s ts=%s — skipping (%s)",
            event_id, ts_utc, exc,
        )
        return False

    conn.execute(
        """
        INSERT OR REPLACE INTO pricer_live_results (
            event_id, ts_utc, basis_used,
            lambda_home, lambda_away,
            our_p_home_1, our_p_away_1,
            our_1up_home_fair, our_1up_home_capped,
            our_1up_away_fair, our_1up_away_capped,
            our_p_home_2, our_p_away_2,
            our_2up_home_fair, our_2up_home_capped,
            our_2up_away_fair, our_2up_away_capped,
            v2_p_home_1, v2_p_away_1,
            v2_1up_home_fair, v2_1up_home_capped,
            v2_1up_away_fair, v2_1up_away_capped,
            v2_p_home_2, v2_p_away_2,
            v2_2up_home_fair, v2_2up_home_capped,
            v2_2up_away_fair, v2_2up_away_capped
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                  ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id, ts_utc, basis,
            res_v2["lambda_home"], res_v2["lambda_away"],
            None, None,  # our_p_home_1, our_p_away_1 (V1 — no longer computed)
            None, None,  # our_1up_home_fair, our_1up_home_capped
            None, None,  # our_1up_away_fair, our_1up_away_capped
            None, None,  # our_p_home_2, our_p_away_2
            None, None,  # our_2up_home_fair, our_2up_home_capped
            None, None,  # our_2up_away_fair, our_2up_away_capped
            res_v2["p_home_1"], res_v2["p_away_1"],
            res_v2["market_1up"]["home_fair"],   res_v2["market_1up"]["home_margin"],
            res_v2["market_1up"]["away_fair"],   res_v2["market_1up"]["away_margin"],
            res_v2["p_home_2"], res_v2["p_away_2"],
            res_v2["market_2up"]["home_fair"],   res_v2["market_2up"]["home_margin"],
            res_v2["market_2up"]["away_fair"],   res_v2["market_2up"]["away_margin"],
        ),
    )
    return True
```

- [ ] **Step 4: Remove unused V1 engine import**

In `live_writer.py` line 17, change:
```python
from . import engine, engine_v2, inputs as input_extract, score_state
```
to:
```python
from . import engine_v2, inputs as input_extract, score_state
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_pricer_live_writer.py -v`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add src/odds_scraper/pricer/live_writer.py tests/test_pricer_live_writer.py
git commit -m "feat(live_writer): V2-only pipeline, V1 columns left NULL"
```

---

### Task 5: Full test suite verification

- [ ] **Step 1: Run full test suite**

Run: `pytest tests/ -v --ignore=tests/test_simulator_routes.py --ignore=tests/test_web_app.py --ignore=tests/test_web_queries.py` (core tests without fastapi)

Then separately: `pytest tests/test_web_app.py tests/test_web_queries.py tests/test_simulator_routes.py -v` (if fastapi is available)

Expected: All pass. If any test still references `v2_home_odds`, `sim_v2`, `SIM v1`, `sim-tag`, or `sim-stack`, update it.

- [ ] **Step 2: Commit any remaining test fixes**

```bash
git add tests/
git commit -m "test: update remaining assertions for V2-primary"
```
