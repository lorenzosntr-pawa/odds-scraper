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
    watched = {"G1", "G2"}
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
