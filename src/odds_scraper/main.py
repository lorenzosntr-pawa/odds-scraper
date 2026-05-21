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
        watchers = [
            EventWatcher(
                event_bp_id=ev,
                cfg=watcher_cfg,
                bp_client=clients[Bookmaker.BETPAWA],
                collector=collector,
                writer=writer,
                resolver=resolver,
            )
            for ev in cfg.events
        ]
        tasks = [
            asyncio.create_task(supervise_watcher(w, ev), name=f"watcher-{ev}")
            for w, ev in zip(watchers, cfg.events)
        ]

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

        wait_for_stop = asyncio.create_task(stop_event.wait())
        all_watchers = asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.wait(
            [wait_for_stop, all_watchers], return_when=asyncio.FIRST_COMPLETED,
        )

        log.info("shutting down, cancelling %d watcher tasks", len(tasks))
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if not wait_for_stop.done():
            wait_for_stop.cancel()

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
