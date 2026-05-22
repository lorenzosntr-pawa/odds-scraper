"""BetPawa event status / clock / score parsing.

Thin wrapper over bookieskit's `extract_live_info` that maps to our
EventStatus enum and tolerates the prematch / live / ended shapes.
"""

from __future__ import annotations

from typing import Any, Optional

from bookieskit import extract_live_info

from .models import EventStatus

_ENDED_PERIODS = {"FT", "AET", "PEN", "ENDED", "FINISHED"}


def parse_status(detail: dict[str, Any]) -> EventStatus:
    info = extract_live_info(detail, "betpawa")
    period = (info.period or "").upper() if info.period else ""

    if period in _ENDED_PERIODS:
        return EventStatus.ENDED

    additional = detail.get("additionalInfo") or {}
    live = additional.get("live")

    # BetPawa flips additionalInfo.live to False once a match ends but
    # keeps the final score in the detail dict. Without this branch,
    # the next check (score_home is not None → STARTED) would keep the
    # event live indefinitely even after FT — observed in production
    # for events where the period doesn't transition to "FT" / "FINAL".
    if live is False and info.score_home is not None:
        return EventStatus.ENDED

    if live is True:
        return EventStatus.STARTED

    if info.minute is not None or info.score_home is not None:
        return EventStatus.STARTED

    if detail.get("startTime"):
        return EventStatus.UPCOMING
    return EventStatus.UNKNOWN


def parse_clock(detail: dict[str, Any]) -> Optional[int]:
    if parse_status(detail) != EventStatus.STARTED:
        return None
    info = extract_live_info(detail, "betpawa")
    return info.minute


def parse_score(detail: dict[str, Any]) -> Optional[tuple[int, int]]:
    if parse_status(detail) not in (EventStatus.STARTED, EventStatus.ENDED):
        return None
    info = extract_live_info(detail, "betpawa")
    if info.score_home is None or info.score_away is None:
        return None
    return info.score_home, info.score_away
