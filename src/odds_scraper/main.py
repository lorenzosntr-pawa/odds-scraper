from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from .collector import OddsCollector
from .config import load_config
from .models import Bookmaker
from .registry import build_registry
from .resolution import ResolutionCache
from .resolution_runtime import (
    make_bookmaker_clients, make_fetchers, resolve_event,
)
from .event_resolver import resolve_event_ids
from .watcher import EventWatcher, WatcherConfig
from .writer import CsvWriter

log = logging.getLogger(__name__)


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
        writer = await stack.enter_async_context(CsvWriter(Path(cfg.output.csv_path)))

        async def resolver(detail: dict[str, Any]):
            return await resolve_event(detail, clients=clients, cache=cache)

        watcher_cfg = WatcherConfig(
            prematch_seconds=cfg.cadence.prematch_seconds,
            live_seconds=cfg.cadence.live_seconds,
            status_retry_backoff_seconds=cfg.cadence.status_retry_backoff_seconds,
            watchdog_after_kickoff_seconds=cfg.cadence.watchdog_after_kickoff_seconds,
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
                    # unbounded over a long server run, and so the active
                    # count below stays honest.
                    tasks[:] = [t for t in tasks if not t.done()]
                    any_active = any(not t.done() for t in tasks)
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
                        active_count = sum(1 for t in tasks if not t.done())
                        log.info(
                            "refresh: no new events (active watchers: %d)",
                            active_count,
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
