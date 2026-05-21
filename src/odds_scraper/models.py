from __future__ import annotations

from dataclasses import dataclass, field
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
    LOOKUP_FAILED = "lookup_failed"
    HTTP_ERROR = "http_error"
    PARSE_ERROR = "parse_error"


class Bookmaker(str, Enum):
    BETPAWA = "betpawa"
    SPORTYBET = "sportybet"
    BET9JA = "bet9ja"
    BETWAY = "betway"


# Bookmakers that expose a fair (pre-margin) probability per outcome.
# Used by the collector to decide whether to populate the prob field,
# and by the watcher to size the tick-log denominator.
PROB_BOOKMAKERS: frozenset[Bookmaker] = frozenset(
    {Bookmaker.BETPAWA, Bookmaker.SPORTYBET},
)


@dataclass(frozen=True)
class MarketSpec:
    canonical_id: str
    column_prefix: str
    sides: tuple[str, ...]
    lines: Optional[tuple[float, ...]]


MARKET_MANIFEST: tuple[MarketSpec, ...] = (
    MarketSpec("1x2_ft",        "1x2_ft",      ("home", "draw", "away"), None),
    MarketSpec("1x2_1up_ft",    "1x2_1up_ft",  ("home", "draw", "away"), None),
    MarketSpec("1x2_2up_ft",    "1x2_2up_ft",  ("home", "draw", "away"), None),
    MarketSpec(
        # column_prefix shortened to "ou"; must remain unique across manifest
        "over_under_ft", "ou", ("over", "under"),
        (1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5, 8.5, 9.5),
    ),
    # Bookieskit uses "none" (not "draw") for the no-more-goals outcome.
    MarketSpec(
        "next_goal_ft", "ng", ("home", "none", "away"),
        (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0),
    ),
    MarketSpec(
        "home_over_under_ft", "ou_home", ("over", "under"),
        (0.5, 1.5, 2.5, 3.5, 4.5, 5.5),
    ),
    MarketSpec(
        "away_over_under_ft", "ou_away", ("over", "under"),
        (0.5, 1.5, 2.5, 3.5, 4.5, 5.5),
    ),
)


@dataclass(frozen=True)
class PriceKey:
    market_id: str
    line: Optional[float]
    side: str


def build_csv_header() -> tuple[str, ...]:
    meta = (
        "ts_utc", "event_bp_id", "sr_id", "genius_id",
        "home", "away", "kickoff_utc",
        "status", "match_minute", "score_home", "score_away",
        "bookmaker", "fetch_status", "fetch_error",
    )
    price_cols: list[str] = []
    for spec in MARKET_MANIFEST:
        if spec.lines is None:
            for side in spec.sides:
                price_cols.append(f"{spec.column_prefix}_{side}_odds")
                price_cols.append(f"{spec.column_prefix}_{side}_prob")
        else:
            for line in spec.lines:
                for side in spec.sides:
                    price_cols.append(f"{spec.column_prefix}_{line}_{side}_odds")
                    price_cols.append(f"{spec.column_prefix}_{line}_{side}_prob")
    return meta + tuple(price_cols)


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
    fetch_status: FetchStatus
    fetch_error: str
    # NB: frozen=True does not freeze the contents of `prices` — the dict
    # itself remains mutable. By convention, populate it at construction in
    # the collector and never mutate afterwards.
    prices: dict[PriceKey, tuple[Optional[float], Optional[float]]] = field(
        default_factory=dict,
    )
    # Event-level metadata captured from BetPawa's region/competition keys.
    # Default "" so existing constructors (sentinel rows, older tests) keep
    # working unchanged.
    country_id: str = ""
    country_name: str = ""
    league_id: str = ""
    league_name: str = ""

    def to_csv_row(self) -> tuple[str, ...]:
        meta = (
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
            self.fetch_status.value,
            self.fetch_error,
        )
        price_cells: list[str] = []
        for spec in MARKET_MANIFEST:
            if spec.lines is None:
                for side in spec.sides:
                    odds, prob = self.prices.get(
                        PriceKey(spec.canonical_id, None, side), (None, None),
                    )
                    price_cells.append(_num(odds, 2))
                    price_cells.append(_num(prob, 5))
            else:
                for line in spec.lines:
                    for side in spec.sides:
                        odds, prob = self.prices.get(
                            PriceKey(spec.canonical_id, line, side), (None, None),
                        )
                        price_cells.append(_num(odds, 2))
                        price_cells.append(_num(prob, 5))
        return meta + tuple(price_cells)


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
