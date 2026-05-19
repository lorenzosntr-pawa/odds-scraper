# 1up / 2up Odds Scraper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Continuously scrape 1up/2up odds (+ bookmaker-exposed probability where available) for four BetPawa-anchored NG soccer events across BetPawa, SportyBet, Bet9ja, Betway. Append every snapshot to a single CSV. Switch from 10-min prematch cadence to 90-s live cadence based on BetPawa status; capture match minute and score when live.

**Architecture:** One long-running asyncio process. `main.py` spawns one `EventWatcher` task per event. Each watcher polls BetPawa for status, decides cadence (`UPCOMING` → 600 s, `STARTED` → 90 s, `ENDED` → exit), and calls a shared stateless `OddsCollector` which fans out 4 bookmaker fetches in parallel via `asyncio.gather`. Cross-bookmaker id resolution uses bookieskit's SR-id + BetGenius-id union-find, cached on disk. A single async-locked `CsvWriter` appends rows to `data/odds_snapshots.csv`. Failure rows are emitted with explicit `fetch_status` so gaps are never silent.

**Tech Stack:** Python 3.11+, `bookieskit @ git+https://github.com/lorenzosntr-pawa/bookieskit`, `pyyaml`, `pytest`, `pytest-asyncio`.

**Spec:** `docs/superpowers/specs/2026-05-19-odds-scraper-design.md`

---

## File map

| Path | Created in | Responsibility |
| ---- | ---------- | -------------- |
| `pyproject.toml` | Task 0 | Project metadata, dependencies, pytest config |
| `.gitignore` | Task 0 | Ignore `data/`, `__pycache__`, venv |
| `config.yaml` | Task 0 | Event IDs, intervals, output paths |
| `src/odds_scraper/__init__.py` | Task 0 | Package marker |
| `src/odds_scraper/models.py` | Task 1 | `Snapshot`, `ResolvedIds`, `EventStatus`, `FetchStatus` |
| `src/odds_scraper/status.py` | Task 2 | `parse_status`, `parse_clock`, `parse_score` for BetPawa detail |
| `src/odds_scraper/writer.py` | Task 3 | Async-locked CSV writer, header-once |
| `src/odds_scraper/registry.py` | Task 4 | BetPawa 1up/2up `MarketMapping`s + `build_registry()` |
| `src/odds_scraper/resolution.py` | Task 5 | ID resolution cache, union-find matching, JSON persistence |
| `src/odds_scraper/collector.py` | Task 6 | Stateless one-tick fan-out fetch; always 24 rows |
| `src/odds_scraper/watcher.py` | Task 7 | Per-event lifecycle, cadence, watchdog |
| `src/odds_scraper/config.py` | Task 8 | YAML loader + env-var overrides |
| `src/odds_scraper/main.py` | Task 9 | Entrypoint, supervisor, signal handling |
| `tests/conftest.py` | Task 1 | Shared fixtures + sample-data loader |
| `tests/fixtures/*.json` | Task 1 | Captured/synthesized bookmaker responses |
| `tests/test_*.py` | Tasks 1–9 | Per-module unit tests |
| `scripts/capture_fixtures.py` | Task 10 | Ad-hoc real-API fixture capture (network-bound, not in CI) |
| `README.md` | Task 10 | How to install, configure, run, troubleshoot |

---

## Task 0: Project scaffold

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `config.yaml`
- Create: `src/odds_scraper/__init__.py`

- [ ] **Step 1: Initialize git repo**

```bash
git init
git checkout -b main
```

- [ ] **Step 2: Write `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "odds-scraper"
version = "0.1.0"
description = "1up/2up odds scraper for BetPawa-anchored NG events"
requires-python = ">=3.11"
dependencies = [
    "bookieskit @ git+https://github.com/lorenzosntr-pawa/bookieskit.git",
    "pyyaml>=6.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
]

[project.scripts]
odds-scraper = "odds_scraper.main:cli"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
pythonpath = ["src"]
```

- [ ] **Step 3: Write `.gitignore`**

```
__pycache__/
*.pyc
.venv/
venv/
.pytest_cache/
.mypy_cache/
*.egg-info/
build/
dist/
data/*.csv
data/*.json
!data/.gitkeep
.env
```

- [ ] **Step 4: Write `config.yaml`**

```yaml
country: ng
events:
  - 33660318
  - 33660319
  - 33605719
  - 33575997
cadence:
  prematch_seconds: 600
  live_seconds: 90
  status_retry_backoff_seconds: [5, 15, 45]
  watchdog_after_kickoff_seconds: 10800
output:
  csv_path: data/odds_snapshots.csv
  resolution_cache_path: data/resolution_cache.json
log_level: INFO
```

- [ ] **Step 5: Create package marker and data dir**

`src/odds_scraper/__init__.py`:
```python
"""1up / 2up odds scraper for BetPawa-anchored NG soccer events."""

__version__ = "0.1.0"
```

```bash
mkdir -p data tests/fixtures scripts
touch data/.gitkeep
```

- [ ] **Step 6: Install dev deps and verify**

```bash
python -m venv .venv
. .venv/Scripts/activate     # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pytest --collect-only
```
Expected: pytest collects 0 tests, no errors.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml .gitignore config.yaml src/ tests/ scripts/ data/.gitkeep
git commit -m "chore: project scaffold"
```

---

## Task 1: `models.py` — dataclasses & enums

**Files:**
- Create: `src/odds_scraper/models.py`
- Create: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

`tests/test_models.py`:
```python
from datetime import datetime, timezone
from odds_scraper.models import (
    EventStatus, FetchStatus, Bookmaker, Market, Outcome,
    Snapshot, ResolvedIds,
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
    assert row[8] == ""           # match_minute empty
    assert row[9] == ""           # score_home empty
    assert row[14] == ""          # odds empty
    assert row[15] == ""          # probability empty
    assert row[16] == "lookup_failed"
    assert row[17] == "sb_id not found via sr/genius"


def test_resolved_ids_has_at_least_one_id():
    r = ResolvedIds(sr_id="sr:match:1", genius_id=None, sb_id="sr:match:1",
                    b9j_id=None, bw_id=None)
    assert r.matched_bookmakers() == {"sportybet"}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_models.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'odds_scraper.models'`.

- [ ] **Step 3: Implement `models.py`**

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
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_models.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/odds_scraper/models.py tests/test_models.py
git commit -m "feat(models): snapshot, ids, enums and csv serialization"
```

---

## Task 2: `status.py` — BetPawa status / clock / score parsing

**Files:**
- Create: `src/odds_scraper/status.py`
- Create: `tests/test_status.py`
- Create: `tests/fixtures/betpawa_event_upcoming.json` (synthetic — shape matches BetPawa convention; replace once real fixture captured in Task 10)
- Create: `tests/fixtures/betpawa_event_live.json`
- Create: `tests/fixtures/betpawa_event_ended.json`

> **Note on fixtures:** Until `scripts/capture_fixtures.py` is run against the real BetPawa API (Task 10), the fixtures used here are minimal synthetic JSON objects with the *fields the parser depends on*. The test suite asserts on parser output, so swapping in real captures later only needs the same fields present. The parser tolerates unknown fields.

- [ ] **Step 1: Write the failing test**

`tests/test_status.py`:
```python
import json
from pathlib import Path
import pytest

from odds_scraper.models import EventStatus
from odds_scraper.status import parse_status, parse_clock, parse_score

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_parse_status_upcoming():
    detail = _load("betpawa_event_upcoming.json")
    assert parse_status(detail) == EventStatus.UPCOMING


def test_parse_status_started():
    detail = _load("betpawa_event_live.json")
    assert parse_status(detail) == EventStatus.STARTED


def test_parse_status_ended():
    detail = _load("betpawa_event_ended.json")
    assert parse_status(detail) == EventStatus.ENDED


def test_parse_status_unknown_falls_back_to_upcoming_if_in_future(monkeypatch):
    detail = {"status": "WHATEVER_NEW_VALUE", "startTime": "2099-01-01T00:00:00Z"}
    assert parse_status(detail) == EventStatus.UPCOMING


def test_parse_clock_normal_minute():
    detail = _load("betpawa_event_live.json")
    # fixture has currentMinute = 34
    assert parse_clock(detail) == 34


def test_parse_clock_stoppage_collapses_to_base():
    detail = {"status": "STARTED", "currentMinute": "45+2"}
    assert parse_clock(detail) == 47


def test_parse_clock_halftime_returns_45_sentinel():
    detail = {"status": "STARTED", "period": "HT"}
    assert parse_clock(detail) == 45


def test_parse_clock_returns_none_when_not_live():
    detail = _load("betpawa_event_upcoming.json")
    assert parse_clock(detail) is None


def test_parse_score_live():
    detail = _load("betpawa_event_live.json")
    # fixture has score = "1-0"
    assert parse_score(detail) == (1, 0)


def test_parse_score_returns_none_when_not_live():
    detail = _load("betpawa_event_upcoming.json")
    assert parse_score(detail) is None
```

- [ ] **Step 2: Write fixtures**

`tests/fixtures/betpawa_event_upcoming.json`:
```json
{
  "id": "33660318",
  "name": "Team A vs Team B",
  "home": {"name": "Team A"},
  "away": {"name": "Team B"},
  "startTime": "2026-05-19T15:00:00Z",
  "status": "UPCOMING",
  "score": null,
  "currentMinute": null,
  "period": null
}
```

`tests/fixtures/betpawa_event_live.json`:
```json
{
  "id": "33660318",
  "name": "Team A vs Team B",
  "home": {"name": "Team A"},
  "away": {"name": "Team B"},
  "startTime": "2026-05-19T15:00:00Z",
  "status": "STARTED",
  "score": "1-0",
  "currentMinute": 34,
  "period": "1H"
}
```

`tests/fixtures/betpawa_event_ended.json`:
```json
{
  "id": "33660318",
  "name": "Team A vs Team B",
  "home": {"name": "Team A"},
  "away": {"name": "Team B"},
  "startTime": "2026-05-19T15:00:00Z",
  "status": "ENDED",
  "score": "2-1",
  "currentMinute": 90,
  "period": "FT"
}
```

- [ ] **Step 3: Run tests, confirm they fail**

```bash
pytest tests/test_status.py -v
```
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 4: Implement `status.py`**

```python
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from .models import EventStatus

_UPCOMING_ALIASES = {"UPCOMING", "PRE", "PREMATCH", "NOT_STARTED", "SCHEDULED"}
_LIVE_ALIASES = {"STARTED", "LIVE", "IN_PROGRESS", "IN_PLAY"}
_ENDED_ALIASES = {"ENDED", "FT", "FINISHED", "COMPLETE", "COMPLETED"}
_SUSPENDED_ALIASES = {"SUSPENDED", "PAUSED"}


def parse_status(detail: dict[str, Any]) -> EventStatus:
    raw = str(detail.get("status", "")).upper()
    if raw in _ENDED_ALIASES:
        return EventStatus.ENDED
    if raw in _LIVE_ALIASES:
        return EventStatus.STARTED
    if raw in _SUSPENDED_ALIASES:
        return EventStatus.SUSPENDED
    if raw in _UPCOMING_ALIASES:
        return EventStatus.UPCOMING
    # Unknown value: fall back based on kickoff time
    kickoff = _parse_iso(detail.get("startTime"))
    if kickoff is None:
        return EventStatus.UNKNOWN
    now = datetime.now(timezone.utc)
    return EventStatus.UPCOMING if kickoff > now else EventStatus.STARTED


def parse_clock(detail: dict[str, Any]) -> Optional[int]:
    if parse_status(detail) != EventStatus.STARTED:
        return None
    period = str(detail.get("period", "")).upper()
    if period in {"HT", "HALFTIME"}:
        return 45
    raw = detail.get("currentMinute")
    if raw is None:
        return None
    if isinstance(raw, int):
        return raw
    text = str(raw).strip()
    if "+" in text:
        base, plus = text.split("+", 1)
        try:
            return int(base) + int(plus)
        except ValueError:
            return None
    try:
        return int(text)
    except ValueError:
        return None


def parse_score(detail: dict[str, Any]) -> Optional[tuple[int, int]]:
    if parse_status(detail) not in (EventStatus.STARTED, EventStatus.ENDED):
        return None
    raw = detail.get("score")
    if raw is None:
        return None
    if isinstance(raw, dict):
        try:
            return int(raw.get("home", 0)), int(raw.get("away", 0))
        except (TypeError, ValueError):
            return None
    text = str(raw).strip()
    if "-" not in text:
        return None
    h, a = text.split("-", 1)
    try:
        return int(h.strip()), int(a.strip())
    except ValueError:
        return None


def _parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        # Accept trailing Z
        s = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(s).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None
```

- [ ] **Step 5: Run tests, verify pass**

```bash
pytest tests/test_status.py -v
```
Expected: 9 passed.

- [ ] **Step 6: Commit**

```bash
git add src/odds_scraper/status.py tests/test_status.py tests/fixtures/betpawa_event_*.json
git commit -m "feat(status): parse betpawa status, clock and score"
```

---

## Task 3: `writer.py` — async-locked CSV writer

**Files:**
- Create: `src/odds_scraper/writer.py`
- Create: `tests/test_writer.py`

- [ ] **Step 1: Write the failing test**

`tests/test_writer.py`:
```python
import asyncio
import csv
from datetime import datetime, timezone
from pathlib import Path

import pytest

from odds_scraper.models import (
    Bookmaker, EventStatus, FetchStatus, Market, Outcome, Snapshot, CSV_HEADER,
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
        market=Market.ONE_UP,
        outcome=Outcome.HOME,
        odds=1.5 + idx * 0.01,
        probability=None,
        fetch_status=FetchStatus.OK,
        fetch_error="",
    )


async def test_header_written_once(tmp_path: Path):
    path = tmp_path / "out.csv"
    async with CsvWriter(path) as w:
        await w.append([_make_snap(0)])
    async with CsvWriter(path) as w:
        await w.append([_make_snap(1)])
    rows = list(csv.reader(path.read_text(encoding="utf-8").splitlines()))
    assert rows[0] == list(CSV_HEADER)
    assert len(rows) == 3  # header + 2 data rows
    assert rows[1][0].startswith("2026-05-19T14:00:00")
    assert rows[2][0].startswith("2026-05-19T14:00:01")


async def test_concurrent_appends_do_not_interleave(tmp_path: Path):
    path = tmp_path / "out.csv"
    snaps_a = [_make_snap(i, Bookmaker.BETPAWA) for i in range(50)]
    snaps_b = [_make_snap(i, Bookmaker.SPORTYBET) for i in range(50)]

    async with CsvWriter(path) as w:
        await asyncio.gather(w.append(snaps_a), w.append(snaps_b))

    rows = list(csv.reader(path.read_text(encoding="utf-8").splitlines()))
    assert rows[0] == list(CSV_HEADER)
    data = rows[1:]
    assert len(data) == 100
    # No partial / malformed rows
    assert all(len(r) == len(CSV_HEADER) for r in data)
    bookmakers = [r[11] for r in data]
    assert bookmakers.count("betpawa") == 50
    assert bookmakers.count("sportybet") == 50
```

- [ ] **Step 2: Run test, expect fail**

```bash
pytest tests/test_writer.py -v
```
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `writer.py`**

```python
from __future__ import annotations

import asyncio
import csv
import logging
from pathlib import Path
from typing import Iterable

from .models import CSV_HEADER, Snapshot

log = logging.getLogger(__name__)


class CsvWriter:
    def __init__(self, path: Path | str):
        self._path = Path(path)
        self._lock = asyncio.Lock()
        self._fh = None
        self._writer = None

    async def __aenter__(self) -> "CsvWriter":
        self._path.parent.mkdir(parents=True, exist_ok=True)
        new_file = not self._path.exists() or self._path.stat().st_size == 0
        self._fh = self._path.open("a", encoding="utf-8", newline="")
        self._writer = csv.writer(self._fh, lineterminator="\n")
        if new_file:
            self._writer.writerow(CSV_HEADER)
            self._fh.flush()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._fh is not None:
            self._fh.flush()
            try:
                import os
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
```

- [ ] **Step 4: Run test, verify pass**

```bash
pytest tests/test_writer.py -v
```
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/odds_scraper/writer.py tests/test_writer.py
git commit -m "feat(writer): async-locked csv writer with header-once"
```

---

## Task 4: `registry.py` — BetPawa 1up/2up market mappings

**Files:**
- Create: `src/odds_scraper/registry.py`
- Create: `tests/test_registry.py`
- Create: `tests/fixtures/betpawa_markets_sample.json` (synthetic — to be replaced after capture in Task 10; flagged in README)

> **Verification gate at implementation time:** before writing the BetPawa `MarketMapping`, the engineer MUST first run `scripts/capture_fixtures.py` (defined in Task 10 — bring forward if needed) against event `33660318` to capture the real BetPawa response, and inspect the actual `marketId` / outcome ids for 1up and 2up. Replace the placeholder `betpawa_id="BP_1UP_PLACEHOLDER"` and outcome ids below with the real values, and update `tests/fixtures/betpawa_markets_sample.json` accordingly. **Do not commit placeholder ids.**

- [ ] **Step 1: Capture a real BetPawa response and identify the 1up/2up market ids**

```bash
python scripts/capture_fixtures.py 33660318 --bookmaker betpawa \
    --out tests/fixtures/betpawa_event_real.json
```

Manually inspect the file. Find the market objects whose names contain "1up" / "2up" / "1-up" / "2-up". Record:
- The BetPawa `marketId` (string) for each
- The outcome ids for home / draw / away

If the capture script doesn't exist yet, jump to Task 10 step 1 (the script is small), run it, then resume here.

- [ ] **Step 2: Write `tests/fixtures/betpawa_markets_sample.json`**

Save a trimmed version of the real response that includes only the 1up and 2up markets and their three outcomes each. Keep the shape identical to the real response — only remove unrelated markets.

- [ ] **Step 3: Write the failing test**

`tests/test_registry.py`:
```python
import json
from pathlib import Path

from bookieskit.markets import parse_markets

from odds_scraper.registry import build_registry, BP_ONE_UP_MARKET_ID, BP_TWO_UP_MARKET_ID

FIXTURES = Path(__file__).parent / "fixtures"


def test_betpawa_1up_2up_parsed_via_extended_registry():
    raw = json.loads((FIXTURES / "betpawa_markets_sample.json").read_text())
    registry = build_registry()
    markets = parse_markets(raw, platform="betpawa", registry=registry)
    canonical_ids = {m.canonical_id for m in markets}
    assert "1x2_1up_ft" in canonical_ids
    assert "1x2_2up_ft" in canonical_ids


def test_betpawa_1up_outcomes_have_odds_and_probability():
    raw = json.loads((FIXTURES / "betpawa_markets_sample.json").read_text())
    registry = build_registry()
    markets = parse_markets(raw, platform="betpawa", registry=registry)
    one_up = next(m for m in markets if m.canonical_id == "1x2_1up_ft")
    by_name = {o.canonical_name: o for o in one_up.outcomes}
    assert {"home", "draw", "away"} <= set(by_name)
    for name in ("home", "draw", "away"):
        assert by_name[name].odds is not None
        # BetPawa exposes probability — assert it's parsed
        assert by_name[name].probability is not None


def test_market_ids_are_not_placeholders():
    assert BP_ONE_UP_MARKET_ID != "BP_1UP_PLACEHOLDER"
    assert BP_TWO_UP_MARKET_ID != "BP_2UP_PLACEHOLDER"
```

- [ ] **Step 4: Run tests, expect fail**

```bash
pytest tests/test_registry.py -v
```
Expected: FAIL — module not implemented.

- [ ] **Step 5: Implement `registry.py`**

Replace placeholder ids with the ones identified in step 1.

```python
"""Extension of bookieskit's market registry for BetPawa 1up / 2up.

bookieskit ships 1x2_1up_ft / 1x2_2up_ft mappings for SportyBet, Bet9ja and
Betway but does not currently include a BetPawa mapping. We add it here.
"""

from __future__ import annotations

from bookieskit.markets import MarketRegistry, OutcomeMapping, default_registry

# Filled in after capture (see Task 4 step 1).
BP_ONE_UP_MARKET_ID: str = "<REPLACE_AFTER_CAPTURE>"
BP_TWO_UP_MARKET_ID: str = "<REPLACE_AFTER_CAPTURE>"

# Outcome ids on BetPawa for these markets. Identified from real response.
BP_HOME_OUTCOME_ID: str = "<REPLACE>"
BP_DRAW_OUTCOME_ID: str = "<REPLACE>"
BP_AWAY_OUTCOME_ID: str = "<REPLACE>"


def build_registry() -> MarketRegistry:
    registry = default_registry()
    _add_one_up(registry)
    _add_two_up(registry)
    return registry


def _add_one_up(registry: MarketRegistry) -> None:
    registry.add(
        canonical_id="1x2_1up_ft",
        name="1X2 — 1 goal lead at any point — Full Time",
        betpawa_id=BP_ONE_UP_MARKET_ID,
        outcomes={
            "home": OutcomeMapping(canonical_name="home", betpawa=BP_HOME_OUTCOME_ID),
            "draw": OutcomeMapping(canonical_name="draw", betpawa=BP_DRAW_OUTCOME_ID),
            "away": OutcomeMapping(canonical_name="away", betpawa=BP_AWAY_OUTCOME_ID),
        },
    )


def _add_two_up(registry: MarketRegistry) -> None:
    registry.add(
        canonical_id="1x2_2up_ft",
        name="1X2 — 2 goal lead at any point — Full Time",
        betpawa_id=BP_TWO_UP_MARKET_ID,
        outcomes={
            "home": OutcomeMapping(canonical_name="home", betpawa=BP_HOME_OUTCOME_ID),
            "draw": OutcomeMapping(canonical_name="draw", betpawa=BP_DRAW_OUTCOME_ID),
            "away": OutcomeMapping(canonical_name="away", betpawa=BP_AWAY_OUTCOME_ID),
        },
    )
```

> **API surface uncertainty:** bookieskit's `default_registry()` / `registry.add(...)` signatures may differ slightly. Verify by reading `bookieskit/markets/__init__.py` once installed; adjust the import names and `add(...)` keyword args to match. The `betpawa_id=` keyword is the most likely difference — bookieskit uses `<platform>_id=` and `<platform>_key=` inconsistently across bookmakers.

- [ ] **Step 6: Run tests, verify pass**

```bash
pytest tests/test_registry.py -v
```
Expected: 3 passed. If `test_market_ids_are_not_placeholders` still fails, the placeholder replacement was missed — go back to step 1.

- [ ] **Step 7: Commit**

```bash
git add src/odds_scraper/registry.py tests/test_registry.py tests/fixtures/betpawa_markets_sample.json
git commit -m "feat(registry): extend market registry with betpawa 1up/2up"
```

---

## Task 5: `resolution.py` — ID resolution cache with union-find

**Files:**
- Create: `src/odds_scraper/resolution.py`
- Create: `tests/test_resolution.py`

- [ ] **Step 1: Write the failing test**

`tests/test_resolution.py`:
```python
from pathlib import Path
import json

import pytest

from odds_scraper.resolution import ResolutionCache, ResolutionKey


def test_loads_empty_when_file_missing(tmp_path: Path):
    cache = ResolutionCache(tmp_path / "missing.json")
    cache.load()
    assert cache.get(ResolutionKey("33660318", "prematch")) is None


def test_set_and_get_roundtrip(tmp_path: Path):
    cache_path = tmp_path / "c.json"
    cache = ResolutionCache(cache_path)
    cache.load()
    key = ResolutionKey("33660318", "prematch")
    entry = {"sr_id": "sr:match:1", "genius_id": "g-9", "sb_id": "sr:match:1",
             "b9j_id": "b9j-7", "bw_id": "sr:match:1"}
    cache.set(key, entry)
    assert cache.get(key) == entry

    cache2 = ResolutionCache(cache_path)
    cache2.load()
    assert cache2.get(key) == entry


def test_mark_stale_forces_reresolve(tmp_path: Path):
    cache = ResolutionCache(tmp_path / "c.json")
    cache.load()
    key = ResolutionKey("33660318", "prematch")
    cache.set(key, {"sr_id": "sr:match:1"})
    cache.mark_stale(key)
    assert cache.get(key) is None


def test_separate_regimes(tmp_path: Path):
    cache = ResolutionCache(tmp_path / "c.json")
    cache.load()
    pre = ResolutionKey("33660318", "prematch")
    live = ResolutionKey("33660318", "live")
    cache.set(pre, {"sr_id": "sr:match:1", "b9j_id": "internal-prematch"})
    cache.set(live, {"sr_id": "sr:match:1", "b9j_id": "genius-live"})
    assert cache.get(pre)["b9j_id"] == "internal-prematch"
    assert cache.get(live)["b9j_id"] == "genius-live"


def test_match_via_any_shared_provider_id():
    from odds_scraper.resolution import match_provider_ids
    # BetPawa has SR + Genius; SportyBet has SR only; Bet9ja-live has Genius only
    bp = {"sr": "sr:match:1", "genius": "g-9"}
    sb = {"sr": "sr:match:1"}
    b9j = {"genius": "g-9"}
    bw = {"sr": "sr:match:1"}
    matched = match_provider_ids(bp, [("sportybet", sb), ("bet9ja", b9j), ("betway", bw)])
    assert matched == {"sportybet": "sr:match:1", "bet9ja": "g-9", "betway": "sr:match:1"}


def test_no_match_returns_empty():
    from odds_scraper.resolution import match_provider_ids
    bp = {"sr": "sr:match:1"}
    other = {"sr": "sr:match:99"}
    assert match_provider_ids(bp, [("sportybet", other)]) == {}
```

- [ ] **Step 2: Run, expect fail**

```bash
pytest tests/test_resolution.py -v
```
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `resolution.py`**

```python
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResolutionKey:
    event_bp_id: str
    regime: str  # "prematch" | "live"

    def as_str(self) -> str:
        return f"{self.event_bp_id}:{self.regime}"

    @classmethod
    def from_str(cls, raw: str) -> "ResolutionKey":
        event, regime = raw.split(":", 1)
        return cls(event, regime)


class ResolutionCache:
    """Get-or-resolve cache for cross-bookmaker ids, persisted to JSON.

    Each new entry is written to disk immediately so a hard kill never
    loses cached ids.
    """

    def __init__(self, path: Path | str):
        self._path = Path(path)
        self._data: dict[str, dict] = {}
        self._loaded = False

    def load(self) -> None:
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                log.warning("resolution cache unreadable, starting fresh: %s", e)
                self._data = {}
        self._loaded = True

    def get(self, key: ResolutionKey) -> Optional[dict]:
        assert self._loaded, "ResolutionCache.load() not called"
        return self._data.get(key.as_str())

    def set(self, key: ResolutionKey, entry: dict) -> None:
        assert self._loaded, "ResolutionCache.load() not called"
        self._data[key.as_str()] = entry
        self._persist()

    def mark_stale(self, key: ResolutionKey) -> None:
        if key.as_str() in self._data:
            del self._data[key.as_str()]
            self._persist()

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)


def match_provider_ids(
    anchor: dict[str, str],
    others: Iterable[tuple[str, dict[str, str]]],
) -> dict[str, str]:
    """Return {bookmaker_name: shared_id} for every bookmaker that shares
    any provider id with the anchor. Provider id keys are e.g. "sr", "genius".
    """
    out: dict[str, str] = {}
    for name, other in others:
        shared = next(
            (anchor[p] for p in anchor if p in other and anchor[p] == other[p]),
            None,
        )
        if shared is not None:
            out[name] = shared
    return out
```

- [ ] **Step 4: Run tests, verify pass**

```bash
pytest tests/test_resolution.py -v
```
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/odds_scraper/resolution.py tests/test_resolution.py
git commit -m "feat(resolution): id cache with union-find provider matching"
```

---

## Task 6: `collector.py` — stateless one-tick fan-out fetch

**Files:**
- Create: `src/odds_scraper/collector.py`
- Create: `tests/test_collector.py`

The collector takes a parsed BetPawa event detail and resolved ids and returns **always 24 rows** — 4 bookmakers × 2 markets × 3 outcomes — with `fetch_status=ok` for successes and explicit failure statuses otherwise.

- [ ] **Step 1: Write the failing test**

`tests/test_collector.py`:
```python
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from odds_scraper.collector import OddsCollector
from odds_scraper.models import (
    Bookmaker, EventStatus, FetchStatus, Market, Outcome,
)


def _bp_detail(status="UPCOMING"):
    return {
        "id": "33660318",
        "home": {"name": "Team A"}, "away": {"name": "Team B"},
        "startTime": "2026-05-19T15:00:00Z",
        "status": status,
        "score": "1-0" if status == "STARTED" else None,
        "currentMinute": 34 if status == "STARTED" else None,
        "period": "1H" if status == "STARTED" else None,
    }


def _ok_markets():
    """Return a parsed-market structure with 1up + 2up, all three outcomes
    priced. Shape mirrors bookieskit NormalizedMarket."""
    return [
        _market("1x2_1up_ft", {"home": (1.85, 0.54), "draw": (3.2, 0.31),
                               "away": (4.5, 0.22)}),
        _market("1x2_2up_ft", {"home": (2.50, 0.40), "draw": (3.8, 0.26),
                               "away": (6.0, 0.16)}),
    ]


def _market(canonical_id, outcomes):
    class _O:
        def __init__(self, name, odds, prob):
            self.canonical_name = name
            self.odds = odds
            self.probability = prob

    class _M:
        def __init__(self, cid, outs):
            self.canonical_id = cid
            self.outcomes = [_O(n, o, p) for n, (o, p) in outs.items()]
    return _M(canonical_id, outcomes)


@pytest.fixture
def collector():
    return OddsCollector(
        fetchers={
            Bookmaker.BETPAWA: AsyncMock(return_value=_ok_markets()),
            Bookmaker.SPORTYBET: AsyncMock(return_value=_ok_markets()),
            Bookmaker.BET9JA: AsyncMock(return_value=_ok_markets()),
            Bookmaker.BETWAY: AsyncMock(return_value=_ok_markets()),
        },
    )


async def test_always_24_rows_all_ok(collector):
    detail = _bp_detail()
    resolved = {
        Bookmaker.SPORTYBET: "sr:match:1",
        Bookmaker.BET9JA: "b9j-7",
        Bookmaker.BETWAY: "sr:match:1",
    }
    rows = await collector.collect(detail, resolved, sr_id="sr:match:1", genius_id="")
    assert len(rows) == 24
    assert sum(1 for r in rows if r.fetch_status == FetchStatus.OK) == 24
    by_bk = {b: [r for r in rows if r.bookmaker == b] for b in Bookmaker}
    for b in Bookmaker:
        assert len(by_bk[b]) == 6  # 2 markets * 3 outcomes


async def test_lookup_failed_bookmaker_emits_6_empty_rows():
    coll = OddsCollector(
        fetchers={
            Bookmaker.BETPAWA: AsyncMock(return_value=_ok_markets()),
            Bookmaker.SPORTYBET: AsyncMock(return_value=_ok_markets()),
            Bookmaker.BET9JA: AsyncMock(return_value=_ok_markets()),
            Bookmaker.BETWAY: AsyncMock(return_value=_ok_markets()),
        },
    )
    rows = await coll.collect(
        _bp_detail(),
        resolved={
            Bookmaker.SPORTYBET: "sr:match:1",
            Bookmaker.BET9JA: None,           # no b9j id
            Bookmaker.BETWAY: "sr:match:1",
        },
        sr_id="sr:match:1", genius_id="",
    )
    b9j_rows = [r for r in rows if r.bookmaker == Bookmaker.BET9JA]
    assert len(b9j_rows) == 6
    assert all(r.fetch_status == FetchStatus.LOOKUP_FAILED for r in b9j_rows)
    assert all(r.odds is None for r in b9j_rows)


async def test_http_error_emits_6_http_error_rows():
    failing_fetcher = AsyncMock(side_effect=RuntimeError("HTTP 503"))
    coll = OddsCollector(
        fetchers={
            Bookmaker.BETPAWA: AsyncMock(return_value=_ok_markets()),
            Bookmaker.SPORTYBET: failing_fetcher,
            Bookmaker.BET9JA: AsyncMock(return_value=_ok_markets()),
            Bookmaker.BETWAY: AsyncMock(return_value=_ok_markets()),
        },
    )
    rows = await coll.collect(
        _bp_detail(),
        resolved={Bookmaker.SPORTYBET: "sr:match:1",
                  Bookmaker.BET9JA: "b9j-7",
                  Bookmaker.BETWAY: "sr:match:1"},
        sr_id="sr:match:1", genius_id="",
    )
    sb_rows = [r for r in rows if r.bookmaker == Bookmaker.SPORTYBET]
    assert len(sb_rows) == 6
    assert all(r.fetch_status == FetchStatus.HTTP_ERROR for r in sb_rows)


async def test_not_offered_when_market_missing():
    only_1up = [_market("1x2_1up_ft", {"home": (1.85, 0.54),
                                       "draw": (3.2, 0.31),
                                       "away": (4.5, 0.22)})]
    coll = OddsCollector(
        fetchers={
            Bookmaker.BETPAWA: AsyncMock(return_value=_ok_markets()),
            Bookmaker.SPORTYBET: AsyncMock(return_value=only_1up),
            Bookmaker.BET9JA: AsyncMock(return_value=_ok_markets()),
            Bookmaker.BETWAY: AsyncMock(return_value=_ok_markets()),
        },
    )
    rows = await coll.collect(
        _bp_detail(),
        resolved={Bookmaker.SPORTYBET: "sr:match:1",
                  Bookmaker.BET9JA: "b9j-7",
                  Bookmaker.BETWAY: "sr:match:1"},
        sr_id="sr:match:1", genius_id="",
    )
    sb_2up = [r for r in rows
              if r.bookmaker == Bookmaker.SPORTYBET and r.market == Market.TWO_UP]
    assert len(sb_2up) == 3
    assert all(r.fetch_status == FetchStatus.NOT_OFFERED for r in sb_2up)


async def test_live_status_populates_clock_and_score(collector):
    detail = _bp_detail("STARTED")
    rows = await collector.collect(detail,
                                   resolved={Bookmaker.SPORTYBET: "sr:match:1",
                                             Bookmaker.BET9JA: "b9j-7",
                                             Bookmaker.BETWAY: "sr:match:1"},
                                   sr_id="sr:match:1", genius_id="g-9")
    assert all(r.status == EventStatus.STARTED for r in rows)
    assert all(r.match_minute == 34 for r in rows)
    assert all(r.score_home == 1 and r.score_away == 0 for r in rows)
    assert all(r.genius_id == "g-9" for r in rows)


async def test_probability_only_for_betpawa_and_sportybet(collector):
    rows = await collector.collect(
        _bp_detail(),
        resolved={Bookmaker.SPORTYBET: "sr:match:1",
                  Bookmaker.BET9JA: "b9j-7",
                  Bookmaker.BETWAY: "sr:match:1"},
        sr_id="sr:match:1", genius_id="",
    )
    for r in rows:
        if r.bookmaker in (Bookmaker.BETPAWA, Bookmaker.SPORTYBET):
            assert r.probability is not None
        else:
            assert r.probability is None
```

- [ ] **Step 2: Run, expect fail**

```bash
pytest tests/test_collector.py -v
```
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `collector.py`**

```python
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from .models import (
    Bookmaker, EventStatus, FetchStatus, Market, Outcome, Snapshot,
)
from .status import parse_status, parse_clock, parse_score

log = logging.getLogger(__name__)

# Bookmakers whose probability field we trust verbatim.
_PROB_BOOKMAKERS = {Bookmaker.BETPAWA, Bookmaker.SPORTYBET}

Fetcher = Callable[..., Awaitable[list]]


class OddsCollector:
    """Stateless one-tick fan-out. Always returns 24 rows per call."""

    def __init__(self, fetchers: dict[Bookmaker, Fetcher]):
        for b in Bookmaker:
            if b not in fetchers:
                raise ValueError(f"fetcher missing for {b}")
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
        home = (bp_detail.get("home") or {}).get("name", "")
        away = (bp_detail.get("away") or {}).get("name", "")
        kickoff_iso = bp_detail.get("startTime", "")
        try:
            kickoff = datetime.fromisoformat(str(kickoff_iso).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            kickoff = ts  # fallback; never used for logic

        # Fan out
        results: dict[Bookmaker, tuple[FetchStatus, str, list]] = {}
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
                log.warning("fetch failed for %s: %s", b.value, e)
                return b, (FetchStatus.HTTP_ERROR, f"{type(e).__name__}: {e}", [])

        coros = [run(b, resolved.get(b) if b != Bookmaker.BETPAWA else None)
                 for b in Bookmaker]
        for b, payload in await asyncio.gather(*coros):
            results[b] = payload

        # Materialize 24 rows
        rows: list[Snapshot] = []
        for b in Bookmaker:
            status_fetch, error, markets = results[b]
            for market in (Market.ONE_UP, Market.TWO_UP):
                outcomes = _outcomes_for(market, markets) if status_fetch == FetchStatus.OK else None
                for outcome in (Outcome.HOME, Outcome.DRAW, Outcome.AWAY):
                    odds: Optional[float] = None
                    prob: Optional[float] = None
                    row_status = status_fetch
                    row_error = error
                    if status_fetch == FetchStatus.OK:
                        if outcomes is None:
                            row_status = FetchStatus.NOT_OFFERED
                            row_error = f"{market.value} not in response"
                        else:
                            o = outcomes.get(outcome.value)
                            if o is None:
                                row_status = FetchStatus.SUSPENDED
                                row_error = "no price for outcome"
                            else:
                                odds = o[0]
                                if b in _PROB_BOOKMAKERS:
                                    prob = o[1]
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
                        market=market,
                        outcome=outcome,
                        odds=odds,
                        probability=prob,
                        fetch_status=row_status,
                        fetch_error=row_error,
                    ))
        return rows


def _outcomes_for(market: Market, markets: list) -> Optional[dict[str, tuple[float, Optional[float]]]]:
    for m in markets:
        if m.canonical_id == market.value:
            out: dict[str, tuple[float, Optional[float]]] = {}
            for o in m.outcomes:
                prob = getattr(o, "probability", None)
                if o.odds is None:
                    continue
                out[o.canonical_name] = (float(o.odds),
                                          float(prob) if prob is not None else None)
            return out
    return None
```

- [ ] **Step 4: Run tests, verify pass**

```bash
pytest tests/test_collector.py -v
```
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/odds_scraper/collector.py tests/test_collector.py
git commit -m "feat(collector): stateless 24-row fan-out with explicit failure modes"
```

---

## Task 7: `watcher.py` — per-event lifecycle and cadence

**Files:**
- Create: `src/odds_scraper/watcher.py`
- Create: `tests/test_watcher.py`

- [ ] **Step 1: Write the failing test**

`tests/test_watcher.py`:
```python
import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from odds_scraper.models import (
    Bookmaker, EventStatus, FetchStatus, Market, Outcome, Snapshot,
)
from odds_scraper.watcher import EventWatcher, WatcherConfig


def _snap_list(status=EventStatus.UPCOMING):
    base = Snapshot(
        ts_utc=datetime.now(timezone.utc),
        event_bp_id="33660318",
        sr_id="", genius_id="",
        home="A", away="B",
        kickoff_utc=datetime.now(timezone.utc) + timedelta(minutes=10),
        status=status,
        match_minute=None, score_home=None, score_away=None,
        bookmaker=Bookmaker.BETPAWA,
        market=Market.ONE_UP, outcome=Outcome.HOME,
        odds=1.5, probability=0.6,
        fetch_status=FetchStatus.OK, fetch_error="",
    )
    return [base] * 24


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
    bp_client.get_event_detail.return_value = {
        "id": "33660318", "home": {"name": "A"}, "away": {"name": "B"},
        "startTime": "2026-05-19T15:00:00Z", "status": "ENDED",
        "score": "2-1",
    }
    collector = AsyncMock()
    collector.collect.return_value = _snap_list(EventStatus.ENDED)
    writer = MagicMock()
    writer.append = AsyncMock()
    resolver = AsyncMock(return_value=(
        {Bookmaker.SPORTYBET: "sr:match:1",
         Bookmaker.BET9JA: "b9j-7",
         Bookmaker.BETWAY: "sr:match:1"},
        "sr:match:1", ""))

    watcher = EventWatcher(
        event_bp_id="33660318",
        cfg=cfg,
        bp_client=bp_client,
        collector=collector,
        writer=writer,
        resolver=resolver,
    )
    await watcher.run()
    # Should have written the final tick once and exited.
    assert writer.append.call_count == 1


async def test_cadence_switch_at_kickoff(cfg, monkeypatch):
    """Simulate UPCOMING then STARTED. Assert that the second tick used
    live_seconds for the sleep."""
    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr("odds_scraper.watcher.asyncio.sleep", fake_sleep)

    statuses = iter(["UPCOMING", "STARTED", "ENDED"])
    bp_client = AsyncMock()
    async def get_detail(_):
        s = next(statuses)
        return {
            "id": "33660318", "home": {"name": "A"}, "away": {"name": "B"},
            "startTime": "2026-05-19T15:00:00Z", "status": s,
            "score": "1-0" if s != "UPCOMING" else None,
        }
    bp_client.get_event_detail.side_effect = get_detail
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
    # First sleep after UPCOMING tick = prematch_seconds
    # Second sleep after STARTED tick = live_seconds
    assert sleeps[0] == 600
    assert sleeps[1] == 90


async def test_status_poll_retries_then_emits_sentinel(cfg, monkeypatch):
    monkeypatch.setattr("odds_scraper.watcher.asyncio.sleep",
                        AsyncMock(return_value=None))

    call_count = {"n": 0}
    async def flaky(_):
        call_count["n"] += 1
        if call_count["n"] <= 3:
            raise RuntimeError("net down")
        return {"id": "33660318", "home": {"name": "A"}, "away": {"name": "B"},
                "startTime": "2026-05-19T15:00:00Z", "status": "ENDED"}

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

    # 3 retries + sentinel + recovered ENDED tick = at least 2 writer calls
    # (sentinel + final). Don't over-assert call count; just verify both happened.
    written_statuses = []
    for call in writer.append.call_args_list:
        for snap in call.args[0]:
            written_statuses.append(snap.fetch_status)
    assert FetchStatus.HTTP_ERROR in written_statuses
```

- [ ] **Step 2: Run, expect fail**

```bash
pytest tests/test_watcher.py -v
```
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `watcher.py`**

```python
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from .collector import OddsCollector
from .models import (
    Bookmaker, EventStatus, FetchStatus, Market, Outcome, Snapshot,
)
from .status import parse_status

log = logging.getLogger(__name__)

# (resolved_ids, sr_id, genius_id)
Resolver = Callable[[dict[str, Any]], Awaitable[tuple[dict[Bookmaker, str | None], str, str]]]


@dataclass(frozen=True)
class WatcherConfig:
    prematch_seconds: int
    live_seconds: int
    status_retry_backoff_seconds: tuple[int, ...]
    watchdog_after_kickoff_seconds: int


class EventWatcher:
    def __init__(
        self,
        event_bp_id: str,
        cfg: WatcherConfig,
        bp_client,
        collector: OddsCollector,
        writer,
        resolver: Resolver,
    ):
        self.event_bp_id = event_bp_id
        self.cfg = cfg
        self._bp = bp_client
        self._collector = collector
        self._writer = writer
        self._resolver = resolver
        self._last_status: EventStatus = EventStatus.UNKNOWN

    async def run(self) -> None:
        start = datetime.now(timezone.utc)
        while True:
            detail = await self._poll_status_with_retries()
            if detail is None:
                # Persistent failure: emit sentinel, keep going.
                await self._writer.append(self._sentinel_rows("status poll failed"))
                await asyncio.sleep(self._cadence(self._last_status))
                continue

            status = parse_status(detail)
            if status != self._last_status:
                log.info("event %s status %s -> %s",
                         self.event_bp_id, self._last_status.value, status.value)
                self._last_status = status

            try:
                resolved, sr_id, genius_id = await self._resolver(detail)
                rows = await self._collector.collect(detail, resolved, sr_id, genius_id)
                await self._writer.append(rows)
            except Exception:  # noqa: BLE001
                log.exception("collector/writer crash for %s", self.event_bp_id)
                await self._writer.append(self._sentinel_rows("collector crashed"))

            if status == EventStatus.ENDED:
                log.info("event %s ENDED — watcher exiting", self.event_bp_id)
                return

            # Watchdog
            elapsed = (datetime.now(timezone.utc) - start).total_seconds()
            if elapsed > self.cfg.watchdog_after_kickoff_seconds:
                log.warning("event %s watchdog tripped after %.0fs — exiting",
                            self.event_bp_id, elapsed)
                return

            await asyncio.sleep(self._cadence(status))

    def _cadence(self, status: EventStatus) -> int:
        if status == EventStatus.STARTED:
            return self.cfg.live_seconds
        return self.cfg.prematch_seconds

    async def _poll_status_with_retries(self) -> dict[str, Any] | None:
        backoffs = self.cfg.status_retry_backoff_seconds
        for attempt, delay in enumerate([0, *backoffs]):
            if delay:
                await asyncio.sleep(delay)
            try:
                return await self._bp.get_event_detail(self.event_bp_id)
            except Exception as e:  # noqa: BLE001
                log.warning("status poll failed (attempt %d): %s", attempt + 1, e)
        return None

    def _sentinel_rows(self, reason: str) -> list[Snapshot]:
        ts = datetime.now(timezone.utc)
        rows: list[Snapshot] = []
        for b in Bookmaker:
            for market in (Market.ONE_UP, Market.TWO_UP):
                for outcome in (Outcome.HOME, Outcome.DRAW, Outcome.AWAY):
                    rows.append(Snapshot(
                        ts_utc=ts,
                        event_bp_id=self.event_bp_id,
                        sr_id="", genius_id="",
                        home="", away="",
                        kickoff_utc=ts,
                        status=self._last_status,
                        match_minute=None, score_home=None, score_away=None,
                        bookmaker=b, market=market, outcome=outcome,
                        odds=None, probability=None,
                        fetch_status=FetchStatus.HTTP_ERROR,
                        fetch_error=reason,
                    ))
        return rows
```

- [ ] **Step 4: Run tests, verify pass**

```bash
pytest tests/test_watcher.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/odds_scraper/watcher.py tests/test_watcher.py
git commit -m "feat(watcher): per-event lifecycle with cadence, retries, watchdog"
```

---

## Task 8: `config.py` — YAML config loader

**Files:**
- Create: `src/odds_scraper/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Failing test**

`tests/test_config.py`:
```python
from pathlib import Path
import textwrap

from odds_scraper.config import load_config, AppConfig


def test_load_minimal_config(tmp_path: Path):
    p = tmp_path / "c.yaml"
    p.write_text(textwrap.dedent("""
        country: ng
        events: [11111, 22222]
        cadence:
          prematch_seconds: 600
          live_seconds: 90
          status_retry_backoff_seconds: [5, 15, 45]
          watchdog_after_kickoff_seconds: 10800
        output:
          csv_path: data/x.csv
          resolution_cache_path: data/r.json
        log_level: INFO
    """))
    cfg = load_config(p)
    assert isinstance(cfg, AppConfig)
    assert cfg.country == "ng"
    assert cfg.events == ["11111", "22222"]
    assert cfg.cadence.live_seconds == 90
    assert cfg.output.csv_path.endswith("x.csv")


def test_env_var_overrides(tmp_path: Path, monkeypatch):
    p = tmp_path / "c.yaml"
    p.write_text("country: ng\nevents: [1]\ncadence: {prematch_seconds: 600, "
                 "live_seconds: 90, status_retry_backoff_seconds: [5,15,45], "
                 "watchdog_after_kickoff_seconds: 10800}\n"
                 "output: {csv_path: a.csv, resolution_cache_path: b.json}\n"
                 "log_level: INFO\n")
    monkeypatch.setenv("ODDS_SCRAPER_LOG_LEVEL", "DEBUG")
    cfg = load_config(p)
    assert cfg.log_level == "DEBUG"
```

- [ ] **Step 2: Run, expect fail**

```bash
pytest tests/test_config.py -v
```

- [ ] **Step 3: Implement `config.py`**

```python
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import yaml


@dataclass(frozen=True)
class CadenceConfig:
    prematch_seconds: int
    live_seconds: int
    status_retry_backoff_seconds: tuple[int, ...]
    watchdog_after_kickoff_seconds: int


@dataclass(frozen=True)
class OutputConfig:
    csv_path: str
    resolution_cache_path: str


@dataclass(frozen=True)
class AppConfig:
    country: str
    events: Sequence[str]
    cadence: CadenceConfig
    output: OutputConfig
    log_level: str


def load_config(path: Path | str) -> AppConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    cad = raw["cadence"]
    out = raw["output"]
    return AppConfig(
        country=str(raw["country"]),
        events=[str(e) for e in raw["events"]],
        cadence=CadenceConfig(
            prematch_seconds=int(cad["prematch_seconds"]),
            live_seconds=int(cad["live_seconds"]),
            status_retry_backoff_seconds=tuple(int(x) for x in cad["status_retry_backoff_seconds"]),
            watchdog_after_kickoff_seconds=int(cad["watchdog_after_kickoff_seconds"]),
        ),
        output=OutputConfig(
            csv_path=str(out["csv_path"]),
            resolution_cache_path=str(out["resolution_cache_path"]),
        ),
        log_level=os.environ.get("ODDS_SCRAPER_LOG_LEVEL", str(raw.get("log_level", "INFO"))),
    )
```

- [ ] **Step 4: Run, verify pass**

```bash
pytest tests/test_config.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/odds_scraper/config.py tests/test_config.py
git commit -m "feat(config): yaml loader with env-var overrides"
```

---

## Task 9: `main.py` — entrypoint, supervisor, signal handling

**Files:**
- Create: `src/odds_scraper/main.py`
- Create: `src/odds_scraper/resolution_runtime.py` (wires resolver against real bookieskit clients)
- Create: `tests/test_main_supervisor.py`

> **Note:** `resolution_runtime.py` is the only part of the codebase that touches real bookmaker APIs synchronously through bookieskit. It's a thin adapter — no tests against real network; tests use mocked clients.

- [ ] **Step 1: Failing test for the supervisor**

`tests/test_main_supervisor.py`:
```python
import asyncio
from unittest.mock import AsyncMock

import pytest

from odds_scraper.main import supervise_watcher


async def test_supervisor_restarts_crashed_watcher(monkeypatch):
    calls = {"n": 0}

    async def fake_run():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("boom")
        # Third call returns normally

    watcher = AsyncMock()
    watcher.run.side_effect = fake_run

    # Patch sleep to zero so the test is instant.
    monkeypatch.setattr("odds_scraper.main.asyncio.sleep", AsyncMock())

    await supervise_watcher(watcher, event_id="x", max_backoff_seconds=1)
    assert calls["n"] == 3
```

- [ ] **Step 2: Run, expect fail**

```bash
pytest tests/test_main_supervisor.py -v
```

- [ ] **Step 3: Implement `main.py`**

```python
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from .collector import OddsCollector
from .config import AppConfig, load_config
from .models import Bookmaker
from .registry import build_registry
from .resolution import ResolutionCache, ResolutionKey, match_provider_ids
from .resolution_runtime import (
    make_bookmaker_clients, make_fetchers, resolve_event,
)
from .watcher import EventWatcher, WatcherConfig
from .writer import CsvWriter

log = logging.getLogger(__name__)


async def supervise_watcher(watcher, event_id: str, max_backoff_seconds: int = 300) -> None:
    backoff = 30
    while True:
        try:
            await watcher.run()
            return
        except Exception:  # noqa: BLE001
            log.exception("watcher crashed for %s — restarting in %ds", event_id, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff_seconds)


async def _amain(config_path: Path) -> int:
    cfg = load_config(config_path)
    logging.basicConfig(
        level=getattr(logging, cfg.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    cache = ResolutionCache(Path(cfg.output.resolution_cache_path))
    cache.load()
    registry = build_registry()

    async with AsyncExitStack() as stack:
        clients = await make_bookmaker_clients(stack, country=cfg.country)
        fetchers = make_fetchers(clients, registry=registry)
        collector = OddsCollector(fetchers=fetchers)
        writer = await stack.enter_async_context(CsvWriter(Path(cfg.output.csv_path)))

        async def resolver(detail: dict[str, Any]):
            return await resolve_event(detail, clients=clients, cache=cache,
                                       match=match_provider_ids,
                                       key_factory=ResolutionKey)

        watcher_cfg = WatcherConfig(
            prematch_seconds=cfg.cadence.prematch_seconds,
            live_seconds=cfg.cadence.live_seconds,
            status_retry_backoff_seconds=cfg.cadence.status_retry_backoff_seconds,
            watchdog_after_kickoff_seconds=cfg.cadence.watchdog_after_kickoff_seconds,
        )
        watchers = [
            EventWatcher(event_bp_id=ev, cfg=watcher_cfg,
                         bp_client=clients[Bookmaker.BETPAWA],
                         collector=collector, writer=writer, resolver=resolver)
            for ev in cfg.events
        ]
        tasks = [
            asyncio.create_task(supervise_watcher(w, ev), name=f"watcher-{ev}")
            for w, ev in zip(watchers, cfg.events)
        ]

        loop = asyncio.get_running_loop()
        stop_event = asyncio.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop_event.set)
            except NotImplementedError:
                # Windows: signal handlers via add_signal_handler unsupported
                pass

        await asyncio.wait(
            [asyncio.create_task(stop_event.wait()), *tasks],
            return_when=asyncio.FIRST_COMPLETED,
        )

        log.info("shutting down, cancelling %d watcher tasks", len(tasks))
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    return 0


def cli() -> None:
    parser = argparse.ArgumentParser(description="1up/2up odds scraper")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    args = parser.parse_args()
    try:
        sys.exit(asyncio.run(_amain(args.config)))
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    cli()
```

- [ ] **Step 4: Implement `resolution_runtime.py`**

This is the bookieskit-binding layer. It wires real bookmaker clients into the abstractions the rest of the code uses. The exact bookieskit API calls here must be cross-checked against installed bookieskit — see the inline `# TODO` markers below for the items requiring verification.

```python
"""Wiring between bookieskit's real clients and our abstractions.

This is the only module that touches real bookmaker APIs through bookieskit.
"""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from typing import Any, Callable

from bookieskit import BetPawa, SportyBet, Bet9ja, Betway
from bookieskit.markets import parse_markets
from bookieskit.matching import extract_event_ids

from .models import Bookmaker, EventStatus
from .resolution import ResolutionCache, ResolutionKey
from .status import parse_status

log = logging.getLogger(__name__)


async def make_bookmaker_clients(stack: AsyncExitStack, country: str) -> dict[Bookmaker, Any]:
    return {
        Bookmaker.BETPAWA: await stack.enter_async_context(BetPawa(country=country)),
        Bookmaker.SPORTYBET: await stack.enter_async_context(SportyBet(country=country)),
        Bookmaker.BET9JA: await stack.enter_async_context(Bet9ja(country=country)),
        Bookmaker.BETWAY: await stack.enter_async_context(Betway(country=country)),
    }


def make_fetchers(clients: dict[Bookmaker, Any], registry) -> dict[Bookmaker, Callable]:
    """Return per-bookmaker async fetcher callables that return parsed markets."""

    async def fetch_betpawa(bp_detail: dict) -> list:
        # BP detail already in hand from the watcher's status poll.
        return parse_markets(bp_detail, platform="betpawa", registry=registry)

    async def fetch_sportybet(sr_id: str) -> list:
        live = _is_live_target(sr_id)
        sb = clients[Bookmaker.SPORTYBET]
        detail = await sb.get_event_detail(event_id=sr_id, live=live)
        return parse_markets(detail, platform="sportybet", registry=registry)

    async def fetch_bet9ja(b9j_id: str) -> list:
        b9j = clients[Bookmaker.BET9JA]
        detail = await b9j.get_event_markets(b9j_id)
        return parse_markets(detail, platform="bet9ja", registry=registry)

    async def fetch_betway(sr_id: str) -> list:
        bw = clients[Bookmaker.BETWAY]
        detail = await bw.get_event_markets(sr_id)
        return parse_markets(detail, platform="betway", registry=registry)

    return {
        Bookmaker.BETPAWA: fetch_betpawa,
        Bookmaker.SPORTYBET: fetch_sportybet,
        Bookmaker.BET9JA: fetch_bet9ja,
        Bookmaker.BETWAY: fetch_betway,
    }


def _is_live_target(_) -> bool:
    # Detection happens upstream via BP status; resolver passes the right id.
    # This shim exists so future per-fetcher logic has a home.
    return False


async def resolve_event(
    bp_detail: dict[str, Any],
    *,
    clients: dict[Bookmaker, Any],
    cache: ResolutionCache,
    match: Callable,
    key_factory: type,
) -> tuple[dict[Bookmaker, str | None], str, str]:
    status = parse_status(bp_detail)
    regime = "live" if status == EventStatus.STARTED else "prematch"
    event_id = str(bp_detail.get("id", ""))
    key = key_factory(event_id, regime)
    cached = cache.get(key)
    if cached is not None:
        return _from_cached(cached)

    bp_ids = extract_event_ids(bp_detail, platform="betpawa")
    sr_id = bp_ids.sportradar or ""
    genius_id = bp_ids.genius or ""

    sb_id, b9j_id, bw_id = await _resolve_others(
        clients, sr_id=sr_id, genius_id=genius_id, regime=regime,
    )

    entry = {"sr_id": sr_id, "genius_id": genius_id,
             "sb_id": sb_id, "b9j_id": b9j_id, "bw_id": bw_id}
    cache.set(key, entry)
    return _from_cached(entry)


async def _resolve_others(clients, *, sr_id: str, genius_id: str, regime: str):
    # SportyBet & Betway accept SR id directly.
    sb_id = f"sr:match:{sr_id}" if sr_id and not sr_id.startswith("sr:match:") else (sr_id or None)
    bw_id = sr_id or None

    # Bet9ja: prematch via SR-id reverse map, live via BetGenius id.
    b9j_id: str | None = None
    if regime == "live" and genius_id:
        b9j_id = genius_id
    elif regime == "prematch" and sr_id:
        try:
            mapping = await clients[Bookmaker.BET9JA].build_prematch_event_map(sport_id="1")
            b9j_id = mapping.get(sr_id) or mapping.get(_strip_sr_prefix(sr_id))
        except Exception as e:  # noqa: BLE001
            log.warning("bet9ja prematch map failed: %s", e)
    return sb_id, b9j_id, bw_id


def _strip_sr_prefix(raw: str) -> str:
    return raw.split(":")[-1] if raw.startswith("sr:match:") else raw


def _from_cached(entry: dict) -> tuple[dict[Bookmaker, str | None], str, str]:
    return (
        {
            Bookmaker.SPORTYBET: entry.get("sb_id") or None,
            Bookmaker.BET9JA: entry.get("b9j_id") or None,
            Bookmaker.BETWAY: entry.get("bw_id") or None,
        },
        entry.get("sr_id", "") or "",
        entry.get("genius_id", "") or "",
    )
```

> **bookieskit API verification at implementation time:**
> - Confirm import paths: `from bookieskit import BetPawa, SportyBet, Bet9ja, Betway` may live under `bookieskit.bookmakers`.
> - Confirm `extract_event_ids` and its `EventIds` shape (`sportradar`, `genius`).
> - Confirm Bet9ja `build_prematch_event_map(sport_id="1")` signature.
> - Confirm SportyBet `get_event_detail(event_id=..., live=...)` signature.
>
> If any of these differs, adjust at this layer only — the rest of the codebase depends only on the adapter contract.

- [ ] **Step 5: Run supervisor test, verify pass**

```bash
pytest tests/test_main_supervisor.py -v
```

- [ ] **Step 6: Commit**

```bash
git add src/odds_scraper/main.py src/odds_scraper/resolution_runtime.py tests/test_main_supervisor.py
git commit -m "feat(main): entrypoint, supervisor and bookieskit runtime adapter"
```

---

## Task 10: Fixture capture script & README

**Files:**
- Create: `scripts/capture_fixtures.py`
- Create: `README.md`

- [ ] **Step 1: Implement `scripts/capture_fixtures.py`**

```python
"""Capture raw JSON responses from one or more bookmakers for a given event.

Usage:
    python scripts/capture_fixtures.py 33660318 --bookmaker betpawa \
        --country ng --out tests/fixtures/betpawa_event_real.json

The output is the raw bookieskit response, written verbatim. Use this to
update test fixtures when API shapes drift.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from contextlib import AsyncExitStack
from pathlib import Path

from bookieskit import BetPawa, SportyBet, Bet9ja, Betway


_CLIENTS = {
    "betpawa": BetPawa,
    "sportybet": SportyBet,
    "bet9ja": Bet9ja,
    "betway": Betway,
}


async def main_async(args) -> int:
    cls = _CLIENTS[args.bookmaker]
    async with AsyncExitStack() as stack:
        client = await stack.enter_async_context(cls(country=args.country))
        if args.bookmaker == "betpawa":
            data = await client.get_event_detail(event_id=args.event_id)
        elif args.bookmaker == "sportybet":
            data = await client.get_event_detail(event_id=args.event_id, live=args.live)
        elif args.bookmaker in ("bet9ja", "betway"):
            data = await client.get_event_markets(args.event_id)
        else:
            raise ValueError(args.bookmaker)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"wrote {out}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("event_id")
    p.add_argument("--bookmaker", required=True, choices=list(_CLIENTS))
    p.add_argument("--country", default="ng")
    p.add_argument("--live", action="store_true")
    p.add_argument("--out", required=True)
    args = p.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Write `README.md`**

````markdown
# odds-scraper

1up / 2up odds scraper for four BetPawa-anchored NG soccer events across
BetPawa, SportyBet, Bet9ja and Betway. Captures bookmaker-exposed probability
where available (BetPawa, SportyBet) and match score/clock once live.

## Install

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

## Run

```powershell
python -m odds_scraper.main --config config.yaml
```

Edit `config.yaml` to change the event list or cadence.

Output: `data/odds_snapshots.csv` (append-only, header on first run).
ID cache: `data/resolution_cache.json` (persisted incrementally — safe across restarts).

## CSV schema

See `docs/superpowers/specs/2026-05-19-odds-scraper-design.md`.

## Tests

```powershell
pytest -v
```

Tests run offline against fixtures in `tests/fixtures/`.

## Refreshing fixtures from real APIs

```powershell
python scripts/capture_fixtures.py 33660318 --bookmaker betpawa --out tests/fixtures/betpawa_event_real.json
python scripts/capture_fixtures.py sr:match:<id> --bookmaker sportybet --out tests/fixtures/sportybet_event.json
```

Run after BetPawa API shape changes or when porting to new events.

## Stop

Ctrl-C. The scraper flushes the CSV and closes bookmaker clients cleanly.

## Known limitations

- BetPawa 1up/2up `marketId` is hardcoded in `src/odds_scraper/registry.py` — update if BetPawa renames the market.
- Bet9ja live BetGenius mapping is best-effort; emits `lookup_failed` rows for Bet9ja-live when no match.
- Single output CSV, no rotation. At ~20 K rows/match-day it stays small for months.
````

- [ ] **Step 3: Commit**

```bash
git add scripts/capture_fixtures.py README.md
git commit -m "feat: fixture capture script and readme"
```

---

## Final verification

- [ ] **Run full test suite**

```bash
pytest -v
```
Expected: all tests pass. Roughly 25–30 tests in total.

- [ ] **Smoke run (manual, network required)**

```bash
python -m odds_scraper.main --config config.yaml
```
Let it run for ~5 minutes. Confirm:
- `data/odds_snapshots.csv` exists with header
- Rows appear approximately every 600 seconds for `UPCOMING` events
- Each tick produces 24 rows per event (verify with `awk -F, '{print $2}' data/odds_snapshots.csv | sort | uniq -c`)
- `data/resolution_cache.json` has entries after the first cycle

Stop with Ctrl-C and verify clean exit.

- [ ] **Tag**

```bash
git tag v0.1.0
```
