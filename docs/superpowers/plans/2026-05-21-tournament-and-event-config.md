# Tournament + standalone event config Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `config.yaml` declare tournament IDs that expand to event lists automatically, with a periodic refresh loop so the scraper picks up new fixtures while running on a server.

**Architecture:** A new `event_resolver.py` owns the BetPawa-API expansion of tournament IDs into event IDs (UPCOMING+LIVE union, paginated, per-tournament failure-tolerant). `config.py` grows two optional knobs (`tournaments`, `refresh_interval_seconds`, `refresh_interval_when_idle_seconds`). `main.py` calls the resolver at startup, runs a background refresh-loop task that re-resolves on a cadence (long when watchers are active, short when idle) and spawns watchers for any newly-discovered event IDs. Process only exits on stop signal — "all watchers done" no longer auto-shuts-down.

**Tech Stack:** Python 3.11+, asyncio, pytest with pytest-asyncio (auto mode), bookieskit (`BetPawa.get_events(tournament_id, event_type, skip, take)`).

**Spec reference:** `docs/superpowers/specs/2026-05-21-tournament-and-event-config-design.md`

---

## File map

| Action | File | Responsibility |
|---|---|---|
| Create | `src/odds_scraper/event_resolver.py` | `resolve_event_ids(standalone_events, tournaments, bp_client) -> list[str]` plus two private helpers `_fetch_tournament_event_ids` and `_ids_from_events_response`. No state; one entry point. |
| Modify | `src/odds_scraper/config.py` | Add `tournaments`, `refresh_interval_seconds`, `refresh_interval_when_idle_seconds` to `AppConfig`. Defaults applied when keys are absent. |
| Modify | `src/odds_scraper/main.py` | Replace direct `cfg.events` loop with `resolve_event_ids` call; add `watched_ids` set + idempotent `_spawn_watcher`; add `_refresh_loop` background task with active/idle cadence; remove auto-exit on watchers-done. |
| Create | `tests/test_event_resolver.py` | 10 tests covering resolver behavior. |
| Modify | `tests/test_config.py` | Add 5 tests covering new fields + backward-compat defaults. |
| Unchanged | `models.py`, `collector.py`, `writer.py`, `watcher.py`, `registry.py`, `resolution*.py`, `status.py`, all their tests | — |

---

## Task 1: Config schema — `tournaments` + refresh intervals with defaults

**Files:**
- Modify: `src/odds_scraper/config.py`
- Test: `tests/test_config.py`

### Step 1.1 — Write failing tests

- [ ] **Append these tests to `tests/test_config.py`** (keep the existing two tests; just add at the end of the file):

```python
def test_load_with_tournaments(tmp_path: Path):
    p = _write(tmp_path / "c.yaml", """
        country: ng
        events: [11111]
        tournaments: [11965, 11963]
        cadence:
          prematch_seconds: 600
          live_seconds: 90
          status_retry_backoff_seconds: [5, 15, 45]
          watchdog_after_kickoff_seconds: 10800
        output:
          csv_path: data/x.csv
          resolution_cache_path: data/r.json
        log_level: INFO
    """)
    cfg = load_config(p)
    assert cfg.tournaments == ["11965", "11963"]


def test_load_without_tournaments_defaults_to_empty(tmp_path: Path):
    p = _write(tmp_path / "c.yaml", """
        country: ng
        events: [11111]
        cadence:
          prematch_seconds: 600
          live_seconds: 90
          status_retry_backoff_seconds: [5, 15, 45]
          watchdog_after_kickoff_seconds: 10800
        output:
          csv_path: data/x.csv
          resolution_cache_path: data/r.json
        log_level: INFO
    """)
    cfg = load_config(p)
    assert cfg.tournaments == []


def test_refresh_interval_seconds_default_is_86400(tmp_path: Path):
    p = _write(tmp_path / "c.yaml", """
        country: ng
        events: [1]
        cadence:
          prematch_seconds: 600
          live_seconds: 90
          status_retry_backoff_seconds: [5, 15, 45]
          watchdog_after_kickoff_seconds: 10800
        output:
          csv_path: a.csv
          resolution_cache_path: b.json
        log_level: INFO
    """)
    cfg = load_config(p)
    assert cfg.refresh_interval_seconds == 86400


def test_refresh_interval_when_idle_seconds_default_is_600(tmp_path: Path):
    p = _write(tmp_path / "c.yaml", """
        country: ng
        events: [1]
        cadence:
          prematch_seconds: 600
          live_seconds: 90
          status_retry_backoff_seconds: [5, 15, 45]
          watchdog_after_kickoff_seconds: 10800
        output:
          csv_path: a.csv
          resolution_cache_path: b.json
        log_level: INFO
    """)
    cfg = load_config(p)
    assert cfg.refresh_interval_when_idle_seconds == 600


def test_load_with_all_new_fields_explicit(tmp_path: Path):
    p = _write(tmp_path / "c.yaml", """
        country: ng
        events: [11111]
        tournaments: [42]
        refresh_interval_seconds: 3600
        refresh_interval_when_idle_seconds: 120
        cadence:
          prematch_seconds: 600
          live_seconds: 90
          status_retry_backoff_seconds: [5, 15, 45]
          watchdog_after_kickoff_seconds: 10800
        output:
          csv_path: a.csv
          resolution_cache_path: b.json
        log_level: INFO
    """)
    cfg = load_config(p)
    assert cfg.tournaments == ["42"]
    assert cfg.refresh_interval_seconds == 3600
    assert cfg.refresh_interval_when_idle_seconds == 120
```

- [ ] **Run config tests — verify the new tests FAIL**

Run: `.venv/Scripts/python.exe -m pytest tests/test_config.py -v`
Expected: the first two existing tests pass, the 5 new tests fail with `AttributeError: 'AppConfig' object has no attribute 'tournaments'` (or similar).

### Step 1.2 — Add the new fields

- [ ] **Edit `src/odds_scraper/config.py`** — replace the file's full content with:

```python
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import yaml


@dataclass(frozen=True)
class CadenceConfig:
    prematch_seconds: int
    live_seconds: int
    status_retry_backoff_seconds: tuple[int, ...]
    watchdog_after_kickoff_seconds: int


@dataclass(frozen=True)
class OutputConfig:
    csv_path: str
    resolution_cache_path: str


@dataclass(frozen=True)
class AppConfig:
    country: str
    events: Sequence[str]
    tournaments: Sequence[str]
    refresh_interval_seconds: int
    refresh_interval_when_idle_seconds: int
    cadence: CadenceConfig
    output: OutputConfig
    log_level: str


def load_config(path: Path | str) -> AppConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    cad = raw["cadence"]
    out = raw["output"]
    return AppConfig(
        country=str(raw["country"]),
        events=[str(e) for e in raw["events"]],
        tournaments=[str(t) for t in (raw.get("tournaments") or [])],
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
        ),
        output=OutputConfig(
            csv_path=str(out["csv_path"]),
            resolution_cache_path=str(out["resolution_cache_path"]),
        ),
        log_level=os.environ.get(
            "ODDS_SCRAPER_LOG_LEVEL", str(raw.get("log_level", "INFO")),
        ),
    )
```

- [ ] **Run config tests — verify all 7 pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_config.py -v`
Expected: 7 tests pass.

- [ ] **Run full suite — confirm `test_main_supervisor.py` is the only thing that breaks**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: pass everywhere except possibly `test_main_supervisor.py`. The test fixture there may need to be updated if it directly constructs `AppConfig` (which now requires three new fields). If `test_main_supervisor.py` only mocks pieces and doesn't build a full AppConfig, it'll still pass — check it.

If it fails: open `tests/test_main_supervisor.py` and find any `AppConfig(...)` construction; add `tournaments=[]`, `refresh_interval_seconds=86400`, `refresh_interval_when_idle_seconds=600` to the kwargs. (Do NOT change behaviors — just add the fields with defaults so construction succeeds.) Re-run the test.

### Step 1.3 — Commit

- [ ] **Commit**

```bash
git add src/odds_scraper/config.py tests/test_config.py
# also tests/test_main_supervisor.py if it needed AppConfig kwargs
git commit -m "$(cat <<'EOF'
feat(config): tournaments list + refresh-interval knobs

Adds Optional `tournaments`, `refresh_interval_seconds` (default 86400),
and `refresh_interval_when_idle_seconds` (default 600). Old configs
without these fields keep loading via defaults.
EOF
)"
```

---

## Task 2: `event_resolver.py` — tournament expansion module

**Files:**
- Create: `src/odds_scraper/event_resolver.py`
- Create: `tests/test_event_resolver.py`

### Step 2.1 — Write failing tests

- [ ] **Create `tests/test_event_resolver.py`** with full content:

```python
from unittest.mock import AsyncMock

import pytest

from odds_scraper.event_resolver import resolve_event_ids


def _events_response(ids: list[str]) -> dict:
    """Build a BetPawa-shaped events response carrying these IDs."""
    return {
        "responses": [
            {"responses": [{"id": ev_id, "participants": []} for ev_id in ids]}
        ]
    }


def _make_bp_client(tournament_responses: dict) -> AsyncMock:
    """Mock a BetPawa client.

    tournament_responses maps (tournament_id, event_type, skip) -> response dict
    OR a single response dict to return regardless of args.
    Unknown keys return empty.
    """
    client = AsyncMock()

    async def get_events(tournament_id, event_type, skip, take):
        key = (str(tournament_id), event_type, skip)
        if key in tournament_responses:
            return tournament_responses[key]
        return _events_response([])

    client.get_events.side_effect = get_events
    return client


@pytest.mark.asyncio
async def test_standalone_only_returns_ids_in_declared_order():
    client = _make_bp_client({})
    out = await resolve_event_ids(
        standalone_events=["33638734", "33583353"],
        tournaments=[],
        bp_client=client,
    )
    assert out == ["33638734", "33583353"]
    client.get_events.assert_not_awaited()


@pytest.mark.asyncio
async def test_single_tournament_single_page():
    client = _make_bp_client({
        ("11965", "UPCOMING", 0): _events_response(["1", "2", "3"]),
        ("11965", "LIVE", 0): _events_response([]),
    })
    out = await resolve_event_ids(
        standalone_events=[],
        tournaments=["11965"],
        bp_client=client,
    )
    assert sorted(out) == ["1", "2", "3"]


@pytest.mark.asyncio
async def test_upcoming_and_live_unioned_dedup_within_tournament():
    client = _make_bp_client({
        ("11965", "UPCOMING", 0): _events_response(["1", "2"]),
        ("11965", "LIVE", 0): _events_response(["2", "3"]),  # 2 appears in both
    })
    out = await resolve_event_ids(
        standalone_events=[],
        tournaments=["11965"],
        bp_client=client,
    )
    assert sorted(out) == ["1", "2", "3"]


@pytest.mark.asyncio
async def test_pagination_walks_until_partial_page():
    page1 = [str(i) for i in range(100)]      # 0..99
    page2 = [str(i) for i in range(100, 200)] # 100..199
    page3 = [str(i) for i in range(200, 250)] # 200..249 (partial — stops)
    client = _make_bp_client({
        ("9", "UPCOMING", 0): _events_response(page1),
        ("9", "UPCOMING", 100): _events_response(page2),
        ("9", "UPCOMING", 200): _events_response(page3),
        ("9", "LIVE", 0): _events_response([]),
    })
    out = await resolve_event_ids(
        standalone_events=[],
        tournaments=["9"],
        bp_client=client,
    )
    assert len(out) == 250
    assert set(out) == set(str(i) for i in range(250))


@pytest.mark.asyncio
async def test_pagination_exactly_100_followed_by_empty():
    page1 = [str(i) for i in range(100)]
    client = _make_bp_client({
        ("9", "UPCOMING", 0): _events_response(page1),
        ("9", "UPCOMING", 100): _events_response([]),  # empty => break
        ("9", "LIVE", 0): _events_response([]),
    })
    out = await resolve_event_ids(
        standalone_events=[],
        tournaments=["9"],
        bp_client=client,
    )
    assert len(out) == 100


@pytest.mark.asyncio
async def test_one_tournament_failure_isolated_others_succeed(caplog):
    import logging
    client = AsyncMock()

    async def get_events(tournament_id, event_type, skip, take):
        if str(tournament_id) == "BROKEN":
            raise RuntimeError("HTTP 500 disaster strikes")
        if str(tournament_id) == "OK1" and event_type == "UPCOMING" and skip == 0:
            return _events_response(["a", "b"])
        if str(tournament_id) == "OK2" and event_type == "UPCOMING" and skip == 0:
            return _events_response(["c"])
        return _events_response([])

    client.get_events.side_effect = get_events

    with caplog.at_level(logging.WARNING, logger="odds_scraper.event_resolver"):
        out = await resolve_event_ids(
            standalone_events=[],
            tournaments=["OK1", "BROKEN", "OK2"],
            bp_client=client,
        )
    assert sorted(out) == ["a", "b", "c"]
    assert any("BROKEN" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_dedup_standalone_and_tournament_event():
    client = _make_bp_client({
        ("11965", "UPCOMING", 0): _events_response(["33", "44"]),
        ("11965", "LIVE", 0): _events_response([]),
    })
    out = await resolve_event_ids(
        standalone_events=["33"],         # also appears in tournament
        tournaments=["11965"],
        bp_client=client,
    )
    # 33 should appear exactly once; it stays as the standalone (first position)
    assert out.count("33") == 1
    assert out[0] == "33"  # standalone-first ordering
    assert sorted(out) == ["33", "44"]


@pytest.mark.asyncio
async def test_dedup_same_event_across_tournaments():
    client = _make_bp_client({
        ("A", "UPCOMING", 0): _events_response(["99", "100"]),
        ("A", "LIVE", 0): _events_response([]),
        ("B", "UPCOMING", 0): _events_response(["99", "200"]),
        ("B", "LIVE", 0): _events_response([]),
    })
    out = await resolve_event_ids(
        standalone_events=[],
        tournaments=["A", "B"],
        bp_client=client,
    )
    assert out.count("99") == 1
    assert sorted(out) == ["100", "200", "99"]


@pytest.mark.asyncio
async def test_empty_inputs_returns_empty():
    client = _make_bp_client({})
    out = await resolve_event_ids(
        standalone_events=[],
        tournaments=[],
        bp_client=client,
    )
    assert out == []


@pytest.mark.asyncio
async def test_tournament_returning_empty_response_yields_no_events():
    client = _make_bp_client({
        ("X", "UPCOMING", 0): _events_response([]),
        ("X", "LIVE", 0): _events_response([]),
    })
    out = await resolve_event_ids(
        standalone_events=[],
        tournaments=["X"],
        bp_client=client,
    )
    assert out == []
```

- [ ] **Run resolver tests — verify they ALL fail (module doesn't exist)**

Run: `.venv/Scripts/python.exe -m pytest tests/test_event_resolver.py -v`
Expected: `ModuleNotFoundError: No module named 'odds_scraper.event_resolver'`.

### Step 2.2 — Create the resolver module

- [ ] **Create `src/odds_scraper/event_resolver.py`** with full content:

```python
from __future__ import annotations

import logging
from typing import Any, Sequence

log = logging.getLogger(__name__)

_TOURNAMENT_PAGE_SIZE = 100
_EVENT_TYPES = ("UPCOMING", "LIVE")


async def resolve_event_ids(
    standalone_events: Sequence[str],
    tournaments: Sequence[str],
    bp_client,
) -> list[str]:
    """One-shot: expand tournaments to event IDs, union with standalone IDs,
    dedupe. Per-tournament failures are logged and skipped, not raised.

    Returns: standalone IDs first in declared order, then tournament-expanded
    IDs sorted lexicographically. Deterministic ordering keeps logs stable.
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
    return ordered


async def _fetch_tournament_event_ids(t_id: str, bp_client) -> list[str]:
    """UPCOMING + LIVE union for one tournament, paginated. Unique IDs."""
    found: set[str] = set()
    for event_type in _EVENT_TYPES:
        skip = 0
        while True:
            resp = await bp_client.get_events(
                tournament_id=t_id,
                event_type=event_type,
                skip=skip,
                take=_TOURNAMENT_PAGE_SIZE,
            )
            ids = _ids_from_events_response(resp)
            if not ids:
                break
            found.update(ids)
            if len(ids) < _TOURNAMENT_PAGE_SIZE:
                break
            skip += _TOURNAMENT_PAGE_SIZE
    return list(found)


def _ids_from_events_response(resp: dict[str, Any]) -> list[str]:
    """Walk BetPawa's `responses[].responses[]` shape and pull event IDs."""
    out: list[str] = []
    for outer in resp.get("responses") or []:
        for entry in outer.get("responses") or []:
            ev_id = entry.get("id")
            if ev_id is not None:
                out.append(str(ev_id))
    return out
```

- [ ] **Run resolver tests — verify all 10 pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_event_resolver.py -v`
Expected: 10 tests pass.

### Step 2.3 — Commit

- [ ] **Commit**

```bash
git add src/odds_scraper/event_resolver.py tests/test_event_resolver.py
git commit -m "$(cat <<'EOF'
feat(event_resolver): tournament -> event IDs with UPCOMING+LIVE union

resolve_event_ids walks each tournament via BetPawa.get_events,
paginating until a partial/empty page. Per-tournament failures log
a warning and skip. Output deterministic: standalone IDs first in
declared order, then tournament-expanded IDs sorted.
EOF
)"
```

---

## Task 3: `main.py` — initial resolve + refresh loop + shutdown-only-on-signal

**Files:**
- Modify: `src/odds_scraper/main.py`

No new tests for the refresh loop itself (spec rationale: covered by resolver unit tests + manual smoke). `test_main_supervisor.py` covers `supervise_watcher`, which is untouched.

### Step 3.1 — Read current main.py to identify the section to rewrite

- [ ] **Read `src/odds_scraper/main.py`** end-to-end. The `_amain` function (currently lines 42-113) is what changes. The `supervise_watcher` function (lines 26-39) and `cli` (lines 116-123) are unchanged.

### Step 3.2 — Rewrite `_amain`

- [ ] **Replace the full `_amain` function** in `src/odds_scraper/main.py` with this version, AND update the file's import block at the top to include `resolve_event_ids`:

First, update the imports near the top of the file. Find:

```python
from .resolution_runtime import (
    make_bookmaker_clients, make_fetchers, resolve_event,
)
```

And add this line right after it:

```python
from .event_resolver import resolve_event_ids
```

Then replace the entire `_amain` function (from `async def _amain` down to the closing `return 0`) with:

```python
async def _amain(config_path: Path) -> int:
    cfg = load_config(config_path)
    logging.basicConfig(
        level=getattr(logging, cfg.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # bookieskit uses httpx; its per-request INFO logs drown out our own.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    cache = ResolutionCache(Path(cfg.output.resolution_cache_path))
    cache.load()
    registry = build_registry()

    async with AsyncExitStack() as stack:
        clients = await make_bookmaker_clients(stack, country=cfg.country)
        fetchers = make_fetchers(clients, registry=registry)
        collector = OddsCollector(fetchers=fetchers)
        writer = await stack.enter_async_context(CsvWriter(Path(cfg.output.csv_path)))

        async def resolver(detail: dict[str, Any]):
            return await resolve_event(detail, clients=clients, cache=cache)

        watcher_cfg = WatcherConfig(
            prematch_seconds=cfg.cadence.prematch_seconds,
            live_seconds=cfg.cadence.live_seconds,
            status_retry_backoff_seconds=cfg.cadence.status_retry_backoff_seconds,
            watchdog_after_kickoff_seconds=cfg.cadence.watchdog_after_kickoff_seconds,
        )

        bp_client = clients[Bookmaker.BETPAWA]
        initial_ids = await resolve_event_ids(
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

        async def _refresh_loop():
            while True:
                try:
                    any_active = any(not t.done() for t in tasks)
                    sleep_sec = (
                        cfg.refresh_interval_seconds if any_active
                        else cfg.refresh_interval_when_idle_seconds
                    )
                    await asyncio.sleep(sleep_sec)
                    current = await resolve_event_ids(
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
                        active_count = sum(1 for t in tasks if not t.done())
                        log.info(
                            "refresh: no new events (active watchers: %d)",
                            active_count,
                        )
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001
                    log.exception("refresh loop iteration failed — continuing")

        refresh_task = asyncio.create_task(_refresh_loop(), name="refresh-loop")

        stop_event = asyncio.Event()

        def _trip_stop():
            stop_event.set()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _trip_stop)
            except NotImplementedError:
                # Windows event loops don't support add_signal_handler;
                # Ctrl-C is delivered via KeyboardInterrupt in cli() instead.
                pass

        # Process only exits on stop signal. Watchers come and go;
        # the refresh loop keeps polling until cancelled.
        await stop_event.wait()

        log.info("shutting down, cancelling refresh + %d watcher tasks", len(tasks))
        refresh_task.cancel()
        for t in tasks:
            t.cancel()
        await asyncio.gather(refresh_task, *tasks, return_exceptions=True)

    return 0
```

### Step 3.3 — Smoke-test imports

- [ ] **Verify imports compile**

Run:
```bash
.venv/Scripts/python.exe -c "from odds_scraper.main import cli, supervise_watcher, _amain; from odds_scraper.event_resolver import resolve_event_ids; print('imports clean')"
```
Expected: `imports clean`.

### Step 3.4 — Run full suite

- [ ] **Run the entire test suite**

Run: `.venv/Scripts/python.exe -m pytest -v`
Expected: all tests pass. The 5 new config tests + 10 new resolver tests + all existing 57 = 72 tests pass.

If `test_main_supervisor.py` fails because of the import or any `_amain` orchestration mock, check that the test still constructs `supervise_watcher` directly (it should — `supervise_watcher` is unchanged). If it constructs `AppConfig`, ensure the three new fields are passed.

### Step 3.5 — Commit

- [ ] **Commit**

```bash
git add src/odds_scraper/main.py
git commit -m "$(cat <<'EOF'
feat(main): resolve tournaments + refresh loop; no auto-exit on idle

_amain now calls resolve_event_ids at startup, tracks watched event IDs
in a set, and runs a background _refresh_loop task that re-resolves on
the active or idle cadence (from config). New event IDs get watchers
spawned on the fly. Process only exits on stop signal — "all watchers
done" no longer terminates, so the scraper can keep monitoring a server
through league breaks and matchday transitions.
EOF
)"
```

---

## Task 4: Update example `config.yaml`

**Files:**
- Modify: `config.yaml` (project root) — OPTIONAL but recommended for documentation

The project's `config.yaml` should demonstrate the new features. If the user wants to keep their current event list, leave it alone. Otherwise:

- [ ] **Read current `config.yaml`**.

- [ ] **Add a `tournaments:` block and the two refresh-interval keys.** Example (only modify if appropriate — do NOT delete the user's existing event IDs):

```yaml
country: ng
events:
  - 33638734  # standalone events (optional now)
tournaments:
  # - 11965   # example: Serie A; uncomment to enable
  # - 11963   # example: Premier League
refresh_interval_seconds: 86400
refresh_interval_when_idle_seconds: 600
cadence:
  prematch_seconds: 600
  live_seconds: 90
  status_retry_backoff_seconds: [5, 15, 45]
  watchdog_after_kickoff_seconds: 10800
output:
  csv_path: data/odds_snapshots.csv
  resolution_cache_path: data/resolution_cache.json
log_level: INFO
```

**Skip this task entirely if the user prefers to keep their personal config.yaml as-is.** Both old and new YAML shapes are supported.

- [ ] **Decide whether to commit**

If you updated `config.yaml`:
```bash
git add config.yaml
git commit -m "docs(config): example tournaments + refresh-interval keys"
```
Otherwise skip.

---

## Task 5: Full-suite smoke + manual run

**Files:** none modified; verification only.

### Step 5.1 — Run all tests

- [ ] **Run all tests**

Run: `.venv/Scripts/python.exe -m pytest -v`
Expected: every test passes (72 total). If anything fails, investigate before continuing — most likely cause is `test_main_supervisor.py` needing the 3 new `AppConfig` fields.

### Step 5.2 — Manual scraper run with tournaments

- [ ] **Edit a temporary `config.yaml` to use tournaments**

For this smoke test, point at a known small tournament (e.g., a small league or cup) to keep the event count low. Comment out the standalone `events` list.

- [ ] **Run the scraper**

Run: `python -m odds_scraper.main --config config.yaml`

Wait for these log lines (in order):
1. `building bet9ja prematch event map (one-shot, shared)` (existing behavior)
2. `tournament <id> expanded to N events (M new)` — one per configured tournament
3. `initial event set: N (from X standalone + Y tournaments)` — total event count
4. Per-event status transitions and tick summaries (existing behavior)

Let it run for a few minutes, then `Ctrl+C`. Expected on shutdown:
- `shutting down, cancelling refresh + N watcher tasks`

### Step 5.3 — Verify the refresh loop logs

- [ ] **Manually set a short refresh interval and re-run**

Temporarily set `refresh_interval_seconds: 60` and `refresh_interval_when_idle_seconds: 30` in `config.yaml`. Run again, let it run for 90+ seconds, then Ctrl+C. Look for:
- `refresh: no new events (active watchers: N)` OR
- `refresh: spawning N new watchers` (if the tournament added something)

The refresh loop firing on schedule confirms the new behavior works.

Restore your `refresh_interval_seconds` to a normal value (86400) before deploying.

### Step 5.4 — Commit any straggler fixes (if needed)

- [ ] If anything required a fix during the smoke test, commit it:

```bash
git add -A
git commit -m "chore: fix straggler from smoke test"
```

If everything worked first try, skip this step.

---

## Self-review

**Spec coverage:**
- Settled inputs table (config shape, scopes, cadences, failure mode) → Task 1 + Task 2 + Task 3
- New module `event_resolver.py` with `resolve_event_ids` + private helpers → Task 2
- Config schema with defaults → Task 1
- `main.py` refresh loop with active/idle cadence + idempotent spawn → Task 3
- "Shutdown only on stop signal" → Task 3 step 3.2 (replaced `asyncio.wait` block)
- `tests/test_event_resolver.py` with 10 named tests → Task 2 step 2.1
- `tests/test_config.py` additions (5 new tests) → Task 1 step 1.1
- Smoke verification path → Task 5

**Placeholder scan:** No "TBD", no "implement later", every code change is full content, every command lists expected output.

**Type consistency:** Function names/signatures consistent across tasks:
- `resolve_event_ids(standalone_events, tournaments, bp_client) -> list[str]` — defined in Task 2 step 2.2, called in Task 3 step 3.2 with the same kwargs.
- `_spawn_watcher(ev_id: str) -> None` — defined in Task 3 step 3.2; called in same task; idempotent via `watched_ids` set.
- `AppConfig.tournaments`, `AppConfig.refresh_interval_seconds`, `AppConfig.refresh_interval_when_idle_seconds` — defined in Task 1, referenced in Task 3. All match.
- `BetPawa.get_events(tournament_id, event_type, skip, take)` — bookieskit API, used in Task 2 step 2.2 with kwargs (positional in tests' mock).
