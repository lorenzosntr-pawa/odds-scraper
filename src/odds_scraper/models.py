from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class EventStatus(str, Enum):
    UNKNOWN = "UNKNOWN"
    UPCOMING = "UPCOMING"
    STARTED = "STARTED"
    SUSPENDED = "SUSPENDED"
    ENDED = "ENDED"


class FetchStatus(str, Enum):
    OK = "ok"
    SUSPENDED = "suspended"
    NOT_OFFERED = "not_offered"
    LOOKUP_FAILED = "lookup_failed"
    HTTP_ERROR = "http_error"
    PARSE_ERROR = "parse_error"


class Bookmaker(str, Enum):
    BETPAWA = "betpawa"
    SPORTYBET = "sportybet"
    BET9JA = "bet9ja"
    BETWAY = "betway"


class Market(str, Enum):
    ONE_UP = "1x2_1up_ft"
    TWO_UP = "1x2_2up_ft"


class Outcome(str, Enum):
    HOME = "home"
    DRAW = "draw"
    AWAY = "away"


CSV_HEADER: tuple[str, ...] = (
    "ts_utc", "event_bp_id", "sr_id", "genius_id",
    "home", "away", "kickoff_utc",
    "status", "match_minute", "score_home", "score_away",
    "bookmaker", "market", "outcome",
    "odds", "probability",
    "fetch_status", "fetch_error",
)


def _iso(dt: datetime | None) -> str:
    if dt is None:
        return ""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _num(value: float | int | None, dp: int) -> str:
    if value is None:
        return ""
    return f"{value:.{dp}f}"


def _maybe(value) -> str:
    return "" if value is None else str(value)


@dataclass(frozen=True)
class Snapshot:
    ts_utc: datetime
    event_bp_id: str
    sr_id: str
    genius_id: str
    home: str
    away: str
    kickoff_utc: datetime
    status: EventStatus
    match_minute: Optional[int]
    score_home: Optional[int]
    score_away: Optional[int]
    bookmaker: Bookmaker
    market: Market
    outcome: Outcome
    odds: Optional[float]
    probability: Optional[float]
    fetch_status: FetchStatus
    fetch_error: str

    def to_csv_row(self) -> tuple[str, ...]:
        return (
            _iso(self.ts_utc),
            self.event_bp_id,
            self.sr_id,
            self.genius_id,
            self.home,
            self.away,
            _iso(self.kickoff_utc),
            self.status.value,
            _maybe(self.match_minute),
            _maybe(self.score_home),
            _maybe(self.score_away),
            self.bookmaker.value,
            self.market.value,
            self.outcome.value,
            _num(self.odds, 2),
            _num(self.probability, 5),
            self.fetch_status.value,
            self.fetch_error,
        )


@dataclass(frozen=True)
class ResolvedIds:
    sr_id: Optional[str]
    genius_id: Optional[str]
    sb_id: Optional[str]
    b9j_id: Optional[str]
    bw_id: Optional[str]

    def matched_bookmakers(self) -> set[str]:
        out: set[str] = set()
        if self.sb_id:
            out.add("sportybet")
        if self.b9j_id:
            out.add("bet9ja")
        if self.bw_id:
            out.add("betway")
        return out
