"""Shared test fixtures.

`pin_now` freezes the web query layer's notion of "now" to a fixed
instant near the test fixtures' hardcoded dates (kickoffs around
2026-05-22). Without this, the stale-upcoming filter in
`queries.get_events_by_status` (which hides UPCOMING events whose
kickoff is >48h in the past) would drop the fixtures once wall-clock
time moves past them, making the web tests time-bombs. Pinning `now`
keeps those fixed-date "upcoming" fixtures current.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def pin_now(monkeypatch):
    """Freeze queries._utcnow_iso so fixed-date upcoming fixtures stay
    within the stale-upcoming window. No-op for test modules that don't
    import the web queries layer."""
    try:
        from odds_scraper.web import queries
    except Exception:
        return
    monkeypatch.setattr(queries, "_utcnow_iso", lambda: "2026-05-22T12:00:00Z")
