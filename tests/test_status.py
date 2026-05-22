import json
from pathlib import Path

from odds_scraper.models import EventStatus
from odds_scraper.status import parse_status, parse_clock, parse_score

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_parse_status_upcoming():
    assert parse_status(_load("betpawa_event_upcoming.json")) == EventStatus.UPCOMING


def test_parse_status_started():
    assert parse_status(_load("betpawa_event_live.json")) == EventStatus.STARTED


def test_parse_status_ended():
    assert parse_status(_load("betpawa_event_ended.json")) == EventStatus.ENDED


def test_parse_status_real_capture_is_upcoming():
    detail = _load("betpawa_event_real_upcoming.json")
    assert parse_status(detail) == EventStatus.UPCOMING


def test_parse_clock_live_returns_minute():
    assert parse_clock(_load("betpawa_event_live.json")) == 34


def test_parse_clock_returns_none_when_prematch():
    assert parse_clock(_load("betpawa_event_upcoming.json")) is None


def test_parse_clock_returns_none_when_ended():
    assert parse_clock(_load("betpawa_event_ended.json")) is None


def test_parse_score_live():
    assert parse_score(_load("betpawa_event_live.json")) == (1, 0)


def test_parse_score_ended():
    assert parse_score(_load("betpawa_event_ended.json")) == (2, 1)


def test_parse_score_returns_none_when_prematch():
    assert parse_score(_load("betpawa_event_upcoming.json")) is None


def test_parse_status_ended_when_live_false_with_score():
    """BetPawa keeps the final score in the detail after a match ends
    and flips additionalInfo.live to False, but doesn't always set
    currentPeriod to "FT" — some events stay at the in-play period
    string. The live=False signal must be enough to declare ENDED
    when a score is present."""
    detail = {
        "id": "1", "startTime": "2026-05-22T15:00:00Z",
        "additionalInfo": {"live": False},
        "results": {
            "display": {
                "minute": 90,
                "currentPeriod": {"name": "2H", "slug": "second-half"},
            },
            "participantPeriodResults": [
                {"participant": {"type": "HOME"},
                 "periodResults": [
                     {"period": {"slug": "FULL_TIME_EXCLUDING_OVERTIME"}, "result": 2},
                 ]},
                {"participant": {"type": "AWAY"},
                 "periodResults": [
                     {"period": {"slug": "FULL_TIME_EXCLUDING_OVERTIME"}, "result": 1},
                 ]},
            ],
        },
    }
    assert parse_status(detail) == EventStatus.ENDED


def test_parse_clock_halftime_returns_45():
    detail = {
        "id": "1", "startTime": "2026-05-19T15:00:00Z",
        "additionalInfo": {"live": True},
        "results": {
            "display": {
                "minute": 45,
                "currentPeriod": {"name": "HT", "slug": "half-time"},
            },
            "participantPeriodResults": [],
        },
    }
    assert parse_clock(detail) == 45
