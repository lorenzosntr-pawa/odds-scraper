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
                    "(id, sr_id, genius_id, home, away, kickoff_utc) "
                    "VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET "
                    "  sr_id = COALESCE(NULLIF(events.sr_id, ''), excluded.sr_id), "
                    "  genius_id = COALESCE(NULLIF(events.genius_id, ''), excluded.genius_id), "
                    "  home = CASE WHEN events.home = '' THEN excluded.home ELSE events.home END, "
                    "  away = CASE WHEN events.away = '' THEN excluded.away ELSE events.away END",
                    (s.event_bp_id, s.sr_id or None, s.genius_id or None,
                     s.home, s.away, _iso(s.kickoff_utc)),
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
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
