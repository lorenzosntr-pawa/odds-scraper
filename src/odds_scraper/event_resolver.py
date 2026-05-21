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
