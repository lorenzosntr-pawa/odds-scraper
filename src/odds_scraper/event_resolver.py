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
    # Sort so the helper's contract is deterministic in isolation. Caller
    # already re-sorts at the end, but a future maintainer could drop that
    # without noticing.
    return sorted(found)


def _ids_from_events_response(resp: dict[str, Any]) -> list[str]:
    """Walk BetPawa's `responses[].responses[]` shape and pull event IDs."""
    out: list[str] = []
    for outer in resp.get("responses") or []:
        for entry in outer.get("responses") or []:
            ev_id = entry.get("id")
            if ev_id is not None:
                out.append(str(ev_id))
    return out
