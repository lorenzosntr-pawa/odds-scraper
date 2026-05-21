# Tournament + standalone-event config — design

**Status:** approved 2026-05-21
**Touches:** `config.py`, `main.py`, new `event_resolver.py`, `tests/`
**Untouched:** `models.py`, `collector.py`, `writer.py`, `watcher.py`, `registry.py`, `resolution*.py`, `status.py`

## Motivation

`config.yaml` currently lists every event ID by hand (`events: [33638734, ...]`). For a server deployment that monitors entire leagues continuously, this is unworkable:

1. Premier League / Serie A / etc. have hundreds of events per season; listing them all is tedious.
2. Event IDs change weekly (new matchday); the config drifts every Saturday.
3. The process needs to survive multi-day uptime, picking up new events as BetPawa schedules them — not be a one-off invocation.

Bookieskit already exposes `BetPawa.get_events(tournament_id=..., event_type="UPCOMING"|"LIVE")` and that's the foundation here: let the config name a tournament, the scraper expands it to live IDs on the fly, and a periodic refresh picks up new events while running.

## Settled inputs

| Decision | Value |
|---|---|
| Config shape | Top-level `events:` (standalone) + `tournaments:` (auto-expanded). Either may be empty. |
| Tournament scope per refresh | Union of `UPCOMING` + `LIVE` event lists |
| Refresh cadence (active) | `refresh_interval_seconds: 86400` (24h, configurable) |
| Refresh cadence (idle: no active watchers) | `refresh_interval_when_idle_seconds: 600` (10 min, configurable) |
| Per-tournament failure | Log warning, skip; healthy tournaments + standalone events still watched |
| Shutdown trigger | ONLY Ctrl+C / SIGTERM. "All watchers done" no longer auto-exits. |
| Sport scope | Football only (bookieskit's `sport_id="2"` default); not exposed |
| Pagination | `take=100`, walk `skip` until empty or partial page |
| Dedup | Standalone first (declared order), then sorted tournament-expanded; one event watched at most once per process lifetime |

## Architecture

### Config schema (`src/odds_scraper/config.py`)

```python
@dataclass(frozen=True)
class AppConfig:
    country: str
    events: Sequence[str]                       # standalone IDs (unchanged)
    tournaments: Sequence[str]                  # NEW — competition IDs
    refresh_interval_seconds: int               # NEW — default 86400
    refresh_interval_when_idle_seconds: int     # NEW — default 600
    cadence: CadenceConfig
    output: OutputConfig
    log_level: str
```

`load_config` defaults missing fields:

```python
tournaments=[str(t) for t in raw.get("tournaments") or []],
refresh_interval_seconds=int(raw.get("refresh_interval_seconds", 86400)),
refresh_interval_when_idle_seconds=int(
    raw.get("refresh_interval_when_idle_seconds", 600),
),
```

Existing configs that lack the new fields keep working — they degenerate to standalone-only with a 24h/10min default refresh schedule (which is harmless if `tournaments` is empty — the refresh loop will find no new events and idle).

### Example config

```yaml
country: ng
events:
  - 33638734
  - 33583353
tournaments:
  - 11965        # Serie A
  - 11963        # Premier League
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

### New module — `src/odds_scraper/event_resolver.py`

One public function, two private helpers.

```python
from __future__ import annotations

import asyncio
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
    IDs sorted lexicographically. Deterministic ordering keeps logs stable
    across runs.
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

**Module boundary:** the resolver takes raw lists (not `AppConfig`) so unit tests can drive it without constructing config objects. It depends only on the BetPawa client's `get_events()` shape — no other internals.

### `main.py` refresh-loop integration

Three changes to `_amain`:

1. **Initial event resolution** replaces `for ev in cfg.events:` watcher spawning.
2. **`watched_ids` set + idempotent `_spawn_watcher`** track who's running.
3. **Background `_refresh_loop` task** re-resolves on the active/idle interval and spawns new watchers for IDs not yet seen.

```python
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
```

**Shutdown logic — process only exits on stop signal:**

```python
# Replaces the previous asyncio.wait([wait_for_stop, all_watchers], ...)
wait_for_stop = asyncio.create_task(stop_event.wait())
await wait_for_stop

log.info("shutting down")
refresh_task.cancel()
for t in tasks:
    t.cancel()
await asyncio.gather(refresh_task, *tasks, return_exceptions=True)
```

### State table

| State | Sleep cadence | Refresh behavior |
|---|---|---|
| Startup: events resolved, watchers running | `refresh_interval_seconds` (24h) | Periodic |
| All watchers reach ENDED | `refresh_interval_when_idle_seconds` (10 min) | More frequent — picks up next-matchday fixtures faster |
| Refresh finds new events | Resume active cadence | New watchers spawned |
| All tournaments fail to resolve | Continues | Per-tournament warnings; standalone events keep working |
| No events AND no tournaments | Idle cadence | Process runs forever, refresh finds nothing, sleeps 10 min, repeats |
| Ctrl+C / SIGTERM | — | `stop_event` set; refresh + watchers cancelled; clean exit |

### Out of scope (explicit)

- **Removing watched IDs when watchers ENDed.** If a match is postponed and BetPawa re-lists it as UPCOMING tomorrow, the dedup will skip it. Solving this needs a watcher → main signaling channel; not worth the complexity for this rare case.
- **Race between `any_active` check and `asyncio.sleep` wakeup.** If the last active watcher completes during a 24h sleep, we wait the full 24h instead of switching to idle cadence. Worst case: one delayed refresh.
- **Non-football sports.** Bookieskit's `sport_id` defaults to `"2"` (football); we never override.
- **Tournaments whose ID is invalid format.** No client-side validation — we let `bp_client.get_events()` raise and rely on the per-tournament try/except.

## Tests

### `tests/test_event_resolver.py` — new

Mock the BetPawa client. Cover:

1. **Standalone-only** — config with only `events`, no tournaments → returns those IDs in declared order.
2. **Single tournament, single page** — one tournament returns 5 events → all 5 IDs in result.
3. **UPCOMING + LIVE union** — same event in both UPCOMING and LIVE responses → returned once.
4. **Pagination — 250 events** — mock returns 100, 100, 50 across three skip values → all 250 IDs.
5. **Pagination — exactly 100 events** — page 1 has 100, page 2 is empty → no infinite loop, 100 IDs.
6. **Per-tournament failure isolation** — three tournaments, middle one raises → 2/3 succeed, warning logged.
7. **Dedup: standalone + tournament overlap** — event 33 listed both as standalone and inside a tournament's event list → returned once.
8. **Dedup: same event in two tournaments** — event 99 listed by tournament A and B → returned once.
9. **Empty everything** — no standalone, no tournaments → `[]`.
10. **Tournament returns empty response** — `responses[].responses[]` empty → 0 IDs, no error.

### `tests/test_config.py` — additions

1. Load YAML with `tournaments: [11965, 11963]` → `AppConfig.tournaments == ["11965", "11963"]`.
2. Load YAML without `tournaments` key → `AppConfig.tournaments == []`.
3. Load YAML without `refresh_interval_seconds` → defaults to 86400.
4. Load YAML without `refresh_interval_when_idle_seconds` → defaults to 600.
5. Load YAML with all new fields set → values used as-is.

### Not unit-tested

- `_refresh_loop` itself (the wrapping loop with `asyncio.sleep` + closure access). Logic is mostly trivial wiring around `resolve_event_ids` (which is exhaustively tested) and `_spawn_watcher` (idempotent set membership). Covered indirectly by manual smoke + the existing main supervisor test.
