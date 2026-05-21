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
