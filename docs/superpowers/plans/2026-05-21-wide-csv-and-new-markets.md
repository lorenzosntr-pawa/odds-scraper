# Wide CSV + new markets — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pivot the snapshot CSV from long format (24 rows per event per tick) to wide format (4 rows per event per tick — one per bookmaker), and add classic `1x2_ft` plus `over_under_ft` lines 1.5 through 9.5.

**Architecture:** Single module-level `MARKET_MANIFEST` in `models.py` is the source of truth — CSV header, collector loop, sentinel rows, and tests all derive from it. `Snapshot` carries a `prices: dict[PriceKey, (odds, prob)]` field that `to_csv_row()` flattens deterministically in manifest order. Bookieskit already maps all four markets (only the existing BetPawa 2up patch in `registry.py` stays).

**Tech Stack:** Python 3.11+, dataclasses, asyncio, csv module, pytest with pytest-asyncio (auto mode), bookieskit for normalized market parsing.

**Spec reference:** `docs/superpowers/specs/2026-05-21-wide-csv-and-new-markets-design.md`

---

## File map

| Action | File | Responsibility |
|---|---|---|
| Modify | `src/odds_scraper/models.py` | Add `MarketSpec`, `MARKET_MANIFEST`, `PriceKey`, `build_csv_header()`. Rewrite `Snapshot` with `prices` field. Shrink `FetchStatus`. Remove `Market` & `Outcome` enums and `CSV_HEADER` constant. Keep `EventStatus`, `Bookmaker`, `ResolvedIds`. |
| Modify | `src/odds_scraper/collector.py` | Add `_extract_prices_for_manifest()`. Refactor `collect()` to return exactly 4 rows. Remove `_outcomes_for()`. |
| Modify | `src/odds_scraper/writer.py` | Header comes from `build_csv_header()`. On open: if existing file's first line is a non-matching header, rename to `*_v1_YYYY-MM-DD.csv` then proceed. |
| Modify | `src/odds_scraper/watcher.py` | `_sentinel_rows()` produces 4 rows; `_log_tick_summary()` uses the new `bp=N/54 sb=N/54 b9j=N/27 bw=N/27` format. |
| Rewrite | `tests/test_models.py` | Manifest, header builder, new Snapshot, `to_csv_row()`, `FetchStatus` post-shrink. |
| Rewrite | `tests/test_collector.py` | 4 rows per call, prices extraction (simple + parameterized), error paths, ignored extra lines. |
| Rewrite | `tests/test_writer.py` | New header, header-based v1-rename behavior, concurrent appends with new shape. |
| Modify | `tests/test_watcher.py` | Helper `_snap_list` uses new Snapshot shape; sentinel count assertion (24 → 4). |
| Unchanged | `registry.py`, `resolution*.py`, `status.py`, `config.py`, `main.py`, `tests/test_registry.py`, `tests/test_status.py`, `tests/test_resolution.py`, `tests/test_config.py`, `tests/test_main_supervisor.py` | — |

---

## Task 1: Models — manifest, header builder, refactored Snapshot

**Files:**
- Modify: `src/odds_scraper/models.py` (full rewrite of file)
- Test: `tests/test_models.py` (full rewrite)

### Step 1.1 — Write failing tests for the new models

- [ ] **Write `tests/test_models.py`**

Replace the file's full content with:

```python
from datetime import datetime, timezone

from odds_scraper.models import (
    Bookmaker, EventStatus, FetchStatus, MarketSpec, MARKET_MANIFEST,
    PriceKey, ResolvedIds, Snapshot, build_csv_header,
)


def test_manifest_lists_expected_markets():
    canonical_ids = [s.canonical_id for s in MARKET_MANIFEST]
    assert canonical_ids == ["1x2_ft", "1x2_1up_ft", "1x2_2up_ft", "over_under_ft"]


def test_manifest_over_under_lines_are_1_5_to_9_5():
    ou = next(s for s in MARKET_MANIFEST if s.canonical_id == "over_under_ft")
    assert ou.lines == (1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5, 8.5, 9.5)
    assert ou.sides == ("over", "under")


def test_simple_markets_have_lines_none_and_3_sides():
    for cid in ("1x2_ft", "1x2_1up_ft", "1x2_2up_ft"):
        spec = next(s for s in MARKET_MANIFEST if s.canonical_id == cid)
        assert spec.lines is None
        assert spec.sides == ("home", "draw", "away")


def test_build_csv_header_has_68_columns():
    header = build_csv_header()
    assert len(header) == 68


def test_build_csv_header_meta_prefix():
    header = build_csv_header()
    assert header[:14] == (
        "ts_utc", "event_bp_id", "sr_id", "genius_id",
        "home", "away", "kickoff_utc",
        "status", "match_minute", "score_home", "score_away",
        "bookmaker", "fetch_status", "fetch_error",
    )


def test_build_csv_header_price_section_order():
    header = build_csv_header()
    # First price columns must be 1x2_ft family in home/draw/away × odds/prob order
    assert header[14:20] == (
        "1x2_ft_home_odds", "1x2_ft_home_prob",
        "1x2_ft_draw_odds", "1x2_ft_draw_prob",
        "1x2_ft_away_odds", "1x2_ft_away_prob",
    )
    # 1up next
    assert header[20:26] == (
        "1x2_1up_ft_home_odds", "1x2_1up_ft_home_prob",
        "1x2_1up_ft_draw_odds", "1x2_1up_ft_draw_prob",
        "1x2_1up_ft_away_odds", "1x2_1up_ft_away_prob",
    )
    # 2up next
    assert header[26:32] == (
        "1x2_2up_ft_home_odds", "1x2_2up_ft_home_prob",
        "1x2_2up_ft_draw_odds", "1x2_2up_ft_draw_prob",
        "1x2_2up_ft_away_odds", "1x2_2up_ft_away_prob",
    )
    # First O/U line 1.5
    assert header[32:36] == (
        "ou_1.5_over_odds", "ou_1.5_over_prob",
        "ou_1.5_under_odds", "ou_1.5_under_prob",
    )
    # Last 4 columns are 9.5
    assert header[-4:] == (
        "ou_9.5_over_odds", "ou_9.5_over_prob",
        "ou_9.5_under_odds", "ou_9.5_under_prob",
    )


def test_fetch_status_enum_only_four_values():
    values = {fs.value for fs in FetchStatus}
    assert values == {"ok", "lookup_failed", "http_error", "parse_error"}


def _meta_kwargs(**overrides):
    kw = dict(
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
        fetch_status=FetchStatus.OK,
        fetch_error="",
        prices={},
    )
    kw.update(overrides)
    return kw


def test_snapshot_to_csv_row_meta_columns():
    snap = Snapshot(**_meta_kwargs())
    row = snap.to_csv_row()
    assert len(row) == 68
    assert row[0] == "2026-05-19T14:32:05Z"
    assert row[1] == "33660318"
    assert row[2] == "sr:match:12345"
    assert row[3] == "g-67890"
    assert row[4] == "Team A"
    assert row[5] == "Team B"
    assert row[6] == "2026-05-19T15:00:00Z"
    assert row[7] == "STARTED"
    assert row[8] == "34"
    assert row[9] == "1"
    assert row[10] == "0"
    assert row[11] == "betpawa"
    assert row[12] == "ok"
    assert row[13] == ""


def test_snapshot_to_csv_row_simple_market_prices():
    prices = {
        PriceKey("1x2_ft", None, "home"): (1.85, 0.54054),
        PriceKey("1x2_ft", None, "draw"): (3.20, 0.31250),
        PriceKey("1x2_ft", None, "away"): (4.50, 0.22222),
    }
    snap = Snapshot(**_meta_kwargs(prices=prices))
    row = snap.to_csv_row()
    # 1x2_ft starts at column 14, fields alternate odds/prob
    assert row[14] == "1.85"
    assert row[15] == "0.54054"
    assert row[16] == "3.20"
    assert row[17] == "0.31250"
    assert row[18] == "4.50"
    assert row[19] == "0.22222"
    # 1up section (unfilled) — all blank
    assert row[20:26] == ("", "", "", "", "", "")


def test_snapshot_to_csv_row_parameterized_market_prices():
    prices = {
        PriceKey("over_under_ft", 2.5, "over"): (1.70, 0.58),
        PriceKey("over_under_ft", 2.5, "under"): (2.10, 0.42),
        PriceKey("over_under_ft", 3.5, "over"): (2.50, None),
        PriceKey("over_under_ft", 3.5, "under"): (1.50, None),
    }
    snap = Snapshot(**_meta_kwargs(prices=prices))
    row = snap.to_csv_row()
    header = build_csv_header()
    # Use header lookup so we don't hard-code positions for parameterized cols
    assert row[header.index("ou_2.5_over_odds")] == "1.70"
    assert row[header.index("ou_2.5_over_prob")] == "0.58000"
    assert row[header.index("ou_2.5_under_odds")] == "2.10"
    assert row[header.index("ou_2.5_under_prob")] == "0.42000"
    assert row[header.index("ou_3.5_over_odds")] == "2.50"
    assert row[header.index("ou_3.5_over_prob")] == ""
    assert row[header.index("ou_3.5_under_odds")] == "1.50"
    # Line not in prices stays blank
    assert row[header.index("ou_4.5_over_odds")] == ""


def test_snapshot_to_csv_row_blanks_when_failure_status():
    snap = Snapshot(**_meta_kwargs(
        fetch_status=FetchStatus.HTTP_ERROR,
        fetch_error="timeout after 10s",
        prices={},
    ))
    row = snap.to_csv_row()
    assert row[12] == "http_error"
    assert row[13] == "timeout after 10s"
    # All 54 price cells blank
    assert all(cell == "" for cell in row[14:])


def test_resolved_ids_matched_bookmakers():
    r = ResolvedIds(sr_id="sr:match:1", genius_id=None, sb_id="sr:match:1",
                    b9j_id=None, bw_id=None)
    assert r.matched_bookmakers() == {"sportybet"}
```

- [ ] **Run the new tests — verify they FAIL**

Run: `pytest tests/test_models.py -v`
Expected: `ImportError` on `MarketSpec`, `MARKET_MANIFEST`, `PriceKey`, `build_csv_header` (and possibly other names) — confirms tests are pointed at code that doesn't exist yet.

### Step 1.2 — Implement the new models module

- [ ] **Rewrite `src/odds_scraper/models.py`** with this exact content:

```python
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
        "over_under_ft", "ou", ("over", "under"),
        (1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5, 8.5, 9.5),
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
    prices: dict[PriceKey, tuple[Optional[float], Optional[float]]] = field(
        default_factory=dict,
    )

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
```

- [ ] **Run model tests — verify they PASS**

Run: `pytest tests/test_models.py -v`
Expected: all 12 tests pass.

- [ ] **Run full suite — expect collector/writer/watcher tests to fail (they reference removed names)**

Run: `pytest -q`
Expected: `test_models.py` passes; failures in `test_collector.py`, `test_writer.py`, `test_watcher.py` due to imports of removed `Market`, `Outcome`, `CSV_HEADER` symbols. These will be fixed in subsequent tasks. Note them but proceed.

### Step 1.3 — Commit

- [ ] **Commit**

```bash
git add src/odds_scraper/models.py tests/test_models.py
git commit -m "$(cat <<'EOF'
feat(models): manifest-driven wide schema, shrunk FetchStatus

Adds MarketSpec/MARKET_MANIFEST/PriceKey/build_csv_header.
Snapshot now carries prices dict and flattens via manifest order.
Removes Market, Outcome, CSV_HEADER, plus SUSPENDED/NOT_OFFERED
FetchStatus values (per-cell info encoded as empty cell instead).
EOF
)"
```

---

## Task 2: Collector — return 4 rows, manifest-driven price extraction

**Files:**
- Modify: `src/odds_scraper/collector.py` (full rewrite)
- Test: `tests/test_collector.py` (full rewrite)

### Step 2.1 — Write failing tests

- [ ] **Rewrite `tests/test_collector.py`**

Replace file content with:

```python
from unittest.mock import AsyncMock

import pytest

from odds_scraper.collector import OddsCollector
from odds_scraper.models import (
    Bookmaker, EventStatus, FetchStatus, PriceKey,
)


def _bp_detail(live: bool = False) -> dict:
    return {
        "id": "33660318",
        "participants": [
            {"id": "1", "name": "Team A", "position": 1},
            {"id": "2", "name": "Team B", "position": 2},
        ],
        "startTime": "2026-05-19T15:00:00Z",
        "additionalInfo": {"live": live},
        "results": {
            "display": {"minute": 34, "currentPeriod": {"name": "1H"}},
            "participantPeriodResults": [
                {"participant": {"type": "HOME"},
                 "periodResults": [{"period": {"slug": "FULL_TIME_EXCLUDING_OVERTIME"}, "result": 1}]},
                {"participant": {"type": "AWAY"},
                 "periodResults": [{"period": {"slug": "FULL_TIME_EXCLUDING_OVERTIME"}, "result": 0}]},
            ],
        } if live else None,
    }


class _O:
    """Stand-in for bookieskit.markets.types.Outcome."""
    def __init__(self, name, odds, prob=None):
        self.canonical_name = name
        self.odds = odds
        self.true_probability = prob


class _M:
    """Stand-in for bookieskit.markets.types.NormalizedMarket — simple market."""
    def __init__(self, cid, outs):
        self.canonical_id = cid
        self.outcomes = [_O(n, o, p) for n, (o, p) in outs.items()]
        self.lines = None


class _PM:
    """Parameterized market stand-in (O/U-style)."""
    def __init__(self, cid, lines):
        self.canonical_id = cid
        self.outcomes = []
        self.lines = {
            ln: [_O(side, odds, prob) for side, (odds, prob) in sides.items()]
            for ln, sides in lines.items()
        }


def _full_markets():
    return [
        _M("1x2_ft", {
            "home": (1.80, 0.55), "draw": (3.40, 0.29), "away": (4.20, 0.23),
        }),
        _M("1x2_1up_ft", {
            "home": (1.85, 0.54), "draw": (3.20, 0.31), "away": (4.50, 0.22),
        }),
        _M("1x2_2up_ft", {
            "home": (2.50, 0.40), "draw": (3.80, 0.26), "away": (6.00, 0.16),
        }),
        _PM("over_under_ft", {
            2.5: {"over": (1.70, 0.58), "under": (2.10, 0.42)},
            3.5: {"over": (2.50, 0.39), "under": (1.50, 0.61)},
        }),
    ]


@pytest.fixture
def collector():
    return OddsCollector(
        fetchers={
            Bookmaker.BETPAWA: AsyncMock(return_value=_full_markets()),
            Bookmaker.SPORTYBET: AsyncMock(return_value=_full_markets()),
            Bookmaker.BET9JA: AsyncMock(return_value=_full_markets()),
            Bookmaker.BETWAY: AsyncMock(return_value=_full_markets()),
        },
    )


async def test_returns_exactly_four_rows(collector):
    rows = await collector.collect(
        _bp_detail(),
        resolved={Bookmaker.SPORTYBET: "sr:match:1",
                  Bookmaker.BET9JA: "b9j-7",
                  Bookmaker.BETWAY: "sr:match:1"},
        sr_id="sr:match:1", genius_id="",
    )
    assert len(rows) == 4
    bookmakers = {r.bookmaker for r in rows}
    assert bookmakers == set(Bookmaker)


async def test_all_four_rows_ok_on_full_success(collector):
    rows = await collector.collect(
        _bp_detail(),
        resolved={Bookmaker.SPORTYBET: "sr:match:1",
                  Bookmaker.BET9JA: "b9j-7",
                  Bookmaker.BETWAY: "sr:match:1"},
        sr_id="sr:match:1", genius_id="",
    )
    assert all(r.fetch_status == FetchStatus.OK for r in rows)


async def test_betpawa_row_has_simple_market_prices(collector):
    rows = await collector.collect(
        _bp_detail(),
        resolved={Bookmaker.SPORTYBET: "sr:match:1",
                  Bookmaker.BET9JA: "b9j-7",
                  Bookmaker.BETWAY: "sr:match:1"},
        sr_id="sr:match:1", genius_id="",
    )
    bp = next(r for r in rows if r.bookmaker == Bookmaker.BETPAWA)
    assert bp.prices[PriceKey("1x2_ft", None, "home")] == (1.80, 0.55)
    assert bp.prices[PriceKey("1x2_1up_ft", None, "away")] == (4.50, 0.22)


async def test_betpawa_row_has_parameterized_prices(collector):
    rows = await collector.collect(
        _bp_detail(),
        resolved={Bookmaker.SPORTYBET: "sr:match:1",
                  Bookmaker.BET9JA: "b9j-7",
                  Bookmaker.BETWAY: "sr:match:1"},
        sr_id="sr:match:1", genius_id="",
    )
    bp = next(r for r in rows if r.bookmaker == Bookmaker.BETPAWA)
    assert bp.prices[PriceKey("over_under_ft", 2.5, "over")] == (1.70, 0.58)
    assert bp.prices[PriceKey("over_under_ft", 3.5, "under")] == (1.50, 0.61)


async def test_probability_populated_only_for_bp_and_sb(collector):
    rows = await collector.collect(
        _bp_detail(),
        resolved={Bookmaker.SPORTYBET: "sr:match:1",
                  Bookmaker.BET9JA: "b9j-7",
                  Bookmaker.BETWAY: "sr:match:1"},
        sr_id="sr:match:1", genius_id="",
    )
    for r in rows:
        sample_key = PriceKey("1x2_ft", None, "home")
        odds, prob = r.prices[sample_key]
        assert odds == 1.80
        if r.bookmaker in (Bookmaker.BETPAWA, Bookmaker.SPORTYBET):
            assert prob == 0.55
        else:
            assert prob is None


async def test_lookup_failed_emits_empty_row():
    coll = OddsCollector(
        fetchers={
            Bookmaker.BETPAWA: AsyncMock(return_value=_full_markets()),
            Bookmaker.SPORTYBET: AsyncMock(return_value=_full_markets()),
            Bookmaker.BET9JA: AsyncMock(return_value=_full_markets()),
            Bookmaker.BETWAY: AsyncMock(return_value=_full_markets()),
        },
    )
    rows = await coll.collect(
        _bp_detail(),
        resolved={Bookmaker.SPORTYBET: "sr:match:1",
                  Bookmaker.BET9JA: None,
                  Bookmaker.BETWAY: "sr:match:1"},
        sr_id="sr:match:1", genius_id="",
    )
    b9j = next(r for r in rows if r.bookmaker == Bookmaker.BET9JA)
    assert b9j.fetch_status == FetchStatus.LOOKUP_FAILED
    assert b9j.prices == {}


async def test_http_error_emits_empty_row():
    failing = AsyncMock(side_effect=RuntimeError("HTTP 503"))
    coll = OddsCollector(
        fetchers={
            Bookmaker.BETPAWA: AsyncMock(return_value=_full_markets()),
            Bookmaker.SPORTYBET: failing,
            Bookmaker.BET9JA: AsyncMock(return_value=_full_markets()),
            Bookmaker.BETWAY: AsyncMock(return_value=_full_markets()),
        },
    )
    rows = await coll.collect(
        _bp_detail(),
        resolved={Bookmaker.SPORTYBET: "sr:match:1",
                  Bookmaker.BET9JA: "b9j-7",
                  Bookmaker.BETWAY: "sr:match:1"},
        sr_id="sr:match:1", genius_id="",
    )
    sb = next(r for r in rows if r.bookmaker == Bookmaker.SPORTYBET)
    assert sb.fetch_status == FetchStatus.HTTP_ERROR
    assert "HTTP 503" in sb.fetch_error
    assert sb.prices == {}


async def test_missing_market_just_omits_those_keys():
    only_1up = [_M("1x2_1up_ft", {
        "home": (1.85, 0.54), "draw": (3.20, 0.31), "away": (4.50, 0.22),
    })]
    coll = OddsCollector(
        fetchers={
            Bookmaker.BETPAWA: AsyncMock(return_value=_full_markets()),
            Bookmaker.SPORTYBET: AsyncMock(return_value=only_1up),
            Bookmaker.BET9JA: AsyncMock(return_value=_full_markets()),
            Bookmaker.BETWAY: AsyncMock(return_value=_full_markets()),
        },
    )
    rows = await coll.collect(
        _bp_detail(),
        resolved={Bookmaker.SPORTYBET: "sr:match:1",
                  Bookmaker.BET9JA: "b9j-7",
                  Bookmaker.BETWAY: "sr:match:1"},
        sr_id="sr:match:1", genius_id="",
    )
    sb = next(r for r in rows if r.bookmaker == Bookmaker.SPORTYBET)
    assert sb.fetch_status == FetchStatus.OK
    # 1up present
    assert PriceKey("1x2_1up_ft", None, "home") in sb.prices
    # 1x2_ft and 2up and O/U not present
    assert PriceKey("1x2_ft", None, "home") not in sb.prices
    assert PriceKey("1x2_2up_ft", None, "home") not in sb.prices
    assert not any(k.market_id == "over_under_ft" for k in sb.prices)


async def test_out_of_manifest_lines_are_ignored():
    extra_ou = [_PM("over_under_ft", {
        2.0: {"over": (1.40, 0.71), "under": (2.80, 0.29)},   # .0 line, ignored
        2.5: {"over": (1.70, 0.58), "under": (2.10, 0.42)},   # in manifest
        12.5: {"over": (50.0, 0.02), "under": (1.01, 0.98)},  # above 9.5
    })]
    coll = OddsCollector(
        fetchers={
            Bookmaker.BETPAWA: AsyncMock(return_value=extra_ou),
            Bookmaker.SPORTYBET: AsyncMock(return_value=extra_ou),
            Bookmaker.BET9JA: AsyncMock(return_value=extra_ou),
            Bookmaker.BETWAY: AsyncMock(return_value=extra_ou),
        },
    )
    rows = await coll.collect(
        _bp_detail(),
        resolved={Bookmaker.SPORTYBET: "sr:match:1",
                  Bookmaker.BET9JA: "b9j-7",
                  Bookmaker.BETWAY: "sr:match:1"},
        sr_id="sr:match:1", genius_id="",
    )
    bp = next(r for r in rows if r.bookmaker == Bookmaker.BETPAWA)
    lines_seen = {k.line for k in bp.prices if k.market_id == "over_under_ft"}
    assert lines_seen == {2.5}


async def test_live_status_populates_clock_and_score(collector):
    rows = await collector.collect(
        _bp_detail(live=True),
        resolved={Bookmaker.SPORTYBET: "sr:match:1",
                  Bookmaker.BET9JA: "b9j-7",
                  Bookmaker.BETWAY: "sr:match:1"},
        sr_id="sr:match:1", genius_id="g-9",
    )
    assert all(r.status == EventStatus.STARTED for r in rows)
    assert all(r.match_minute == 34 for r in rows)
    assert all(r.score_home == 1 and r.score_away == 0 for r in rows)
    assert all(r.genius_id == "g-9" for r in rows)
    assert all(r.home == "Team A" and r.away == "Team B" for r in rows)


async def test_outcome_missing_odds_is_skipped(collector):
    no_draw = [_M("1x2_ft", {
        "home": (1.80, 0.55),
        "draw": (None, None),       # suspended
        "away": (4.20, 0.23),
    })]
    coll = OddsCollector(
        fetchers={
            Bookmaker.BETPAWA: AsyncMock(return_value=no_draw),
            Bookmaker.SPORTYBET: AsyncMock(return_value=no_draw),
            Bookmaker.BET9JA: AsyncMock(return_value=no_draw),
            Bookmaker.BETWAY: AsyncMock(return_value=no_draw),
        },
    )
    rows = await coll.collect(
        _bp_detail(),
        resolved={Bookmaker.SPORTYBET: "sr:match:1",
                  Bookmaker.BET9JA: "b9j-7",
                  Bookmaker.BETWAY: "sr:match:1"},
        sr_id="sr:match:1", genius_id="",
    )
    bp = next(r for r in rows if r.bookmaker == Bookmaker.BETPAWA)
    assert PriceKey("1x2_ft", None, "home") in bp.prices
    assert PriceKey("1x2_ft", None, "draw") not in bp.prices
    assert PriceKey("1x2_ft", None, "away") in bp.prices
```

- [ ] **Run collector tests — verify they FAIL**

Run: `pytest tests/test_collector.py -v`
Expected: failures — current `collect()` returns 24 rows, doesn't have `prices` attribute, etc.

### Step 2.2 — Rewrite the collector

- [ ] **Replace `src/odds_scraper/collector.py`** with:

```python
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from bookieskit import extract_kickoff, extract_participants

from .models import (
    MARKET_MANIFEST, Bookmaker, FetchStatus, PriceKey, Snapshot,
)
from .status import parse_clock, parse_score, parse_status

log = logging.getLogger(__name__)

_PROB_BOOKMAKERS = {Bookmaker.BETPAWA, Bookmaker.SPORTYBET}

Fetcher = Callable[..., Awaitable[list]]


class OddsCollector:
    """Stateless one-tick fan-out. Always returns 4 Snapshot rows (one per
    bookmaker). Failures are encoded into the row via fetch_status; the
    collector itself never raises.
    """

    def __init__(self, fetchers: dict[Bookmaker, Fetcher]):
        missing = [b for b in Bookmaker if b not in fetchers]
        if missing:
            raise ValueError(f"fetcher missing for: {[b.value for b in missing]}")
        self._fetchers = fetchers

    async def collect(
        self,
        bp_detail: dict[str, Any],
        resolved: dict[Bookmaker, Optional[str]],
        sr_id: str,
        genius_id: str,
    ) -> list[Snapshot]:
        ts = datetime.now(timezone.utc)
        status = parse_status(bp_detail)
        minute = parse_clock(bp_detail)
        score = parse_score(bp_detail)

        participants = extract_participants(bp_detail, "betpawa")
        home = participants.home or ""
        away = participants.away or ""

        kickoff = extract_kickoff(bp_detail, "betpawa") or ts

        async def run(b: Bookmaker, target_id: Optional[str]):
            if b != Bookmaker.BETPAWA and not target_id:
                return b, (FetchStatus.LOOKUP_FAILED,
                           "no id resolved for bookmaker", [])
            try:
                if b == Bookmaker.BETPAWA:
                    markets = await self._fetchers[b](bp_detail)
                else:
                    markets = await self._fetchers[b](target_id)
                return b, (FetchStatus.OK, "", markets)
            except Exception as e:  # noqa: BLE001
                short = " ".join(str(e).split())[:120]
                log.warning("fetch failed for %s: %s", b.value, short)
                return b, (FetchStatus.HTTP_ERROR,
                           f"{type(e).__name__}: {short}", [])

        coros = [
            run(b, resolved.get(b) if b != Bookmaker.BETPAWA else None)
            for b in Bookmaker
        ]
        results: dict[Bookmaker, tuple[FetchStatus, str, list]] = {}
        for b, payload in await asyncio.gather(*coros):
            results[b] = payload

        rows: list[Snapshot] = []
        for b in Bookmaker:
            status_fetch, error, markets = results[b]
            want_prob = b in _PROB_BOOKMAKERS
            prices = (
                _extract_prices_for_manifest(markets, want_prob)
                if status_fetch == FetchStatus.OK
                else {}
            )
            rows.append(Snapshot(
                ts_utc=ts,
                event_bp_id=str(bp_detail.get("id", "")),
                sr_id=sr_id or "",
                genius_id=genius_id or "",
                home=home, away=away,
                kickoff_utc=kickoff,
                status=status,
                match_minute=minute,
                score_home=score[0] if score else None,
                score_away=score[1] if score else None,
                bookmaker=b,
                fetch_status=status_fetch,
                fetch_error=error,
                prices=prices,
            ))
        return rows


def _extract_prices_for_manifest(
    markets: list, want_prob: bool,
) -> dict[PriceKey, tuple[Optional[float], Optional[float]]]:
    by_canon = {getattr(m, "canonical_id", None): m for m in markets}
    out: dict[PriceKey, tuple[Optional[float], Optional[float]]] = {}
    for spec in MARKET_MANIFEST:
        m = by_canon.get(spec.canonical_id)
        if m is None:
            continue
        if spec.lines is None:
            by_side = {o.canonical_name: o for o in getattr(m, "outcomes", [])}
            for side in spec.sides:
                o = by_side.get(side)
                if o is None or o.odds is None:
                    continue
                prob = getattr(o, "true_probability", None) if want_prob else None
                out[PriceKey(spec.canonical_id, None, side)] = (
                    float(o.odds),
                    float(prob) if prob is not None else None,
                )
        else:
            lines_map = getattr(m, "lines", None) or {}
            for line in spec.lines:
                outcomes = lines_map.get(line)
                if not outcomes:
                    continue
                by_side = {o.canonical_name: o for o in outcomes}
                for side in spec.sides:
                    o = by_side.get(side)
                    if o is None or o.odds is None:
                        continue
                    prob = (getattr(o, "true_probability", None)
                            if want_prob else None)
                    out[PriceKey(spec.canonical_id, line, side)] = (
                        float(o.odds),
                        float(prob) if prob is not None else None,
                    )
    return out
```

- [ ] **Run collector tests — verify they PASS**

Run: `pytest tests/test_collector.py -v`
Expected: all 11 tests pass.

### Step 2.3 — Commit

- [ ] **Commit**

```bash
git add src/odds_scraper/collector.py tests/test_collector.py
git commit -m "$(cat <<'EOF'
feat(collector): 4 rows per tick, manifest-driven price extraction

One Snapshot per bookmaker carrying prices dict keyed by PriceKey.
Handles simple and parameterized markets. Probability only for BP/SB.
Out-of-manifest O/U lines (.0 lines, >9.5) silently ignored.
EOF
)"
```

---

## Task 3: Writer — header from manifest, v1 rename on header mismatch

**Files:**
- Modify: `src/odds_scraper/writer.py` (full rewrite)
- Test: `tests/test_writer.py` (full rewrite)

### Step 3.1 — Write failing tests

- [ ] **Rewrite `tests/test_writer.py`**

Replace file content with:

```python
import asyncio
import csv
from datetime import datetime, timezone
from pathlib import Path

from odds_scraper.models import (
    Bookmaker, EventStatus, FetchStatus, PriceKey, Snapshot, build_csv_header,
)
from odds_scraper.writer import CsvWriter


def _make_snap(idx: int, bookmaker=Bookmaker.BETPAWA) -> Snapshot:
    return Snapshot(
        ts_utc=datetime(2026, 5, 19, 14, 0, idx % 60, tzinfo=timezone.utc),
        event_bp_id="33660318",
        sr_id="sr:match:1", genius_id="",
        home="A", away="B",
        kickoff_utc=datetime(2026, 5, 19, 15, 0, 0, tzinfo=timezone.utc),
        status=EventStatus.UPCOMING,
        match_minute=None, score_home=None, score_away=None,
        bookmaker=bookmaker,
        fetch_status=FetchStatus.OK,
        fetch_error="",
        prices={
            PriceKey("1x2_ft", None, "home"): (1.5 + idx * 0.01, None),
        },
    )


async def test_header_written_once_on_fresh_file(tmp_path: Path):
    path = tmp_path / "out.csv"
    async with CsvWriter(path) as w:
        await w.append([_make_snap(0)])
    async with CsvWriter(path) as w:
        await w.append([_make_snap(1)])
    rows = list(csv.reader(path.read_text(encoding="utf-8").splitlines()))
    assert rows[0] == list(build_csv_header())
    assert len(rows) == 3  # header + 2 data rows


async def test_concurrent_appends_do_not_interleave(tmp_path: Path):
    path = tmp_path / "out.csv"
    snaps_a = [_make_snap(i, Bookmaker.BETPAWA) for i in range(50)]
    snaps_b = [_make_snap(i, Bookmaker.SPORTYBET) for i in range(50)]

    async with CsvWriter(path) as w:
        await asyncio.gather(w.append(snaps_a), w.append(snaps_b))

    rows = list(csv.reader(path.read_text(encoding="utf-8").splitlines()))
    header = build_csv_header()
    assert rows[0] == list(header)
    data = rows[1:]
    assert len(data) == 100
    assert all(len(r) == len(header) for r in data)
    bookmaker_col = header.index("bookmaker")
    bookmakers = [r[bookmaker_col] for r in data]
    assert bookmakers.count("betpawa") == 50
    assert bookmakers.count("sportybet") == 50


async def test_old_header_file_is_renamed_with_v1_suffix(tmp_path: Path):
    path = tmp_path / "odds_snapshots.csv"
    # Simulate pre-pivot file with the old long-format header
    old_header = (
        "ts_utc,event_bp_id,sr_id,genius_id,home,away,kickoff_utc,"
        "status,match_minute,score_home,score_away,"
        "bookmaker,market,outcome,odds,probability,fetch_status,fetch_error\n"
    )
    path.write_text(old_header + "2026-05-20T11:00:00Z,33,,,,A,B,UPCOMING,,,"
                                  ",betpawa,1x2_1up_ft,home,1.85,0.54,ok,\n",
                    encoding="utf-8")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    async with CsvWriter(path) as w:
        await w.append([_make_snap(0)])

    renamed = tmp_path / f"odds_snapshots_v1_{today}.csv"
    assert renamed.exists(), "old file must be renamed with v1 suffix"
    assert "1x2_1up_ft,home,1.85" in renamed.read_text(encoding="utf-8")
    # New file exists with new header
    rows = list(csv.reader(path.read_text(encoding="utf-8").splitlines()))
    assert rows[0] == list(build_csv_header())
    assert len(rows) == 2  # header + 1 new row


async def test_existing_new_header_file_is_appended_not_renamed(tmp_path: Path):
    path = tmp_path / "odds_snapshots.csv"
    # Pre-create file with the CURRENT (new) header
    new_header_line = ",".join(build_csv_header()) + "\n"
    path.write_text(new_header_line, encoding="utf-8")

    async with CsvWriter(path) as w:
        await w.append([_make_snap(0)])

    # No v1 file should have been created
    siblings = list(tmp_path.glob("odds_snapshots_v1_*.csv"))
    assert siblings == []
    rows = list(csv.reader(path.read_text(encoding="utf-8").splitlines()))
    assert rows[0] == list(build_csv_header())
    assert len(rows) == 2


async def test_to_csv_row_value_round_trips_to_correct_column(tmp_path: Path):
    path = tmp_path / "out.csv"
    snap = _make_snap(0, Bookmaker.BETPAWA)
    async with CsvWriter(path) as w:
        await w.append([snap])
    rows = list(csv.reader(path.read_text(encoding="utf-8").splitlines()))
    header = build_csv_header()
    data = rows[1]
    home_odds_col = header.index("1x2_ft_home_odds")
    assert data[home_odds_col] == "1.50"
```

- [ ] **Run writer tests — verify they FAIL**

Run: `pytest tests/test_writer.py -v`
Expected: failures — `CSV_HEADER` import gone from models; writer still uses old header; rename logic doesn't exist.

### Step 3.2 — Rewrite the writer

- [ ] **Replace `src/odds_scraper/writer.py`** with:

```python
from __future__ import annotations

import asyncio
import csv
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .models import Snapshot, build_csv_header

log = logging.getLogger(__name__)


class CsvWriter:
    def __init__(self, path: Path | str):
        self._path = Path(path)
        self._lock = asyncio.Lock()
        self._fh = None
        self._writer = None

    async def __aenter__(self) -> "CsvWriter":
        self._path.parent.mkdir(parents=True, exist_ok=True)
        header = build_csv_header()
        expected_first_line = ",".join(header)

        if self._path.exists() and self._path.stat().st_size > 0:
            actual_first_line = _read_first_line(self._path)
            if actual_first_line != expected_first_line:
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                renamed = self._path.with_name(
                    f"{self._path.stem}_v1_{today}{self._path.suffix}",
                )
                log.info(
                    "csv header mismatch — renaming %s to %s",
                    self._path.name, renamed.name,
                )
                self._path.rename(renamed)

        new_file = not self._path.exists() or self._path.stat().st_size == 0
        self._fh = self._path.open("a", encoding="utf-8", newline="")
        self._writer = csv.writer(self._fh, lineterminator="\n")
        if new_file:
            self._writer.writerow(header)
            self._fh.flush()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._fh is not None:
            self._fh.flush()
            try:
                os.fsync(self._fh.fileno())
            except (OSError, AttributeError):
                pass
            self._fh.close()
            self._fh = None
            self._writer = None

    async def append(self, snapshots: Iterable[Snapshot]) -> None:
        snaps = list(snapshots)
        if not snaps:
            return
        async with self._lock:
            assert self._writer is not None, "CsvWriter not entered"
            for s in snaps:
                self._writer.writerow(s.to_csv_row())
            assert self._fh is not None
            self._fh.flush()


def _read_first_line(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as f:
        return f.readline().rstrip("\r\n")
```

- [ ] **Run writer tests — verify they PASS**

Run: `pytest tests/test_writer.py -v`
Expected: all 5 tests pass.

### Step 3.3 — Commit

- [ ] **Commit**

```bash
git add src/odds_scraper/writer.py tests/test_writer.py
git commit -m "$(cat <<'EOF'
feat(writer): manifest-derived header, v1 rename on header mismatch

CsvWriter now derives the header from build_csv_header(). On open,
if an existing file has a mismatched header (e.g. old long-format),
the file is renamed to *_v1_YYYY-MM-DD.csv and a fresh file is
started with the new schema. One-shot — subsequent opens are no-ops.
EOF
)"
```

---

## Task 4: Watcher — 4 sentinel rows, new tick-summary log

**Files:**
- Modify: `src/odds_scraper/watcher.py:124-143` (sentinel + tick-summary methods)
- Modify: `src/odds_scraper/watcher.py:110-122` (`_log_tick_summary`)
- Test: `tests/test_watcher.py` (update `_snap_list` helper + add 2 new tests)

### Step 4.1 — Write failing tests

- [ ] **Rewrite `tests/test_watcher.py`** (preserving the three existing scenarios with updated helper; add 2 new tests for sentinel-count and log-format):

```python
import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from odds_scraper.models import (
    Bookmaker, EventStatus, FetchStatus, PriceKey, Snapshot,
)
from odds_scraper.watcher import EventWatcher, WatcherConfig


def _one_snap(bookmaker: Bookmaker, status: EventStatus,
              prices: dict | None = None) -> Snapshot:
    return Snapshot(
        ts_utc=datetime.now(timezone.utc),
        event_bp_id="33660318",
        sr_id="", genius_id="",
        home="A", away="B",
        kickoff_utc=datetime.now(timezone.utc) + timedelta(minutes=10),
        status=status,
        match_minute=None, score_home=None, score_away=None,
        bookmaker=bookmaker,
        fetch_status=FetchStatus.OK,
        fetch_error="",
        prices=prices or {},
    )


def _snap_list(status=EventStatus.UPCOMING):
    """4 snapshots (one per bookmaker), each with a small prices dict."""
    return [
        _one_snap(b, status, prices={
            PriceKey("1x2_ft", None, "home"): (1.85, 0.54 if b in (
                Bookmaker.BETPAWA, Bookmaker.SPORTYBET) else None),
        })
        for b in Bookmaker
    ]


def _detail(live=False, ended=False) -> dict:
    if ended:
        return {
            "id": "33660318",
            "participants": [{"name": "A"}, {"name": "B"}],
            "startTime": "2026-05-19T15:00:00Z",
            "additionalInfo": {"live": False},
            "results": {
                "display": {"minute": 90, "currentPeriod": {"name": "FT"}},
                "participantPeriodResults": [],
            },
        }
    return {
        "id": "33660318",
        "participants": [{"name": "A"}, {"name": "B"}],
        "startTime": "2026-05-19T15:00:00Z",
        "additionalInfo": {"live": live},
        "results": {
            "display": {"minute": 34, "currentPeriod": {"name": "1H"}},
            "participantPeriodResults": [],
        } if live else None,
    }


@pytest.fixture
def cfg():
    return WatcherConfig(
        prematch_seconds=600,
        live_seconds=90,
        status_retry_backoff_seconds=(5, 15, 45),
        watchdog_after_kickoff_seconds=10800,
    )


async def test_exits_on_ended(cfg):
    bp_client = AsyncMock()
    bp_client.get_event_detail.return_value = _detail(ended=True)
    collector = AsyncMock()
    collector.collect.return_value = _snap_list(EventStatus.ENDED)
    writer = MagicMock()
    writer.append = AsyncMock()
    resolver = AsyncMock(return_value=(
        {Bookmaker.SPORTYBET: "sr:match:1",
         Bookmaker.BET9JA: "b9j-7",
         Bookmaker.BETWAY: "sr:match:1"},
        "sr:match:1", ""))

    watcher = EventWatcher("33660318", cfg, bp_client, collector, writer, resolver)
    await watcher.run()
    assert writer.append.call_count == 1


async def test_cadence_switch_at_kickoff(cfg, monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr("odds_scraper.watcher.asyncio.sleep", fake_sleep)

    statuses = iter([_detail(live=False), _detail(live=True), _detail(ended=True)])
    bp_client = AsyncMock()
    bp_client.get_event_detail.side_effect = lambda _id: next(statuses)
    collector = AsyncMock()
    collector.collect.return_value = _snap_list()
    writer = MagicMock()
    writer.append = AsyncMock()
    resolver = AsyncMock(return_value=(
        {Bookmaker.SPORTYBET: "sr:match:1",
         Bookmaker.BET9JA: "b9j-7",
         Bookmaker.BETWAY: "sr:match:1"},
        "sr:match:1", ""))

    watcher = EventWatcher("33660318", cfg, bp_client, collector, writer, resolver)
    await watcher.run()
    assert sleeps[0] == 600
    assert sleeps[1] == 90


async def test_status_poll_retries_then_emits_sentinel(cfg, monkeypatch):
    monkeypatch.setattr(
        "odds_scraper.watcher.asyncio.sleep", AsyncMock(return_value=None),
    )

    call_count = {"n": 0}

    async def flaky(_):
        call_count["n"] += 1
        if call_count["n"] <= 4:
            raise RuntimeError("net down")
        return _detail(ended=True)

    bp_client = AsyncMock()
    bp_client.get_event_detail.side_effect = flaky
    collector = AsyncMock()
    collector.collect.return_value = _snap_list(EventStatus.ENDED)
    writer = MagicMock()
    writer.append = AsyncMock()
    resolver = AsyncMock(return_value=(
        {Bookmaker.SPORTYBET: "sr:match:1",
         Bookmaker.BET9JA: "b9j-7",
         Bookmaker.BETWAY: "sr:match:1"},
        "sr:match:1", ""))

    watcher = EventWatcher("33660318", cfg, bp_client, collector, writer, resolver)
    await watcher.run()

    written_statuses = []
    for call in writer.append.call_args_list:
        for snap in call.args[0]:
            written_statuses.append(snap.fetch_status)
    assert FetchStatus.HTTP_ERROR in written_statuses


def test_sentinel_rows_produces_one_per_bookmaker(cfg):
    bp_client = AsyncMock()
    collector = AsyncMock()
    writer = MagicMock()
    resolver = AsyncMock()
    watcher = EventWatcher("33660318", cfg, bp_client, collector, writer, resolver)
    rows = watcher._sentinel_rows("status poll failed")
    assert len(rows) == 4
    assert {r.bookmaker for r in rows} == set(Bookmaker)
    assert all(r.fetch_status == FetchStatus.HTTP_ERROR for r in rows)
    assert all(r.fetch_error == "status poll failed" for r in rows)
    assert all(r.prices == {} for r in rows)


def test_log_tick_summary_format(cfg, caplog):
    bp_client = AsyncMock()
    collector = AsyncMock()
    writer = MagicMock()
    resolver = AsyncMock()
    watcher = EventWatcher("33660318", cfg, bp_client, collector, writer, resolver)
    watcher._last_status = EventStatus.STARTED
    rows = [
        # BP: 2 outcomes with odds+prob = 4 filled cells
        _one_snap(Bookmaker.BETPAWA, EventStatus.STARTED, prices={
            PriceKey("1x2_ft", None, "home"): (1.85, 0.54),
            PriceKey("1x2_ft", None, "draw"): (3.20, 0.31),
        }),
        # SB: 1 outcome with odds+prob = 2 filled cells
        _one_snap(Bookmaker.SPORTYBET, EventStatus.STARTED, prices={
            PriceKey("1x2_ft", None, "home"): (1.90, 0.53),
        }),
        # B9J: 1 outcome odds only = 1 filled cell
        _one_snap(Bookmaker.BET9JA, EventStatus.STARTED, prices={
            PriceKey("1x2_ft", None, "home"): (1.88, None),
        }),
        # BW: 3 outcomes odds only = 3 filled cells
        _one_snap(Bookmaker.BETWAY, EventStatus.STARTED, prices={
            PriceKey("1x2_ft", None, "home"): (1.87, None),
            PriceKey("1x2_ft", None, "draw"): (3.30, None),
            PriceKey("1x2_ft", None, "away"): (4.10, None),
        }),
    ]
    with caplog.at_level(logging.INFO, logger="odds_scraper.watcher"):
        watcher._log_tick_summary(rows)
    msgs = [r.getMessage() for r in caplog.records]
    expected = "tick 33660318 status=STARTED bp=4/54 sb=2/54 b9j=1/27 bw=3/27"
    assert any(expected in m for m in msgs), f"didn't find expected log: {msgs}"
```

- [ ] **Run watcher tests — verify the 2 new tests FAIL and existing tests may or may not pass**

Run: `pytest tests/test_watcher.py -v`
Expected: `test_sentinel_rows_produces_one_per_bookmaker` and `test_log_tick_summary_format` fail; the three existing scenarios may already pass since they only check `writer.append.call_count` and statuses (not row count).

### Step 4.2 — Update the watcher

- [ ] **Edit `src/odds_scraper/watcher.py`** to replace `_log_tick_summary` and `_sentinel_rows`. Find the existing methods (lines 110-143 in the current file) and replace them with:

```python
    def _log_tick_summary(self, rows: list[Snapshot]) -> None:
        denom = {
            Bookmaker.BETPAWA: 54, Bookmaker.SPORTYBET: 54,
            Bookmaker.BET9JA: 27, Bookmaker.BETWAY: 27,
        }
        counts: dict[Bookmaker, int] = {b: 0 for b in Bookmaker}
        for r in rows:
            for _key, (odds, prob) in r.prices.items():
                if odds is not None:
                    counts[r.bookmaker] += 1
                if prob is not None:
                    counts[r.bookmaker] += 1
        log.info(
            "tick %s status=%s bp=%d/%d sb=%d/%d b9j=%d/%d bw=%d/%d",
            self.event_bp_id, self._last_status.value,
            counts[Bookmaker.BETPAWA],   denom[Bookmaker.BETPAWA],
            counts[Bookmaker.SPORTYBET], denom[Bookmaker.SPORTYBET],
            counts[Bookmaker.BET9JA],    denom[Bookmaker.BET9JA],
            counts[Bookmaker.BETWAY],    denom[Bookmaker.BETWAY],
        )

    def _sentinel_rows(self, reason: str) -> list[Snapshot]:
        ts = datetime.now(timezone.utc)
        rows: list[Snapshot] = []
        for b in Bookmaker:
            rows.append(Snapshot(
                ts_utc=ts,
                event_bp_id=self.event_bp_id,
                sr_id="", genius_id="",
                home="", away="",
                kickoff_utc=ts,
                status=self._last_status,
                match_minute=None, score_home=None, score_away=None,
                bookmaker=b,
                fetch_status=FetchStatus.HTTP_ERROR,
                fetch_error=reason,
                prices={},
            ))
        return rows
```

- [ ] **Edit `src/odds_scraper/watcher.py`** to fix the imports at the top of the file. The current import is:

```python
from .models import (
    Bookmaker, EventStatus, FetchStatus, Market, Outcome, Snapshot,
)
```

Replace with:

```python
from .models import (
    Bookmaker, EventStatus, FetchStatus, Snapshot,
)
```

(Removes `Market` and `Outcome`, which no longer exist.)

- [ ] **Run watcher tests — verify they PASS**

Run: `pytest tests/test_watcher.py -v`
Expected: all 5 tests pass.

### Step 4.3 — Commit

- [ ] **Commit**

```bash
git add src/odds_scraper/watcher.py tests/test_watcher.py
git commit -m "$(cat <<'EOF'
feat(watcher): 4 sentinel rows + new tick-summary format

_sentinel_rows now produces one row per bookmaker (4 total) with empty
prices and fetch_status=http_error. _log_tick_summary reports filled
price cells per bookmaker: bp=N/54 sb=N/54 b9j=N/27 bw=N/27.
EOF
)"
```

---

## Task 5: Full-suite smoke + manual run

**Files:** none modified; verification only.

### Step 5.1 — Run the entire test suite

- [ ] **Run all tests**

Run: `pytest -v`
Expected: every test passes. Specifically verify `test_registry.py`, `test_status.py`, `test_resolution.py`, `test_config.py`, `test_main_supervisor.py` are still green (we didn't touch their modules).

If any of those fail with `ImportError` referring to `Market`, `Outcome`, or `CSV_HEADER`, that means a stray reference slipped through — grep for it:

```bash
grep -rn "from odds_scraper.models import" src/ tests/
grep -rn "CSV_HEADER\|Market\b\|Outcome\b" src/odds_scraper/ tests/
```

Fix any remaining references (none should exist, since `registry.py` uses `bookieskit.markets.OutcomeMapping`, not the local `Outcome` enum).

### Step 5.2 — Manual run smoke

- [ ] **Run the scraper for one tick and inspect the CSV**

Before running, back up the existing CSV in case the rename misbehaves:

```powershell
Copy-Item data\odds_snapshots.csv data\odds_snapshots.csv.bak
```

Run the scraper:

```powershell
python -m odds_scraper.main --config config.yaml
```

Wait for the first few tick-summary log lines, then stop with `Ctrl+C`.

Expected observations:
- A file `data\odds_snapshots_v1_YYYY-MM-DD.csv` exists with the old long-format header (the rename happened).
- `data\odds_snapshots.csv` is fresh, first line is the new 68-column header.
- Each tick wrote ≤16 rows (4 events × 4 bookmakers) to the new file.
- A `tick <id> status=<X> bp=N/54 sb=N/54 b9j=N/27 bw=N/27` log line appeared per event per tick.

Open the new CSV and spot-check that prices columns are populated where expected. Compare against `data\odds_snapshots.csv.bak` for the same event to verify nothing was lost.

### Step 5.3 — Final commit (only if any fixes were needed in step 5.1)

- [ ] **Commit any fixes**

If step 5.1 surfaced stray imports or references, the test runs in earlier tasks should already have caught them, but if anything was patched here:

```bash
git add -A
git commit -m "chore: fix straggler imports after model refactor"
```

If no fixes were needed, skip this step.

---

## Self-review

**Spec coverage:**
- Manifest, header builder, PriceKey, refactored Snapshot, shrunk FetchStatus → Task 1
- Collector returns 4 rows, manifest-driven extraction, prob rule, error paths, out-of-manifest ignored → Task 2
- Writer header from manifest + v1 rename → Task 3
- Watcher sentinel count + tick log format → Task 4
- Test impact table → Tasks 1-4 each include test rewrites
- Smoke + migration verification → Task 5

**Placeholder scan:** no TBDs, no "implement later", every code step has full code, every command has expected output.

**Type consistency:** `MarketSpec`, `MARKET_MANIFEST`, `PriceKey`, `Snapshot.prices`, `FetchStatus` (4 values), `build_csv_header()` — names consistent across all tasks. Collector helper `_extract_prices_for_manifest` referenced in Task 2 implementation only (private). `_one_snap` helper in test_watcher.py used internally to that file only.
