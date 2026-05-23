from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from bookieskit import extract_kickoff

from .collector import OddsCollector
from .models import (
    MARKET_MANIFEST, PROB_BOOKMAKERS, Bookmaker, EventStatus, FetchStatus,
    Snapshot,
)
from .status import parse_status


def _price_cell_count(want_prob: bool) -> int:
    """Total price cells per bookmaker, derived from MARKET_MANIFEST.

    BP/SB emit odds AND prob per outcome (2 cells each).
    B9J/BW emit odds only (1 cell each). Used as denominators in tick logs.
    """
    cells = 0
    for spec in MARKET_MANIFEST:
        outcomes = len(spec.lines or (None,)) * len(spec.sides)
        cells += outcomes * (2 if want_prob else 1)
    return cells

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
    # How many seconds BEFORE kickoff to start sampling at live cadence.
    # The previous status-driven cadence waited for BetPawa to flip its
    # `live=true` flag, which often lagged the actual kickoff by 5-15
    # minutes — the first half of every live match would arrive with
    # huge gaps. Kickoff-driven cadence guarantees we're already
    # polling every `live_seconds` when the whistle blows.
    live_lead_seconds: int = 300


def _utcnow() -> datetime:
    """Indirection so tests can patch the watcher's clock."""
    return datetime.now(timezone.utc)


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
        # Last observed live state, carried forward into the synthetic
        # ENDED snapshot the watchdog writes on exit. Without these the
        # ENDED tab card would lose the final score / minute.
        self._last_minute: int | None = None
        self._last_score: tuple[int, int] | None = None

    async def run(self) -> None:
        # Kickoff is learned from the first successful detail tick.
        # The watchdog is "N seconds AFTER kickoff" per its config-key name,
        # so until we know kickoff we cannot trip it. For events whose
        # kickoff is in the future (prematch), the elapsed-since-kickoff
        # is negative and the watchdog cannot trip until the event has
        # been live for `watchdog_after_kickoff_seconds`.
        kickoff_utc: datetime | None = None
        while True:
            detail = await self._poll_status_with_retries()
            if detail is None:
                await self._writer.append(self._sentinel_rows("status poll failed"))
                await asyncio.sleep(self._cadence(self._last_status, kickoff_utc))
                continue

            if kickoff_utc is None:
                kickoff_utc = extract_kickoff(detail, "betpawa")

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
                # Carry forward the last observed minute/score from any
                # row that has them set — the watchdog uses these to
                # populate the synthetic ENDED snapshot on exit.
                for r in rows:
                    if r.score_home is not None and r.score_away is not None:
                        self._last_score = (r.score_home, r.score_away)
                    if r.match_minute is not None:
                        self._last_minute = r.match_minute
            except Exception:  # noqa: BLE001
                log.exception("collector/writer crash for %s", self.event_bp_id)
                await self._writer.append(self._sentinel_rows("collector crashed"))

            if status == EventStatus.ENDED:
                log.info("event %s ENDED — watcher exiting", self.event_bp_id)
                return

            if kickoff_utc is not None:
                elapsed_since_kickoff = (
                    _utcnow() - kickoff_utc
                ).total_seconds()
                if elapsed_since_kickoff > self.cfg.watchdog_after_kickoff_seconds:
                    log.warning(
                        "event %s watchdog tripped %.0fs after kickoff — exiting",
                        self.event_bp_id, elapsed_since_kickoff,
                    )
                    # Write a synthetic ENDED snapshot so the event leaves
                    # the live tab. Without this the last observed snapshot
                    # (typically STARTED) stays as the head and the card
                    # sticks in the live tab forever — BetPawa doesn't
                    # always flip its status fields to "ended" cleanly,
                    # and we have no other source of truth.
                    await self._writer.append(self._ended_sentinel_rows())
                    return

            await asyncio.sleep(self._cadence(status, kickoff_utc))

    def _cadence(
        self,
        status: EventStatus,
        kickoff_utc: datetime | None,
    ) -> int:
        """Sleep interval until the next tick.

        Driven primarily by `kickoff_utc` rather than BetPawa's status
        flag — BP's prematch→live transition often lags the real
        whistle by minutes, which under the old status-driven cadence
        meant a 10-minute prematch poll could swallow the entire start
        of the match. With kickoff time as the source of truth, we
        ramp up to live cadence `live_lead_seconds` before the listed
        kickoff and stay there for the whole in-play window.

        Falls back to status-based cadence only when kickoff couldn't
        be extracted (first tick failure path).
        """
        if kickoff_utc is None:
            if status == EventStatus.STARTED:
                return self.cfg.live_seconds
            return self.cfg.prematch_seconds
        sec_to_kickoff = (kickoff_utc - _utcnow()).total_seconds()
        if sec_to_kickoff <= self.cfg.live_lead_seconds:
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
        denom = {b: _price_cell_count(b in PROB_BOOKMAKERS) for b in Bookmaker}
        counts: dict[Bookmaker, int] = {b: 0 for b in Bookmaker}
        for r in rows:
            for odds, prob in r.prices.values():
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

    def _ended_sentinel_rows(self) -> list[Snapshot]:
        """Synthetic snapshot rows declaring the event ENDED.

        Written when the watchdog trips. Carries forward the last
        observed minute and score so the ENDED tab card still shows the
        final state. fetch_status=OK because this is not a fetch failure
        — it's the watcher's authoritative call that the event is over.
        """
        ts = datetime.now(timezone.utc)
        score_home = self._last_score[0] if self._last_score else None
        score_away = self._last_score[1] if self._last_score else None
        rows: list[Snapshot] = []
        for b in Bookmaker:
            rows.append(Snapshot(
                ts_utc=ts,
                event_bp_id=self.event_bp_id,
                sr_id="", genius_id="",
                home="", away="",
                kickoff_utc=ts,
                status=EventStatus.ENDED,
                match_minute=self._last_minute,
                score_home=score_home, score_away=score_away,
                bookmaker=b,
                fetch_status=FetchStatus.OK,
                fetch_error="",
                prices={},
            ))
        return rows
