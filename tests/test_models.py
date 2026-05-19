from datetime import datetime, timezone

from odds_scraper.models import (
    Bookmaker, EventStatus, FetchStatus, Market, Outcome,
    ResolvedIds, Snapshot,
)


def test_snapshot_to_csv_row_full():
    snap = Snapshot(
        ts_utc=datetime(2026, 5, 19, 14, 32, 5, tzinfo=timezone.utc),
        event_bp_id="33660318",
        sr_id="sr:match:12345",
        genius_id="g-67890",
        home="Team A", away="Team B",
        kickoff_utc=datetime(2026, 5, 19, 15, 0, 0, tzinfo=timezone.utc),
        status=EventStatus.STARTED,
        match_minute=34,
        score_home=1, score_away=0,
        bookmaker=Bookmaker.BETPAWA,
        market=Market.ONE_UP,
        outcome=Outcome.HOME,
        odds=1.85,
        probability=0.54054,
        fetch_status=FetchStatus.OK,
        fetch_error="",
    )
    row = snap.to_csv_row()
    assert row[0] == "2026-05-19T14:32:05Z"
    assert row[1] == "33660318"
    assert row[11] == "betpawa"
    assert row[12] == "1x2_1up_ft"
    assert row[13] == "home"
    assert row[14] == "1.85"
    assert row[15] == "0.54054"
    assert row[16] == "ok"


def test_snapshot_to_csv_row_failure_empty_columns():
    snap = Snapshot(
        ts_utc=datetime(2026, 5, 19, 14, 32, 5, tzinfo=timezone.utc),
        event_bp_id="33660318",
        sr_id="", genius_id="",
        home="Team A", away="Team B",
        kickoff_utc=datetime(2026, 5, 19, 15, 0, 0, tzinfo=timezone.utc),
        status=EventStatus.UPCOMING,
        match_minute=None, score_home=None, score_away=None,
        bookmaker=Bookmaker.BET9JA,
        market=Market.TWO_UP,
        outcome=Outcome.DRAW,
        odds=None, probability=None,
        fetch_status=FetchStatus.LOOKUP_FAILED,
        fetch_error="sb_id not found via sr/genius",
    )
    row = snap.to_csv_row()
    assert row[8] == ""
    assert row[9] == ""
    assert row[14] == ""
    assert row[15] == ""
    assert row[16] == "lookup_failed"
    assert row[17] == "sb_id not found via sr/genius"


def test_resolved_ids_matched_bookmakers():
    r = ResolvedIds(sr_id="sr:match:1", genius_id=None, sb_id="sr:match:1",
                    b9j_id=None, bw_id=None)
    assert r.matched_bookmakers() == {"sportybet"}
