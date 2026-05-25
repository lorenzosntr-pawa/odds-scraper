# BP Global Event Discovery Design

**Date:** 2026-05-25
**Status:** Approved (pending user review of this spec)

## Goal

Replace the tournament-list-driven event discovery with a global BetPawa
football sweep so the watcher covers every football event BP lists, not
just the competitions enumerated in `config.yaml`. The existing
`tournaments` list stays as an always-include overlay; a new soft cap
keeps concurrent watcher count bounded.

## Motivation

Today, `event_resolver.resolve_event_ids` only knows about events that
roll up under tournament IDs in `config.yaml`. Coverage drops the moment
a relevant fixture lives outside that whitelist — domestic cups, lower
divisions, regional friendlies, anything BP adds between config edits.
`bookieskit.BetPawa.get_events(tournament_id=None, ...)` already exposes
the global feed; we just don't use it.

## Non-goals

- No new bookmaker support (BP-only).
- No change to the watcher's tick loop, collector, or pricer.
- No change to the SQLite write path (the recent `busy_timeout` discussion
  is tracked separately).
- No automatic tournament tier filtering or quality scoring — the user
  explicitly asked for "everything BP lists for football, no filter."

## Architecture

The refresh loop in `main.py` already calls `resolve_event_ids` on a
schedule and spawns watchers for new IDs. Two changes:

1. `resolve_event_ids` gains an optional global-sweep stage that
   paginates `bp_client.get_events(tournament_id=None, sport_id="2",
   event_type=...)` for UPCOMING and LIVE, dedupes against priority
   IDs, and appends the result sorted by kickoff ASC.
2. The spawn loop in `main.py` honors a `max_active_watchers` cap for
   non-priority events; priority IDs (standalone events + tournament-
   expanded IDs) always spawn regardless of cap.

```
config.yaml
  events:              [...]    # standalone (always-include)
  tournaments:         [...]    # always-include overlay
  max_active_watchers: 500      # NEW
  global_sweep:                  # NEW
    enabled: true
    sport_id: "2"
    event_types: [UPCOMING, LIVE]

event_resolver.resolve_event_ids
  ├─ priority IDs: standalone + tournament-expanded   (always-include)
  └─ if global_sweep.enabled:
       global_ids = _fetch_global_event_ids(...)      (kickoff ASC)
       merge, dedupe

main._refresh_loop spawn gate
  for ev_id in resolved:
    if ev_id in priority_ids:           spawn unconditionally
    elif n_active < max_active_watchers: spawn
    else:                                skip (re-tried next refresh)
```

## Components

### `event_resolver._fetch_global_event_ids(bp_client, sport_id, event_types) -> list[str]`

Paginates `bp_client.get_events(tournament_id=None, sport_id=sport_id,
event_type=t, skip=N, take=100)` once per entry in `event_types`.
Walks the same `responses[].responses[]` shape that
`_ids_from_events_response` already handles, but also extracts each
entry's kickoff timestamp so the caller can sort the union.

Returns event IDs sorted by kickoff ASC; entries missing a kickoff drop
to the end (still spawnable if the cap allows, just not prioritized).

Per-page failure: log and break pagination for that `event_type`, return
what we have. Don't lose the whole sweep over one bad page.

### `event_resolver.resolve_event_ids` (extended signature)

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
```

Returns `(ordered_ids, priority_ids)` so the caller can apply the cap
without re-deriving the priority set. Ordered output:

1. Standalone events, declared order.
2. Tournament-expanded IDs, sorted lexicographically (unchanged from
   today).
3. Global-sweep IDs, sorted by kickoff ASC, deduped against the above.

`priority_ids` is the union of (1) and (2).

When `global_sweep=False`, behavior is byte-identical to today.

### `config.GlobalSweepConfig` and `config.Config.max_active_watchers`

```python
@dataclass
class GlobalSweepConfig:
    enabled: bool = False
    sport_id: str = "2"
    event_types: list[str] = field(default_factory=lambda: ["UPCOMING", "LIVE"])

@dataclass
class Config:
    ...
    max_active_watchers: int = 500
    global_sweep: GlobalSweepConfig = field(default_factory=GlobalSweepConfig)
```

All new fields are optional with safe defaults so existing `config.yaml`
files load unchanged and the new behavior is opt-in.

### `main._refresh_loop` cap gate

The refresh loop already prunes finished watcher tasks before deciding
to sleep. After the prune, when iterating `resolve_event_ids` output:

- Track `priority_ids` from the resolver's second return value.
- For each `ev_id` not already in `watched_ids`:
  - If `ev_id in priority_ids`: call `_spawn_watcher(ev_id)`.
  - Else if `len(tasks) < cfg.max_active_watchers`: call `_spawn_watcher`
    (and increment a local counter so the same refresh tick doesn't
    overspawn).
  - Else: skip; next refresh will retry once slots free up.

No separate queue: the resolver already returns kickoff-sorted output,
so re-resolving naturally re-presents the next batch.

## Data flow

```
[clock] every refresh_interval_seconds (or *_when_idle when no watchers)
   │
   ▼
resolve_event_ids(standalone, tournaments, bp_client,
                  global_sweep=cfg.global_sweep.enabled,
                  sport_id=cfg.global_sweep.sport_id,
                  event_types=cfg.global_sweep.event_types)
   │
   ▼
 (ordered_ids, priority_ids)
   │
   ▼
main._refresh_loop walks ordered_ids
   │   priority -> always spawn
   │   non-priority -> spawn iff n_active < max_active_watchers
   ▼
EventWatcher tasks (unchanged downstream)
```

## Error handling

| Scenario | Behavior |
|---|---|
| Global feed entirely unavailable (BP API down) | Log; treat as empty list. Priority IDs still flow through. |
| One page of the global sweep raises | Log; break pagination for that `event_type`; keep what we have. |
| Kickoff missing for some entries | Sort them to the end of the global list. They still spawn if the cap allows. |
| Cap reached, more events queued | Skip; next refresh retries. Priority IDs always bypass the cap. |
| Watcher exits (ENDED, watchdog, crash) | Existing prune logic discards from `watched_ids`. Next refresh fills the freed slot from the queue head. |

## Testing

### `tests/test_event_resolver.py` (extend existing)

- `_fetch_global_event_ids` with mock `bp_client` returning a multi-page
  response — pagination terminates correctly, IDs sorted by kickoff ASC,
  missing-kickoff entries last.
- `_fetch_global_event_ids` when one page raises — returns partial
  results, no propagation.
- `resolve_event_ids(global_sweep=True)` with `events`, `tournaments`,
  and global pool overlapping — dedup correct, ordering correct
  (standalone → tournaments → global tail), `priority_ids` matches
  standalone ∪ tournament-expanded.
- `resolve_event_ids(global_sweep=False)` returns same output as today
  (regression guard).

### `tests/test_main_global_sweep.py` (new, small)

- Cap respects priority: `max_active_watchers=2`, 1 priority event +
  5 global events → priority spawns, only 1 global spawns (2 − 1 = 1
  slot for non-priority), remaining 4 queued.
- Re-fill on completion: after a non-priority watcher exits, next
  refresh spawns the next queued global event.

### Manual smoke

`global_sweep.enabled: true`, `max_active_watchers: 10`. One refresh
cycle should log
`initial event set: N (from M standalone + K tournaments + L global, capped at 10)`.

## Backward compatibility

- All new config fields optional with defaults preserving today's
  behavior (`global_sweep.enabled: false`, `max_active_watchers: 500`).
- `resolve_event_ids` keyword-only new args; existing callers (one in
  `main.py`) need a no-op signature update.
- Existing tests pass without modification.

## Rollout

1. Land code + tests under feature-off default.
2. User flips `global_sweep.enabled: true` in their `config.yaml` and
   restarts the scraper. Initial sweep populates several hundred
   watchers over a few minutes.
3. Observe DB lock contention, BP API rate-limit behavior, and watcher
   count over 1–2 days. If the cap proves too high or too low, adjust
   `max_active_watchers` in config — no code change needed.

## Open questions / deferred

- **Busy-timeout pragma on the watcher's writer**: separate concern,
  separate commit. The current SQLite lock contention is a known issue
  from the recent backfill; global sweep does not introduce new lock
  surface area, only amplifies existing.
- **Cap eviction policy**: this design treats the cap as a *new-spawn*
  gate only — no eviction of running watchers to make room for closer
  kickoffs. Eviction would be a follow-up if real load shows priority
  inversion (e.g. far-future watcher blocking a near-kickoff event from
  spawning). Punted until we see it in practice.
