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

Resolver = Callable[
    [dict[str, Any]],
    Awaitable[tuple[dict[Bookmaker, str | None], str, str]],
]


@dataclass(frozen=True)
class WatcherConfig:
    prematch_seconds: int
    live_seconds: int
    status_retry_backoff_seconds: tuple[int, ...]
    watchdog_after_kickoff_seconds: int


class EventWatcher:
    """Owns one event's lifecycle: status poll, cadence, fan-out, watchdog."""

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
                await self._writer.append(self._sentinel_rows("status poll failed"))
                await asyncio.sleep(self._cadence(self._last_status))
                continue

            status = parse_status(detail)
            if status != self._last_status:
                log.info(
                    "event %s status %s -> %s",
                    self.event_bp_id, self._last_status.value, status.value,
                )
                self._last_status = status

            try:
                resolved, sr_id, genius_id = await self._resolver(detail)
                rows = await self._collector.collect(detail, resolved, sr_id, genius_id)
                await self._writer.append(rows)
                self._log_tick_summary(rows)
            except Exception:  # noqa: BLE001
                log.exception("collector/writer crash for %s", self.event_bp_id)
                await self._writer.append(self._sentinel_rows("collector crashed"))

            if status == EventStatus.ENDED:
                log.info("event %s ENDED — watcher exiting", self.event_bp_id)
                return

            elapsed = (datetime.now(timezone.utc) - start).total_seconds()
            if elapsed > self.cfg.watchdog_after_kickoff_seconds:
                log.warning(
                    "event %s watchdog tripped after %.0fs — exiting",
                    self.event_bp_id, elapsed,
                )
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
                log.warning(
                    "status poll for %s failed (attempt %d): %s",
                    self.event_bp_id, attempt + 1, e,
                )
        return None

    def _log_tick_summary(self, rows: list[Snapshot]) -> None:
        # rows are exactly 24 = 4 bookmakers x 2 markets x 3 outcomes.
        # Summarize as one OK count per bookmaker (max 6).
        counts: dict[str, int] = {b.value: 0 for b in Bookmaker}
        for r in rows:
            if r.fetch_status == FetchStatus.OK:
                counts[r.bookmaker.value] += 1
        log.info(
            "tick %s status=%s bp=%d/6 sb=%d/6 b9j=%d/6 bw=%d/6",
            self.event_bp_id, self._last_status.value,
            counts["betpawa"], counts["sportybet"],
            counts["bet9ja"], counts["betway"],
        )

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
