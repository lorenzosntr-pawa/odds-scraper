from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sqlite3
import sys
from contextlib import AsyncExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .collector import OddsCollector
from .config import load_config
from .event_resolver import resolve_event_ids
from .models import Bookmaker, EventStatus, FetchStatus, Snapshot
from .registry import build_registry
from .resolution import ResolutionCache
from .resolution_runtime import (
    make_bookmaker_clients, make_fetchers, resolve_event,
)
from .watcher import EventWatcher, WatcherConfig
from .writer import SqliteWriter

log = logging.getLogger(__name__)


async def _reap_stuck_started_events(
    db_path: Path, writer: SqliteWriter, watchdog_seconds: int,
) -> int:
    """Backfill a synthetic ENDED snapshot for events whose head row is
    STARTED but kickoff was more than `watchdog_seconds` ago.

    Catches two failure modes the watchdog alone can't fix:
      - Events whose watcher exited in a prior run (the live → ENDED
        transition was never written because the watchdog of an older
        binary just returned without writing).
      - Events that were live yesterday but are no longer in any
        tournament list today, so no new watcher spawns for them and
        they sit in the live tab forever.

    Returns the number of events reaped.
    """
    cutoff_iso = (
        datetime.now(timezone.utc) - timedelta(seconds=watchdog_seconds)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            WITH latest AS (
                SELECT event_id, MAX(ts_utc) AS max_ts
                FROM snapshots GROUP BY event_id
            )
            SELECT DISTINCT e.id,
                   s.match_minute, s.score_home, s.score_away
            FROM events e
            JOIN latest l ON l.event_id = e.id
            JOIN snapshots s
              ON s.event_id = l.event_id AND s.ts_utc = l.max_ts
            WHERE s.status = 'STARTED'
              AND e.kickoff_utc < ?
            """,
            (cutoff_iso,),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return 0

    ts = datetime.now(timezone.utc)
    for r in rows:
        synthetic = [
            Snapshot(
                ts_utc=ts,
                event_bp_id=r["id"],
                sr_id="", genius_id="",
                home="", away="",
                kickoff_utc=ts,
                status=EventStatus.ENDED,
                match_minute=r["match_minute"],
                score_home=r["score_home"],
                score_away=r["score_away"],
                bookmaker=bm,
                fetch_status=FetchStatus.OK,
                fetch_error="",
                prices={},
            )
            for bm in Bookmaker
        ]
        await writer.append(synthetic)
    return len(rows)


async def supervise_watcher(
    watcher, event_id: str, max_backoff_seconds: int = 300,
) -> None:
    backoff = 30
    while True:
        try:
            await watcher.run()
            return
        except Exception:  # noqa: BLE001
            log.exception(
                "watcher crashed for %s — restarting in %ds", event_id, backoff,
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff_seconds)


async def _amain(config_path: Path) -> int:
    cfg = load_config(config_path)
    logging.basicConfig(
        level=getattr(logging, cfg.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # bookieskit uses httpx; its per-request INFO logs drown out our own.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    cache = ResolutionCache(Path(cfg.output.resolution_cache_path))
    cache.load()
    registry = build_registry()

    async with AsyncExitStack() as stack:
        clients = await make_bookmaker_clients(stack, country=cfg.country)
        fetchers = make_fetchers(clients, registry=registry)
        collector = OddsCollector(fetchers=fetchers)
        writer = await stack.enter_async_context(SqliteWriter(Path(cfg.output.db_path)))

        # Reap stuck STARTED events from prior runs: their watchers
        # either exited cleanly (writing nothing) or were never spawned
        # again because the events fell out of every tournament list.
        # Without this they'd sit in the live tab forever.
        reaped = await _reap_stuck_started_events(
            Path(cfg.output.db_path), writer,
            cfg.cadence.watchdog_after_kickoff_seconds,
        )
        if reaped:
            log.info(
                "reaped %d stuck STARTED events (wrote synthetic ENDED snapshots)",
                reaped,
            )

        async def resolver(detail: dict[str, Any]):
            return await resolve_event(detail, clients=clients, cache=cache)

        watcher_cfg = WatcherConfig(
            prematch_seconds=cfg.cadence.prematch_seconds,
            live_seconds=cfg.cadence.live_seconds,
            status_retry_backoff_seconds=cfg.cadence.status_retry_backoff_seconds,
            watchdog_after_kickoff_seconds=cfg.cadence.watchdog_after_kickoff_seconds,
            live_lead_seconds=cfg.cadence.live_lead_seconds,
        )

        bp_client = clients[Bookmaker.BETPAWA]
        initial_ids = await resolve_event_ids(
            standalone_events=cfg.events,
            tournaments=cfg.tournaments,
            bp_client=bp_client,
        )
        log.info(
            "initial event set: %d (from %d standalone + %d tournaments)",
            len(initial_ids), len(cfg.events), len(cfg.tournaments),
        )

        watched_ids: set[str] = set()
        tasks: list[asyncio.Task] = []

        def _spawn_watcher(ev_id: str) -> None:
            if ev_id in watched_ids:
                return
            watched_ids.add(ev_id)
            w = EventWatcher(
                event_bp_id=ev_id, cfg=watcher_cfg,
                bp_client=bp_client, collector=collector,
                writer=writer, resolver=resolver,
            )
            tasks.append(asyncio.create_task(
                supervise_watcher(w, ev_id), name=f"watcher-{ev_id}",
            ))

        for ev_id in initial_ids:
            _spawn_watcher(ev_id)

        async def _refresh_loop():
            while True:
                try:
                    # Prune completed watcher tasks so `tasks` doesn't grow
                    # unbounded over a long server run. ALSO discard the
                    # event_id from `watched_ids` for any pruned watcher,
                    # so the next resolve_event_ids pass can re-spawn it
                    # if the event is still listed. Without this discard,
                    # a watchdog-tripped or otherwise-dead watcher would
                    # leave its id stuck in watched_ids forever and the
                    # process would idle with no active watchers.
                    done_tasks = [t for t in tasks if t.done()]
                    for t in done_tasks:
                        name = t.get_name()
                        if name.startswith("watcher-"):
                            watched_ids.discard(name[len("watcher-"):])
                    tasks[:] = [t for t in tasks if not t.done()]
                    any_active = bool(tasks)
                    sleep_sec = (
                        cfg.refresh_interval_seconds if any_active
                        else cfg.refresh_interval_when_idle_seconds
                    )
                    await asyncio.sleep(sleep_sec)
                    current = await resolve_event_ids(
                        standalone_events=cfg.events,
                        tournaments=cfg.tournaments,
                        bp_client=bp_client,
                    )
                    new_ids = [i for i in current if i not in watched_ids]
                    if new_ids:
                        log.info("refresh: spawning %d new watchers", len(new_ids))
                        for ev_id in new_ids:
                            _spawn_watcher(ev_id)
                    else:
                        log.info(
                            "refresh: no new events (active watchers: %d)",
                            len(tasks),
                        )
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001
                    log.exception("refresh loop iteration failed — continuing")

        refresh_task = asyncio.create_task(_refresh_loop(), name="refresh-loop")

        stop_event = asyncio.Event()

        def _trip_stop():
            stop_event.set()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _trip_stop)
            except NotImplementedError:
                # Windows event loops don't support add_signal_handler;
                # Ctrl-C is delivered via KeyboardInterrupt in cli() instead.
                pass

        # Process only exits on stop signal. Watchers come and go;
        # the refresh loop keeps polling until cancelled.
        await stop_event.wait()

        active = [t for t in tasks if not t.done()]
        log.info(
            "shutting down, cancelling refresh + %d live watcher tasks",
            len(active),
        )
        refresh_task.cancel()
        for t in active:
            t.cancel()
        await asyncio.gather(refresh_task, *active, return_exceptions=True)

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
