# BP Global Event Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace tournament-list-driven discovery with a global BetPawa football sweep, with a soft cap on concurrent watchers and the existing tournaments list preserved as an always-include overlay.

**Architecture:** Add a new `_fetch_global_event_ids` helper in `event_resolver.py` that paginates `bp_client.get_events(tournament_id=None, ...)`. Change `resolve_event_ids` to return both an ordered ID list and a priority-set so the caller can apply a per-spawn cap that always-includes standalone + tournament events. Wire a new `GlobalSweepConfig` and `max_active_watchers` through `config.py` and `main._refresh_loop`.

**Tech Stack:** Python 3.13, `bookieskit.BetPawa`, asyncio, pytest + pytest-asyncio. Existing patterns in `src/odds_scraper/event_resolver.py` and `tests/test_event_resolver.py` are the reference.

**Spec:** `docs/superpowers/specs/2026-05-25-bp-global-discovery-design.md`

---

## File Structure

**Files to modify:**

| File | Responsibility | What changes |
|---|---|---|
| `src/odds_scraper/event_resolver.py` | One-shot expansion of standalone + tournament IDs into the watcher set | Add `_fetch_global_event_ids` helper; extend `resolve_event_ids` signature and return shape; preserve existing per-tournament path verbatim. |
| `src/odds_scraper/config.py` | Load + validate `config.yaml` into typed dataclasses | Add `GlobalSweepConfig`; add `max_active_watchers` + `global_sweep` to `AppConfig` with safe defaults. |
| `src/odds_scraper/main.py` | Process lifecycle, refresh loop, watcher spawning | Update `resolve_event_ids` call to receive the new tuple; apply cap in `_refresh_loop`'s spawn step; log priority/global split. |
| `tests/test_event_resolver.py` | Unit tests for resolver | Extend with global-sweep + return-shape tests. |
| `tests/test_config.py` | Tests for config loader (if exists; otherwise create) | Add tests for the new fields. |
| `tests/test_main_global_sweep.py` | NEW — focused integration test of the spawn cap | Cap honors priority; spots fill on watcher exit. |

**Why this decomposition:**

- `event_resolver.py` is already focused — adding one helper + one keyword path keeps it under ~150 lines and one responsibility (resolve config inputs → watcher IDs).
- `config.py` is the single typed boundary for `config.yaml` — all new YAML fields land here as dataclass fields.
- `main.py` is large but the change is localized to `_refresh_loop`'s spawn block. No restructure.
- New test file `test_main_global_sweep.py` isolates the cap logic so we don't bloat `test_event_resolver.py` with concerns it shouldn't own.

---

## Task 1: `_fetch_global_event_ids` helper

**Files:**
- Modify: `src/odds_scraper/event_resolver.py`
- Test: `tests/test_event_resolver.py`

This helper paginates BP's global feed (one call per `event_type`) and returns event IDs sorted by `startTime` ASC. Entries missing `startTime` drop to the end (BP returns `startTime` as ISO-8601 UTC like `"2026-05-25T16:30:00Z"` on each entry under `responses[].responses[]`). Per-page failure logs and stops pagination for that event_type — returns partial.

- [ ] **Step 1: Write the failing test for happy-path pagination + sorting**

Append to `tests/test_event_resolver.py`:

```python
def _events_response_with_kickoff(items: list[tuple[str, str | None]]) -> dict:
    """items = [(event_id, start_time_iso_or_None), ...]."""
    return {
        "responses": [{
            "responses": [
                {"id": ev_id, "participants": [], **({"startTime": kt} if kt else {})}
                for ev_id, kt in items
            ]
        }]
    }


@pytest.mark.asyncio
async def test_global_fetch_sorts_by_kickoff_asc_missing_last():
    """_fetch_global_event_ids paginates per event_type and sorts the
    union by startTime ASC; entries missing startTime drop to the end."""
    from odds_scraper.event_resolver import _fetch_global_event_ids

    client = AsyncMock()
    async def get_events(tournament_id, sport_id, event_type, skip, take):
        assert tournament_id is None
        assert sport_id == "2"
        if event_type == "UPCOMING" and skip == 0:
            return _events_response_with_kickoff([
                ("a", "2026-06-01T18:00:00Z"),
                ("b", "2026-05-30T15:00:00Z"),
                ("missing", None),
            ])
        if event_type == "LIVE" and skip == 0:
            return _events_response_with_kickoff([
                ("c", "2026-05-25T20:00:00Z"),
            ])
        return _events_response_with_kickoff([])
    client.get_events.side_effect = get_events

    out = await _fetch_global_event_ids(
        client, sport_id="2", event_types=("UPCOMING", "LIVE"),
    )
    assert out == ["c", "b", "a", "missing"]
```

- [ ] **Step 2: Run the failing test**

Run: `python -m pytest tests/test_event_resolver.py::test_global_fetch_sorts_by_kickoff_asc_missing_last -v`
Expected: FAIL with `ImportError: cannot import name '_fetch_global_event_ids' from 'odds_scraper.event_resolver'`

- [ ] **Step 3: Add the helper to `event_resolver.py`**

Insert before `async def _fetch_tournament_event_ids`:

```python
async def _fetch_global_event_ids(
    bp_client,
    *,
    sport_id: str,
    event_types: Sequence[str],
) -> list[str]:
    """Paginate BP's global event feed (tournament_id=None) for each
    event_type, dedupe across types, and return event IDs sorted by
    startTime ASC. Entries without a startTime drop to the end so the
    soft cap still spawns them when there's room.

    Per-page failure logs and breaks pagination for that event_type —
    we keep whatever pages did succeed so a single bad page doesn't
    wipe out the global sweep.
    """
    by_id: dict[str, str | None] = {}  # event_id -> startTime (or None)
    for event_type in event_types:
        skip = 0
        while True:
            try:
                resp = await bp_client.get_events(
                    tournament_id=None,
                    sport_id=sport_id,
                    event_type=event_type,
                    skip=skip,
                    take=_TOURNAMENT_PAGE_SIZE,
                )
            except Exception as e:  # noqa: BLE001
                log.warning(
                    "global sweep page failed (event_type=%s skip=%d): %s — stopping pagination",
                    event_type, skip, " ".join(str(e).split())[:120],
                )
                break
            page = _entries_from_events_response(resp)
            if not page:
                break
            for ev_id, start in page:
                # Keep the first non-None startTime seen for an ID — UPCOMING
                # iterates before LIVE, so prematch kickoff wins over a missing
                # live-feed startTime.
                if ev_id not in by_id or (by_id[ev_id] is None and start is not None):
                    by_id[ev_id] = start
            if len(page) < _TOURNAMENT_PAGE_SIZE:
                break
            skip += _TOURNAMENT_PAGE_SIZE

    # Sort: ISO-8601 lexical ordering matches chronological for the
    # "YYYY-MM-DDTHH:MM:SSZ" shape BP returns. None drops to the end.
    def _key(item: tuple[str, str | None]) -> tuple[int, str]:
        ev_id, start = item
        return (0, start) if start is not None else (1, ev_id)

    return [ev_id for ev_id, _ in sorted(by_id.items(), key=_key)]


def _entries_from_events_response(resp: dict[str, Any]) -> list[tuple[str, str | None]]:
    """Walk BetPawa's `responses[].responses[]` and pull (id, startTime)
    for each entry. startTime is BP's ISO-8601 UTC kickoff string."""
    out: list[tuple[str, str | None]] = []
    for outer in resp.get("responses") or []:
        for entry in outer.get("responses") or []:
            ev_id = entry.get("id")
            if ev_id is None:
                continue
            out.append((str(ev_id), entry.get("startTime")))
    return out
```

- [ ] **Step 4: Run the test, see it pass**

Run: `python -m pytest tests/test_event_resolver.py::test_global_fetch_sorts_by_kickoff_asc_missing_last -v`
Expected: PASS.

- [ ] **Step 5: Add the failing-page resilience test**

Append to `tests/test_event_resolver.py`:

```python
@pytest.mark.asyncio
async def test_global_fetch_one_event_type_failure_returns_partial(caplog):
    """If one event_type's pagination raises, we keep the IDs from the
    successful event_type — a single bad page doesn't wipe the sweep."""
    from odds_scraper.event_resolver import _fetch_global_event_ids

    client = AsyncMock()
    async def get_events(tournament_id, sport_id, event_type, skip, take):
        if event_type == "UPCOMING":
            return _events_response_with_kickoff([
                ("good1", "2026-05-25T18:00:00Z"),
            ])
        if event_type == "LIVE":
            raise RuntimeError("HTTP 500 from BP")
        return _events_response_with_kickoff([])
    client.get_events.side_effect = get_events

    with caplog.at_level(logging.WARNING, logger="odds_scraper.event_resolver"):
        out = await _fetch_global_event_ids(
            client, sport_id="2", event_types=("UPCOMING", "LIVE"),
        )
    assert out == ["good1"]
    assert any("LIVE" in r.getMessage() for r in caplog.records)
```

- [ ] **Step 6: Run the test, see it pass**

Run: `python -m pytest tests/test_event_resolver.py::test_global_fetch_one_event_type_failure_returns_partial -v`
Expected: PASS — the `try/except ... break` we wrote already covers this.

- [ ] **Step 7: Add the multi-page pagination test**

Append to `tests/test_event_resolver.py`:

```python
@pytest.mark.asyncio
async def test_global_fetch_paginates_until_partial_page():
    """Pagination walks `take`-sized pages until a partial page signals
    the tail. Mirrors `_fetch_tournament_event_ids`."""
    from odds_scraper.event_resolver import _fetch_global_event_ids

    page1 = [(str(i), f"2026-06-{(i%30)+1:02d}T00:00:00Z") for i in range(100)]
    page2 = [(str(i), f"2026-06-{(i%30)+1:02d}T00:00:00Z") for i in range(100, 150)]

    client = AsyncMock()
    async def get_events(tournament_id, sport_id, event_type, skip, take):
        if event_type == "UPCOMING" and skip == 0:
            return _events_response_with_kickoff(page1)
        if event_type == "UPCOMING" and skip == 100:
            return _events_response_with_kickoff(page2)
        return _events_response_with_kickoff([])
    client.get_events.side_effect = get_events

    out = await _fetch_global_event_ids(
        client, sport_id="2", event_types=("UPCOMING",),
    )
    assert len(out) == 150
    assert set(out) == {str(i) for i in range(150)}
```

- [ ] **Step 8: Run the test, see it pass**

Run: `python -m pytest tests/test_event_resolver.py::test_global_fetch_paginates_until_partial_page -v`
Expected: PASS.

- [ ] **Step 9: Run the full resolver test file to confirm no regressions**

Run: `python -m pytest tests/test_event_resolver.py -v`
Expected: PASS for all tests (existing + 3 new = 13 passing).

- [ ] **Step 10: Commit**

```bash
git add src/odds_scraper/event_resolver.py tests/test_event_resolver.py
git commit -m "feat(event_resolver): add _fetch_global_event_ids helper

Paginates bookieskit's BetPawa.get_events(tournament_id=None) global
feed for each event_type, dedupes across types, and returns IDs sorted
by startTime ASC (entries without startTime drop to the end).

One bad page or event_type stops that branch's pagination but keeps
whatever IDs were already collected — same defensive posture as the
existing per-tournament helper."
```

---

## Task 2: Change `resolve_event_ids` return shape to `(ordered, priority)`

**Files:**
- Modify: `src/odds_scraper/event_resolver.py:12-51`
- Modify: `src/odds_scraper/main.py:176-180,228-232` (callers)
- Test: `tests/test_event_resolver.py`

The cap gate in `main.py` needs to know which IDs are "always-include" (standalone + tournament-expanded) so they bypass the cap. The cheapest way is to have `resolve_event_ids` return both pieces. This task only changes the shape — global sweep wiring lands in Task 3.

- [ ] **Step 1: Write the failing test for the new tuple return**

Append to `tests/test_event_resolver.py`:

```python
@pytest.mark.asyncio
async def test_resolve_returns_priority_set_with_ordered_list():
    """resolve_event_ids returns (ordered_ids, priority_ids). priority_ids
    is the union of standalone + tournament-expanded; ordered keeps the
    'standalone declared order, then tournament IDs lexically' rule."""
    client = _make_bp_client({
        ("11965", "UPCOMING", 0): _events_response(["33", "44"]),
        ("11965", "LIVE", 0): _events_response([]),
    })
    ordered, priority = await resolve_event_ids(
        standalone_events=["77"],
        tournaments=["11965"],
        bp_client=client,
    )
    assert ordered == ["77", "33", "44"]
    assert priority == {"77", "33", "44"}
```

- [ ] **Step 2: Run the failing test**

Run: `python -m pytest tests/test_event_resolver.py::test_resolve_returns_priority_set_with_ordered_list -v`
Expected: FAIL with `TypeError: cannot unpack non-iterable list object` (today's return is `list[str]`).

- [ ] **Step 3: Change `resolve_event_ids` to return the tuple**

In `src/odds_scraper/event_resolver.py`, replace lines 12-51 (the existing `resolve_event_ids` body) with:

```python
async def resolve_event_ids(
    standalone_events: Sequence[str],
    tournaments: Sequence[str],
    bp_client,
) -> tuple[list[str], set[str]]:
    """One-shot: expand tournaments to event IDs, union with standalone IDs,
    dedupe. Per-tournament failures are logged and skipped, not raised.

    Returns `(ordered_ids, priority_ids)`. `ordered_ids` lists standalone
    IDs first in declared order, then tournament-expanded IDs sorted
    lexicographically. `priority_ids` is the set of always-include IDs
    (the union of both sources) — callers apply this when deciding which
    IDs may bypass a concurrent-watcher cap.
    """
    seen: set[str] = set()
    ordered: list[str] = []

    for ev_id in standalone_events:
        if ev_id not in seen:
            seen.add(ev_id)
            ordered.append(ev_id)

    tournament_ids: list[str] = []
    for t_id in tournaments:
        try:
            ids = await _fetch_tournament_event_ids(t_id, bp_client)
        except Exception as e:  # noqa: BLE001
            log.warning(
                "tournament %s failed to resolve: %s — skipping",
                t_id, " ".join(str(e).split())[:120],
            )
            continue
        new_for_tournament = [i for i in ids if i not in seen]
        for i in new_for_tournament:
            seen.add(i)
        tournament_ids.extend(new_for_tournament)
        log.info(
            "tournament %s expanded to %d events (%d new)",
            t_id, len(ids), len(new_for_tournament),
        )

    ordered.extend(sorted(tournament_ids))
    priority = set(ordered)
    return ordered, priority
```

- [ ] **Step 4: Update the two callers in `main.py`**

Open `src/odds_scraper/main.py`. Find line 176-180 (initial resolve) and change:

```python
        initial_ids = await resolve_event_ids(
            standalone_events=cfg.events,
            tournaments=cfg.tournaments,
            bp_client=bp_client,
        )
```

to:

```python
        initial_ids, priority_ids = await resolve_event_ids(
            standalone_events=cfg.events,
            tournaments=cfg.tournaments,
            bp_client=bp_client,
        )
```

Find lines 228-232 (refresh resolve) and change:

```python
                    current = await resolve_event_ids(
                        standalone_events=cfg.events,
                        tournaments=cfg.tournaments,
                        bp_client=bp_client,
                    )
                    new_ids = [i for i in current if i not in watched_ids]
```

to:

```python
                    current, priority_ids = await resolve_event_ids(
                        standalone_events=cfg.events,
                        tournaments=cfg.tournaments,
                        bp_client=bp_client,
                    )
                    new_ids = [i for i in current if i not in watched_ids]
```

(The `priority_ids` variable is unused at this step; Task 5 wires it into the cap gate.)

- [ ] **Step 5: Run the new test, see it pass**

Run: `python -m pytest tests/test_event_resolver.py::test_resolve_returns_priority_set_with_ordered_list -v`
Expected: PASS.

- [ ] **Step 6: Update existing resolver tests to unpack the tuple**

Open `tests/test_event_resolver.py`. Replace every existing assertion of the form `assert out == ...` / `assert sorted(out) == ...` / `assert len(out) == ...` / `assert out.count(...) == ...` after a `out = await resolve_event_ids(...)` line so the call instead reads `out, _ = await resolve_event_ids(...)`.

Specifically these 10 existing tests need the tuple unpack added:
- `test_standalone_only_returns_ids_in_declared_order`
- `test_single_tournament_single_page`
- `test_upcoming_and_live_unioned_dedup_within_tournament`
- `test_pagination_walks_until_partial_page`
- `test_pagination_exactly_100_followed_by_empty`
- `test_one_tournament_failure_isolated_others_succeed`
- `test_dedup_standalone_and_tournament_event`
- `test_dedup_same_event_across_tournaments`
- `test_empty_inputs_returns_empty`
- `test_tournament_returning_empty_response_yields_no_events`

For each, change:

```python
    out = await resolve_event_ids(
        standalone_events=...,
        tournaments=...,
        bp_client=client,
    )
```

to:

```python
    out, _ = await resolve_event_ids(
        standalone_events=...,
        tournaments=...,
        bp_client=client,
    )
```

- [ ] **Step 7: Run the full test_event_resolver.py file**

Run: `python -m pytest tests/test_event_resolver.py -v`
Expected: PASS for all tests (existing 10 updated + 4 new from Task 1 = 14 total).

- [ ] **Step 8: Run the full test suite to catch any other caller breakage**

Run: `python -m pytest -q`
Expected: PASS (all tests, no regressions).

- [ ] **Step 9: Commit**

```bash
git add src/odds_scraper/event_resolver.py src/odds_scraper/main.py tests/test_event_resolver.py
git commit -m "refactor(event_resolver): return (ordered_ids, priority_ids) tuple

Callers (main.py's initial spawn and refresh loop) now receive both the
ordered ID list and the always-include set so a downstream cap gate can
let standalone + tournament-expanded events bypass the cap.

No behavior change yet — priority_ids is bound but unused until the
cap gate lands."
```

---

## Task 3: Wire `global_sweep` kwargs through `resolve_event_ids`

**Files:**
- Modify: `src/odds_scraper/event_resolver.py` (extend `resolve_event_ids` signature)
- Test: `tests/test_event_resolver.py`

Add the three new keyword-only args (`global_sweep`, `sport_id`, `event_types`) with safe defaults. When `global_sweep=True`, call the Task 1 helper and append its IDs after the priority block, deduped.

- [ ] **Step 1: Write the failing test for `global_sweep=True`**

Append to `tests/test_event_resolver.py`:

```python
@pytest.mark.asyncio
async def test_resolve_appends_global_ids_when_sweep_enabled():
    """global_sweep=True: priority IDs come first (standalone then tournament),
    then global IDs sorted by startTime ASC, deduped against priority."""
    client = AsyncMock()
    async def get_events(tournament_id=None, sport_id="2",
                         event_type="UPCOMING", skip=0, take=100):
        if tournament_id == "11965" and event_type == "UPCOMING" and skip == 0:
            return _events_response(["33", "44"])
        if tournament_id == "11965" and event_type == "LIVE":
            return _events_response([])
        if tournament_id is None and event_type == "UPCOMING" and skip == 0:
            # 44 overlaps tournament — must dedupe; "G2" sorts before "G1"
            # by startTime so it should come first in the global block.
            return _events_response_with_kickoff([
                ("44", "2026-05-30T15:00:00Z"),
                ("G1", "2026-06-10T15:00:00Z"),
                ("G2", "2026-05-26T15:00:00Z"),
            ])
        if tournament_id is None and event_type == "LIVE":
            return _events_response_with_kickoff([])
        return _events_response([])
    client.get_events.side_effect = get_events

    ordered, priority = await resolve_event_ids(
        standalone_events=["77"],
        tournaments=["11965"],
        bp_client=client,
        global_sweep=True,
        sport_id="2",
        event_types=("UPCOMING", "LIVE"),
    )
    # Priority block first (standalone → tournament lexical), then global
    # by kickoff ASC, with the overlapping "44" deduped out.
    assert ordered == ["77", "33", "44", "G2", "G1"]
    assert priority == {"77", "33", "44"}
```

- [ ] **Step 2: Run the failing test**

Run: `python -m pytest tests/test_event_resolver.py::test_resolve_appends_global_ids_when_sweep_enabled -v`
Expected: FAIL with `TypeError: resolve_event_ids() got an unexpected keyword argument 'global_sweep'`.

- [ ] **Step 3: Extend the `resolve_event_ids` signature**

In `src/odds_scraper/event_resolver.py`, replace the `resolve_event_ids` definition with:

```python
async def resolve_event_ids(
    standalone_events: Sequence[str],
    tournaments: Sequence[str],
    bp_client,
    *,
    global_sweep: bool = False,
    sport_id: str = "2",
    event_types: Sequence[str] = ("UPCOMING", "LIVE"),
) -> tuple[list[str], set[str]]:
    """One-shot: expand tournaments to event IDs, union with standalone IDs,
    optionally append a global BP sweep, dedupe. Per-tournament failures
    are logged and skipped, not raised.

    Returns `(ordered_ids, priority_ids)`. `ordered_ids` lists standalone
    IDs first in declared order, then tournament-expanded IDs sorted
    lexicographically, then (when `global_sweep=True`) global-feed IDs
    sorted by kickoff ASC. `priority_ids` is the always-include set
    (standalone ∪ tournament-expanded) — global IDs are NOT in priority.
    """
    seen: set[str] = set()
    ordered: list[str] = []

    for ev_id in standalone_events:
        if ev_id not in seen:
            seen.add(ev_id)
            ordered.append(ev_id)

    tournament_ids: list[str] = []
    for t_id in tournaments:
        try:
            ids = await _fetch_tournament_event_ids(t_id, bp_client)
        except Exception as e:  # noqa: BLE001
            log.warning(
                "tournament %s failed to resolve: %s — skipping",
                t_id, " ".join(str(e).split())[:120],
            )
            continue
        new_for_tournament = [i for i in ids if i not in seen]
        for i in new_for_tournament:
            seen.add(i)
        tournament_ids.extend(new_for_tournament)
        log.info(
            "tournament %s expanded to %d events (%d new)",
            t_id, len(ids), len(new_for_tournament),
        )

    ordered.extend(sorted(tournament_ids))
    priority = set(ordered)

    if global_sweep:
        global_ids = await _fetch_global_event_ids(
            bp_client, sport_id=sport_id, event_types=event_types,
        )
        new_global = [i for i in global_ids if i not in seen]
        for i in new_global:
            seen.add(i)
        ordered.extend(new_global)
        log.info(
            "global sweep: %d events (%d new after dedup against priority)",
            len(global_ids), len(new_global),
        )

    return ordered, priority
```

- [ ] **Step 4: Run the new test, see it pass**

Run: `python -m pytest tests/test_event_resolver.py::test_resolve_appends_global_ids_when_sweep_enabled -v`
Expected: PASS.

- [ ] **Step 5: Add the `global_sweep=False` regression-guard test**

Append to `tests/test_event_resolver.py`:

```python
@pytest.mark.asyncio
async def test_resolve_with_sweep_disabled_matches_legacy_behavior():
    """global_sweep=False (default): output identical to pre-feature
    behavior. The global helper is not called."""
    client = _make_bp_client({
        ("11965", "UPCOMING", 0): _events_response(["1", "2"]),
        ("11965", "LIVE", 0): _events_response([]),
    })
    ordered, priority = await resolve_event_ids(
        standalone_events=["77"],
        tournaments=["11965"],
        bp_client=client,
        # global_sweep defaults to False
    )
    assert ordered == ["77", "1", "2"]
    assert priority == {"77", "1", "2"}
    # Confirm the global path was not invoked — tournament_id=None never appeared.
    for call in client.get_events.await_args_list:
        assert call.kwargs.get("tournament_id") is not None or \
               call.args and call.args[0] is not None
```

- [ ] **Step 6: Run the regression-guard test**

Run: `python -m pytest tests/test_event_resolver.py::test_resolve_with_sweep_disabled_matches_legacy_behavior -v`
Expected: PASS.

- [ ] **Step 7: Run the full resolver test file**

Run: `python -m pytest tests/test_event_resolver.py -v`
Expected: 16 tests pass (10 original + 4 from Task 1 + 2 from this task).

- [ ] **Step 8: Commit**

```bash
git add src/odds_scraper/event_resolver.py tests/test_event_resolver.py
git commit -m "feat(event_resolver): optional global BP sweep in resolve_event_ids

When global_sweep=True, append IDs from _fetch_global_event_ids after
the priority block (standalone + tournament-expanded), deduped. Global
IDs are NOT in priority_ids — the caller's cap gate must decide whether
to spawn them.

Default global_sweep=False keeps existing behavior byte-for-byte."
```

---

## Task 4: Add `GlobalSweepConfig` + `max_active_watchers` to `config.py`

**Files:**
- Modify: `src/odds_scraper/config.py`
- Test: `tests/test_config.py` (create if missing)

Add the two new optional config fields with defaults that match the spec (`global_sweep.enabled: False`, `max_active_watchers: 500`).

- [ ] **Step 1: Check whether `tests/test_config.py` exists**

Run: `python -c "import os; print('exists' if os.path.exists('tests/test_config.py') else 'missing')"`

If it prints `missing`, the test file in the next step is created fresh. If `exists`, the new tests are appended to it.

- [ ] **Step 2: Write the failing test for the new config fields**

Either create `tests/test_config.py` with the following content, or append the test functions to the existing file (importing `load_config` from `odds_scraper.config` at the top of the file):

```python
import textwrap
from pathlib import Path

from odds_scraper.config import load_config


_BASE_YAML = """\
country: ng
events: []
tournaments: []
cadence:
  prematch_seconds: 600
  live_seconds: 90
  status_retry_backoff_seconds: [5, 15, 45]
  watchdog_after_kickoff_seconds: 10800
output:
  db_path: data/odds.db
  resolution_cache_path: data/resolution_cache.json
"""


def test_load_config_defaults_max_active_watchers_to_500(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text(_BASE_YAML, encoding="utf-8")
    cfg = load_config(p)
    assert cfg.max_active_watchers == 500


def test_load_config_defaults_global_sweep_disabled(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text(_BASE_YAML, encoding="utf-8")
    cfg = load_config(p)
    assert cfg.global_sweep.enabled is False
    assert cfg.global_sweep.sport_id == "2"
    assert cfg.global_sweep.event_types == ("UPCOMING", "LIVE")


def test_load_config_reads_global_sweep_block(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text(_BASE_YAML + textwrap.dedent("""\
        max_active_watchers: 250
        global_sweep:
          enabled: true
          sport_id: "2"
          event_types: [UPCOMING]
    """), encoding="utf-8")
    cfg = load_config(p)
    assert cfg.max_active_watchers == 250
    assert cfg.global_sweep.enabled is True
    assert cfg.global_sweep.event_types == ("UPCOMING",)
```

- [ ] **Step 3: Run the failing tests**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL with `AttributeError: 'AppConfig' object has no attribute 'max_active_watchers'` (or similar).

- [ ] **Step 4: Add the dataclasses and loader fields**

In `src/odds_scraper/config.py`, add after `class CadenceConfig` and `class OutputConfig`:

```python
@dataclass(frozen=True)
class GlobalSweepConfig:
    enabled: bool = False
    sport_id: str = "2"
    event_types: tuple[str, ...] = ("UPCOMING", "LIVE")
```

Change `class AppConfig` to:

```python
@dataclass(frozen=True)
class AppConfig:
    country: str
    events: tuple[str, ...]
    tournaments: tuple[str, ...]
    refresh_interval_seconds: int
    refresh_interval_when_idle_seconds: int
    cadence: CadenceConfig
    output: OutputConfig
    log_level: str
    max_active_watchers: int = 500
    global_sweep: GlobalSweepConfig = GlobalSweepConfig()
```

Change `load_config` to wire up the new fields. Replace the body with:

```python
def load_config(path: Path | str) -> AppConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    cad = raw["cadence"]
    out = raw["output"]
    sweep_raw = raw.get("global_sweep") or {}
    return AppConfig(
        country=str(raw["country"]),
        events=tuple(str(e) for e in (raw.get("events") or [])),
        tournaments=tuple(str(t) for t in (raw.get("tournaments") or [])),
        refresh_interval_seconds=int(raw.get("refresh_interval_seconds", 86400)),
        refresh_interval_when_idle_seconds=int(
            raw.get("refresh_interval_when_idle_seconds", 600),
        ),
        cadence=CadenceConfig(
            prematch_seconds=int(cad["prematch_seconds"]),
            live_seconds=int(cad["live_seconds"]),
            status_retry_backoff_seconds=tuple(
                int(x) for x in cad["status_retry_backoff_seconds"]
            ),
            watchdog_after_kickoff_seconds=int(cad["watchdog_after_kickoff_seconds"]),
            live_lead_seconds=int(cad.get("live_lead_seconds", 300)),
            poll_timeout_seconds=int(cad.get("poll_timeout_seconds", 30)),
            resolver_timeout_seconds=int(
                cad.get("resolver_timeout_seconds", 90),
            ),
        ),
        output=OutputConfig(
            db_path=str(out.get("db_path", "data/odds.db")),
            resolution_cache_path=str(out["resolution_cache_path"]),
        ),
        log_level=os.environ.get(
            "ODDS_SCRAPER_LOG_LEVEL", str(raw.get("log_level", "INFO")),
        ),
        max_active_watchers=int(raw.get("max_active_watchers", 500)),
        global_sweep=GlobalSweepConfig(
            enabled=bool(sweep_raw.get("enabled", False)),
            sport_id=str(sweep_raw.get("sport_id", "2")),
            event_types=tuple(
                str(t) for t in (sweep_raw.get("event_types") or ("UPCOMING", "LIVE"))
            ),
        ),
    )
```

- [ ] **Step 5: Run the config tests**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS — 3 tests.

- [ ] **Step 6: Run the full test suite**

Run: `python -m pytest -q`
Expected: PASS — no regressions (existing config consumers see defaults).

- [ ] **Step 7: Commit**

```bash
git add src/odds_scraper/config.py tests/test_config.py
git commit -m "feat(config): add max_active_watchers + global_sweep block

Both fields optional with safe defaults: max_active_watchers=500,
global_sweep.enabled=False. Existing config.yaml files load unchanged."
```

---

## Task 5: Wire the spawn cap and global-sweep kwargs into `main._refresh_loop`

**Files:**
- Modify: `src/odds_scraper/main.py:175-244`
- Test: `tests/test_main_global_sweep.py` (new)

Two pieces:
1. Pass the config's `global_sweep` + `max_active_watchers` through both `resolve_event_ids` calls (initial + refresh).
2. Apply the cap when spawning non-priority watchers; priority IDs always spawn.

- [ ] **Step 1: Write the failing test for the cap gate**

Create `tests/test_main_global_sweep.py`:

```python
"""Integration test for the spawn-cap gate in main._refresh_loop.

Exercises the cap logic in isolation by calling the cap function
directly with controlled inputs — main.py's loop machinery (asyncio
tasks, supervisors) is out of scope; we only test that the spawn
decision honors priority and the soft cap.
"""

from odds_scraper.main import _decide_spawns


def test_decide_spawns_priority_always_spawns_even_at_cap():
    """Priority IDs spawn regardless of cap. Non-priority IDs only
    spawn while there's headroom under max_active_watchers."""
    ordered = ["P1", "P2", "G1", "G2", "G3"]
    priority = {"P1", "P2"}
    watched: set[str] = set()
    out = _decide_spawns(
        ordered=ordered,
        priority=priority,
        watched=watched,
        n_active=0,
        max_active=3,
    )
    # 3 slots total: P1 + P2 (priority, always) + 1 global (G1, first by order)
    assert out == ["P1", "P2", "G1"]


def test_decide_spawns_priority_bypasses_cap_when_full():
    """When the cap is already full of non-priority watchers, a new
    priority ID still spawns — the cap is a new-non-priority gate only."""
    ordered = ["P1", "G1", "G2", "G3"]
    priority = {"P1"}
    watched = {"G1", "G2", "G3"}  # cap is full with non-priority
    out = _decide_spawns(
        ordered=ordered,
        priority=priority,
        watched=watched,
        n_active=3,
        max_active=3,
    )
    assert out == ["P1"]  # P1 spawns despite cap; G1..G3 already watched


def test_decide_spawns_fills_from_queue_head_when_room_frees_up():
    """Once a non-priority watcher exits, the next refresh fills the slot
    from the ordered list head (kickoff ASC for global IDs)."""
    ordered = ["G1", "G2", "G3", "G4"]
    priority: set[str] = set()
    # G1 and G2 already running. G3 wasn't spawned last refresh (cap=2).
    # Now n_active dropped to 1 because G2 finished -> G3 fills the slot.
    watched = {"G1"}
    out = _decide_spawns(
        ordered=ordered,
        priority=priority,
        watched=watched,
        n_active=1,
        max_active=2,
    )
    assert out == ["G3"]


def test_decide_spawns_skips_already_watched():
    """IDs already in `watched` (running watchers or pending spawn from
    this same refresh) are never re-spawned."""
    ordered = ["P1", "P2", "G1"]
    priority = {"P1", "P2"}
    watched = {"P1"}  # P1 already running
    out = _decide_spawns(
        ordered=ordered,
        priority=priority,
        watched=watched,
        n_active=1,
        max_active=5,
    )
    assert out == ["P2", "G1"]
```

- [ ] **Step 2: Run the failing tests**

Run: `python -m pytest tests/test_main_global_sweep.py -v`
Expected: FAIL with `ImportError: cannot import name '_decide_spawns' from 'odds_scraper.main'`.

- [ ] **Step 3: Add the `_decide_spawns` helper to `main.py`**

In `src/odds_scraper/main.py`, after the existing imports near the top of the file (anywhere before the function `run` is fine), insert:

```python
def _decide_spawns(
    *,
    ordered: list[str],
    priority: set[str],
    watched: set[str],
    n_active: int,
    max_active: int,
) -> list[str]:
    """Per-refresh spawn decision. Priority IDs always spawn regardless
    of the cap; non-priority IDs spawn only while there's room.

    Args:
        ordered: resolver output (priority IDs first, then global by kickoff).
        priority: always-include IDs (standalone + tournament-expanded).
        watched: IDs already being watched OR queued for spawn this refresh.
        n_active: current count of running watcher tasks.
        max_active: configured soft cap on concurrent watchers.

    Returns: the list of IDs to spawn this refresh, in the order to spawn
    them. The cap is a NEW-non-priority gate — running watchers are never
    evicted to make room.
    """
    to_spawn: list[str] = []
    headroom = max(0, max_active - n_active)
    for ev_id in ordered:
        if ev_id in watched:
            continue
        if ev_id in priority:
            to_spawn.append(ev_id)
            continue
        if headroom > 0:
            to_spawn.append(ev_id)
            headroom -= 1
    return to_spawn
```

- [ ] **Step 4: Run the cap-gate tests, see them pass**

Run: `python -m pytest tests/test_main_global_sweep.py -v`
Expected: PASS — 4 tests.

- [ ] **Step 5: Wire `_decide_spawns` + global_sweep config into `run`**

In `src/odds_scraper/main.py`, replace the existing initial-resolve block (around lines 175-203):

```python
        bp_client = clients[Bookmaker.BETPAWA]
        initial_ids, priority_ids = await resolve_event_ids(
            standalone_events=cfg.events,
            tournaments=cfg.tournaments,
            bp_client=bp_client,
        )
        log.info(
            "initial event set: %d (from %d standalone + %d tournaments)",
            len(initial_ids), len(cfg.events), len(cfg.tournaments),
        )

        watched_ids: set[str] = set()
        tasks: list[asyncio.Task] = []

        def _spawn_watcher(ev_id: str) -> None:
            if ev_id in watched_ids:
                return
            watched_ids.add(ev_id)
            w = EventWatcher(
                event_bp_id=ev_id, cfg=watcher_cfg,
                bp_client=bp_client, collector=collector,
                writer=writer, resolver=resolver,
            )
            tasks.append(asyncio.create_task(
                supervise_watcher(w, ev_id), name=f"watcher-{ev_id}",
            ))

        for ev_id in initial_ids:
            _spawn_watcher(ev_id)
```

with:

```python
        bp_client = clients[Bookmaker.BETPAWA]
        initial_ids, priority_ids = await resolve_event_ids(
            standalone_events=cfg.events,
            tournaments=cfg.tournaments,
            bp_client=bp_client,
            global_sweep=cfg.global_sweep.enabled,
            sport_id=cfg.global_sweep.sport_id,
            event_types=cfg.global_sweep.event_types,
        )
        n_global_initial = len(initial_ids) - len(priority_ids)
        log.info(
            "initial event set: %d (priority=%d from %d standalone + %d tournaments; global=%d; cap=%d)",
            len(initial_ids), len(priority_ids),
            len(cfg.events), len(cfg.tournaments),
            n_global_initial, cfg.max_active_watchers,
        )

        watched_ids: set[str] = set()
        tasks: list[asyncio.Task] = []

        def _spawn_watcher(ev_id: str) -> None:
            if ev_id in watched_ids:
                return
            watched_ids.add(ev_id)
            w = EventWatcher(
                event_bp_id=ev_id, cfg=watcher_cfg,
                bp_client=bp_client, collector=collector,
                writer=writer, resolver=resolver,
            )
            tasks.append(asyncio.create_task(
                supervise_watcher(w, ev_id), name=f"watcher-{ev_id}",
            ))

        for ev_id in _decide_spawns(
            ordered=initial_ids,
            priority=priority_ids,
            watched=watched_ids,
            n_active=0,
            max_active=cfg.max_active_watchers,
        ):
            _spawn_watcher(ev_id)
```

Then replace the refresh-loop's resolve + spawn block (around lines 228-242):

```python
                    current, priority_ids = await resolve_event_ids(
                        standalone_events=cfg.events,
                        tournaments=cfg.tournaments,
                        bp_client=bp_client,
                    )
                    new_ids = [i for i in current if i not in watched_ids]
                    if new_ids:
                        log.info("refresh: spawning %d new watchers", len(new_ids))
                        for ev_id in new_ids:
                            _spawn_watcher(ev_id)
                    else:
                        log.info(
                            "refresh: no new events (active watchers: %d)",
                            len(tasks),
                        )
```

with:

```python
                    current, priority_ids = await resolve_event_ids(
                        standalone_events=cfg.events,
                        tournaments=cfg.tournaments,
                        bp_client=bp_client,
                        global_sweep=cfg.global_sweep.enabled,
                        sport_id=cfg.global_sweep.sport_id,
                        event_types=cfg.global_sweep.event_types,
                    )
                    to_spawn = _decide_spawns(
                        ordered=current,
                        priority=priority_ids,
                        watched=watched_ids,
                        n_active=len(tasks),
                        max_active=cfg.max_active_watchers,
                    )
                    if to_spawn:
                        log.info(
                            "refresh: spawning %d new watchers (active=%d, cap=%d)",
                            len(to_spawn), len(tasks), cfg.max_active_watchers,
                        )
                        for ev_id in to_spawn:
                            _spawn_watcher(ev_id)
                    else:
                        log.info(
                            "refresh: no new events (active watchers: %d, cap=%d)",
                            len(tasks), cfg.max_active_watchers,
                        )
```

- [ ] **Step 6: Run the full test suite to confirm nothing else broke**

Run: `python -m pytest -q`
Expected: PASS — all tests, including the 4 new cap-gate tests.

- [ ] **Step 7: Manual import-sanity check**

Run: `python -c "from odds_scraper.main import run, _decide_spawns; print('ok')"`
Expected: `ok` — confirms `main.py` parses and exposes the helper for tests.

- [ ] **Step 8: Commit**

```bash
git add src/odds_scraper/main.py tests/test_main_global_sweep.py
git commit -m "feat(main): apply spawn cap, wire global_sweep config

_refresh_loop now consults _decide_spawns to decide which resolved
events to spawn this tick:
  - priority IDs (standalone + tournament-expanded) always spawn
  - non-priority (global-sweep) IDs spawn only while n_active < cap

Global sweep is opt-in via cfg.global_sweep.enabled; default-off
configs see no behavior change."
```

---

## Task 6: Smoke test against real BP

**Files:**
- Modify: `config.yaml` (temporary, revert before final commit)

Final verification that the new path makes a real BP call and the cap behaves as expected.

- [ ] **Step 1: Add the global_sweep block to `config.yaml` for smoke**

Open `config.yaml` and append:

```yaml
max_active_watchers: 10
global_sweep:
  enabled: true
  sport_id: "2"
  event_types: [UPCOMING, LIVE]
```

- [ ] **Step 2: Start the scraper for one refresh cycle**

Run: `python -m odds_scraper`

Watch the logs. Within ~30 seconds you should see a single line resembling:

```
initial event set: 10 (priority=N from M standalone + K tournaments; global=Q; cap=10)
```

where `priority` equals your existing standalone + tournament count and `global` is non-zero. The cap should hold the total at 10 (priority + first few global by kickoff).

- [ ] **Step 3: Stop the scraper (Ctrl+C) and revert config**

Open `config.yaml` and remove the `max_active_watchers: 10` and `global_sweep:` block you added (or set them to the production values you actually want). The plan does not commit this smoke-test override.

Verify clean:

```bash
git diff config.yaml
```

Expected: no changes (you reverted the smoke override) OR only the production values you want to ship.

- [ ] **Step 4: Final test suite run**

Run: `python -m pytest -q`
Expected: all tests pass.

- [ ] **Step 5: Final commit (only if you kept production config values)**

If you intentionally want `global_sweep.enabled: true` in the committed config:

```bash
git add config.yaml
git commit -m "config: enable global BP sweep with cap"
```

Otherwise skip this step — the feature ships in code, configs stay opt-in per environment.

---

## Self-review

**Spec coverage:**

- "Replace tournament-driven discovery with global sweep" → Tasks 1 + 3
- "Tournaments list stays as always-include overlay" → priority_ids in Task 2 + cap bypass in Task 5
- "Soft cap, kickoff ASC, default 500" → Task 1 sort + Task 4 config + Task 5 `_decide_spawns`
- "All new config fields optional with safe defaults" → Task 4
- "Existing tests pass without modification" → Task 2 step 6 updates tests for the tuple return (unavoidable since the return type changed); Tasks 4–5 only add tests
- "Per-page failure logs and breaks pagination for that event_type" → Task 1 step 5 covers this
- "Kickoff missing drops to end" → Task 1 step 1 + step 3 sort key

**Placeholder scan:** none — every code block is concrete and complete.

**Type consistency:** `resolve_event_ids` returns `tuple[list[str], set[str]]` throughout (Tasks 2, 3). `_decide_spawns` signature matches its call sites in Task 5. `GlobalSweepConfig.event_types` is `tuple[str, ...]` everywhere (Tasks 3, 4, 5).
