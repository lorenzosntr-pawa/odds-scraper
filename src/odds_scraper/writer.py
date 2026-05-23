from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .db_schema import init_schema
from .models import Snapshot

log = logging.getLogger(__name__)


class SqliteWriter:
    """Write Snapshots to a normalized SQLite database.

    Same async context-manager + append() interface as the prior CsvWriter,
    so main.py only needs a name + path swap.
    """

    def __init__(self, path: Path | str):
        self._path = Path(path)
        self._lock = asyncio.Lock()
        self._conn: sqlite3.Connection | None = None

    async def __aenter__(self) -> "SqliteWriter":
        self._path.parent.mkdir(parents=True, exist_ok=True)
        loop = asyncio.get_running_loop()
        self._conn = await loop.run_in_executor(None, self._open)
        return self

    def _open(self) -> sqlite3.Connection:
        # check_same_thread=False is safe here: the asyncio.Lock in append()
        # serialises all run_in_executor dispatches, so only one thread ever
        # holds the connection at a time. The lock is the synchronisation
        # primitive — sqlite3's own thread-check would refuse to let the
        # default executor thread pool re-bind the connection across calls.
        conn = sqlite3.connect(str(self._path), isolation_level=None, check_same_thread=False)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        init_schema(conn)
        return conn

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._conn is not None:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._conn.close)
            self._conn = None

    async def append(self, snapshots: Iterable[Snapshot]) -> None:
        snaps = list(snapshots)
        if not snaps:
            return
        async with self._lock:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._write_batch, snaps)

    async def append_pricer_live(
        self,
        event_id: str,
        ts_utc: str,
        rows: list[Snapshot],
        score: tuple[int, int] = (0, 0),
    ) -> bool:
        """Compute + persist the engine's OUR output for one tick.

        Called after `append(rows)` for the same tick so the historical
        record carries OUR alongside the scraped odds. Returns True if
        a row was written, False if the engine couldn't produce a
        result (missing inputs, etc).
        """
        if not rows:
            return False
        async with self._lock:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None, self._write_pricer_live, event_id, ts_utc, rows, score,
            )

    def _write_pricer_live(
        self, event_id: str, ts_utc: str,
        rows: list[Snapshot], score: tuple[int, int],
    ) -> bool:
        # Local import keeps writer.py free of the pricer package at
        # module load time — avoids any chance of circular import via
        # models/snapshot during startup.
        from .pricer import live_writer as lw

        conn = self._conn
        assert conn is not None
        try:
            conn.execute("BEGIN")
            ok = lw.compute_and_write(conn, event_id, ts_utc, rows, score)
            conn.execute("COMMIT")
            return ok
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def _write_batch(self, snaps: list[Snapshot]) -> None:
        conn = self._conn
        assert conn is not None
        try:
            conn.execute("BEGIN")
            for s in snaps:
                # Upsert events. ON CONFLICT DO UPDATE patches placeholder
                # rows (empty home/away from a sentinel snapshot that
                # happened to land first) on the next good tick. The
                # UPDATE only fires when the existing values are empty,
                # so good data is never overwritten by later sentinels.
                conn.execute(
                    "INSERT INTO events "
                    "(id, sr_id, genius_id, home, away, kickoff_utc, "
                    " country_id, country_name, league_id, league_name) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET "
                    "  sr_id = COALESCE(NULLIF(events.sr_id, ''), excluded.sr_id), "
                    "  genius_id = COALESCE(NULLIF(events.genius_id, ''), excluded.genius_id), "
                    "  home = CASE WHEN events.home = '' THEN excluded.home ELSE events.home END, "
                    "  away = CASE WHEN events.away = '' THEN excluded.away ELSE events.away END, "
                    "  country_id = COALESCE(NULLIF(events.country_id, ''), excluded.country_id), "
                    "  country_name = CASE "
                    "      WHEN events.country_name IS NULL OR events.country_name = '' "
                    "      THEN excluded.country_name ELSE events.country_name END, "
                    "  league_id = COALESCE(NULLIF(events.league_id, ''), excluded.league_id), "
                    "  league_name = CASE "
                    "      WHEN events.league_name IS NULL OR events.league_name = '' "
                    "      THEN excluded.league_name ELSE events.league_name END",
                    (s.event_bp_id, s.sr_id or None, s.genius_id or None,
                     s.home, s.away, _iso(s.kickoff_utc),
                     s.country_id or None, s.country_name or None,
                     s.league_id or None, s.league_name or None),
                )
                cur = conn.execute(
                    "INSERT INTO snapshots "
                    "(ts_utc, event_id, bookmaker, status, match_minute, "
                    "score_home, score_away, fetch_status, fetch_error) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (_iso(s.ts_utc), s.event_bp_id, s.bookmaker.value,
                     s.status.value, s.match_minute, s.score_home, s.score_away,
                     s.fetch_status.value, s.fetch_error),
                )
                snap_id = cur.lastrowid
                rows = [
                    (snap_id, s.event_bp_id, _iso(s.ts_utc),
                     s.bookmaker.value,
                     key.market_id,
                     key.line if key.line is not None else 0.0,
                     key.side, odds, prob)
                    for key, (odds, prob) in s.prices.items()
                ]
                if rows:
                    conn.executemany(
                        "INSERT INTO prices "
                        "(snapshot_id, event_id, ts_utc, bookmaker, "
                        "market_id, line, side, odds, probability) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        rows,
                    )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


def _iso(dt: datetime) -> str:
    """Format a non-None UTC datetime as ISO-8601.

    Snapshot.ts_utc and Snapshot.kickoff_utc are both non-Optional, so None
    is never passed here. models._iso accepts Optional[datetime] — keep
    that distinction in mind if you ever copy this helper.
    """
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
